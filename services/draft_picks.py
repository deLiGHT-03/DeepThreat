"""Traded draft picks: what moved, who ended up holding each one, and what it became.

A drafted player is easy to look up -- his name is on the pick. A *traded* pick is not, because
Sleeper records a pick by three numbers and nothing else: the draft year, the round, and the
roster it started with. Every hand-off is a trade whose ``draft_picks`` entry says which roster
the pick came from (``previous_owner_id``) and which one it went to (``owner_id``), so the chain
is reconstructed by following those, and the pick's identity -- year, round, original roster --
is what stays fixed across it.

What that buys is an honest answer, and what it cannot buy is worth stating plainly:

* **Who holds it now** is exact: follow the hand-offs to the end. A pick can come back to the
  roster that started with it, which is why the walk follows the recorded previous owner rather
  than assuming the pick only ever moves forwards.
* **What it became** is named by the draft itself. No place in the draft is written on a traded
  pick, but the draft's order maps every manager to a slot and a pick keeps the slot of the
  roster it started with, so the pick's place -- and the rookie taken with it -- is worked out
  exactly: seats the order names are placed from it, and a seat it leaves out (a slot nobody
  holds, or one held by a manager who has since left the league) is placed by the team the hand-
  offs say ended up with the pick, since that team is the one that made that seat's pick. A
  pick that changed hands therefore still ends with the player it became. Where the last recorded
  move and the draft's own seat disagree, the seat still names the player and the disagreement is
  reported beside him, because a move missing from Sleeper's feed is not a reason to leave a
  question unanswered. Only a pick that cannot be placed at all comes back as every pick the
  holding team used in that round, rather than as one of them chosen by guesswork.

This module is what the Trades page opens when a pick is clicked, and what Ask AI reads when it
is asked what became of one.
"""

from __future__ import annotations

from services import trades as trades_service
from services.sleeper import (MAX_NFL_WEEK, SleeperError, get_json, get_league_dashboard,
                              get_league_season_history, get_player_index)

#: How many picks a draft board will list before it says the rest were left out.
MAX_BOARD_ROWS = 80


def chain(league_id: str) -> list[dict]:
    """This league's seasons, newest first."""
    try:
        return get_league_season_history(str(league_id))
    except SleeperError:
        return []


def team_names(league_id: str, season: str) -> dict:
    """Roster id to team name for one season's league."""
    entry = next((item for item in chain(league_id) if str(item["season"]) == str(season)), None)
    if not entry:
        return {}
    try:
        dashboard = get_league_dashboard(entry["league_id"])
    except SleeperError:
        return {}
    return {team["roster_id"]: team["name"] for team in dashboard["teams"]}


def pick_events(league_id: str, up_to: str = "") -> list[dict]:
    """Every traded draft pick in this league's history, as Sleeper filed it, oldest first.

    Only settled trades moved anything, which is the same rule the Trades page counts by, so a
    chain built from here agrees with the trades the page shows.

    ``up_to`` names the newest season to read, for a caller asking about a pick that was spent in
    that season: a pick for a draft already held cannot have been traded after it, so the seasons
    after it hold nothing about it and are not worth reading. Omitted, every season is read.
    """
    jobs: list[tuple[str, str, int]] = []
    for entry in chain(league_id):
        if up_to and str(entry["season"]) > str(up_to):
            continue
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        weeks = min(trades_service._season_weeks(dashboard["league"]), MAX_NFL_WEEK)
        jobs += [(str(entry["season"]), entry["league_id"], week) for week in range(1, weeks + 1)]
    if not jobs:
        return []
    rows_by_season, _ = trades_service._read_weeks(jobs)
    events: list[dict] = []
    for season, rows in rows_by_season.items():
        for row in rows:
            if row.get("type") != "trade" or row.get("status") not in trades_service.SETTLED_STATUSES:
                continue
            for pick in row.get("draft_picks") or []:
                try:
                    round_number = int(pick.get("round") or 0)
                except (TypeError, ValueError):
                    continue
                events.append({
                    "draft_season": str(pick.get("season") or ""),
                    "round": round_number,
                    "original": pick.get("roster_id"),
                    "from": pick.get("previous_owner_id"),
                    "to": pick.get("owner_id"),
                    "filed_season": str(season),
                    "filed_week": int(row.get("leg") or 0),
                    "created": int(row.get("created") or 0),
                    "transaction_id": str(row.get("transaction_id") or ""),
                    # The trade itself, so a caller can say what the pick cost either side
                    # rather than only that it moved.
                    "transaction": row,
                })
    events.sort(key=lambda event: (event["filed_season"], event["filed_week"], event["created"]))
    return events


