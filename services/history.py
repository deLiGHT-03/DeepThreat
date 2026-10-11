"""Every season this league has ever played, read as one stretch of history.

Sleeper keeps one league record per season and links them newest to oldest, so a league's
history is a chain of league records. This module walks that chain and reads each season's
games, moves, draft and bracket, then answers the questions a league argues about: who has
won the most, what the highest score ever was, who hoards draft picks, and which player
someone picked up off the wire the year he broke out.

Two scopes sit side by side, and each is the one that makes its numbers mean something:

* Season totals -- wins, losses, points -- count the regular season. That is what Sleeper's
  own roster totals show, and it is the only stretch where every team plays the same number
  of games, so one manager's career line can be read against another's.
* Game records and streaks count every game, playoffs included. A game is a game, and a
  playoff run that carries a streak past the regular season is part of that streak.

A season still being played is read only as far as it has scored. Its unfinished week would
otherwise arrive as a half-scored game -- live points against a zero -- and hand out a win
and a record nobody has earned yet.

The draft is read for two more questions. One trade in the league's history stands out as the
one that bought a single player with nothing but picks and got the most back for the capital it
spent. And each season's rookie draft is read for what became of it: which rookie led his class,
who hoarded picks and who barely used any, and what happened to the rookies their teams lost
interest in. A rookie here is a player somebody spent a pick on in that season's rookie draft,
not everybody in his first NFL season.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.sleeper import (MAX_NFL_WEEK, SleeperError, display_handle, get_json,
                              get_league_dashboard, get_league_season_history,
                              get_player_index, get_user_by_id, regular_season_week_count,
                              season_end_week, week_label)
from services.trades import round_label

#: Only a settled transaction moved a player; a declined claim or a vetoed trade did not.
SETTLED_STATUSES = ("complete",)
#: The transaction types that count as a manager's move, and the one that counts as a trade.
MOVE_TYPES = ("trade", "waiver", "free_agent")
#: How many rows each leaderboard shows.
LEADER_ROWS = 8
#: How many seasons of a league are walked before the chain is treated as too long to read.
MAX_SEASONS = 25
#: A rookie draft is the short draft a season runs: a startup runs twenty-odd rounds, a rookie
#: draft five or fewer. A season whose only draft is the long one has no rookie draft to read.
MAX_ROOKIE_ROUNDS = 8
#: From this round on, a rookie was a late pick rather than a guess everybody made early.
LATE_ROUND = 3
#: What one draft pick costs the buyer in a trade for a player: a first rounder this much, and
#: each round after it one less. It is only ever a ratio against the points that came back.
PICK_VALUE_FROM = 6


def _points(row: dict | None) -> float | None:
    """Read a matchup row's score, preferring a commissioner override when there is one."""
    if row is None:
        return None
    value = row.get("custom_points") if row.get("custom_points") is not None else row.get("points")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clean(name) -> str:
    """A team name as Sleeper stores it, with the padding its metadata arrives with removed.

    Team names come through with trailing spaces often enough that a page showing two of them
    side by side reads as a typo, so every name is trimmed where it enters a row.
    """
    return str(name or "").strip()


def _player_name(player: dict, player_id: str) -> str:
    name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    return name or str(player_id)


def _top(items, key):
    """The best of a list by one measure, or None when the list is empty."""
    return max(items, key=key) if items else None


def _pair_rows(rows: list[dict]) -> list[tuple[int, int, float, float]]:
    """Pair a week's matchup rows into games of ``(roster, opponent, points, opponent points)``.

    Each team gets its game twice -- once from its own side -- so a page can read one team's
    score without hunting down the other row. A row with no ``matchup_id`` is a team without a
    game: a playoff week leaves the teams already eliminated sitting there, still holding a
    lineup. Those rows are dropped rather than paired off against each other.
    """
    by_matchup = defaultdict(list)
    for row in rows:
        if row.get("matchup_id") is None:
            continue
        score = _points(row)
        roster_id = row.get("roster_id")
        if score is None or roster_id is None:
            continue
        by_matchup[row["matchup_id"]].append((roster_id, score))

    games = []
    for group in by_matchup.values():
        if len(group) != 2:
            continue
        (first, first_points), (second, second_points) = group
        games.append((first, second, first_points, second_points))
        games.append((second, first, second_points, first_points))
    return games


def _season_window(league: dict, state: dict) -> tuple[int, int, int]:
    """Return ``(regular weeks, last week, weeks already scored)`` for one season.

    A finished season has scored every week it covers. The season being played has scored
    only the weeks behind the NFL's current one, and that is as far as anything counts.
    """
    regular = regular_season_week_count(league)
    try:
        end = min(max(regular, season_end_week(league)), MAX_NFL_WEEK)
    except (TypeError, ValueError):
        end = regular
    if not state or str(state.get("season") or "") != str(league.get("season") or ""):
        return regular, end, end
    try:
        current = int(state.get("week") or state.get("display_week") or 1)
    except (TypeError, ValueError):
        current = 1
    return regular, end, max(0, min(end, current - 1))


def season_plan(league_id: str, state: dict | None = None) -> list[dict]:
    """List this league's seasons, newest first, with the weeks each one covers.

    The plan is what both the summary and a preload read: the summary needs the weeks to
    count, and the preload needs them to fetch before the summary asks for them.
    """
    if state is None:
        state = get_json("/state/nfl") or {}
    plan = []
    for item in get_league_season_history(str(league_id))[:MAX_SEASONS]:
        season_id = item.get("league_id")
        league = get_json(f"/league/{season_id}") or {}
        if not league:
            continue
        regular, end, played = _season_window(league, state)
        plan.append({
            "league": league,
            "league_id": str(league.get("league_id") or season_id),
            "season": str(league.get("season") or item.get("season") or ""),
            "regular": regular,
            "end": end,
            "played": played,
            "in_progress": played < end,
            # The week in progress can still take a transaction, so moves are read one week
            # further than games are counted.
            "read": end if played >= end else min(end, played + 1),
        })
    return plan


def _fetch_job(path: str, failure: str) -> str:
    """Read one path, and a draft's picks along with it. Returns a label when it failed."""
    try:
        payload = get_json(path)
    except SleeperError:
        return failure
    if not path.endswith("/drafts"):
        return ""
    for draft in payload or []:
        draft_id = (draft or {}).get("draft_id")
        if not draft_id:
            continue
        try:
            get_json(f"/draft/{draft_id}/picks")
        except SleeperError:
            return "draft picks"
    return ""


def warm_history(plan: list[dict], season: str = "", progress=None) -> list[str]:
    """Read everything the summary needs, so building it needs no further network.

    The player index is read first, on its own: it is a ten-megabyte download, and five
    seasons asking for it at once would each fetch a copy of their own.

    Every season in the chain is read for its bracket and its teams, because the season chips
    name the champion of each year whatever year is being read. Only the season in view has its
    weeks and its draft read: a page about 2024 has no use for 2022's weekly scores.

    Returns a list of what could not be read, named the way the page names it (``"2024 Wk9"``),
    because a league history that is one week short is still a history worth showing.
    """
    if not plan:
        return []
    try:
        get_player_index()
    except SleeperError:
        pass  # Without it the names fall back to player ids; the page still reads.

    jobs = []
    for entry in plan:
        season_id, name = entry["league_id"], entry["season"]
        jobs.append((f"/league/{season_id}/winners_bracket", f"{name} bracket", name))
        jobs.append((f"/league/{season_id}/rosters", f"{name} rosters", name))
        jobs.append((f"/league/{season_id}/users", f"{name} managers", name))
        if season and entry["season"] != season:
            continue
        for week in range(1, entry["played"] + 1):
            jobs.append((f"/league/{season_id}/matchups/{week}", f"{name} Wk{week}", name))
        for week in range(1, entry["read"] + 1):
            jobs.append((f"/league/{season_id}/transactions/{week}", f"{name} moves Wk{week}",
                         name))
        jobs.append((f"/league/{season_id}/drafts", f"{name} draft", name))

    failed = []
    done = 0
    total = len(jobs) or 1
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_job, path, failure): name
                   for path, failure, name in jobs}
        for future in as_completed(futures):
            done += 1
            failure = future.result()
            if failure:
                failed.append(failure)
            if progress is not None:
                progress(done, total, futures[future])
    failed.sort()
    return failed


