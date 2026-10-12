"""Player production summaries from players who started in Sleeper matchups."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from services.matchups import _number, get_default_week
from services.sleeper import (SleeperError, get_json, get_league_dashboard,
                              get_nfl_week_stats, get_player_index, season_end_week,
                              week_label)


POSITION_ORDER = ("QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "IDP", "P", "OL")
VOAS_STARTER_COUNTS = {"QB": 24, "RB": 24, "WR": 24, "TE": 12}


def _position(player: dict) -> str:
    position = str(player.get("position") or "UNK").upper()
    return "DEF" if position in {"DST", "D/ST"} else position


def _starter_score(row: dict, player_id: str, index: int) -> float | None:
    points = row.get("starters_points") or []
    if isinstance(points, list) and index < len(points):
        score = _number(points[index])
        if score is not None:
            return score
    player_points = row.get("players_points") or {}
    if isinstance(player_points, dict):
        return _number(player_points.get(player_id, player_points.get(str(player_id))))
    return None


def _season_week_count(league: dict) -> int:
    """How many of the league's weeks count towards a player's season so far.

    The week in progress counts as far as it has been played. Sleeper records who took the field
    and what each of them scored as each game finishes, so a player whose game is over has his
    points and his game counted while one whose game has not started yet does not. Stopping at
    the last completed week would leave production a reader has just watched out of every number
    they are looking at, and the card opened from these tables counts the live week the same way.
    The season runs past the end of the regular season into the league's playoff weeks, so a
    finished season reads whole.

    A week's official stats are asked for here and an unplayed week answers with an empty set,
    so a week the NFL has not reached yet contributes nothing rather than failing.
    """
    end_week = season_end_week(league)
    try:
        state = get_json("/state/nfl") or {}
        if str(state.get("season")) == str(league.get("season")):
            current = int(state.get("week") or state.get("display_week") or 1)
            return min(end_week, max(0, current))
    except (SleeperError, TypeError, ValueError):
        pass
    return end_week


def _player_from_start(player_index: dict, player_id: str, points: float) -> dict:
    player = player_index.get(str(player_id), {})
    name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip() or str(player_id)
    return {"id": str(player_id), "name": name, "position": _position(player), "points": points}


def _sort_positions(positions: set[str]) -> list[str]:
    return [position for position in POSITION_ORDER if position in positions] + sorted(positions - set(POSITION_ORDER))


def _add_voas(players_by_position: dict, score_key: str = "points") -> None:
    """Compare each player with the average of the top starters at his position.

    Value is measured with the same score as ``score_key``, so a per-game key keeps
    the comparison per-game and never penalises a player for games he did not play.
    """
    for position, players in players_by_position.items():
        scores = sorted((float(player[score_key]) for player in players), reverse=True)
        starter_count = VOAS_STARTER_COUNTS.get(position, 12)
        if not scores:
            continue
        average_starter_score = sum(scores[:starter_count]) / min(starter_count, len(scores))
        for player in players:
            # A player who has not appeared has no per-game value to compare.
            player["voas"] = (float(player[score_key]) - average_starter_score
                              if player.get("games", 1) else None)


def _position_tables(players_by_position: dict, score_key: str) -> list[dict]:
    """Group players into one table per position, best first.

    Both Player Data tabs that show positions share this shape — the weekly one scores on
    that week's points, the seasonal one on points per game — so the two read the same and
    an extra position or a new league position cannot appear in one and not the other.
    """
    tables = []
    for position in _sort_positions(set(players_by_position)):
        players = players_by_position[position]
        players.sort(key=lambda item: (-item[score_key], str(item["team"]["name"]).casefold(),
                                       item["name"].casefold()))
        tables.append({"position": position, "players": players})
    return tables


def get_player_data(league_id: str, username: str, manager_id: str = "",
                    week: int | None = None, seasonal: bool = False,
                    drop_lowest: bool = False, drop_highest: bool = False) -> dict:
    dashboard = get_league_dashboard(league_id)
    username_key = username.strip().casefold()
    manager_id = str(manager_id).strip()
    own_team = next((team for team in dashboard["teams"]
                     if manager_id and str(team["owner_id"]) == manager_id), None)
    if own_team is None:
        own_team = next((team for team in dashboard["teams"]
                         if username_key and team["username"].casefold() == username_key), None)

    teams_by_id = {team["roster_id"]: team for team in dashboard["teams"]}
    player_index = get_player_index()
    if seasonal:
        weeks_played = _season_week_count(dashboard["league"])
        weeks = range(1, weeks_played + 1)
    else:
        week = int(week or get_default_week(league_id))
        # The tabs run past the regular season into the playoff rounds, so a playoff week is a
        # week of this league's season like any other and the same page serves it.
        max_week = dashboard["league"].get("last_week") or dashboard["league"]["regular_season_weeks"]
        if not 1 <= week <= max_week:
            raise ValueError(f"Choose a week between 1 and {max_week} for this league.")
        weeks_played = week
        weeks = (week,)

    def fetch_week(week_number: int) -> tuple[int, list[dict], dict[str, dict] | None, bool]:
        try:
            rows = get_json(f"/league/{league_id}/matchups/{week_number}") or []
            if not isinstance(rows, list):
                return week_number, [], None, True
        except SleeperError:
            return week_number, [], None, True
        if seasonal:
            try:
                stats = get_nfl_week_stats(str(dashboard["league"].get("season", "")), week_number)
            except SleeperError:
                # Without official playing time we cannot tell a missed game from a
                # played one, and counting the week would inflate GP and dilute PPG.
                return week_number, rows, None, True
        else:
            stats = {}
        return week_number, rows, stats, False

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(fetch_week, weeks))
    failed_weeks = [week_number for week_number, _, _, failed in results if failed]
    totals = defaultdict(lambda: defaultdict(lambda: {"name": "", "position": "UNK", "points": 0.0, "games": 0}))
    score_samples = defaultdict(lambda: defaultdict(list))
    weekly_players = defaultdict(list)
    current_roster_ids = {
        roster_id: {str(player["player_id"]) for player in team["players"]}
        for roster_id, team in teams_by_id.items()
    }
    if seasonal:
        for roster_id, team in teams_by_id.items():
            for roster_player in team["players"]:
                player_id = str(roster_player["player_id"])
                player = player_index.get(player_id, {})
                stats = totals[roster_id][player_id]
                stats["name"] = roster_player["name"]
                stats["position"] = _position(player)

    for week_number, rows, nfl_week_stats, failed in results:
        if failed:
            continue
        seen_by_team = defaultdict(set)
        for row in rows:
            roster_id = row.get("roster_id")
            if roster_id not in teams_by_id:
                continue
            if seasonal:
                raw_points = row.get("players_points") or {}
                if not isinstance(raw_points, dict):
                    continue
                points_by_player = {str(player_id): points for player_id, points in raw_points.items()}
                weekly_roster = {str(player_id) for player_id in (row.get("players") or [])}
                starter_indexes = {
                    str(player_id): index for index, player_id in enumerate(row.get("starters") or [])
                    if str(player_id or "") not in {"", "0"}
                }
                for player_id in current_roster_ids[roster_id]:
                    if weekly_roster and player_id not in weekly_roster:
                        continue
                    # GP counts NFL games actually played, so a week is only counted
                    # when Sleeper's official stats record an appearance for him.
                    player_week_stats = (nfl_week_stats or {}).get(player_id) or {}
                    if (_number(player_week_stats.get("gp")) or 0.0) <= 0:
                        continue
                    score = _number(points_by_player.get(player_id))
                    if score is None and player_id in starter_indexes:
                        score = _starter_score(row, player_id, starter_indexes[player_id])
                    if score is None:
                        score = 0.0
                    player = _player_from_start(player_index, player_id, score)
                    stats = totals[roster_id][player_id]
                    stats["name"] = player["name"]
                    stats["position"] = player["position"]
                    stats["points"] += score
                    stats["games"] += 1
                    score_samples[roster_id][player_id].append(score)
                continue
            for index, raw_id in enumerate(row.get("starters") or []):
                player_id = str(raw_id or "")
                if not player_id or player_id == "0" or player_id in seen_by_team[roster_id]:
                    continue
                score = _starter_score(row, player_id, index)
                if score is None:
                    continue
                player = _player_from_start(player_index, player_id, score)
                seen_by_team[roster_id].add(player_id)
                item = {**player, "team": teams_by_id[roster_id],
                        "is_mine": own_team is not None and roster_id == own_team["roster_id"]}
                weekly_players[player["position"]].append(item)
                stats = totals[roster_id][player_id]
                stats["name"] = player["name"]
                stats["position"] = player["position"]
                stats["points"] += score
                stats["games"] += 1

    if seasonal:
        for roster_id, player_samples in score_samples.items():
            for player_id, samples in player_samples.items():
                remaining = list(samples)
                if drop_lowest and remaining:
                    remaining.remove(min(remaining))
                if drop_highest and remaining:
                    remaining.remove(max(remaining))
                stats = totals[roster_id][player_id]
                stats["points"] = sum(remaining)
                stats["games"] = len(remaining)

        team_summaries = []
        players_by_position = defaultdict(list)
        for roster_id, team in teams_by_id.items():
            players = []
            for player_id, stats in totals[roster_id].items():
                player = {"id": player_id, "name": stats["name"],
                          "position": stats["position"],
                          "ppg": stats["points"] / stats["games"] if stats["games"] else 0.0,
                          "total_points": stats["points"],
                          "games": stats["games"],
                          # The seasonal position tables carry the same player records as
                          # the team cards, and those tables name the player's team.
                          "team": team,
                          "is_mine": own_team is not None and roster_id == own_team["roster_id"]}
                players.append(player)
                players_by_position[player["position"]].append(player)
            players.sort(key=lambda item: (-item["ppg"], item["position"], item["name"].casefold()))
            team_summaries.append({"team": team, "players": players,
                                   "is_mine": own_team is not None and roster_id == own_team["roster_id"]})
        # Season VOAS compares points per game, not season totals, so a player is
        # not ranked below a lesser player simply for having missed games.
        _add_voas(players_by_position, "ppg")
        position_tables = _position_tables(players_by_position, "ppg")
        team_summaries.sort(key=lambda item: (not item["is_mine"], str(item["team"]["name"]).casefold()))
        return {"league": dashboard["league"], "username": username, "manager_id": manager_id,
                "team": own_team, "teams": team_summaries,
                "position_tables": position_tables,
                "positions": [table["position"] for table in position_tables],
                "seasonal": True, "weeks_played": weeks_played, "failed_weeks": failed_weeks,
                "drop_lowest": drop_lowest, "drop_highest": drop_highest}

    _add_voas(weekly_players, "points")
    position_tables = _position_tables(weekly_players, "points")
    return {"league": dashboard["league"], "username": username, "manager_id": manager_id,
            "team": own_team, "position_tables": position_tables,
            "positions": [table["position"] for table in position_tables],
            "seasonal": False, "week": week, "week_label": week_label(dashboard["league"], week),
            "weeks_played": weeks_played,
            "failed_weeks": failed_weeks}
