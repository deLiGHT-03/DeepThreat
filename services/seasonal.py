"""Season-long per-game position-group production vs the league average."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from services.matchups import (POSITION_ORDER, get_normalized_slot_order,
                               get_team_position_groups)
from services.impact_players import count_player_impact_flips, player_impact_percentage
from services.sleeper import (SleeperError, active_week, completed_teams, get_json,
                              get_league_dashboard, get_player_index, season_end_week,
                              season_week_count)


def _points(row: dict | None) -> float | None:
    if row is None:
        return None
    value = row.get("custom_points") if row.get("custom_points") is not None else row.get("points")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _starter_finished(player: dict, playing: set, finished: set) -> bool:
    """Whether a starter has no game left to play in the week being checked.

    A player with no NFL team, or whose team is on a bye, cannot score again this week; one
    whose team plays has finished only once that game is over.
    """
    team = str(player.get("team") or "").strip().upper()
    return not team or team not in playing or team in finished


def _settled_rosters(season: str, week: int, rows: list[dict], player_index: dict,
                     schedule: list[dict] | None = None) -> set:
    """Roster ids whose lineup for a week has stopped scoring, so the week is a finished one.

    A team's week is over when every starter's NFL game has finished -- a player on a bye, or
    one without an NFL team, has no game left to play and counts as finished. The week in
    progress joins a season figure only for the teams this returns, because a lineup still
    being played would otherwise be averaged as though it had finished, scoring a team by how
    many of its players had already kicked off. When the schedule cannot say which games are
    over, no team's partial week is counted.
    """
    info = completed_teams(season, week, schedule)
    if info is None:
        return set()
    playing, finished = info["playing"], info["finished"]
    settled = set()
    for row in rows:
        starters = [str(player_id) for player_id in (row.get("starters") or [])
                    if str(player_id or "") not in {"", "0"}]
        if all(_starter_finished(player_index.get(player_id, {}), playing, finished)
               for player_id in starters):
            settled.add(row.get("roster_id"))
    return settled


def get_seasonal_summary(league_id: str, username: str, week_count: int | None = None,
                         manager_id: str = "", normalized: bool = False,
                         defense: bool = False, true_record: bool = False,
                         ppg: bool = False) -> dict:
    normalized = normalized or defense
    dashboard = get_league_dashboard(league_id)
    # The window the Player Data Season tables count: every week the league has scored, and the
    # week in progress as far as it has been played. It runs to the end of the league's season
    # rather than the end of its regular season, so a finished season reads whole.
    max_week = season_end_week(dashboard["league"])
    week_count = (season_week_count(dashboard["league"]) if week_count is None
                  else min(max(int(week_count), 0), max_week))
    username_key = username.strip().casefold()
    manager_id = str(manager_id).strip()
    own_team = next((team for team in dashboard["teams"]
                     if manager_id and str(team["owner_id"]) == manager_id), None)
    if own_team is None:
        own_team = next((team for team in dashboard["teams"]
                         if username_key and team["username"].casefold() == username_key), None)
    if own_team is None:
        return {"league": dashboard["league"], "username": username, "manager_id": manager_id,
                "team": None, "teams": [], "positions": [], "groups": [], "games": 0,
                "weeks_played": 0, "normalized": normalized, "defense": defense,
                "true_record": true_record, "ppg": ppg, "failed_weeks": []}

    player_index = get_player_index()
    roster_positions = dashboard["league"].get("roster_positions") or []
    lineup_slots = [str(slot) for slot in roster_positions
                    if str(slot).upper() not in {"BN", "IR", "TAXI", "RESERVE"}]
    point_totals = defaultdict(lambda: defaultdict(float))
    league_point_totals = defaultdict(float)
    team_games = defaultdict(int)
    league_games = 0
    points_against_totals = defaultdict(float)
    points_against_games = defaultdict(int)
    league_points_against_total = 0.0
    league_points_against_games = 0
    all_play_records = defaultdict(lambda: [0, 0, 0])

    def fetch_week(week: int) -> tuple[int, list[dict], bool]:
        try:
            return week, get_json(f"/league/{league_id}/matchups/{week}") or [], False
        except SleeperError:
            return week, [], True

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(fetch_week, range(1, week_count + 1)))

    failed_weeks = [week for week, _, failed in results if failed]
    # The week in progress is the one this window may end on while the league's season is being
    # played. Its games are still finishing, so a team joins it only once every starter's game
    # is over -- a half-played lineup would score a team by how many of its players had already
    # kicked off, which is exactly what an average of finished games must not do.
    live_week = active_week(dashboard["league"])
    if live_week is None or live_week > week_count:
        live_week = None
    settled = set()
    if live_week is not None:
        live_rows = next((rows for week, rows, _ in results if week == live_week), [])
        settled = _settled_rosters(str(dashboard["league"].get("season", "")), live_week,
                                   live_rows, player_index)

    def counts(week: int, roster_id) -> bool:
        """Whether a team's week is a finished one that may join the season averages."""
        return week != live_week or roster_id in settled

    order = get_normalized_slot_order(lineup_slots) if normalized else POSITION_ORDER
    teams_by_id = {team["roster_id"]: team for team in dashboard["teams"]}
    for week, rows, failed in results:
        if true_record:
            scored_teams = [(row.get("roster_id"), _points(row)) for row in rows
                            if row.get("roster_id") in teams_by_id and _points(row) is not None
                            and counts(week, row.get("roster_id"))]
            for index, (roster_id, score) in enumerate(scored_teams):
                for opponent_id, opponent_score in scored_teams[index + 1:]:
                    if round(score, 2) == round(opponent_score, 2):
                        all_play_records[roster_id][2] += 1
                        all_play_records[opponent_id][2] += 1
                    elif score > opponent_score:
                        all_play_records[roster_id][0] += 1
                        all_play_records[opponent_id][1] += 1
                    else:
                        all_play_records[roster_id][1] += 1
                        all_play_records[opponent_id][0] += 1
        by_matchup = defaultdict(list)
        for row in rows:
            if row.get("matchup_id") is not None:
                by_matchup[row["matchup_id"]].append(row)
        for matchup_rows in by_matchup.values():
            if len(matchup_rows) != 2:
                continue
            for row in matchup_rows:
                roster_id = row.get("roster_id")
                if roster_id not in teams_by_id or _points(row) is None:
                    continue
                if not counts(week, roster_id):
                    continue
                opponent_row = next((opponent for opponent in matchup_rows
                                     if opponent.get("roster_id") != roster_id), None)
                if (normalized and opponent_row and _points(opponent_row) is not None
                        and counts(week, opponent_row.get("roster_id"))):
                    opponent_points = _points(opponent_row)
                    points_against_totals[roster_id] += opponent_points
                    points_against_games[roster_id] += 1
                    league_points_against_total += opponent_points
                    league_points_against_games += 1
                scoring_row = row
                if defense:
                    scoring_row = opponent_row
                    if (scoring_row is None or _points(scoring_row) is None
                            or not counts(week, scoring_row.get("roster_id"))):
                        continue
                groups, scores_available = get_team_position_groups(
                    scoring_row, player_index, lineup_slots, normalized
                )
                if not scores_available:
                    continue
                team_games[roster_id] += 1
                league_games += 1
                for position, group in groups.items():
                    points = group["points"]
                    point_totals[roster_id][position] += points
                    league_point_totals[position] += points

    all_positions = set(league_point_totals)
    if normalized and league_games:
        position_order = list(order)
    else:
        position_order = [pos for pos in order if pos in all_positions]
        position_order.extend(sorted(all_positions - set(order)))
    team_summaries = []
    for roster_id, team in teams_by_id.items():
        groups = [{
            "position": position,
            "total_diff": (
                point_totals[roster_id][position] / team_games[roster_id]
                - league_point_totals[position] / league_games
            ) * team_games[roster_id] if team_games[roster_id] and league_games else 0,
            "games": team_games[roster_id],
            "average": (
                point_totals[roster_id][position] / team_games[roster_id]
                - league_point_totals[position] / league_games
            ) if team_games[roster_id] and league_games else 0,
            "ppg": point_totals[roster_id][position] / team_games[roster_id]
                if team_games[roster_id] else 0,
        } for position in position_order]
        team_summary = {"team": team, "groups": groups,
                        "games": team_games[roster_id],
                        "is_mine": roster_id == own_team["roster_id"]}
        if normalized:
            league_points_against_average = (
                league_points_against_total / league_points_against_games
                if league_points_against_games else 0
            )
            team_points_against_average = (
                points_against_totals[roster_id] / points_against_games[roster_id]
                if points_against_games[roster_id] else 0
            )
            luck = league_points_against_average - team_points_against_average
            team_summary["luck"] = luck
        if true_record:
            wins, losses, ties = all_play_records[roster_id]
            team_summary["true_record"] = f"{wins}-{losses}-{ties}"
        team_summaries.append(team_summary)
    team_summaries.sort(key=lambda item: (not item["is_mine"], str(item["team"]["name"]).casefold()))
    own_summary = team_summaries[0] if team_summaries and team_summaries[0]["is_mine"] else None
    return {"league": dashboard["league"], "username": username, "manager_id": manager_id,
            "team": own_team, "teams": team_summaries,
            "positions": position_order,
            "groups": own_summary["groups"] if own_summary else [],
            "games": own_summary["games"] if own_summary else 0,
            "weeks_played": week_count,
            "normalized": normalized,
            "defense": defense,
            "true_record": true_record,
            "ppg": ppg,
            "failed_weeks": failed_weeks}