def _season_team_records(league_id: str, season: str) -> dict:
    """Roster id to team record for one season, the shape the trades service shapes sides with."""
    entry = next((item for item in chain(league_id) if str(item["season"]) == str(season)), None)
    if not entry:
        return {}
    try:
        dashboard = get_league_dashboard(entry["league_id"])
    except SleeperError:
        return {}
    return {team["roster_id"]: team for team in dashboard["teams"]}


def _pick_label(pick: dict) -> str:
    """One traded pick, named the way the page names it: the round, then where it came from.

    The roster it came from is what tells two picks of the same round apart -- a trade swapping
    1st-rounders moves two of them, and without it the consideration would read as the same pick
    twice.
    """
    label = f"the {pick['full']} pick"
    return f"{label} via {pick['via']}" if pick.get("has_via") else label


def _received(side: dict) -> str:
    """What one side of a trade received, said briefly: its players, then its picks."""
    items = [f"{player['name']} ({player['position']})" for player in side["players"]]
    items += [_pick_label(pick) for pick in side["picks"]]
    return ", ".join(items) if items else "nothing"


def _deal(league_id: str, row: dict, filed_season: str) -> dict | None:
    """One trade shaped into what each side gave and received: the pick's price, not just its move.

    A pick changing hands is only half of what a manager asking where it came from wants to know
    -- the other half is what the trade cost. Both sides are named with everything each received,
    because that is the trade; the pick being followed is one of the things on one of the sides.
    """
    if not row:
        return None
    try:
        player_index = get_player_index()
    except SleeperError:
        player_index = {}
    trade = trades_service._shape_trade(row, _season_team_records(league_id, filed_season),
                                        player_index, None)
    sides = [{"team": str(side["team"].get("name") or "a team"),
              "received": _received(side),
              "players": [player["name"] for player in side["players"]],
              "picks": [pick["full"] for pick in side["picks"]]}
             for side in trade["sides"]]
    if not sides:
        return None
    return {
        "transaction_id": trade["transaction_id"],
        "week": trade["week"],
        "sides": sides,
        "note": (f"{filed_season} Wk{trade['week']}: "
                 + "; ".join(f"{side['team']} received {side['received']}" for side in sides)),
    }


def _deal_sentence(handoffs: list[dict]) -> str:
    """The consideration behind a pick's hand-offs, as sentences an answer can carry."""
    notes = [step["deal"]["note"] for step in handoffs if step.get("deal")]
    if not notes:
        return ""
    if len(notes) == 1:
        return f" The trade that moved it — {notes[0]}."
    return " The trades that moved it — " + ". ".join(notes) + "."


def _roster_owners(league_id: str, season: str) -> dict:
    """Roster id to the manager who held it in one season."""
    entry = next((item for item in chain(league_id) if str(item["season"]) == str(season)), None)
    if not entry:
        return {}
    try:
        dashboard = get_league_dashboard(entry["league_id"])
    except SleeperError:
        return {}
    return {team["roster_id"]: str(team.get("owner_id") or "") for team in dashboard["teams"]}


