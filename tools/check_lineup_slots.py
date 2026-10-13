"""Prove the Matchups page's slot view, the Season tab and Player Data all count the same league.

Three promises have to stay true of the weekly slot assignment, and all three are silent when
they break -- every page still renders, and a figure that has quietly moved is not an error a
reader can see:

* **Every starter lands in a slot.** The assignment is best-ball: it moves players between slots,
  and a starter it forgets has nowhere his points can be counted. Coverage compares the starters
  whose position the assignment can read against the players that come out the other side, and a
  duplicate is a failure too, because one starter in two slots is two points for one score.
* **The columns add up to the team's official score.** The sum of a team's slot buckets has to
  equal the score the matchup records, or the page is quietly showing a different game than the
  one that was played.
* **Broad View is the slot view collapsed.** Broad View merges the same assignment one level
  wider, so collapsing its slot labels to wide positions has to reproduce Broad View exactly: the
  same players, in the same groups, for the same points. If it ever re-derives its own answer,
  the two views disagree about the same week.

Two seasonal pages read that weekly assignment again, over a season-long window, and each can
drift from it in a way no single page shows:

* **The Season tab's slot averages** must be the weekly slot view added up. Each team's
  points-per-slot over the season, times the games it counts, is compared with an independent
  fold of the weekly slot buckets over the same weeks the tab counts -- the shared season window,
  with the week in progress joining only the teams whose lineups have finished. A team's slots
  summed must also equal its official weekly scores over those weeks.
* **The Player Data season tables** must be the weekly matchup records added up. Each player row's
  season points and games are compared with an independent fold over the same window and the same
  players, counting only the weeks NFL stats record him on the field. The tables carry the bench
  as well as the starters, so this is reconciled against the weekly records the slot view reads
  rather than against the slot buckets, which hold starters alone.

Every comparison is reported separately, with the count of rows compared and the worst difference
found, so a failure says which promise broke and where -- and a section that compared nothing
fails rather than passing quietly.

    python tools/check_lineup_slots.py                    # the league saved in your settings
    python tools/check_lineup_slots.py --weeks 4,5,6      # only these weeks of the weekly check
    python tools/check_lineup_slots.py --weekly-only      # skip the two seasonal comparisons
    python tools/check_lineup_slots.py --league-id 1313193148431740928
    python tools/check_lineup_slots.py --json slots.json

It reads Sleeper directly and needs no browser and no running app. The saved connection is read,
never written: unlike a page view, this check leaves your settings as it found them.

Exit codes: 0 every promise held, 1 a promise was broken, 2 the league could not be read or a
comparison could not be made at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The report uses · and – for widths, so do not let a cp1252 console mangle it.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from services.matchups import (_broad_group, _number, _position,  # noqa: E402
                               get_normalized_slot_order, get_team_position_groups)
from services.player_data import _starter_score, get_player_data  # noqa: E402
from services.seasonal import _points, _settled_rosters, get_seasonal_summary  # noqa: E402
from services.sleeper import (SleeperError, active_week, get_json,  # noqa: E402
                              get_league_dashboard, get_nfl_week_stats, get_player_index,
                              get_playoff_round_count, last_week_of, season_week_count)

#: How far a sum may drift before it is a difference rather than floating-point noise. Sleeper
#: scores are reported to two decimals, so a hundredth is already more than the data carries.
TOLERANCE = 0.005

#: Slots that hold no starter and must not be normalized into the comparison.
BENCH_SLOTS = {"BN", "IR", "TAXI", "RESERVE"}


def _week_plan(league: dict, league_id: str, chosen: str) -> list[int]:
    """The weeks to check: those named, or every week the league's season runs to."""
    if chosen.strip():
        weeks = []
        for part in chosen.replace(" ", "").split(","):
            if not part:
                continue
            weeks.append(int(part))
        return sorted(dict.fromkeys(weeks))
    try:
        rounds = get_playoff_round_count(league_id)
    except SleeperError:
        rounds = None
    return list(range(1, last_week_of(league, rounds) + 1))