#: How far a team went, as a number. The page's Result column sorts on this rather than on the
#: word, so a champion leads the column and a team that missed the bracket sits at its bottom.
RESULT_RANKS = {"Champion": 3, "Runner-up": 2, "Playoffs": 1, "": 0}


def _manager_label(display_name: str, username: str, team_name: str) -> str:
    """What the History page calls a manager: his handle, not the team he was called then.

    A team name is the thing a manager changes most, so the page names people instead of teams
    wherever it used to name a team. The handle is spelled the way he capitalises it, and the
    display name and then the team name stand in for an account Sleeper no longer returns.
    """
    display_name = _clean(display_name)
    if display_name == "Unknown owner":
        display_name = ""
    return display_handle(display_name, username) or _clean(team_name) or "Team"


def _manager_entry(managers: dict, owner_id: str, team: dict) -> dict:
    """The career line for one owner, opened on the first season he is seen in.

    Seasons are read newest first, so the name and avatar an entry keeps are the most recent
    ones: a manager reads about himself under the name he goes by now, not the name of a team
    he abandoned four years ago.
    """
    entry = managers.get(owner_id)
    if entry is None:
        entry = {
            "owner_id": owner_id,
            "name": _clean(team.get("name")) or "Team",
            "owner": _clean(team.get("owner")),
            "username": team.get("username") or "",
            "logo_url": team.get("logo_url") or "",
            "logo_fallback_urls": team.get("logo_fallback_urls") or [],
            "seasons": [],
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "games": 0,
            "points": 0.0,
            "titles": 0,
            "runner_ups": 0,
            "playoffs": 0,
            "is_mine": False,
        }
        managers[owner_id] = entry
    return entry


def _best_streaks(results: list[tuple[int, str]]) -> tuple[tuple, tuple]:
    """Longest run of wins and of losses in one season, from its games in week order.

    A week a team had no game -- an eliminated team in the playoffs -- never reaches this
    list, so a streak runs straight through it instead of being cut in half by a week nobody
    played. Returns ``((wins, from, to), (losses, from, to))``.
    """
    best = {"win": (0, None, None), "loss": (0, None, None)}
    run = {"win": 0, "loss": 0}
    started = {"win": None, "loss": None}
    for week, result in results:
        for kind in ("win", "loss"):
            if result != kind:
                run[kind] = 0
                continue
            started[kind] = week if run[kind] == 0 else started[kind]
            run[kind] += 1
            if run[kind] > best[kind][0]:
                best[kind] = (run[kind], started[kind], week)
    return best["win"], best["loss"]


def _record(label: str, value: str, holder: str, meta: str, hint: str = "",
            is_mine: bool = False) -> dict:
    return {"label": label, "value": value, "holder": holder, "meta": meta,
            "hint": hint, "is_mine": is_mine}


def _counted(count: int, noun: str) -> str:
    """A count with its noun, singular when there is one of them: ``1 rookie``, ``9 rookies``."""
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _draft_rounds(draft: dict, picks: list[dict]) -> int:
    """How many rounds a draft ran, from its own settings or from the picks it holds."""
    try:
        rounds = int((draft.get("settings") or {}).get("rounds") or 0)
    except (TypeError, ValueError):
        rounds = 0
    if rounds:
        return rounds
    return max([int(pick.get("round") or 0) for pick in picks] + [0])


def _rookie_draft_of(season_drafts: list[tuple[dict, list[dict]]]) -> tuple[list[dict], int] | None:
    """The short draft a season ran, and how many rounds it had: where its rookies came from.

    A dynasty league drafts its rookies in a draft of four or five rounds and its whole league in
    one of twenty-odd, so the shortest draft is the rookie draft. A season whose only draft is
    the long one -- the league's first -- has no rookie draft and no rookies to record.
    """
    short = []
    for draft, picks in season_drafts:
        rounds = _draft_rounds(draft, picks)
        if 0 < rounds <= MAX_ROOKIE_ROUNDS:
            short.append((picks, rounds))
    if not short:
        return None
    return min(short, key=lambda entry: entry[1])


def _pick_price(picks: list[dict]) -> str:
    """Name a package of traded picks the way a manager would say it, and briefly."""
    labels = []
    for pick in picks[:3]:
        season = str(pick.get("season") or "").strip()
        try:
            round_text = round_label(int(pick.get("round") or 0))
        except (TypeError, ValueError):
            round_text = "pick"
        labels.append(f"{season} {round_text}".strip())
    if len(picks) > len(labels):
        labels.append(f"+{len(picks) - len(labels)} more")
    return " + ".join(labels)


def _pick_weight(picks: list[dict]) -> float:
    """What a package of picks costs: an early round costs more than a late one."""
    weight = 0
    for pick in picks:
        try:
            round_number = int(pick.get("round") or 0)
        except (TypeError, ValueError):
            round_number = 0
        weight += max(1, PICK_VALUE_FROM - round_number) if round_number else 1
    return float(weight or 1)


def _big_swing_row(transaction: dict, teams: dict, labels: dict, scored_by: dict,
                   player_index: dict, league: dict, season: str, week: int,
                   own_roster) -> dict | None:
    """Read one trade as a player bought with picks alone, and what he scored afterwards.

    Only a trade that sent exactly one player one way and nothing but picks the other is one of
    these: a trade with players on both sides is a swap, and a buyer who also received picks was
    not buying his man for capital. The points counted are the ones the player scored from the
    week the trade was filed in onwards, which is what the buyer actually got for his picks.
    """
    adds = {str(player_id): roster_id
            for player_id, roster_id in (transaction.get("adds") or {}).items()}
    if len(adds) != 1:
        return None
    picks = [pick for pick in (transaction.get("draft_picks") or []) if isinstance(pick, dict)]
    if not picks:
        return None
    player_id, buyer = next(iter(adds.items()))
    if any(pick.get("owner_id") == buyer for pick in picks):
        return None
    scored = scored_by.get(player_id)
    team = teams.get(buyer)
    if not scored or team is None:
        return None
    points = sum(value for scored_week, value in scored["weeks"].items() if scored_week >= week)
    if points <= 0:
        return None
    player = player_index.get(player_id) or {}
    return {
        "season": season, "week": week, "id": player_id, "points": points,
        "name": _player_name(player, player_id),
        "week_label": week_label(league, week),
        "team": labels.get(buyer) or _clean(team["name"]),
        "price": _pick_price(picks), "weight": _pick_weight(picks), "picks": len(picks),
        "is_mine": buyer == own_roster,
    }