def _shape_pick(pick: dict, draft: dict, draft_id: str, season: str, teams: int) -> dict:
    """One raw pick as the page and Ask AI read it: the player, and where he was taken."""
    metadata = pick.get("metadata") or {}
    name = f"{metadata.get('first_name', '')} {metadata.get('last_name', '')}".strip()
    round_number = int(pick.get("round") or 0)
    number = pick.get("pick_no")
    # Managers say a pick by its round and its place in that round -- "1.05" -- while Sleeper
    # numbers picks through the whole draft, so both are worked out here from the team count the
    # draft was held with.
    slot_label = ""
    if teams and isinstance(number, int) and number > 0:
        slot_label = f"{round_number}.{(number - 1) % teams + 1:02d}"
    return {
        "draft_id": str(draft_id),
        "draft_type": str(draft.get("type") or ""),
        "season": str(draft.get("season") or season),
        "round": round_number,
        "pick_no": number,
        "slot_label": slot_label,
        "roster_id": pick.get("roster_id"),
        "player_id": str(pick.get("player_id") or ""),
        "player": name or str(pick.get("player_id") or ""),
        "position": metadata.get("position") or "",
    }


def _read_drafts(league_id: str, season: str) -> list[dict]:
    """Every draft a season held, each with its picks in order and the order that placed them.

    A season normally holds one draft; the league's first can hold two, a startup and a rookie
    draft. Both are read, because a pick is read off one board while a player can be taken on
    either.
    """
    entry = next((item for item in chain(league_id) if str(item["season"]) == str(season)), None)
    if not entry:
        return []
    try:
        drafts = get_json(f"/league/{entry['league_id']}/drafts") or []
    except SleeperError:
        return []
    boards: list[dict] = []
    for draft in drafts:
        draft_id = draft.get("draft_id")
        if not draft_id:
            continue
        try:
            teams = int((draft.get("settings") or {}).get("teams") or 0)
        except (TypeError, ValueError):
            teams = 0
        try:
            rows = get_json(f"/draft/{draft_id}/picks") or []
        except SleeperError:
            continue
        if not rows:
            continue
        order = {}
        for owner, slot in (draft.get("draft_order") or {}).items():
            try:
                order[str(owner)] = int(slot)
            except (TypeError, ValueError):
                continue
        picks = [_shape_pick(pick, draft, str(draft_id), season, teams) for pick in rows]
        picks.sort(key=lambda item: (item.get("pick_no") or 0))
        boards.append({"draft_id": str(draft_id), "type": str(draft.get("type") or ""),
                       "teams": teams, "order": order,
                       "season": str(draft.get("season") or season), "picks": picks})
    return boards


def season_boards(league_id: str, season: str) -> list[dict]:
    """Every draft one season held, each with its own picks in order.

    This is what a question about a *player* needs rather than a question about a pick: the pick
    that took him may sit on either board of a season that held two.
    """
    return _read_drafts(league_id, season)


def _draft(league_id: str, season: str) -> tuple[list[dict], dict]:
    """One season's draft: every pick in the order it was made, and the order that placed them.

    ``draft_order`` is the part that matters for a traded pick. It maps each manager to the slot
    he picks from, so a pick -- which Sleeper records only by its year, round and original
    roster -- can be placed: the slot of the roster it started with. A linear draft keeps that
    order every round and a snake draft turns it around on even ones.

    A season can hold more than one draft -- a startup and a rookie draft, say -- and then a
    traded pick names a round of two different boards. Naming a place in either would be a guess,
    so a season that held more than one draft comes back without an order to place by.
    """
    boards = _read_drafts(league_id, season)
    picks = boards[0]["picks"] if boards else []
    # ``boards`` is carried either way: one held draft brings the order to place a pick by, and
    # more than one says a round belongs to two boards, which is something an answer has to admit
    # rather than read off the one it happens to have.
    meta = ({"boards": 1, **{key: value for key, value in boards[0].items() if key != "picks"}}
            if len(boards) == 1 else {"boards": len(boards)})
    return picks, meta


def _draft_picks(league_id: str, season: str) -> list[dict]:
    """Every pick of one season's draft, in the order it was made."""
    return _draft(league_id, season)[0]