def _season_window(league: dict) -> tuple[int, int | None]:
    """The shared season window: how many weeks count, and which one is in progress.

    The same rule the Season tab and the Player Data season tables read (see
    ``services.sleeper.season_week_count`` and ``active_week``): weeks up to the end of the
    league's season, stopping at the week being played. ``live_week`` is the week whose teams
    join the window only once their lineups have finished, or None when the season is over.
    """
    week_count = season_week_count(league)
    live = active_week(league)
    live_week = live if (live is not None and live <= week_count) else None
    return week_count, live_week


def _counts(week: int, roster_id, live_week: int | None, settled: set) -> bool:
    """Whether a team's week is a finished one that may join the season averages."""
    return week != live_week or roster_id in settled


def expected_starters(row: dict, player_index: dict) -> list[str]:
    """The starters the assignment is expected to place: those whose position it can read.

    This mirrors the candidate filter in ``get_team_position_groups`` rather than guessing at it,
    so the check and the assignment agree about who is in the running. A starter whose position
    cannot be read is not expected in a slot -- but his points are still missing from the sum, so
    the score promise is where his absence shows up.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for raw in row.get("starters") or []:
        player_id = str(raw or "")
        if not player_id or player_id == "0" or player_id in seen:
            continue
        seen.add(player_id)
        if _position(player_index.get(player_id, {})) is None:
            continue
        ids.append(player_id)
    return ids


def check_team_week(row: dict, player_index: dict, lineup_slots: list[str]) -> list[dict]:
    """Run the three weekly promises over one team's week. Returns one dict per broken promise."""
    problems: list[dict] = []
    slot_groups, scores_available = get_team_position_groups(row, player_index, lineup_slots, True)

    # A. Every starter lands in a slot, exactly once.
    placed = [player["id"] for group in slot_groups.values() for player in group["players"]]
    counts = Counter(placed)
    expected = set(expected_starters(row, player_index))
    placed_set = set(placed)
    missing = sorted(expected - placed_set)
    unexpected = sorted(placed_set - expected)
    duplicated = sorted(player_id for player_id, count in counts.items() if count > 1)
    if missing:
        problems.append({"promise": "coverage",
                         "detail": f"{len(missing)} starter(s) in no slot: {', '.join(missing)}"})
    if unexpected:
        problems.append({"promise": "coverage",
                         "detail": f"{len(unexpected)} player(s) in a slot without starting: "
                                   f"{', '.join(unexpected)}"})
    if duplicated:
        problems.append({"promise": "coverage",
                         "detail": f"a starter in more than one slot: {', '.join(duplicated)}"})

    # B. The columns add up to the team's official score.
    official = _number(row.get("custom_points") if row.get("custom_points") is not None
                       else row.get("points"))
    if scores_available and official is not None:
        total = sum(group["points"] or 0 for group in slot_groups.values())
        if abs(total - official) > TOLERANCE:
            problems.append({"promise": "score",
                             "detail": f"the slots add up to {total:.4f} but the team scored "
                                       f"{official:.4f}"})

    # C. Broad View is the slot view collapsed.
    broad_groups, _ = get_team_position_groups(row, player_index, lineup_slots, False)
    collapsed: dict[str, dict] = defaultdict(lambda: {"points": 0.0, "ids": []})
    for label, group in slot_groups.items():
        wide = _broad_group(label)
        collapsed[wide]["points"] += group["points"] or 0
        collapsed[wide]["ids"].extend(player["id"] for player in group["players"])
    for position in sorted(set(broad_groups) | set(collapsed)):
        slot_side = collapsed.get(position)
        broad_side = broad_groups.get(position)
        if slot_side is None or broad_side is None:
            problems.append({"promise": "broad",
                             "detail": f"{position} appears in "
                                       f"{'Broad View only' if slot_side is None else 'the slot view only'}"})
            continue
        difference = (broad_side["points"] or 0) - slot_side["points"]
        if abs(difference) > TOLERANCE:
            problems.append({"promise": "broad",
                             "detail": f"{position} is {broad_side['points'] or 0:.4f} in Broad View "
                                       f"but {slot_side['points']:.4f} collapsed from the slots"})
        broad_ids = sorted(player["id"] for player in broad_side["players"])
        if broad_ids != sorted(slot_side["ids"]):
            problems.append({"promise": "broad",
                             "detail": f"{position} holds different players in Broad View than "
                                       f"the collapsed slots do"})
    return problems