def _franchise_records(standings: list[dict], best_season: dict | None,
                       most_playoff: dict | None, playoff_out_of: int) -> list[dict]:
    """Career records: what a manager has piled up over every season in the chain."""
    cards = []
    winningest = _top(standings, lambda m: (m["wins"], m["points"]))
    if winningest:
        cards.append(_record(
            "Winningest Manager", f"{winningest['wins']}-{winningest['losses']}",
            winningest["name"],
            f"{winningest['games']} games · {winningest['season_count']} "
            f"season{'s' if winningest['season_count'] != 1 else ''}",
            "Regular season games, the way Sleeper counts them.", winningest["is_mine"]))
    scorer = _top(standings, lambda m: m["points"])
    if scorer:
        cards.append(_record(
            "Most Career Points", f"{scorer['points']:,.2f}", scorer["name"],
            f"{scorer['ppg']:,.1f} a game across {scorer['season_count']} "
            f"season{'s' if scorer['season_count'] != 1 else ''}",
            "Regular season scoring.", scorer["is_mine"]))
    if best_season:
        cards.append(_record(
            "Best Season Record", best_season["record"], best_season["name"],
            f"{best_season['season']} · {best_season['points']:,.2f} points",
            "A full regular season.", best_season["is_mine"]))
    if most_playoff and most_playoff["playoffs"]:
        cards.append(_record(
            "Most Playoff Trips", str(most_playoff["playoffs"]), most_playoff["name"],
            f"out of {playoff_out_of} season{'s' if playoff_out_of != 1 else ''} played",
            "Times through to the championship bracket.", most_playoff["is_mine"]))
    # Titles and the finals lost, read from the same bracket the season chips name their
    # champions from. Ties lean on the deeper playoff record, then on wins, so a league with
    # two three-time champions still has one name to print.
    champion = _top(standings, lambda m: (m["titles"], m["playoffs"], m["wins"]))
    if champion and champion["titles"]:
        cards.append(_record(
            "Most Championships", str(champion["titles"]), champion["name"],
            f"out of {champion['season_count']} season"
            f"{'s' if champion['season_count'] != 1 else ''} played",
            "Championship brackets won.", champion["is_mine"]))
    bridesmaid = _top(standings, lambda m: (m["runner_ups"], m["playoffs"], m["wins"]))
    if bridesmaid and bridesmaid["runner_ups"]:
        cards.append(_record(
            "Most Runner-Ups", str(bridesmaid["runner_ups"]), bridesmaid["name"],
            f"out of {bridesmaid['season_count']} season"
            f"{'s' if bridesmaid['season_count'] != 1 else ''} played",
            "Championship finals lost, one season each.", bridesmaid["is_mine"]))
    # The other end of each career record. These are the cards a league reads for a laugh, so
    # they are only left off when there is nothing to say: a manager with no losses yet, or a
    # single-manager league where the best and the worst are the same line.
    losingest = _top(standings, lambda m: (m["losses"], -m["wins"]))
    if losingest and losingest["losses"]:
        cards.append(_record(
            "Most Career Losses", f"{losingest['wins']}-{losingest['losses']}",
            losingest["name"],
            f"{losingest['games']} games · {losingest['season_count']} "
            f"season{'s' if losingest['season_count'] != 1 else ''}",
            "Regular season games, the way Sleeper counts them.", losingest["is_mine"]))
    leanest = _top(standings, lambda m: -m["points"])
    if leanest:
        cards.append(_record(
            "Fewest Career Points", f"{leanest['points']:,.2f}", leanest["name"],
            f"{leanest['ppg']:,.1f} a game across {leanest['season_count']} "
            f"season{'s' if leanest['season_count'] != 1 else ''}",
            "Regular season scoring.", leanest["is_mine"]))
    # Ties broken towards the longer tenure: never reaching the bracket in five seasons is a
    # record; missing it in one is just a bad year.
    least_playoff = _top(standings, lambda m: (-m["playoffs"], m["season_count"]))
    if least_playoff:
        cards.append(_record(
            "Fewest Playoff Trips", str(least_playoff["playoffs"]), least_playoff["name"],
            f"out of {least_playoff['season_count']} season"
            f"{'s' if least_playoff['season_count'] != 1 else ''} played",
            "The fewest times through to the championship bracket.", least_playoff["is_mine"]))
    return cards


def _scoring_records(season_points: dict | None, low_season: dict | None,
                     games: list[dict]) -> list[dict]:
    """Records about a single score: a season, a game, or a pair of teams.

    Each of these has its other end as well: the most and the fewest points in a season, the
    highest and lowest score, the highest and lowest combined game, and the widest and thinnest
    win. The pair reads better than the peak alone, and a league remembers its duds as fondly
    as its fireworks.
    """
    cards = []
    if season_points:
        cards.append(_record(
            "Most Points in a Season", f"{season_points['points']:,.2f}", season_points["name"],
            f"{season_points['season']} · {season_points['ppg']:,.1f} a game · "
            f"{season_points['record']}",
            "Regular season scoring only.", season_points["is_mine"]))
    if low_season:
        cards.append(_record(
            "Fewest Points in a Season", f"{low_season['points']:,.2f}", low_season["name"],
            f"{low_season['season']} · {low_season['ppg']:,.1f} a game · {low_season['record']}",
            "Regular season scoring only.", low_season["is_mine"]))
    highest = _top(games, lambda game: game["points"])
    if highest:
        cards.append(_record(
            "Highest Scoring Game", f"{highest['points']:,.2f}", highest["team"],
            f"{highest['week_label']}, {highest['season']} · beat {highest['opponent']} "
            f"by {highest['margin']:,.2f}",
            "Every week counts, playoffs included.", highest["is_mine"]))
    lowest = _top(games, lambda game: -game["points"])
    if lowest:
        cards.append(_record(
            "Lowest Scoring Game", f"{lowest['points']:,.2f}", lowest["team"],
            f"{lowest['week_label']}, {lowest['season']} · against {lowest['opponent']}'s "
            f"{lowest['opponent_points']:,.2f}",
            "Every week counts, playoffs included.", lowest["is_mine"]))
    combined = _top(games, lambda game: game["combined"])
    if combined:
        cards.append(_record(
            "Highest Combined Game", f"{combined['combined']:,.2f}",
            f"{combined['team']} vs {combined['opponent']}",
            f"{combined['week_label']}, {combined['season']} · "
            f"{combined['points']:,.2f}–{combined['opponent_points']:,.2f}",
            "Both teams' scores added together.", combined["is_mine"]))
    low_combined = _top(games, lambda game: -game["combined"])
    if low_combined:
        cards.append(_record(
            "Lowest Combined Game", f"{low_combined['combined']:,.2f}",
            f"{low_combined['team']} vs {low_combined['opponent']}",
            f"{low_combined['week_label']}, {low_combined['season']} · "
            f"{low_combined['points']:,.2f}–{low_combined['opponent_points']:,.2f}",
            "Both teams' scores added together.", low_combined["is_mine"]))
    blowout = _top([game for game in games if game["margin"] > 0], lambda game: game["margin"])
    if blowout:
        cards.append(_record(
            "Biggest Blowout", f"+{blowout['margin']:,.2f}", blowout["team"],
            f"over {blowout['opponent']}, {blowout['week_label']} of {blowout['season']}",
            "Largest winning margin in any game.", blowout["is_mine"]))
    squeaker = _top([game for game in games if game["margin"] > 0], lambda game: -game["margin"])
    if squeaker:
        cards.append(_record(
            "Narrowest Win", f"+{squeaker['margin']:,.2f}", squeaker["team"],
            f"over {squeaker['opponent']}, {squeaker['week_label']} of {squeaker['season']} · "
            f"{squeaker['points']:,.2f}–{squeaker['opponent_points']:,.2f}",
            "Smallest winning margin in any game.", squeaker["is_mine"]))
    return cards


def _streak_records(streaks: list[dict], games: list[dict],
                    worst_season: dict | None) -> list[dict]:
    """Records about runs of games, bad beats, and the season nobody wants."""
    cards = []
    win_streak = _top(streaks, lambda streak: streak["win"][0])
    if win_streak and win_streak["win"][0] > 1:
        count, start, finish = win_streak["win"]
        cards.append(_record(
            "Longest Winning Streak", f"{count} straight", win_streak["name"],
            f"{win_streak['season']} · weeks {start}–{finish}",
            "One season, playoffs included.", win_streak["is_mine"]))
    loss_streak = _top(streaks, lambda streak: streak["loss"][0])
    if loss_streak and loss_streak["loss"][0] > 1:
        count, start, finish = loss_streak["loss"]
        cards.append(_record(
            "Longest Losing Streak", f"{count} straight", loss_streak["name"],
            f"{loss_streak['season']} · weeks {start}–{finish}",
            "One season, playoffs included.", loss_streak["is_mine"]))
    heartbreak = _top([game for game in games if game["result"] == "loss"],
                      lambda game: game["points"])
    if heartbreak:
        cards.append(_record(
            "Most Points in a Loss", f"{heartbreak['points']:,.2f}", heartbreak["team"],
            f"{heartbreak['week_label']}, {heartbreak['season']} · fell to "
            f"{heartbreak['opponent']} by {abs(heartbreak['margin']):,.2f}",
            "The best score that still lost.", heartbreak["is_mine"]))
    if worst_season:
        cards.append(_record(
            "Wooden Spoon", worst_season["record"], worst_season["name"],
            f"{worst_season['season']} · {worst_season['points']:,.2f} points, "
            f"the fewest of any full season",
            "Fewest wins in a full regular season.", worst_season["is_mine"]))
    return cards