def _seat_slots(league_id: str, season: str, board: dict) -> dict:
    """Which slot each team picks from in one draft, and what the draft's order leaves over.

    ``draft_order`` is the record: it maps a manager to the slot he picks from, so each roster's
    slot is read off it. What it leaves over are the seats it does not place -- a slot no manager
    holds, and a slot held by a manager who has since left the league -- and those are read from
    the picks instead: a team that used a slot the order never gave it was picking from its own
    seat, and the order simply leaves that seat out. One team and one slot left over after that
    are each other's. More than one of each is left untied rather than paired by guesswork.
    """
    owners = _roster_owners(league_id, season)
    teams = int(board.get("teams") or 0)
    order = board.get("order") or {}
    slots: dict[int, int] = {}
    missing: list[int] = []
    for roster_id, owner in owners.items():
        slot = order.get(str(owner))
        if slot:
            slots[roster_id] = int(slot)
        else:
            missing.append(roster_id)
    used = set(slots.values())
    left = [slot for slot in range(1, teams + 1) if slot not in used] if teams else []
    # A team that used a slot the order never gave it holds that seat. Read from the first round,
    # which runs in seat order in both a linear and a snake draft.
    first_round = {pick.get("pick_no"): pick for pick in board.get("picks") or []}
    for slot in list(left):
        pick = first_round.get(slot)
        maker = pick.get("roster_id") if pick else None
        if maker in missing:
            slots[maker] = slot
            missing.remove(maker)
            left.remove(slot)
    if len(missing) == 1 and len(left) == 1:
        slots[missing[0]] = left[0]
        missing, left = [], []
    return {"slots": slots, "missing": missing, "left": left}


def _seat_pick(board: dict, slot: int, round_number: int) -> dict | None:
    """The pick one seat's slot was spent on, in one round of one draft board."""
    try:
        teams = int(board.get("teams") or 0)
        slot = int(slot)
        round_number = int(round_number)
    except (TypeError, ValueError):
        return None
    if not teams or not 1 <= slot <= teams or round_number < 1:
        return None
    if str(board.get("type")) == "snake" and round_number % 2 == 0:
        slot = teams + 1 - slot
    number = (round_number - 1) * teams + slot
    return next((pick for pick in board.get("picks") or [] if pick["pick_no"] == number), None)


def _placed_pick(league_id: str, season: str, round_number: int, origin,
                 holder=None) -> dict:
    """The pick one roster's seat was spent on in a round, and how it was placed.

    A seat the draft's order names is placed exactly. A seat the order leaves out is placed by
    the team the trade records say ended up with the pick: the pick that seat's slot holds must be
    the one that team made, which is the same rule the trace already trusts, used here to pick
    between the seats the order never named instead of only to confirm one. ``holder`` is that
    team's roster id.

    Returns the pick (or None) and how it was arrived at: "order" for a seat the draft's order
    names that the team also used, "unplaced" for a seat read off the team that used it,
    "order-disagrees" when the only placement is one the team did not use, and "" when nothing
    could be placed.

    A season that held two drafts is why the boards are all tried rather than the first one:
    a round exists on both, and the board whose pick the team actually made is the one holding
    this pick. A seat that the team did not use is only the answer once every board has been.
    """
    disagreeing = None
    for board in _read_drafts(league_id, season):
        seats = _seat_slots(league_id, season, board)
        if origin in seats["slots"]:
            pick = _seat_pick(board, seats["slots"][origin], round_number)
            if pick is None:
                continue
            if holder is None or pick.get("roster_id") == holder:
                return {"pick": pick, "how": "order"}
            disagreeing = disagreeing or {"pick": pick, "how": "order-disagrees"}
            continue
        if holder is None:
            continue
        hits = [pick for pick in (_seat_pick(board, slot, round_number)
                                  for slot in seats["left"])
                if pick and pick.get("roster_id") == holder]
        if len(hits) == 1:
            return {"pick": hits[0], "how": "unplaced"}
    return disagreeing or {"pick": None, "how": ""}


def _shape(pick: dict) -> dict:
    """One pick as the page and Ask AI read it."""
    return {"pick_no": pick["pick_no"], "slot_label": pick["slot_label"],
            "player_id": pick["player_id"], "player": pick["player"],
            "position": pick["position"]}