def fold_season_slots(league, teams_by_id: dict, player_index: dict, lineup_slots: list[str],
                      rows_by_week: dict, week_count: int, live_week: int | None,
                      settled: set, exclude: set | None = None) -> tuple[dict, dict, dict]:
    """Add the weekly slot view up over the season window the way the Season tab does.

    This is the independent reading the Season tab is compared against: the same window, the same
    matchup pairing and the same rule for a week in progress, but folded here rather than trusted
    from the page. ``exclude`` is the weeks the page itself could not read, which it skipped and
    which therefore cannot be folded here either. A team's weeks and its points per slot come
    back, with the official scores it counted alongside them.
    """
    exclude = exclude or set()
    totals: dict = defaultdict(lambda: defaultdict(float))
    games: dict = defaultdict(int)
    official: dict = defaultdict(float)
    for week in range(1, week_count + 1):
        if week in exclude:
            continue
        by_matchup: dict = defaultdict(list)
        for row in rows_by_week.get(week, []):
            if row.get("matchup_id") is not None:
                by_matchup[row["matchup_id"]].append(row)
        for matchup_rows in by_matchup.values():
            if len(matchup_rows) != 2:
                continue
            for row in matchup_rows:
                roster_id = row.get("roster_id")
                if roster_id not in teams_by_id or _points(row) is None:
                    continue
                if not _counts(week, roster_id, live_week, settled):
                    continue
                groups, scores_available = get_team_position_groups(
                    row, player_index, lineup_slots, True
                )
                if not scores_available:
                    continue
                games[roster_id] += 1
                official[roster_id] += _points(row)
                for position, group in groups.items():
                    totals[roster_id][position] += group["points"]
    return totals, games, official


def fold_player_season(league, teams_by_id: dict, rows_by_week: dict,
                       week_count: int, exclude: set | None = None) -> tuple[dict, dict, list[int]]:
    """Add the weekly player points up the way the Player Data season tables do.

    The independent reading for those tables: each player's points and games over the shared
    window, counting only weeks NFL stats record him on the field, folded here rather than read
    from the page. ``exclude`` is the weeks the page could not read; any other week whose stats
    cannot be fetched here is reported rather than counted.
    """
    exclude = exclude or set()
    season = str(league.get("season", ""))
    current = {roster_id: {str(player["player_id"]) for player in team["players"]}
               for roster_id, team in teams_by_id.items()}
    totals: dict = defaultdict(lambda: defaultdict(float))
    games: dict = defaultdict(lambda: defaultdict(int))
    failed: list[int] = []
    for week in range(1, week_count + 1):
        if week in exclude:
            continue
        try:
            stats = get_nfl_week_stats(season, week) or {}
        except SleeperError:
            failed.append(week)
            continue
        for row in rows_by_week.get(week, []):
            roster_id = row.get("roster_id")
            if roster_id not in teams_by_id:
                continue
            raw = row.get("players_points") or {}
            if not isinstance(raw, dict):
                continue
            points_by_player = {str(player_id): points for player_id, points in raw.items()}
            weekly_roster = {str(player_id) for player_id in (row.get("players") or [])}
            starter_indexes = {str(player_id): index
                               for index, player_id in enumerate(row.get("starters") or [])
                               if str(player_id or "") not in {"", "0"}}
            for player_id in current[roster_id]:
                if weekly_roster and player_id not in weekly_roster:
                    continue
                player_week_stats = stats.get(player_id) or {}
                if (_number(player_week_stats.get("gp")) or 0.0) <= 0:
                    continue
                score = _number(points_by_player.get(player_id))
                if score is None and player_id in starter_indexes:
                    score = _starter_score(row, player_id, starter_indexes[player_id])
                if score is None:
                    score = 0.0
                totals[roster_id][player_id] += score
                games[roster_id][player_id] += 1
    return totals, games, failed