def _wire_records(gems: list[dict], bids: list[dict], moves: list[dict], drafts: list[dict],
                  player_weeks: list[dict], swings: list[dict]) -> list[dict]:
    """Records about the wire, the trade block and the draft."""
    cards = []
    gem = _top(gems, lambda row: row["points"])
    if gem:
        cards.append(_record(
            "Best Waiver Gem", f"{gem['points']:,.2f}", gem["name"],
            f"added by {gem['team']}, week {gem['week']} of {gem['season']} · "
            f"{gem['after']:,.2f} of it after the add"
            + (" · never drafted" if gem["undrafted"] else ""),
            "Most points in a season by a player picked up off the wire that season.",
            gem["is_mine"]))
    bid = _top(bids, lambda row: row["amount"])
    if bid:
        cards.append(_record(
            "Biggest Waiver Bid", f"${bid['amount']:,}", bid["name"],
            f"{bid['team']}, week {bid['week']} of {bid['season']}",
            "The largest FAAB bid that won its claim.", bid["is_mine"]))
    trader = _top(moves, lambda row: (row["trades"], row["moves"]))
    if trader and trader["trades"]:
        cards.append(_record(
            "Most Trades in a Season", f"{trader['trades']} trades", trader["name"],
            f"{trader['season']} · {trader['moves']} moves in all",
            "Trades settled against that season.", trader["is_mine"]))
    busiest = _top(moves, lambda row: (row["moves"], row["trades"]))
    if busiest:
        cards.append(_record(
            "Most Moves in a Season", f"{busiest['moves']} moves", busiest["name"],
            f"{busiest['season']} · {busiest['trades']} trades, {busiest['waivers']} waivers, "
            f"{busiest['free_agents']} free-agent adds",
            "Every settled trade, waiver claim and free-agent add.", busiest["is_mine"]))
    hoarder = _top(drafts, lambda row: (row["extra"], row["picks"]))
    if hoarder:
        cards.append(_record(
            "Most Draft Capital", f"+{_counted(hoarder['extra'], 'pick')}", hoarder["name"],
            f"{_counted(hoarder['picks'], 'pick')} in the {hoarder['season']} draft, "
            f"{hoarder['base']} apiece by default",
            "Picks held in one draft, over the number every team starts with.",
            hoarder["is_mine"]))
    player_week = _top(player_weeks, lambda row: row["points"])
    if player_week:
        cards.append(_record(
            "Biggest Player Week", f"{player_week['points']:,.2f}", player_week["name"],
            f"{player_week['week_label']}, {player_week['season']} · on "
            f"{player_week['team']}'s roster",
            "One player, one week.", player_week["is_mine"]))
    cards.extend(_big_swing_record(swings))
    return cards


def _big_swing_record(swings: list[dict]) -> list[dict]:
    """The best bargain among the trades that bought one player with draft picks alone.

    Ranked by what came back per unit of capital rather than by points alone, because the record
    is about the price as much as the production: a player bought for a fifth who scored 90 is a
    bigger swing than one bought for a first who scored 100.
    """
    best = _top(swings, lambda row: (row["points"] / row["weight"], row["points"]))
    if best is None:
        return []
    return [_record(
        "Big Swing", f"{best['points']:,.2f}", best["name"],
        f"bought by {best['team']} for {best['price']}, {best['week_label']} of "
        f"{best['season']} · {best['points']:,.2f} of it after",
        "One player bought with draft picks alone, against the picks it cost.",
        best["is_mine"])]


def _rookie_records(rookie_seasons: list[dict], rookie_games: list[dict],
                    rookie_drafts: list[dict], rookie_cuts: list[dict],
                    rookie_trades: list[dict]) -> dict:
    """The draft records: who the rookies were, and what the league did with them.

    Every card is about a player taken in a season's rookie draft, so a rookie here is somebody a
    manager spent a pick on rather than everybody in his first NFL season. A league whose first
    season ran only a startup draft has no rookie class that year, and nothing to say about one.
    """
    cards = []
    best = _top(rookie_seasons, lambda row: (row["points"], -row["pick_no"]))
    if best:
        cards.append(_record(
            "Best Rookie Season", f"{best['points']:,.2f}", best["name"],
            f"{round_label(best['round'])} pick {best['pick_no']} by {best['team']}, "
            f"{best['season']} · {best['ppg']:,.1f} a game over {best['weeks']} weeks",
            "Most points a drafted rookie scored in his own rookie season.", best["is_mine"]))
    # Left off when the best late pick is the best rookie season as well: the same player twice
    # on one group of cards says nothing the first card did not.
    steal = _top([row for row in rookie_seasons
                  if row["round"] >= LATE_ROUND and (best is None or row["id"] != best["id"])],
                 lambda row: (row["points"], -row["pick_no"]))
    if steal:
        cards.append(_record(
            "Best Late Pick", f"{steal['points']:,.2f}", steal["name"],
            f"{round_label(steal['round'])} pick {steal['pick_no']} by {steal['team']}, "
            f"{steal['season']} · {steal['ppg']:,.1f} a game over {steal['weeks']} weeks",
            f"The best rookie season by a player taken in round {LATE_ROUND} or later.",
            steal["is_mine"]))
    game = _top(rookie_games, lambda row: row["points"])
    if game:
        cards.append(_record(
            "Highest Rookie Game", f"{game['points']:,.2f}", game["name"],
            f"{game['week_label']} of {game['season']} · on {game['team']}'s roster",
            "One rookie, one week.", game["is_mine"]))
    haul = _top(rookie_drafts, lambda row: (row["picks"], row["rounds"]))
    if haul and haul["picks"]:
        cards.append(_record(
            "Most Rookies Drafted", _counted(haul["picks"], "rookie"), haul["name"],
            f"in the {haul['season']} rookie draft, {haul['rounds']} rounds deep",
            "Picks a team actually used that season.", haul["is_mine"]))
    thinnest = _top(rookie_drafts, lambda row: (-row["picks"], row["season"]))
    if thinnest:
        cards.append(_record(
            "Fewest Picks Used", _counted(thinnest["picks"], "pick"), thinnest["name"],
            f"of {thinnest['rounds']} in the {thinnest['season']} rookie draft · "
            "the rest went to other teams",
            "Picks a team used itself, after the ones it traded away.", thinnest["is_mine"]))
    cut = _top(rookie_cuts, lambda row: (row["cuts"], row["season"]))
    if cut:
        extra = cut["cuts"] - len(cut["names"])
        cards.append(_record(
            "Most Rookie Cuts", _counted(cut["cuts"], "rookie"), cut["name"],
            f"dropped {', '.join(cut['names'])}"
            + (f" and {extra} more" if extra > 0 else "")
            + f" before the {cut['season']} season ended",
            "Rookies a team drafted and then cut in the same season.", cut["is_mine"]))
    first = min(rookie_trades, key=lambda row: (row["season"], row["created"])) \
        if rookie_trades else None
    if first:
        cards.append(_record(
            "First Rookie Traded", f"Wk{first['week']}", first["name"],
            f"{round_label(first['round'])} pick by {first['from']} in {first['season']} · "
            f"sent to {first['to']}",
            "The earliest a rookie changed teams after his rookie draft.", first["is_mine"]))
    return {"title": "Rookie Fever",
            "note": "Rookie drafts only: the players teams spent picks on, and what became "
                    "of them.",
            "records": cards}


