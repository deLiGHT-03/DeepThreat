"""Weekly fantasy matchup breakdowns grouped by each starter's position."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from services.impact_players import count_player_impact_flips, player_impact_percentage
from services.sleeper import (SleeperError, get_json, get_league_dashboard,
                              get_nfl_schedule, get_playoff_brackets,
                              get_playoff_round_count, get_player_index, last_week_of,
                              playoff_matchup_stages, playoff_week_start,
                              regular_season_week_count, week_label, MAX_NFL_WEEK)


POSITION_ORDER = ("QB", "SF", "RB", "WR", "TE", "FLEX", "K", "DEF", "DL", "LB", "DB", "IDP", "P", "OL")


def get_normalized_slot_order(lineup_slots: list[str]) -> tuple[str, ...]:
    """Build normalized display labels from this league's actual lineup format."""
    slots = [str(slot).upper() for slot in lineup_slots]
    counts = {
        position: sum(slot == position for slot in slots)
        for position in ("QB", "RB", "WR", "TE")
    }
    flex_count = sum(slot in {"FLEX", "REC_FLEX", "WRRB_FLEX", "IDP_FLEX"} for slot in slots)
    sf_count = slots.count("SUPER_FLEX")
    ordered = []
    if counts["QB"]:
        ordered.append("QB1")
    if sf_count == 1:
        ordered.append("SF")
    else:
        ordered.extend(f"SF{index}" for index in range(1, sf_count + 1))
    ordered.extend(f"QB{index}" for index in range(2, counts["QB"] + 1))
    for position in ("RB", "WR"):
        ordered.extend(f"{position}{index}" for index in range(1, counts[position] + 1))
    if counts["TE"] == 1:
        ordered.append("TE")
    else:
        ordered.extend(f"TE{index}" for index in range(1, counts["TE"] + 1))
    ordered.extend(f"FLEX{index}" for index in range(1, flex_count + 1))
    return tuple(ordered)