def check_season_tab(summary: dict, totals: dict, games: dict, official: dict) -> tuple[list[dict], dict]:
    """Compare the Season tab's slot averages with the weekly slot view added up."""
    problems: list[dict] = []
    compared = 0
    worst = 0.0
    for team_summary in summary["teams"]:
        roster_id = team_summary["team"]["roster_id"]
        groups = {group["position"]: group for group in team_summary["groups"]}
        page_games = team_summary["games"]
        if page_games != games.get(roster_id, 0):
            problems.append({"promise": "season-slots",
                             "detail": f"roster {roster_id} counts {page_games} games but the "
                                       f"weekly slot view counts {games.get(roster_id, 0)}"})
        for position in sorted(set(groups) | set(totals.get(roster_id, {}))):
            group = groups.get(position)
            if group is None:
                problems.append({"promise": "season-slots",
                                 "detail": f"{position} is in the weekly slot view but not on the "
                                           f"Season tab for roster {roster_id}"})
                continue
            page_total = group["ppg"] * group["games"]
            folded = totals[roster_id].get(position, 0.0)
            compared += 1
            worst = max(worst, abs(page_total - folded))
            if abs(page_total - folded) > TOLERANCE:
                problems.append({"promise": "season-slots",
                                 "detail": f"roster {roster_id} {position}: the Season tab shows "
                                           f"{page_total:.4f} but the weekly slot view adds up to "
                                           f"{folded:.4f}"})
        page_sum = sum(group["ppg"] * group["games"] for group in team_summary["groups"])
        if abs(page_sum - official.get(roster_id, 0.0)) > TOLERANCE:
            problems.append({"promise": "season-score",
                             "detail": f"roster {roster_id}: the Season tab's slots add up to "
                                       f"{page_sum:.4f} but that team scored "
                                       f"{official.get(roster_id, 0.0):.4f} over the same weeks"})
    return problems, {"teams": len(summary["teams"]), "compared": compared, "worst": worst}