def get_seasonal_impact_summary(league_id: str, username: str, week_count: int | None = None,
                                manager_id: str = "") -> dict:
    """Aggregate each normalized starter's weekly outcome flips for every team."""
    dashboard = get_league_dashboard(league_id)
    # The window the Player Data Season tables count: every week the league has scored, and the
    # week in progress as far as it has been played. It runs to the end of the league's season
    # rather than the end of its regular season, so a finished season reads whole.
    max_week = season_end_week(dashboard["league"])
    week_count = (season_week_count(dashboard["league"]) if week_count is None
                  else min(max(int(week_count), 0), max_week))
    username_key = username.strip().casefold()
    manager_id = str(manager_id).strip()
    own_team = next((team for team in dashboard["teams"]
                     if manager_id and str(team["owner_id"]) == manager_id), None)
    if own_team is None:
        own_team = next((team for team in dashboard["teams"]
                         if username_key and team["username"].casefold() == username_key), None)

    player_index = get_player_index()
    roster_positions = dashboard["league"].get("roster_positions") or []
    lineup_slots = [str(slot) for slot in roster_positions
                    if str(slot).upper() not in {"BN", "IR", "TAXI", "RESERVE"}]
    positions = get_normalized_slot_order(lineup_slots)
    teams_by_id = {team["roster_id"]: team for team in dashboard["teams"]}
    player_totals = defaultdict(lambda: defaultdict(lambda: {"name": "", "adjusted_points": 0.0,
                                                            "starts": 0}))
    team_season_totals = defaultdict(float)

    def fetch_week(week: int) -> tuple[int, list[dict], bool]:
        try:
            return week, get_json(f"/league/{league_id}/matchups/{week}") or [], False
        except SleeperError:
            return week, [], True

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(fetch_week, range(1, week_count + 1)))
    failed_weeks = [week for week, _, failed in results if failed]
    # The same rule as the team Season tab: the week in progress joins only for the teams whose
    # lineups have finished, so an unfinished one cannot be read as though it were a whole week.
    live_week = active_week(dashboard["league"])
    if live_week is None or live_week > week_count:
        live_week = None
    settled = set()
    if live_week is not None:
        live_rows = next((rows for week, rows, _ in results if week == live_week), [])
        settled = _settled_rosters(str(dashboard["league"].get("season", "")), live_week,
                                   live_rows, player_index)

    for week, rows, failed in results:
        if failed:
            continue
        if week == live_week:
            rows = [row for row in rows if row.get("roster_id") in settled]
        team_scores = []
        for row in rows:
            roster_id = row.get("roster_id")
            score = _points(row)
            if roster_id in teams_by_id and score is not None:
                team_scores.append((roster_id, score))
                team_season_totals[roster_id] += score
        normalized_groups = {}
        slot_totals = defaultdict(float)
        for row in rows:
            roster_id = row.get("roster_id")
            if roster_id not in teams_by_id:
                continue
            groups, _ = get_team_position_groups(row, player_index, lineup_slots, True)
            normalized_groups[roster_id] = groups
            for position, group in groups.items():
                slot_totals[position] += group["points"]
        slot_averages = {position: points / len(team_scores) for position, points in slot_totals.items()} \
            if team_scores else {}
        for row in rows:
            roster_id = row.get("roster_id")
            if roster_id not in normalized_groups:
                continue
            groups = normalized_groups[roster_id]
            started_ids = set()
            for group in groups.values():
                for player in group["players"]:
                    player_id = str(player.get("id") or player["name"])
                    if player_id in started_ids:
                        continue
                    started_ids.add(player_id)
                    totals = player_totals[roster_id][player_id]
                    totals["name"] = player["name"]
                    totals["starts"] += 1
                    if player["points"] is None:
                        continue
                    above_average = player["points"] - slot_averages.get(player["slot"], 0.0)
                    totals["adjusted_points"] += above_average

    season_scores = [(roster_id, team_season_totals[roster_id])
                     for roster_id in teams_by_id if roster_id in team_season_totals]
    team_summaries = []
    for roster_id, team in teams_by_id.items():
        players = []
        for player_id, stats in player_totals[roster_id].items():
            if stats["starts"] <= 0:
                continue
            flips, comparisons = count_player_impact_flips(
                stats["adjusted_points"], season_scores
            )
            players.append({
                "id": player_id,
                "name": stats["name"],
                "impact": player_impact_percentage(flips, comparisons),
                "flips": flips,
                "comparisons": comparisons,
                "starts": stats["starts"],
                "adjusted_points": stats["adjusted_points"],
            })
        players.sort(key=lambda player: (player["impact"] is None,
                                         -(player["impact"] or 0), player["name"].casefold()))
        team_summaries.append({"team": team, "players": players, "is_mine": own_team is not None
                               and roster_id == own_team["roster_id"]})
    team_summaries.sort(key=lambda item: (not item["is_mine"],
                                          str(item["team"]["name"]).casefold()))
    return {"league": dashboard["league"], "username": username, "manager_id": manager_id,
            "team": own_team, "teams": team_summaries, "positions": positions,
            "weeks_played": week_count, "normalized": True, "impact": True,
            "failed_weeks": failed_weeks}