def _number(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _position(player: dict) -> str | None:
    """The position a player counts as, read from his own record.

    His lineup slot is deliberately not consulted: which group a starter belongs to is
    ``_slot_group``'s question, and answering it here as well left two mappings that could
    disagree -- a superflex counted as a quarterback in one of them and by the slot in the other.
    """
    player_position = str(player.get("position") or "").upper()
    if player_position in {"DEF", "DST", "D/ST"}:
        return "DEF"
    if player_position in {"", "BN", "IR", "TAXI"}:
        return None
    return player_position


def _starter_score(row: dict, player_id: str, index: int) -> float | None:
    ordered = row.get("starters_points") or []
    if isinstance(ordered, list) and index < len(ordered):
        score = _number(ordered[index])
        if score is not None:
            return score
    player_scores = row.get("players_points") or {}
    if isinstance(player_scores, dict):
        return _number(player_scores.get(player_id, player_scores.get(str(player_id))))
    return None


def _slot_group(slot: str, player: dict) -> str | None:
    """The group a starter occupies in a lineup slot.

    A superflex keeps a bucket of its own. It accepts any of the offensive positions and takes
    one whenever no quarterback is left for it, so a slot-level answer is the only honest one.
    """
    slot = str(slot or "").upper()
    if slot == "SUPER_FLEX":
        return "SF"
    if slot == "QB":
        return "QB"
    if slot in {"FLEX", "REC_FLEX", "WRRB_FLEX", "IDP_FLEX"}:
        return "FLEX"
    if slot in {"DEF", "DST", "D/ST"}:
        return "DEF"
    if slot in {"RB", "WR", "TE", "K"}:
        return slot
    return _position(player)


def _player_display_names(player: dict, player_id: str) -> tuple[str, str]:
    """Return the full name plus a compact 'F. Last' form for tight score columns."""
    first = str(player.get("first_name") or "").strip()
    last = str(player.get("last_name") or "").strip()
    full = f"{first} {last}".strip() or player_id
    if _position(player) == "DEF":
        return full, f"{str(player.get('team') or player_id).strip()} D/ST"
    tokens = full.split()
    if len(tokens) > 1:
        return full, f"{tokens[0][0].upper()}. {' '.join(tokens[1:])}"
    return full, full


def _broad_group(slot_label: str) -> str:
    """The wide position group a normalized slot belongs to.

    A superflex keeps a bucket of its own rather than joining the quarterbacks. The slot accepts
    any of the offensive positions, and it takes one whenever no quarterback is left for it, so
    filing it under the quarterbacks would book a running back's or a receiver's points as
    quarterback production -- which this league does in about one team-week in twenty. Every team
    starts exactly one, so its own column still compares like with like.
    """
    return str(slot_label).rstrip("0123456789")


def get_team_position_groups(row: dict, player_index: dict, lineup_slots: list[str],
                             normalized: bool = False) -> tuple[dict, bool]:
    """Return points by lineup group: by slot when normalized, by wide position when not.

    Group View is not a second reading of the week: it is the same best-ball assignment merged
    one level wider, so a starter's points belong to the slot his score earned him rather than
    the one he was named in. Without that, a wide receiver started in a flex slot would count
    as flex production in Group View and as wide receiver production in the slot view, and the
    two pages would disagree about the same week.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    starters = row.get("starters") or []
    player_scores = row.get("players_points") or {}
    score_data_available = False

    if normalized:
        # Only starters in this matchup can be normalized; bench scores are excluded.
        ids = starters
        candidates = []
        starter_indices = {str(pid): index for index, pid in enumerate(starters)}
        for raw_id in ids:
            player_id = str(raw_id or "")
            if not player_id or player_id == "0":
                continue
            player = player_index.get(player_id, {})
            position = _position(player)
            if not position:
                continue
            score = _number(player_scores.get(player_id)) if isinstance(player_scores, dict) else None
            if score is None and player_id in starter_indices:
                score = _starter_score(row, player_id, starter_indices[player_id])
            name, short_name = _player_display_names(player, player_id)
            candidates.append({"id": player_id, "position": position, "points": score,
                               "name": name, "short_name": short_name})
        candidates.sort(key=lambda player: (
            player["points"] is None,
            -(player["points"] or 0),
            player["name"],
        ))
        available = {player["id"]: player for player in candidates}
        assignment_counts: dict[str, int] = defaultdict(int)

        def assign(slot: str, eligible: set[str], label: str) -> None:
            nonlocal score_data_available
            selection = next((player for player in candidates
                              if player["id"] in available and player["position"] in eligible), None)
            assignment_counts[label] += 1
            if selection is None:
                return
            available.pop(selection["id"])
            if selection["points"] is not None:
                score_data_available = True
            slot_number = assignment_counts[label]
            if label in {"QB", "RB", "WR", "FLEX"}:
                slot_name = f"{label}{slot_number}"
            elif label == "TE" and sum(str(item).upper() == "TE" for item in lineup_slots) > 1:
                slot_name = f"TE{slot_number}"
            elif label == "SF" and sum(str(item).upper() == "SUPER_FLEX" for item in lineup_slots) > 1:
                slot_name = f"SF{slot_number}"
            else:
                slot_name = label
            grouped[slot_name].append({"id": selection["id"], "name": selection["name"],
                                       "short_name": selection["short_name"],
                                       "points": selection["points"], "slot": slot_name})

        fixed_eligibility = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
                             "K": {"K"}, "DEF": {"DEF"}, "DST": {"DEF"}, "D/ST": {"DEF"}}
        for slot in lineup_slots:
            normalized_slot = str(slot).upper()
            if normalized_slot in fixed_eligibility:
                label = "DEF" if normalized_slot in {"DEF", "DST", "D/ST"} else normalized_slot
                assign(normalized_slot, fixed_eligibility[normalized_slot], label)
        # The superflex takes the highest-scoring player left once the dedicated slots are
        # filled, with one forced exception: a leftover quarterback is eligible only for the QB
        # slot or the superflex, and the QB slot already holds the better one, so he must come
        # here. Playing a running back or receiver in his place would leave his points without a
        # legal slot anywhere and the team's columns would stop adding up to its score. With no
        # quarterback left, the best remaining RB/WR/TE takes the slot by score.
        for slot in lineup_slots:
            if str(slot).upper() == "SUPER_FLEX":
                if any(player["id"] in available and player["position"] == "QB"
                       for player in candidates):
                    assign(slot, {"QB"}, "SF")
                else:
                    assign(slot, {"RB", "WR", "TE"}, "SF")
        for slot in lineup_slots:
            normalized_slot = str(slot).upper()
            eligible = {"FLEX": {"RB", "WR", "TE"}, "REC_FLEX": {"WR", "TE"},
                        "WRRB_FLEX": {"RB", "WR"}, "IDP_FLEX": {"DL", "LB", "DB", "IDP"}}.get(normalized_slot)
            if eligible:
                assign(slot, eligible, "FLEX")
    else:
        # Group View reads the slot view's own answer instead of re-deriving a second one, so
        # the two cannot disagree about which group a starter's points belong to.
        slot_labels = get_normalized_slot_order(lineup_slots)
        slot_groups, score_data_available = get_team_position_groups(
            row, player_index, lineup_slots, True
        )
        for slot_label in list(slot_labels) + sorted(set(slot_groups) - set(slot_labels)):
            for starter in slot_groups.get(slot_label, {}).get("players", []):
                grouped[_broad_group(slot_label)].append(starter)

    groups = {}
    order = get_normalized_slot_order(lineup_slots) if normalized else POSITION_ORDER
    position_order = list(order) + sorted(set(grouped) - set(order))
    for position in position_order:
        players = grouped.get(position)
        if players:
            groups[position] = {
                "points": sum(p["points"] or 0 for p in players),
                "players": players,
            }
    return groups, score_data_available


def _team_week(row: dict, player_index: dict, teams_by_roster: dict,
               lineup_slots: list[str], normalized: bool) -> dict:
    groups, score_data_available = get_team_position_groups(row, player_index, lineup_slots, normalized)
    if normalized:
        points = sum(group["points"] for group in groups.values()) if score_data_available else None
    else:
        points = _number(row.get("custom_points")) if row.get("custom_points") is not None else _number(row.get("points"))
    return {
        "roster_id": row.get("roster_id"),
        "name": teams_by_roster.get(row.get("roster_id"), {}).get("name", f"Team {row.get('roster_id', '')}"),
        "record": teams_by_roster.get(row.get("roster_id"), {}).get("record", "0-0"),
        "logo_url": teams_by_roster.get(row.get("roster_id"), {}).get("logo_url", ""),
        "logo_fallback_urls": teams_by_roster.get(row.get("roster_id"), {}).get("logo_fallback_urls", []),
        "points": points,
        "groups": groups,
        "score_data_available": score_data_available,
    }


def _should_project_week(league: dict, week: int, rows: list[dict]) -> tuple[bool, list[dict], dict]:
    """Project only before any NFL game in this week starts; fall back to live scores."""
    season = str(league.get("season", ""))
    schedule = []
    state = {}
    try:
        schedule = get_nfl_schedule(season)
    except SleeperError:
        pass
    try:
        state = get_json("/state/nfl") or {}
    except SleeperError:
        pass

    week_games = [game for game in schedule if str(game.get("week")) == str(week)]
    if week_games:
        not_started = {"pre_game", "scheduled", "canceled", "cancelled"}
        started = any(str(game.get("status", "")).casefold() not in not_started
                      for game in week_games)
        return not started, schedule, state

    if str(state.get("season")) == season:
        try:
            current_week = int(state.get("week") or state.get("display_week") or 1)
            if week > current_week:
                return True, schedule, state
            if week < current_week:
                return False, schedule, state
        except (TypeError, ValueError):
            pass
    has_live_points = any(
        (_number(row.get("custom_points") if row.get("custom_points") is not None
                 else row.get("points")) or 0) > 0
        for row in rows
    )
    return not has_live_points and str(state.get("season")) == season, schedule, state


def is_week_started(season: str, week: int) -> bool | None:
    """Return live/finished status for any NFL game in a week, if schedule is available."""
    schedule = get_nfl_schedule(season)
    week_games = [game for game in schedule if str(game.get("week")) == str(week)]
    if not week_games:
        return None
    not_started = {"pre_game", "scheduled", "canceled", "cancelled"}
    return any(str(game.get("status", "")).casefold() not in not_started
               for game in week_games)


def _completed_projection_weeks(week: int, season: str, schedule: list[dict], state: dict) -> list[int]:
    if str(state.get("season")) == season:
        try:
            upper_week = min(week - 1, int(state.get("week") or state.get("display_week") or 1) - 1)
            return list(range(1, max(0, upper_week) + 1))
        except (TypeError, ValueError):
            pass
    statuses_by_week: dict[int, list[str]] = defaultdict(list)
    for game in schedule:
        try:
            game_week = int(game.get("week"))
        except (TypeError, ValueError):
            continue
        if game_week < week:
            statuses_by_week[game_week].append(str(game.get("status", "")).casefold())
    complete_statuses = {"complete", "completed", "canceled", "cancelled"}
    return sorted(game_week for game_week, statuses in statuses_by_week.items()
                  if statuses and all(status in complete_statuses for status in statuses))


def _team_position_averages(league_id: str, weeks: list[int], player_index: dict,
                            teams_by_roster: dict, lineup_slots: list[str],
                            normalized: bool) -> dict:
    """Average each team's standard groups or normalized slots over completed weeks."""
    if not weeks:
        return {}

    def fetch_week(week: int) -> list[dict]:
        return get_json(f"/league/{league_id}/matchups/{week}") or []

    with ThreadPoolExecutor(max_workers=6) as executor:
        week_rows = list(executor.map(fetch_week, weeks))
    totals = defaultdict(lambda: defaultdict(float))
    games = defaultdict(int)
    for rows in week_rows:
        for row in rows:
            roster_id = row.get("roster_id")
            if roster_id not in teams_by_roster or _number(
                row.get("custom_points") if row.get("custom_points") is not None else row.get("points")
            ) is None:
                continue
            groups, scores_available = get_team_position_groups(
                row, player_index, lineup_slots, normalized
            )
            if not scores_available:
                continue
            games[roster_id] += 1
            for position, group in groups.items():
                totals[roster_id][position] += group["points"]
    return {roster_id: {position: points / games[roster_id]
                        for position, points in totals[roster_id].items()}
            for roster_id in games}


def get_week_matchups(league_id: str, week: int, username: str = "", manager_id: str = "",
                      normalized: bool = False, impact: bool = False) -> dict:
    dashboard = get_league_dashboard(league_id)
    # The tabs run past the regular season into the league's playoff rounds, and those weeks
    # are weeks of its season, so they open here exactly as a regular-season week does.
    max_week = dashboard["league"].get("last_week") or dashboard["league"]["regular_season_weeks"]
    if not 1 <= int(week) <= max_week:
        raise ValueError(f"Choose a week between 1 and {max_week} for this league.")
    raw_rows = get_json(f"/league/{league_id}/matchups/{week}") or []
    player_index = get_player_index()
    teams_by_roster = {team["roster_id"]: team for team in dashboard["teams"]}
    username_key = username.strip().casefold()
    manager_id = str(manager_id).strip()
    my_team = next((team for team in dashboard["teams"]
                    if manager_id and str(team["owner_id"]) == manager_id), None)
    if my_team is None:
        my_team = next((team for team in dashboard["teams"]
                        if username_key and team["username"].casefold() == username_key), None)
    my_roster_id = my_team["roster_id"] if my_team else None
    roster_positions = dashboard["league"].get("roster_positions") or []
    lineup_slots = [str(slot) for slot in roster_positions if str(slot).upper() not in {"BN", "IR", "TAXI", "RESERVE"}]
    # A playoff week's cards say which bracket they belong to, so a playoff match can be told
    # from a consolation one and the final from the rest of the round. Only playoff weeks pay
    # for the brackets, and only the matches the bracket has already decided can be named.
    playoff_start = playoff_week_start(dashboard["league"])
    week_stages = {}
    if playoff_start is not None and int(week) >= playoff_start:
        brackets = get_playoff_brackets(league_id)
        week_stages = playoff_matchup_stages(dashboard["league"], brackets).get(int(week), {})

    project_week, schedule, state = _should_project_week(dashboard["league"], week, raw_rows)
    show_projections = project_week and not impact
    grouping_normalized = normalized or impact
    player_win_rates = {}
    if impact:
        team_scores = []
        for row in raw_rows:
            score = _number(row.get("custom_points") if row.get("custom_points") is not None
                            else row.get("points"))
            if score is not None:
                team_scores.append((row.get("roster_id"), score))
        normalized_groups = {}
        slot_totals = defaultdict(float)
        for row in raw_rows:
            groups, _ = get_team_position_groups(row, player_index, lineup_slots, True)
            normalized_groups[row.get("roster_id")] = groups
            for position, group in groups.items():
                slot_totals[position] += group["points"]
        slot_averages = {position: points / len(team_scores) for position, points in slot_totals.items()} \
            if team_scores else {}
        for groups in normalized_groups.values():
            for position, group in groups.items():
                for player in group["players"]:
                    if player["points"] is None:
                        player_win_rates[player["id"]] = None
                        continue
                    above_average = player["points"] - slot_averages.get(position, 0.0)
                    flips, comparisons = count_player_impact_flips(above_average, team_scores)
                    player_win_rates[player["id"]] = player_impact_percentage(flips, comparisons)
    averages = {}
    if normalized:
        projection_positions = get_normalized_slot_order(lineup_slots)
    else:
        standard_groups = {_slot_group(slot, {}) for slot in lineup_slots}
        projection_positions = tuple(position for position in POSITION_ORDER
                                     if position in standard_groups)
    if show_projections:
        history_weeks = _completed_projection_weeks(
            week, str(dashboard["league"].get("season", "")), schedule, state
        )
        try:
            averages = _team_position_averages(
                league_id, history_weeks, player_index, teams_by_roster, lineup_slots,
                normalized,
            )
        except SleeperError:
            averages = {}

    rows_by_matchup = defaultdict(list)
    for row in raw_rows:
        matchup_id = row.get("matchup_id")
        if matchup_id is not None:
            team = _team_week(row, player_index, teams_by_roster, lineup_slots, grouping_normalized)
            if impact:
                for group in team["groups"].values():
                    for player in group["players"]:
                        player["win_rate"] = player_win_rates.get(player.get("id"))
            if show_projections:
                team_averages = averages.get(row.get("roster_id"))
                team["groups"] = {
                    position: {"points": (team_averages or {}).get(position, 0.0), "players": []}
                    for position in projection_positions
                }
                team["points"] = sum(group["points"] for group in team["groups"].values()) \
                    if team_averages is not None else None
                team["score_data_available"] = team_averages is not None
            rows_by_matchup[matchup_id].append(team)

    matchups = []
    for matchup_id, teams in sorted(rows_by_matchup.items(), key=lambda item: str(item[0])):
        if len(teams) != 2:
            continue
        teams.sort(key=lambda team: team["roster_id"] or 0)
        left, right = teams
        if my_roster_id is not None and right["roster_id"] == my_roster_id:
            left, right = right, left
        all_positions = set(left["groups"]) | set(right["groups"])
        order = get_normalized_slot_order(lineup_slots) if grouping_normalized else POSITION_ORDER
        positions = list(projection_positions) if show_projections else (
            list(order) if normalized else [pos for pos in order if pos in all_positions]
        )
        if impact:
            positions = list(order)
        positions.extend(sorted(all_positions - set(order)))
        comparison = []
        for position in positions:
            left_group = left["groups"].get(position, {"points": 0, "players": []})
            right_group = right["groups"].get(position, {"points": 0, "players": []})
            comparison.append({
                "position": position,
                "left": left_group,
                "right": right_group,
                "diff": left_group["points"] - right_group["points"]
                    if left_group["points"] is not None and right_group["points"] is not None else None,
            })
        matchups.append({
            "teams": [left, right],
            "groups": comparison,
            "stage": week_stages.get(frozenset({left["roster_id"], right["roster_id"]})),
            "is_mine": my_roster_id is not None and my_roster_id in {left["roster_id"], right["roster_id"]},
            "total_diff": (left["points"] - right["points"])
                if left["points"] is not None and right["points"] is not None else None,
            "scores_available": ((left["score_data_available"] and right["score_data_available"])
                                 or (impact and bool(left["groups"] or right["groups"]))),
            "projected": show_projections,
        })

    matchups.sort(key=lambda item: (not item["is_mine"], item["teams"][0]["roster_id"] or 0))
    return {"league": dashboard["league"], "week": week, "matchups": matchups,
            "week_label": week_label(dashboard["league"], week),
            "username": username, "manager_id": manager_id, "my_roster_id": my_roster_id,
            "normalized": normalized, "impact": impact, "projected": show_projections}


def get_default_week(league_id: str) -> int:
    """Use Sleeper's display week for the active season, otherwise Week 1.

    The league's own last week is the limit rather than the end of its regular season, so a
    league in its playoffs opens on the round being played instead of its final regular week.
    """
    try:
        league = get_json(f"/league/{league_id}") or {}
        state = get_json("/state/nfl") or {}
        if str(league.get("season")) == str(state.get("season")):
            week = int(state.get("display_week") or state.get("week") or 1)
            limit = last_week_of(league, get_playoff_round_count(league_id))
            return min(max(week, 1), limit)
    except (SleeperError, TypeError, ValueError):
        pass
    return 1