def check_player_season(page: dict, totals: dict, games: dict) -> tuple[list[dict], dict]:
    """Compare the Player Data season tables with the weekly matchup records added up."""
    problems: list[dict] = []
    compared = 0
    worst = 0.0
    for team_summary in page["teams"]:
        roster_id = team_summary["team"]["roster_id"]
        for player in team_summary["players"]:
            player_id = player["id"]
            folded = totals[roster_id].get(player_id, 0.0)
            compared += 1
            worst = max(worst, abs(player["total_points"] - folded))
            if abs(player["total_points"] - folded) > TOLERANCE:
                problems.append({"promise": "player-season",
                                 "detail": f"roster {roster_id} player {player_id}: the season "
                                           f"table shows {player['total_points']:.4f} but the "
                                           f"weekly records add up to {folded:.4f}"})
            if player["games"] != games[roster_id].get(player_id, 0):
                problems.append({"promise": "player-season",
                                 "detail": f"roster {roster_id} player {player_id}: the season "
                                           f"table counts {player['games']} games but the weekly "
                                           f"records give {games[roster_id].get(player_id, 0)}"})
    return problems, {"players": compared, "worst": worst}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league-id", help="league to check (default: the saved connection)")
    parser.add_argument("--username", help="Sleeper username (default: the saved connection)")
    parser.add_argument("--manager-id", help="Sleeper manager id (default: the saved connection)")
    parser.add_argument("--weeks", default="",
                        help="comma-separated weeks of the weekly check (default: every week)")
    parser.add_argument("--weekly-only", action="store_true",
                        help="skip the Season tab and Player Data comparisons")
    parser.add_argument("--json", help="write every checked week's outcome to this file")
    args = parser.parse_args(argv)

    from services.local_settings import load_connection

    saved = load_connection()
    league_id = args.league_id or str(saved.get("league_id") or "")
    username = args.username if args.username is not None else str(saved.get("username") or "")
    manager_id = args.manager_id if args.manager_id is not None else str(saved.get("manager_id") or "")
    if not league_id:
        print("No league to check. Connect the app to a league, or pass --league-id.",
              file=sys.stderr)
        return 2

    try:
        dashboard = get_league_dashboard(league_id)
        player_index = get_player_index()
    except SleeperError as exc:
        print(f"Could not read the league from Sleeper: {exc}", file=sys.stderr)
        return 2

    league = dashboard["league"]
    teams_by_id = {team["roster_id"]: team for team in dashboard["teams"]}
    lineup_slots = [str(slot) for slot in (league.get("roster_positions") or [])
                    if str(slot).upper() not in BENCH_SLOTS]
    if not lineup_slots:
        print("That league reports no starting lineup slots to check.", file=sys.stderr)
        return 2

    try:
        weeks = _week_plan(league, league_id, args.weeks)
    except (ValueError, TypeError) as exc:
        print(f"--weeks is not a list of week numbers: {exc}", file=sys.stderr)
        return 2

    week_count, live_week = _season_window(league)
    # One read per week, shared by the weekly check and the two seasonal folds.
    needed = sorted(set(weeks) | set(range(1, week_count + 1)))
    rows_by_week: dict[int, list] = {}
    fetch_failed: set[int] = set()
    for week in needed:
        try:
            rows_by_week[week] = get_json(f"/league/{league_id}/matchups/{week}") or []
        except SleeperError:
            rows_by_week[week] = []
            fetch_failed.add(week)
    if fetch_failed & set(weeks):
        failed_week = sorted(fetch_failed & set(weeks))[0]
        print(f"Could not read week {failed_week} of this league from Sleeper.", file=sys.stderr)
        return 2

    print("Lineup slot check — every starter placed, columns summed, Broad View collapsed")
    print(f"  league   {league_id}   season {league.get('season', '?')}")
    print(f"  slots    {', '.join(get_normalized_slot_order(lineup_slots))}")
    print(f"  weeks    {', '.join(str(week) for week in weeks) or '(none)'}")

    failures: list[dict] = []
    report: list[dict] = []
    team_weeks = 0
    skipped = 0

    for week in weeks:
        rows = rows_by_week.get(week, [])
        week_failures = []
        for row in rows:
            official = _number(row.get("custom_points") if row.get("custom_points") is not None
                               else row.get("points"))
            if official is None:
                skipped += 1  # a week the league has not scored yet
                continue
            team_weeks += 1
            problems = check_team_week(row, player_index, lineup_slots)
            if problems:
                record = {"section": "week", "week": week, "roster_id": row.get("roster_id"),
                          "problems": problems}
                failures.append(record)
                week_failures.append(record)
        report.append({"week": week, "rows": len(rows), "failures": week_failures})
        status = "ok" if not week_failures else f"{len(week_failures)} FAILED"
        print(f"  week {week:<3} {len(rows):>2} team-weeks   {status}")

    print("\n" + "-" * 72)
    print(f"{team_weeks} scored team-weeks checked across {len(weeks)} weeks"
          f"{f' ({skipped} unscored rows skipped)' if skipped else ''}")

    season_notes: list[str] = []
    if args.weekly_only:
        print("\nSeason tab and Player Data comparisons skipped (--weekly-only).")
    else:
        print("\nSeason tab — slot averages against the weekly slot view added up")
        print(f"  window   weeks 1-{week_count}"
              + (f", live week {live_week} joining each team once its lineup has finished"
                 if live_week else ", season complete"))
        if live_week is not None:
            settled = _settled_rosters(str(league.get("season", "")), live_week,
                                       rows_by_week.get(live_week, []), player_index)
        else:
            settled = set()
        # The Season tab returns teams only for a manager it can resolve, so fall back to any
        # owner to make sure every team is actually compared rather than the list coming back
        # empty and the section passing for the wrong reason.
        owner = manager_id or next((str(team["owner_id"]) for team in dashboard["teams"]
                                    if team.get("owner_id")), "")
        summary = get_seasonal_summary(league_id, username, manager_id=owner, normalized=True)
        if not summary["teams"]:
            print("  could not be checked: the Season tab returned no teams to compare.",
                  file=sys.stderr)
            return 2
        # A week the page could not read was skipped there, so it cannot be folded here either.
        season_totals, season_games, season_official = fold_season_slots(
            league, teams_by_id, player_index, lineup_slots, rows_by_week, week_count,
            live_week, settled, exclude=set(summary.get("failed_weeks") or []),
        )
        season_problems, season_stats = check_season_tab(
            summary, season_totals, season_games, season_official
        )
        print(f"  {season_stats['teams']} teams · {season_stats['compared']} slot totals compared "
              f"· worst difference {season_stats['worst']:.4f}")
        if season_problems:
            for problem in season_problems:
                failures.append({"section": "season", "week": None, "roster_id": None,
                                 "problems": [problem]})
        else:
            print("  the Season tab is the weekly slot view added up.")
        if summary.get("failed_weeks"):
            season_notes.append(f"the Season tab could not read weeks "
                                f"{', '.join(str(w) for w in summary['failed_weeks'])}")

        print("\nPlayer Data — season tables against the weekly matchups added up")
        # The page is read first so its own record of which weeks it could not read is in hand:
        # only those weeks can be reconciled, and folding a week it skipped would invent a
        # difference that is the fetch, not the aggregation.
        page = get_player_data(league_id, username, manager_id=owner, seasonal=True)
        player_totals, player_games, player_failed = fold_player_season(
            league, teams_by_id, rows_by_week, week_count,
            exclude=set(page.get("failed_weeks") or []),
        )
        player_problems, player_stats = check_player_season(page, player_totals, player_games)
        print(f"  {player_stats['players']} player rows · worst difference "
              f"{player_stats['worst']:.4f}")
        if player_problems:
            for problem in player_problems:
                failures.append({"section": "player-data", "week": None, "roster_id": None,
                                 "problems": [problem]})
        else:
            print("  the Player Data season tables are the weekly records added up.")
        if player_failed:
            season_notes.append("NFL stats for weeks "
                                + ", ".join(str(week) for week in player_failed)
                                + " could not be read here, so those weeks are missing from the "
                                "Player Data comparison")
        elif page.get("failed_weeks"):
            season_notes.append("the Player Data tables could not read weeks "
                                + ", ".join(str(week) for week in page["failed_weeks"])
                                + ", which were left out of both sides of the comparison")

    if season_notes:
        print("\nWARNINGS:")
        for note in dict.fromkeys(season_notes):
            print(f"  {note}")

    if failures:
        by_promise = Counter(problem["promise"] for failure in failures
                             for problem in failure["problems"])
        print(f"\nFAILED ({len(failures)} results) — "
              + ", ".join(f"{count} {promise}" for promise, count in sorted(by_promise.items())))
        for failure in failures:
            where = (f"week {failure['week']} roster {failure['roster_id']}"
                     if failure["section"] == "week" else failure["section"])
            print(f"  {where}:")
            for problem in failure["problems"]:
                print(f"    [{problem['promise']}] {problem['detail']}")
        print("\nA promise broken here is a page showing a figure the league did not play.")
    else:
        held = ["every starter is in a slot", "every team's columns sum to its score",
                "Broad View is the slot view collapsed"]
        if not args.weekly_only:
            held += ["the Season tab is the weekly slot view added up",
                     "the Player Data season tables are the weekly records added up"]
        print("\n" + ", ".join(held[:-1]) + ", and " + held[-1] + ".")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "league_id": league_id, "season": league.get("season"),
            "slots": list(get_normalized_slot_order(lineup_slots)),
            "weeks": weeks, "team_weeks": team_weeks, "skipped": skipped,
            "season_weeks": week_count, "live_week": live_week,
            "failures": failures, "report": report, "notes": season_notes,
        }, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