def drafted_with(league_id: str, season: str, round_number: int, roster_id,
                 origin=None) -> tuple[list[dict], str, int]:
    """The pick a team used in one round of one draft, and the player it took.

    ``origin`` is the roster the pick started with. With it the pick is placed by the draft's own
    order, and a seat that order leaves out is placed by the team the trade records say ended up
    with the pick -- so a pick that changed hands still ends with the player it became.

    ``problem`` carries a caveat worth saying next to the player: that the last recorded move of
    the pick and the draft's own seat disagree, which means a move is missing from Sleeper's
    feed. Only when the pick cannot be placed at all does every pick the holding team used in
    that round come back, because naming one of several rookies would be a guess dressed as a
    fact.
    """
    if not chain(league_id):
        return [], "This league's seasons could not be read.", 0
    if not any(str(item["season"]) == str(season) for item in chain(league_id)):
        return [], f"This league has not played a {season} season.", 0
    boards_list = _read_drafts(league_id, season)
    boards = len(boards_list)
    if not boards_list:
        return [], f"The {season} draft could not be read.", boards
    names = team_names(league_id, str(season))

    def who(some_id) -> str:
        return names.get(some_id) or f"roster {some_id}"

    if origin is not None:
        verdict = _placed_pick(league_id, season, int(round_number), origin, holder=roster_id)
        placed = verdict.get("pick")
        if placed is not None:
            # A named seat fixes the pick's place even when the draft says another team used it:
            # the seat is a fact of the draft's order, so a move that disagrees with it is a move
            # missing from the feed, not a reason to leave the player unnamed.
            problem = ("" if verdict.get("how") != "order-disagrees" else
                       f"The {season} draft's order seats this pick with {who(placed['roster_id'])}, "
                       f"and the trades leave it with {who(roster_id)}: a move of it is missing "
                       f"from Sleeper's trade feed.")
            return [_shape(placed)], problem, boards
    taken = [_shape(pick) for pick in boards_list[0]["picks"]
             if pick["round"] == int(round_number) and pick["roster_id"] == roster_id]
    return sorted(taken, key=lambda item: item.get("pick_no") or 0), "", boards


def draft_board(league_id: str, season: str, round_number: int | None = None) -> dict:
    """One season's draft, in order: who was taken, where, and by which team.

    This is the whole draft rather than a trace of one pick, which is what a question like "who
    went in the third round" needs. Later rounds of a startup draft are long, so the board is
    listed to a limit and says how many more there were.
    """
    picks = _draft_picks(league_id, season)
    if not picks:
        return {"picks": [], "note": (f"No {season} draft could be read for this league."
                                      if chain(league_id) else
                                      "This league's seasons could not be read.")}
    names = team_names(league_id, season)
    if round_number:
        picks = [pick for pick in picks if pick["round"] == int(round_number)]
        if not picks:
            rounds = sorted({int(pick["round"]) for pick in _draft_picks(league_id, season)})
            return {"picks": [], "note": (f"The {season} draft had no round {round_number}; "
                                          f"it ran {rounds[0]}-{rounds[-1]}.")}
    listed = picks[:MAX_BOARD_ROWS]
    rows = [{"round": pick["round"], "pick": pick["pick_no"], "place": pick["slot_label"],
             "team": names.get(pick["roster_id"], f"roster {pick['roster_id']}"),
             "player": pick["player"], "position": pick["position"]} for pick in listed]
    what = f"round {round_number} of the {season} draft" if round_number else f"the {season} draft"
    note = (f"{len(picks)} pick{'s' if len(picks) != 1 else ''} in {what}, in draft order: "
            + "; ".join(f"{row['place'] or row['pick']} {row['player']} ({row['position']}, "
                        f"{row['team']})" for row in rows[:12]))
    if len(rows) > 12:
        note += f" … and {len(rows) - 12} more."
    if len(picks) > len(listed):
        note += f" Listing the first {len(listed)}."
    return {"picks": rows, "pick_count": len(picks), "draft": f"{season}", "note": note}