def _season_record_groups(team_rows: list[dict], games: list[dict], streaks: list[dict],
                          moves: list[dict], drafts: list[dict], gems: list[dict],
                          bids: list[dict], player_weeks: list[dict], swings: list[dict],
                          champion: dict | None, played: int, regular: int) -> list[dict]:
    """The record book for one season.

    The all-time book answers "who has done the most ever"; this one answers "what happened
    that year", so its labels drop the words the page's own heading already says -- there is no
    "Most Trades in a Season" on a page that is one season -- and its records are the leaders of
    the season's own lists, not the tops of career ones.
    """
    cards = []
    if champion:
        cards.append(_record(
            "Champion", champion.get("record") or "—", champion["name"],
            f"beat {champion['beat']} in the final" if champion.get("beat") else "won the final",
            "Settled by the winners bracket final.", champion["is_mine"]))
    else:
        cards.append(_record(
            "Champion", "—", "Still to be decided",
            f"{played} of {regular} weeks scored so far",
            "The final has not been played yet."))
    best = _top(team_rows, lambda row: (row["wins"], row["points"]))
    if best:
        cards.append(_record("Best Record", best["record"], best["name"],
                             f"{best['points']:,.2f} points · {best['ppg']:,.1f} a game",
                             "Regular season games only.", best["is_mine"]))
    scorer = _top(team_rows, lambda row: row["points"])
    if scorer:
        cards.append(_record("Most Points", f"{scorer['points']:,.2f}", scorer["name"],
                             f"{scorer['ppg']:,.1f} a game · {scorer['record']}",
                             "Regular season scoring only.", scorer["is_mine"]))
    worst = _top(team_rows, lambda row: (-row["wins"], -row["points"]))
    if worst:
        cards.append(_record("Wooden Spoon", worst["record"], worst["name"],
                             f"{worst['points']:,.2f} points, the fewest of the season",
                             "Fewest wins in the regular season.", worst["is_mine"]))

    highest = _top(games, lambda game: game["points"])
    if highest:
        cards.append(_record(
            "Highest Scoring Game", f"{highest['points']:,.2f}", highest["team"],
            f"{highest['week_label']} · beat {highest['opponent']} by {highest['margin']:,.2f}",
            "Every week counts, playoffs included.", highest["is_mine"]))
    combined = _top(games, lambda game: game["combined"])
    if combined:
        cards.append(_record(
            "Highest Combined Game", f"{combined['combined']:,.2f}",
            f"{combined['team']} vs {combined['opponent']}",
            f"{combined['week_label']} · {combined['points']:,.2f}–"
            f"{combined['opponent_points']:,.2f}",
            "Both teams' scores added together.", combined["is_mine"]))
    blowout = _top([game for game in games if game["margin"] > 0], lambda game: game["margin"])
    if blowout:
        cards.append(_record("Biggest Blowout", f"+{blowout['margin']:,.2f}", blowout["team"],
                             f"over {blowout['opponent']}, {blowout['week_label']}",
                             "Largest winning margin of the season.", blowout["is_mine"]))
    heartbreak = _top([game for game in games if game["result"] == "loss"],
                      lambda game: game["points"])
    if heartbreak:
        cards.append(_record(
            "Most Points in a Loss", f"{heartbreak['points']:,.2f}", heartbreak["team"],
            f"{heartbreak['week_label']} · fell to {heartbreak['opponent']} by "
            f"{abs(heartbreak['margin']):,.2f}",
            "The best score that still lost.", heartbreak["is_mine"]))
    win_streak = _top(streaks, lambda streak: streak["win"][0])
    if win_streak and win_streak["win"][0] > 1:
        count, start, finish = win_streak["win"]
        cards.append(_record("Longest Winning Streak", f"{count} straight", win_streak["name"],
                             f"weeks {start}–{finish}", "Playoffs included.",
                             win_streak["is_mine"]))
    loss_streak = _top(streaks, lambda streak: streak["loss"][0])
    if loss_streak and loss_streak["loss"][0] > 1:
        count, start, finish = loss_streak["loss"]
        cards.append(_record("Longest Losing Streak", f"{count} straight", loss_streak["name"],
                             f"weeks {start}–{finish}", "Playoffs included.",
                             loss_streak["is_mine"]))

    gem = _top(gems, lambda row: row["points"])
    if gem:
        cards.append(_record(
            "Best Waiver Gem", f"{gem['points']:,.2f}", gem["name"],
            f"added by {gem['team']} in week {gem['week']} · {gem['after']:,.2f} of it after"
            + (" · never drafted" if gem["undrafted"] else ""),
            "Most points by a player picked up off the wire that season.", gem["is_mine"]))
    bid = _top(bids, lambda row: row["amount"])
    if bid:
        cards.append(_record("Biggest Waiver Bid", f"${bid['amount']:,}", bid["name"],
                             f"{bid['team']}, week {bid['week']}",
                             "The largest FAAB bid that won its claim.", bid["is_mine"]))
    player_week = _top(player_weeks, lambda row: row["points"])
    if player_week:
        cards.append(_record("Biggest Player Week", f"{player_week['points']:,.2f}",
                             player_week["name"],
                             f"{player_week['week_label']} · on {player_week['team']}'s roster",
                             "One player, one week.", player_week["is_mine"]))
    trader = _top(moves, lambda row: (row["trades"], row["moves"]))
    if trader and trader["trades"]:
        cards.append(_record("Most Trades", f"{trader['trades']} trades", trader["name"],
                             f"{trader['moves']} moves in all",
                             "Trades settled against the season.", trader["is_mine"]))
    busiest = _top(moves, lambda row: (row["moves"], row["trades"]))
    if busiest:
        cards.append(_record(
            "Most Moves", f"{busiest['moves']} moves", busiest["name"],
            f"{busiest['trades']} trades, {busiest['waivers']} waivers, "
            f"{busiest['free_agents']} free-agent adds",
            "Every settled trade, waiver claim and free-agent add.", busiest["is_mine"]))
    hoarder = _top(drafts, lambda row: (row["extra"], row["picks"]))
    if hoarder:
        cards.append(_record(
            "Most Draft Capital", f"+{_counted(hoarder['extra'], 'pick')}", hoarder["name"],
            f"{_counted(hoarder['picks'], 'pick')} in the draft, "
            f"{hoarder['base']} apiece by default",
            "Picks held in the draft, over the number every team starts with.",
            hoarder["is_mine"]))

    cards.extend(_big_swing_record(swings))
    return [
        {"title": "The Season", "note": "How the season finished.", "records": cards[:4]},
        {"title": "Games & Streaks",
         "note": "Single scores. Every week counts here, playoffs included.",
         "records": cards[4:10]},
        {"title": "Moves & The Wire", "note": "Trades, claims and draft capital.",
         "records": cards[10:]},
    ]