def trace(league_id: str, season: str, round_number: int, origin=None) -> dict:
    """Follow one traded pick from the roster that started with it to the player it became.

    ``origin`` is the roster id the pick started with. Without one, every team whose pick of
    that round was traded is named instead, because a round on its own is several different
    picks and picking one of them would be inventing a fact.
    """
    events = [event for event in pick_events(league_id)
              if event["draft_season"] == str(season) and event["round"] == int(round_number)]
    if origin is not None:
        events = [event for event in events if event["original"] == origin]
    label = f"{season} {trades_service.round_label(int(round_number))}"

    def naming(roster_id, at_season: str) -> str:
        return team_names(league_id, at_season).get(roster_id) or f"roster {roster_id}"

    if not events:
        # A round on its own is several picks, so "nothing moved a 2019 1st" is only true of a
        # round nobody's pick of it was asked for. Naming the team the pick started with keeps
        # the answer about that team's pick rather than about every team's.
        who = naming(origin, str(season)) if origin is not None else ""
        return {"pick": label, "handoffs": [], "drafted_with_it": [], "multi_draft": False,
                "note": (f"No trade in this league's history moved a {label}"
                         + (f" that started with {who}" if who else "")
                         + (f", so {who} kept it: that pick was their own and no trade moved "
                            f"it." if who else "")
                         + f" If it was traded, that trade is not in the weeks this app can "
                           f"read.")}

    originals = sorted({event["original"] for event in events})
    if origin is None and len(originals) > 1:
        names = team_names(league_id, events[0]["filed_season"])
        holders = [names.get(item) or f"roster {item}" for item in originals]
        return {"pick": label, "handoffs": [], "drafted_with_it": [], "teams_with_this_pick": holders,
                "note": (f"{len(originals)} different teams had a {label} traded. Name the team "
                         f"the pick started with: {', '.join(holders)}.")}

    original_id = originals[0]
    # Each recorded hand-off moves the pick to the roster it names, in the order they were filed.
    # A hand-off whose starting roster is not the one the record before it left the pick with is
    # still a real move -- Sleeper's feed is not a complete history of a pick, and a trade that is
    # missing from it must not make every hand-off after it disappear. Such a step is counted and
    # the chain is reported as starting later than the pick did.
    steps: list[dict] = []
    current = original_id
    unread = 0
    for event in events:
        if event["from"] != current:
            unread += 1
        steps.append(event)
        current = event["to"]
    final_holder = steps[-1]["to"] if steps else original_id

    starters = naming(original_id, steps[0]["filed_season"] if steps else str(season))
    # Each hand-off carries the trade that made it, not only who it went between: the price is
    # what a manager asking where a pick came from is usually after.
    handoffs = [{"season": event["filed_season"], "week": event["filed_week"],
                 "from": naming(event["from"], event["filed_season"]),
                 "to": naming(event["to"], event["filed_season"]),
                 "deal": _deal(league_id, event.get("transaction"), event["filed_season"])}
                for event in steps]
    holder_name = handoffs[-1]["to"] if handoffs else starters
    taken, problem, boards = drafted_with(league_id, season, int(round_number), final_holder,
                                          origin=original_id)
    result = {"pick": label, "original_team": starters, "current_holder": holder_name,
              "handoffs": handoffs, "handed_off": len(handoffs), "unread_moves": unread,
              "multi_draft": boards > 1, "drafted_with_it": taken,
              # A named player and a caveat about a missing move are not alternatives: the page
              # shows both, because the player is what the pick became and the caveat is why the
              # route to him may be short a step.
              "caveat": problem if taken else ""}
    if taken:
        names = ", ".join(f"{item['player']}"
                          + (f" (pick {item['slot_label']})" if item.get("slot_label")
                             else f" (pick {item['pick_no']})" if item.get("pick_no") else "")
                          for item in taken)
        note = (f"The {label} that started with {starters} changed hands "
                f"{len(handoffs)} time{'s' if len(handoffs) != 1 else ''} and was held by "
                f"{holder_name} when the {season} draft was made. "
                + (f"{holder_name} drafted {names} with it." if len(taken) == 1
                   else f"{holder_name} held {len(taken)} picks in that round and drafted "
                        f"{names} with them, so which of those rookies this particular pick "
                        f"became cannot be told apart. {starters} did not use this pick."))
    elif problem:
        note = (f"The {label} started with {starters} and was last held by {holder_name} after "
                f"{len(handoffs)} hand-off(s). {problem}")
    else:
        note = (f"The {label} started with {starters} and is held by {holder_name} after "
                f"{len(handoffs)} hand-off(s). That draft has not been held yet, so no player "
                f"has been taken with it.")
    if taken and problem:
        note += f" {problem}"
    note += _deal_sentence(handoffs)
    if unread:
        note += (f" {unread} move{'s' if unread != 1 else ''} of this pick happened in weeks "
                 f"this app cannot read, so its route is only complete from "
                 f"{handoffs[0]['season']} Wk{handoffs[0]['week']} on.")
    if boards > 1:
        note += (f" That season held {boards} drafts, so the place above is read from the one "
                 f"whose own order holds this pick, not from its year alone.")
    result["note"] = note
    return result


def provenance(league_id: str, season: str, round_number: int, pick_no: int,
               draft_id: str = "") -> dict:
    """Whose pick a made pick was, and the trade that brought it where it was used.

    A held draft records only the team that *made* a pick, so whose pick it originally was has to
    be worked out: the draft's order says which slot belonged to which team, and a pick keeps the
    slot of the roster it started with. That is the same placement :func:`trace` trusts, which is
    why it is the same code -- and only the trades whose own placement lands on *this* pick's
    number are its provenance, which is what stops a search of the round being read as this pick.

    ``draft_id`` names the draft the pick is on, for a caller that found the pick itself -- a
    season that held two drafts has two picks of the same number, and only that caller knows which
    one is being asked about. Omitted, every board of the season has to agree before an origin is
    named.

    Returns the team it started with, the hand-offs that moved it, and what each of those trades
    cost both sides. A pick nobody traded comes back with no hand-offs, which is an answer too --
    "it was his own" is as much an answer as a chain of trades. When the draft's own order does not
    place the pick, and so whose pick it was cannot be read, that is said rather than left blank:
    a question answered by silence looks like one that was never asked about.
    """
    label = f"{season} {trades_service.round_label(int(round_number))}"
    named = f"That pick -- {label}, pick {int(pick_no)} --"
    all_boards = _read_drafts(league_id, season)
    if not all_boards:
        return {"origin": "", "handoffs": [], "traded": None, "placed": False, "note": (
            f"{named} cannot be traced to the team it started with: the {season} draft could "
            f"not be read.")}
    asked = [board for board in all_boards if str(board["draft_id"]) == str(draft_id)] if draft_id else []
    boards = asked or all_boards
    # Whose pick it was is asked of the draft's order, not of the trades: a pick nobody traded has
    # no trade to read its owner from, and "it was his own" is the answer to that question. Every
    # seat of every draft that season held is offered the same placement the trace trusts, and the
    # seat that lands on this pick's number is the one it started with -- provided only one does.
    # A season that held two drafts can place one number with a different team on each board, and
    # then nothing in the records separates them.
    round_events = [event for event in pick_events(league_id, up_to=str(season))
                    if event["draft_season"] == str(season)
                    and event["round"] == int(round_number)]

    # Each board is asked on its own, by two witnesses: the seats its order places this number
    # with, and -- only where the season held a single draft -- the trades that filed a pick of this
    # round under a roster and left it with the team that used this pick. A traded pick names its
    # year, round and original roster but not which draft it belongs to, so in a season that held
    # two (a startup and a rookie draft) a trade of "that round" may be about the other board and
    # is no witness at all. Where the witnesses agree, or only one of them speaks, the origin is
    # read off it; where they disagree, that board settles nothing.
    trades_speak = len(all_boards) == 1
    solved = set()
    conflicted = False
    for board in boards:
        seats = _seat_slots(league_id, str(season), board)
        seat = {roster_id for roster_id, slot in seats["slots"].items()
                if (_seat_pick(board, slot, int(round_number)) or {}).get("pick_no") == pick_no}
        here = next((pick for pick in board["picks"]
                     if pick["round"] == int(round_number) and pick["pick_no"] == pick_no), None)
        trade = set()
        if trades_speak and here is not None:
            trade = {event["original"] for event in round_events
                     if event["to"] == here.get("roster_id") and event.get("original") is not None}
        if len(seat) == 1 and len(trade) == 1:
            if next(iter(seat)) == next(iter(trade)):
                solved.add(next(iter(seat)))
            else:
                conflicted = True
        elif len(seat) == 1:
            solved.add(next(iter(seat)))
        elif len(trade) == 1:
            solved.add(next(iter(trade)))
        elif len(seat) > 1 or len(trade) > 1:
            conflicted = True
    origin_id = next(iter(solved)) if len(solved) == 1 else None
    if origin_id is None:
        names = team_names(league_id, str(season))
        if len(solved) > 1:
            holders = ", ".join(names.get(item) or f"roster {item}" for item in sorted(solved))
            return {"origin": "", "handoffs": [], "traded": None, "placed": False, "note": (
                f"{named} cannot be traced to the team it started with: the {season} records "
                f"place that pick with {len(solved)} different teams ({holders}), between its "
                f"draft{'s' if len(boards) != 1 else ''} and the trades that moved a pick of that "
                f"round, so which of them it started with cannot be told apart.")}
        if conflicted:
            return {"origin": "", "handoffs": [], "traded": None, "placed": False, "note": (
                f"{named} cannot be traced to the team it started with: the {season} draft's own "
                f"order and the trades that moved a pick of that round disagree about it, and "
                f"nothing in the records settles which is right.")}
        # Two ways this happens, and both are Sleeper's records rather than a reading of them: a
        # draft order that leaves the slot out, and a slot held by a manager who has since left
        # the league, whose roster no longer exists in that season to be named by.
        return {"origin": "", "handoffs": [], "traded": None, "placed": False, "note": (
            f"{named} cannot be traced back to the team it started with: the {season} draft's "
            f"own order does not place it, which is what happens when the manager who held that "
            f"slot has left the league and the trade records do not say which team used it. "
            f"Sleeper files a traded pick under the roster it started with, so with that roster "
            f"unplaceable, whether the pick moved cannot be read either.")}
    events = [event for event in round_events if event["original"] == origin_id]
    if len(all_boards) > 1 and events:
        # A traded pick names its round but not its draft, so with two drafts a chain of that
        # round's picks may be the other board's. It is read as this pick's only when it ends
        # where this pick was made -- the same rule the trace trusts, used here to keep a trade
        # of a different draft's pick off this one.
        makers = set()
        for board in boards:
            here = next((pick for pick in board["picks"] if pick["round"] == int(round_number)
                         and pick["pick_no"] == pick_no), None)
            if here is not None and here.get("roster_id") is not None:
                makers.add(here["roster_id"])
        if events[-1]["to"] not in makers:
            events = []
    original_id = origin_id
    steps = events

    def naming(roster_id, at_season: str) -> str:
        return team_names(league_id, at_season).get(roster_id) or f"roster {roster_id}"

    handoffs = [{"season": event["filed_season"], "week": event["filed_week"],
                 "from": naming(event["from"], event["filed_season"]),
                 "to": naming(event["to"], event["filed_season"]),
                 "deal": _deal(league_id, event.get("transaction"), event["filed_season"])}
                for event in steps]
    # A pick nobody traded is named by the team that held it at the draft, which is the season
    # being asked about; a traded one by the team it started with, named as it was when the first
    # trade was filed -- both are the team a manager reading the answer would have called it.
    starters = naming(original_id, steps[0]["filed_season"] if steps else str(season))
    result = {"pick": f"{label}, pick {int(pick_no)}", "origin": starters,
              "origin_roster": original_id, "handoffs": handoffs,
              "handed_off": len(handoffs), "traded": bool(handoffs), "placed": True}
    if handoffs:
        result["note"] = (f"That pick -- {label}, pick {int(pick_no)} -- was originally "
                          f"{starters}'s." + _deal_sentence(handoffs))
    else:
        result["note"] = (f"That pick -- {label}, pick {int(pick_no)} -- was {starters}'s own; "
                          f"no trade moved it.")
    return result