def get_history_summary(league_id: str, username: str = "", manager_id: str = "",
                        season: str = "") -> dict:
    """Answer the record book for one season, or for every season the league has played."""
    state = get_json("/state/nfl") or {}
    plan = season_plan(league_id, state)
    if not plan:
        raise SleeperError("That league's seasons could not be read from Sleeper.")
    scope = str(season or "").strip()
    scoped = next((entry for entry in plan if entry["season"] == scope), None) if scope else None
    if scope and scoped is None:
        raise SleeperError(f"{scope} is not one of this league's seasons.")
    failed = warm_history(plan, scope)

    # Every manager in the chain, resolved once to the name the History page calls him by. The
    # page names people rather than teams, because a team name is the thing a manager changes
    # most. The names each manager went by are read newest season first, so a career line is
    # named the way he goes by it now; the display name and then the team name stand in for an
    # account Sleeper no longer returns. The handle lookups go out in one batch: a chain shares
    # its managers, and asking one after another would put every round trip end to end on the
    # page's own critical path.
    owner_display: dict[str, str] = {}
    owner_team: dict[str, str] = {}
    for entry in plan:
        for team in get_league_dashboard(entry["league_id"])["teams"]:
            owner_id = str(team.get("owner_id") or "")
            if not owner_id:
                continue
            owner_display.setdefault(owner_id, _clean(team.get("owner")))
            owner_team.setdefault(owner_id, _clean(team.get("name")))
    usernames: dict[str, str] = {}
    if owner_display:
        ordered_owners = sorted(owner_display)
        with ThreadPoolExecutor(max_workers=8) as executor:
            found = list(executor.map(
                lambda owner_id: _clean(get_user_by_id(owner_id).get("username")),
                ordered_owners))
        usernames = dict(zip(ordered_owners, found))
    manager_label = {owner_id: _manager_label(owner_display.get(owner_id, ""),
                                              usernames.get(owner_id, ""),
                                              owner_team.get(owner_id, ""))
                     for owner_id in owner_display}

    player_index = get_player_index()
    manager_id = str(manager_id or "").strip()
    username_key = str(username or "").strip().casefold()

    managers: dict[str, dict] = {}
    season_list: list[dict] = []
    team_seasons: list[dict] = []
    game_rows: list[dict] = []
    gem_rows: list[dict] = []
    bid_rows: list[dict] = []
    move_rows: list[dict] = []
    draft_rows: list[dict] = []
    streak_rows: list[dict] = []
    player_weeks: list[dict] = []
    swing_rows: list[dict] = []
    rookie_rows: list[dict] = []
    rookie_game_rows: list[dict] = []
    rookie_draft_rows: list[dict] = []
    rookie_cut_rows: list[dict] = []
    rookie_trade_rows: list[dict] = []
    totals = {"games": 0, "trades": 0, "moves": 0, "points": 0.0}

    for entry in plan:
        season_id, label = entry["league_id"], entry["season"]
        collect = scoped is None or entry["season"] == scope
        dashboard = get_league_dashboard(season_id)
        league = dashboard["league"]
        teams = {team["roster_id"]: team for team in dashboard["teams"]}
        owner_of = {roster_id: str(team.get("owner_id") or "") for roster_id, team in teams.items()}
        own_roster = next((roster_id for roster_id, team in teams.items()
                           if manager_id and str(team.get("owner_id")) == manager_id), None)
        if own_roster is None:
            own_roster = next((roster_id for roster_id, team in teams.items()
                               if username_key
                               and str(team.get("username", "")).casefold() == username_key), None)
        regular, played = entry["regular"], entry["played"]
        # What the page calls each team, for every row below that used to carry a team name.
        roster_label = {
            roster_id: manager_label.get(str(team.get("owner_id") or ""), "")
                       or _clean(team.get("name")) or "Team"
            for roster_id, team in teams.items()
        }

        # The champion is the match Sleeper marks as settling first place. A season still
        # being played has that match named but not yet decided, and comes back with nobody.
        bracket = [match for match in (get_json(f"/league/{season_id}/winners_bracket") or [])
                   if isinstance(match, dict)]
        champion = None
        finalist_owners = set()
        for match in bracket:
            for key in ("t1", "t2"):
                try:
                    finalist_owners.add(owner_of.get(int(match[key])))
                except (KeyError, TypeError, ValueError):
                    continue
            if match.get("p") != 1:
                continue
            try:
                champion_roster = int(match["w"])
            except (KeyError, TypeError, ValueError):
                continue
            if champion_roster not in teams:
                continue
            champion = {**teams[champion_roster], "season": label,
                        "is_mine": champion_roster == own_roster}
            champion["owner"] = _clean(champion.get("owner"))
            # The bar above the record book, and the year chips' tooltips, name each champion
            # the way every other row on the page names a manager: by his handle.
            champion["name"] = roster_label[champion_roster]
            champion["handle"] = champion["name"]
            try:
                loser_roster = int(match["l"])
            except (KeyError, TypeError, ValueError):
                loser_roster = None
            runner_up = teams.get(loser_roster) if loser_roster is not None else None
            if runner_up is not None:
                champion["beat"] = roster_label[loser_roster]
                champion["beat_owner"] = owner_of.get(loser_roster, "")
            # A title only counts for a season the record book is about: reading one season
            # must not hand a title to a champion of another. The loser of the same final is
            # that season's runner-up, and counts on the same terms.
            if collect:
                _manager_entry(managers, owner_of[champion_roster],
                               teams[champion_roster])["titles"] += 1
                if runner_up is not None:
                    runner_owner = owner_of.get(loser_roster, "")
                    if runner_owner:
                        _manager_entry(managers, runner_owner, runner_up)["runner_ups"] += 1
        finalist_owners.discard(None)

        # The season's champion is named as soon as its bracket is read, which is all the chips
        # above the record book need. A season that is not the one in view stops here: its weeks
        # and its draft are never read.
        season_list.append({
            "season": label, "league_id": season_id, "name": league.get("name") or "League",
            "in_progress": entry["in_progress"], "weeks_played": played, "regular": regular,
            "champion": champion,
            "owners": sorted(owner for owner in owner_of.values() if owner),
            "playoff_owners": sorted(finalist_owners),
        })
        if not collect:
            continue

        # Every played week, paired into games. Regular-season weeks are what the season totals
        # count; every week is what the game records and the streaks count.
        team_stats = {roster_id: {"points": 0.0, "wins": 0, "losses": 0, "ties": 0, "games": 0}
                      for roster_id in teams}
        results = defaultdict(list)
        season_players = defaultdict(lambda: {"points": 0.0, "weeks": {}})
        week_best: dict[str, dict] = {}
        for week in range(1, played + 1):
            rows = get_json(f"/league/{season_id}/matchups/{week}") or []
            label_of_week = week_label(league, week)
            playoff = week > regular
            for row in rows:
                roster_id = row.get("roster_id")
                if roster_id not in teams:
                    continue
                for player_id, score in (row.get("players_points") or {}).items():
                    if score is None:
                        continue
                    player_id = str(player_id)
                    scored = season_players[player_id]
                    scored["points"] += float(score)
                    scored["weeks"][week] = scored["weeks"].get(week, 0.0) + float(score)
                    best = week_best.get(player_id)
                    if best is None or float(score) > best["points"]:
                        week_best[player_id] = {
                            "id": player_id,
                            "points": float(score), "week": week,
                            "week_label": label_of_week, "season": label,
                            "team": roster_label[roster_id],
                            "is_mine": roster_id == own_roster,
                        }
            for roster_id, opponent_id, score, opponent_score in _pair_rows(rows):
                team = teams.get(roster_id)
                if team is None:
                    continue
                opponent = teams.get(opponent_id)
                margin = score - opponent_score
                result = "win" if margin > 0 else "loss" if margin < 0 else "tie"
                game_rows.append({
                    "season": label, "week": week, "week_label": label_of_week,
                    "playoff": playoff, "team": roster_label[roster_id],
                    "opponent": roster_label[opponent_id] if opponent else "—",
                    "points": score, "opponent_points": opponent_score,
                    "margin": margin, "combined": score + opponent_score,
                    "result": result, "is_mine": roster_id == own_roster,
                })
                results[roster_id].append((week, result))
                # Only the regular season reaches the season and career totals: it is the stretch
                # every team plays the same number of games, and what Sleeper's own totals show.
                # The games themselves are still kept, for the records that count any week.
                if playoff:
                    continue
                stats = team_stats[roster_id]
                stats["games"] += 1
                stats[{"win": "wins", "loss": "losses", "tie": "ties"}[result]] += 1
                stats["points"] += score
                totals["points"] += score
        for player_id, best in week_best.items():
            player = player_index.get(player_id) or {}
            best["name"] = _player_name(player, player_id)
            best["position"] = player.get("position") or "—"
        player_weeks.extend(week_best.values())

        # The draft is read before the moves, because the rookies a team drafted are what its
        # later cuts and trades are read against. The longest draft is the one whose capital is
        # counted -- a startup runs twenty-odd rounds deep and a rookie draft four or five --
        # while the shortest is the rookie draft itself, and where every rookie below comes from.
        season_drafts: list[tuple[dict, list[dict]]] = []
        picks: list[dict] = []
        for draft in get_json(f"/league/{season_id}/drafts") or []:
            if not isinstance(draft, dict) or not draft.get("draft_id"):
                continue
            made = [pick for pick in (get_json(f"/draft/{draft['draft_id']}/picks") or [])
                    if isinstance(pick, dict)]
            season_drafts.append((draft, made))
            if len(made) > len(picks):
                picks = made
        drafted = {str(pick.get("player_id")) for pick in picks if pick.get("player_id") is not None}
        pick_counts = defaultdict(int)
        for pick in picks:
            pick_counts[pick.get("roster_id")] += 1
        base = len(picks) // len(teams) if teams else 0
        for roster_id, count in pick_counts.items():
            if roster_id not in teams:
                continue
            draft_rows.append({
                "season": label, "name": roster_label[roster_id], "picks": count,
                "base": base, "extra": count - base, "is_mine": roster_id == own_roster,
            })

        rookie_draft = _rookie_draft_of(season_drafts)
        rookies: dict[str, dict] = {}
        if rookie_draft is not None:
            rookie_picks, rookie_rounds = rookie_draft
            for pick in rookie_picks:
                player_id = pick.get("player_id")
                if player_id is not None:
                    rookies.setdefault(str(player_id), pick)
            used_by_team = defaultdict(int)
            for pick in rookies.values():
                used_by_team[pick.get("roster_id")] += 1
            for roster_id, team in teams.items():
                rookie_draft_rows.append({
                    "season": label, "name": roster_label[roster_id],
                    "picks": used_by_team.get(roster_id, 0), "rounds": rookie_rounds,
                    "is_mine": roster_id == own_roster,
                })
        for player_id, pick in rookies.items():
            player = player_index.get(player_id) or {}
            scored = season_players.get(player_id)
            team = teams.get(pick.get("roster_id"))
            if not scored or not scored["points"] or team is None:
                continue
            rookie_rows.append({
                "season": label, "id": player_id, "name": _player_name(player, player_id),
                "position": player.get("position") or "—", "points": scored["points"],
                "weeks": len(scored["weeks"]),
                "ppg": scored["points"] / len(scored["weeks"]) if scored["weeks"] else 0.0,
                "round": int(pick.get("round") or 0), "pick_no": int(pick.get("pick_no") or 0),
                "round_label": round_label(int(pick.get("round") or 0)),
                "team": roster_label[team["roster_id"]],
                "is_mine": pick.get("roster_id") == own_roster,
            })
            best_week_of = week_best.get(player_id)
            if best_week_of is not None:
                rookie_game_rows.append({**best_week_of,
                                        "round": int(pick.get("round") or 0),
                                        "drafted_by": roster_label[team["roster_id"]]})

        # Season totals per team, and the career line they feed. Only counted weeks are in
        # here, so a season in progress adds the games it has actually played.
        for roster_id, stats in team_stats.items():
            if not stats["games"]:
                continue
            team = teams[roster_id]
            owner_id = owner_of.get(roster_id, "")
            record = f"{stats['wins']}-{stats['losses']}"
            if stats["ties"]:
                record = f"{record}-{stats['ties']}"
            is_mine = roster_id == own_roster
            team_seasons.append({
                "season": label, "name": roster_label[roster_id],
                "owner": _clean(team.get("owner")),
                "wins": stats["wins"], "losses": stats["losses"], "ties": stats["ties"],
                "games": stats["games"], "points": stats["points"], "record": record,
                "ppg": stats["points"] / stats["games"] if stats["games"] else 0.0,
                # A season is only compared against another when the regular season is done:
                # a 4-0 start is not a better record than 14 games of work.
                "full": regular > 0 and stats["games"] >= regular,
                "is_mine": is_mine,
            })
            manager = _manager_entry(managers, owner_id, team)
            manager["wins"] += stats["wins"]
            manager["losses"] += stats["losses"]
            manager["ties"] += stats["ties"]
            manager["games"] += stats["games"]
            manager["points"] += stats["points"]
            manager["is_mine"] = manager["is_mine"] or is_mine
            if label not in manager["seasons"]:
                manager["seasons"].append(label)

        # Playoff trips are counted once the season's own entries exist, so the trip lands on
        # the career line whether or not that manager won a game in the bracket.
        for owner_id in finalist_owners:
            if owner_id in managers:
                managers[owner_id]["playoffs"] += 1

        for roster_id, week_results in results.items():
            team = teams.get(roster_id)
            if team is None:
                continue
            wins, losses = _best_streaks(week_results)
            if max(wins[0], losses[0]) > 1:
                streak_rows.append({"season": label, "name": roster_label[roster_id], "win": wins,
                                    "loss": losses, "is_mine": roster_id == own_roster})

        # Moves: every settled trade, waiver claim and free-agent add, counted once per roster
        # involved. A transaction Sleeper files under two weeks is only read once.
        move_counts = defaultdict(int)
        move_types = defaultdict(lambda: defaultdict(int))
        adds: dict[str, dict] = {}
        # Keyed by player so a rookie dropped, re-added and dropped again is one cut, not two.
        rookie_cuts: dict = defaultdict(dict)
        seen = set()
        for week in range(1, entry["read"] + 1):
            for transaction in get_json(f"/league/{season_id}/transactions/{week}") or []:
                if not isinstance(transaction, dict):
                    continue
                if transaction.get("status") not in SETTLED_STATUSES:
                    continue
                kind = transaction.get("type")
                if kind not in MOVE_TYPES:
                    continue
                transaction_id = str(transaction.get("transaction_id") or "")
                if not transaction_id or transaction_id in seen:
                    continue
                seen.add(transaction_id)
                try:
                    leg = int(transaction.get("leg") or week)
                except (TypeError, ValueError):
                    leg = week
                totals["moves"] += 1
                if kind == "trade":
                    totals["trades"] += 1
                    swing = _big_swing_row(transaction, teams, roster_label, season_players,
                                           player_index, league, label, leg, own_roster)
                    if swing is not None:
                        swing_rows.append(swing)
                    for player_id, roster_id in (transaction.get("adds") or {}).items():
                        player_id = str(player_id)
                        rookie_pick = rookies.get(player_id)
                        if rookie_pick is None or rookie_pick.get("roster_id") == roster_id:
                            continue  # a rookie only counts as traded once he changes teams
                        drafted_by = teams.get(rookie_pick.get("roster_id"))
                        rookie_trade_rows.append({
                            "season": label, "id": player_id,
                            "created": int(transaction.get("created") or 0), "week": leg,
                            "name": _player_name(player_index.get(player_id) or {}, player_id),
                            "from": roster_label[drafted_by["roster_id"]] if drafted_by else "—",
                            "to": roster_label[roster_id] if roster_id in teams else "—",
                            "round": int(rookie_pick.get("round") or 0),
                            "is_mine": roster_id == own_roster,
                        })
                for roster_id in transaction.get("roster_ids") or []:
                    if roster_id not in teams:
                        continue
                    move_counts[roster_id] += 1
                    move_types[roster_id][kind] += 1
                if kind not in ("waiver", "free_agent"):
                    continue
                try:
                    amount = int((transaction.get("settings") or {}).get("waiver_bid") or 0)
                except (TypeError, ValueError):
                    amount = 0
                for player_id, roster_id in (transaction.get("drops") or {}).items():
                    player_id = str(player_id)
                    rookie_pick = rookies.get(player_id)
                    if rookie_pick is None or rookie_pick.get("roster_id") != roster_id:
                        continue  # only a team cutting the rookie it drafted counts as a cut
                    rookie_cuts[roster_id][player_id] = _player_name(
                        player_index.get(player_id) or {}, player_id)
                for player_id, roster_id in (transaction.get("adds") or {}).items():
                    player_id = str(player_id)
                    previous = adds.get(player_id)
                    if previous is None or leg < previous["week"]:
                        adds[player_id] = {"week": leg, "roster_id": roster_id, "kind": kind}
                    if amount > 0 and roster_id in teams:
                        player = player_index.get(player_id) or {}
                        bid_rows.append({
                            "season": label, "amount": amount, "week": leg,
                            "id": player_id, "name": _player_name(player, player_id),
                            "position": player.get("position") or "—",
                            "team": roster_label[roster_id],
                            "is_mine": roster_id == own_roster,
                        })
        for roster_id, count in move_counts.items():
            move_rows.append({
                "season": label, "name": roster_label[roster_id], "moves": count,
                "trades": move_types[roster_id].get("trade", 0),
                "waivers": move_types[roster_id].get("waiver", 0),
                "free_agents": move_types[roster_id].get("free_agent", 0),
                "is_mine": roster_id == own_roster,
            })
        for roster_id, cut_players in rookie_cuts.items():
            team = teams.get(roster_id)
            if team is None:
                continue
            names = sorted(cut_players.values(), key=str.casefold)
            rookie_cut_rows.append({
                "season": label, "name": roster_label[roster_id], "cuts": len(names),
                "names": names[:3], "is_mine": roster_id == own_roster,
            })

        # Waiver gems: players added off the wire that season, measured by what they scored in
        # it, with the points from the week they were added separated out -- that second number
        # is what the manager actually got out of the pickup.
        for player_id, add in adds.items():
            scored = season_players.get(player_id)
            team = teams.get(add["roster_id"])
            if not scored or scored["points"] <= 0 or team is None:
                continue
            player = player_index.get(player_id) or {}
            gem_rows.append({
                "season": label, "week": add["week"], "id": player_id,
                "name": _player_name(player, player_id),
                "position": player.get("position") or "—", "team": roster_label[add["roster_id"]],
                "points": scored["points"],
                "after": sum(points for week, points in scored["weeks"].items()
                             if week >= add["week"]),
                "undrafted": player_id not in drafted,
                "is_mine": add["roster_id"] == own_roster,
            })

    # Career lines are named the way every other row on the page is: by the manager himself,
    # not by the team he happened to be called the last season he played.
    for owner_id, entry in managers.items():
        entry["name"] = manager_label.get(owner_id) or entry["name"]

    totals["games"] = len(game_rows) // 2  # Both sides of every game are in the list.
    standings = []
    for entry in managers.values():
        if not entry["games"]:
            continue  # An owner named only by a bracket has no line of his own to stand on.
        entry["season_count"] = len(entry["seasons"])
        entry["win_pct"] = entry["wins"] / entry["games"] if entry["games"] else 0.0
        entry["ppg"] = entry["points"] / entry["games"] if entry["games"] else 0.0
        standings.append(entry)
    standings.sort(key=lambda row: (-row["wins"], -row["points"], row["name"].casefold()))
    for rank, entry in enumerate(standings, start=1):
        entry["rank"] = rank

    # One season's standings say how each team finished it: the bracket settles the top two,
    # and everyone else reads by whether they were in it at all.
    if scoped:
        season_entry = next(row for row in season_list if row["league_id"] == scoped["league_id"])
        champion = season_entry["champion"] or {}
        champion_owner = str(champion.get("owner_id") or "")
        runner_up_owner = str(champion.get("beat_owner") or "")
        for entry in standings:
            if champion_owner and entry["owner_id"] == champion_owner:
                entry["result"] = "Champion"
            elif runner_up_owner and entry["owner_id"] == runner_up_owner:
                entry["result"] = "Runner-up"
            elif entry["owner_id"] in season_entry["playoff_owners"]:
                entry["result"] = "Playoffs"
            else:
                entry["result"] = ""
            entry["result_rank"] = RESULT_RANKS[entry["result"]]

    # Career records only compare finished seasons, so a four-week start is never called a
    # better record than fourteen games of work. Reading one season, that season is the subject
    # and stands on its own however far it has got.
    full_seasons = [row for row in team_seasons if row["full"]]
    comparison_pool = team_seasons if scoped else full_seasons
    season_leaderboards = {
        "seasons": sorted(comparison_pool,
                          key=lambda row: (-row["points"], row["name"].casefold()))[:LEADER_ROWS],
        "games": sorted(game_rows, key=lambda row: -row["points"])[:LEADER_ROWS],
        "blowouts": sorted([row for row in game_rows if row["margin"] > 0],
                           key=lambda row: -row["margin"])[:LEADER_ROWS],
        "gems": sorted(gem_rows,
                       key=lambda row: (-row["points"], row["name"].casefold()))[:LEADER_ROWS],
        # The late rounds are where a rookie draft is won, so these are the picks nobody expected
        # much of -- the third round on -- read by what they scored in that same season. Ties are
        # settled toward the later pick, which is the bigger steal of the two.
        "steals": sorted([row for row in rookie_rows if row["round"] >= LATE_ROUND],
                         key=lambda row: (-row["points"], -row["pick_no"]))[:LEADER_ROWS],
        "bids": sorted(bid_rows, key=lambda row: -row["amount"])[:LEADER_ROWS],
        "moves": sorted(move_rows,
                        key=lambda row: (-row["moves"], row["name"].casefold()))[:LEADER_ROWS],
    }

    if scoped:
        record_groups = _season_record_groups(
            team_seasons, game_rows, streak_rows, move_rows, draft_rows, gem_rows, bid_rows,
            player_weeks, swing_rows, champion or None, scoped["played"], scoped["regular"])
    else:
        best_season = _top(full_seasons, lambda row: (row["wins"], row["points"]))
        worst_season = _top(full_seasons, lambda row: (-row["wins"], -row["points"]))
        season_points = _top(full_seasons, lambda row: row["points"])
        low_season = _top(full_seasons, lambda row: -row["points"])
        most_playoff = _top(standings, lambda row: (row["playoffs"], row["wins"]))
        playoff_out_of = 0
        if most_playoff:
            playoff_out_of = sum(1 for season in season_list
                                 if most_playoff["owner_id"] in season["owners"])
        record_groups = [
            {"title": "Franchise",
             "note": "Career numbers. Regular season only, the way Sleeper counts them.",
             "records": _franchise_records(standings, best_season, most_playoff, playoff_out_of)},
            {"title": "Scoring",
             "note": "Single scores. Every week counts here, playoffs included.",
             "records": _scoring_records(season_points, low_season, game_rows)},
            {"title": "Streaks & Heartbreak",
             "note": "Runs of games, bad beats, and the season nobody wants.",
             "records": _streak_records(streak_rows, game_rows, worst_season)},
            {"title": "Moves & The Wire",
             "note": "Trades, claims and draft capital.",
             "records": _wire_records(gem_rows, bid_rows, move_rows, draft_rows, player_weeks,
                                      swing_rows)},
        ]

    # The rookie draft reads the same in both scopes, so its group is added to whichever book was
    # built above. A season with no rookie draft of its own leaves the group empty, and the page
    # draws no section for it rather than an empty one.
    rookie_group = _rookie_records(rookie_rows, rookie_game_rows, rookie_draft_rows,
                                   rookie_cut_rows, rookie_trade_rows)
    if rookie_group["records"]:
        record_groups.append(rookie_group)

    return {
        # The league record is the season in view's, so every link the page draws points at the
        # season the reader is looking at rather than at whatever season is newest.
        "league": (scoped or plan[0])["league"],
        "username": username,
        "manager_id": manager_id,
        "season": (scoped or plan[0])["season"],
        "scope_season": scope,
        "scope": {
            "season": scope,
            "weeks_played": scoped["played"] if scoped else 0,
            "regular": scoped["regular"] if scoped else 0,
            # The last week the season covers, playoffs included: a finished season has read all
            # of them, and the season being played has read only those behind the current one.
            "weeks_total": scoped["end"] if scoped else 0,
            "in_progress": bool(scoped and scoped["in_progress"]),
        },
        "seasons": season_list,
        "champions": [row for row in season_list
                      if row["champion"] and (not scoped or row["league_id"] == scoped["league_id"])],
        "season_count": len(season_list),
        "season_label": (f"{season_list[-1]['season']}–{season_list[0]['season']}"
                         if len(season_list) > 1
                         else (season_list[0]["season"] if season_list else "")),
        "has_live_season": any(row["in_progress"] for row in season_list),
        "standings": standings,
        "streaks": streak_rows,
        "leaderboards": season_leaderboards,
        "record_groups": record_groups,
        "totals": {**totals, "managers": len(standings)},
        "failed": failed,
    }
