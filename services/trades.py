"""This season's trades, read from Sleeper's weekly transaction records.

Sleeper publishes no trades endpoint, so a trade is a weekly transaction whose ``type``
is ``"trade"``. The season's weeks are walked for them, and each one is shaped into the
rosters that traded, the players each side gained, and the draft picks that changed hands.

Picks are labelled briefly -- ``1st Rd`` rather than ``1st round pick via pharmaski`` --
because a card listing half a dozen picks reads faster short. The roster a pick came from
rides along as ``via`` so a page can reveal it on demand instead of showing it up front.

The page reads one season, one week of it, or one name. A name search reads every season the
league has played, because a player's trades are a career rather than a season, and it matches
what was typed loosely (see :mod:`services.name_search`), so a name spelled from memory -- with
a letter or two in the wrong place -- still finds the player meant. The same box takes a team:
the name a manager reads next to a trade is as likely to be the other team's as it is a
player's, so a name no player answers to is tried against the teams these trades involved.

A team is followed by its manager's user id, which survives a rename, so a search for a team
name finds the seasons it traded under that name and the ones it traded under another -- and
says so, because trades from a season under a name the reader does not recognise need
explaining.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services import name_search
from services import pick_query
from services.sleeper import (MAX_NFL_WEEK, SleeperError, get_json,
                              get_league_dashboard, get_league_season_history,
                              get_player_index, week_label)

#: Only transactions in these states changed hands; a declined or failed one did not.
SETTLED_STATUSES = ("complete",)

#: How many weeks of transactions are read from Sleeper at once. Each week is a round trip, and
#: a name search reads every week of every season, so they are read side by side.
FETCH_WORKERS = 8

#: How many of the players a search settles on are named in the sentence above the results.
NAMED_MATCHES = 4

#: How many near-miss names a search that settled on nobody may offer. Players are offered
#: first and a team only if a player does not fill the list, because a team named after a
#: player ("HI IM RAY RICE") is common and the player is the more particular reading.
NEAR_MISSES = 3


def round_label(round_number: int) -> str:
    """Name a draft round the way a manager would say it: ``3rd Rd``, not ``round 3``."""
    number = int(round_number)
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix} Rd"


def _player_entry(player_id, player_index: dict) -> dict:
    """Shape one moved player: the name and the position badge that sits beside it.

    Sleeper's injury designations (``IR``, ``Out``, ``Questionable``) are deliberately
    left off. A trade is a record of something that already happened, so a status filed
    with the deal would read as this week's news rather than that week's.
    """
    player = player_index.get(str(player_id)) or {}
    name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    return {
        "id": str(player_id),
        "name": name or str(player_id),
        "position": player.get("position") or "—",
        "team": player.get("team") or "FA",
    }


def _pick_entry(pick: dict, teams_by_id: dict) -> dict:
    """Shape one traded draft pick.

    ``owner_id`` is the roster holding it now and ``previous_owner_id`` the one it came
    from, which is the "via" a manager reads a pick by. Sleeper keeps no name for either,
    so the team records supply it.
    """
    round_number = int(pick.get("round") or 0)
    season = str(pick.get("season") or "").strip()
    label = round_label(round_number) if round_number else "Pick"
    full = f"{season} {label}".strip() if season else label
    via_team = teams_by_id.get(pick.get("previous_owner_id"))
    via_name = str(via_team["name"]) if via_team else ""
    # ``roster_id`` is the roster the pick STARTED with, and that -- with the season and the
    # round -- is what identifies a pick for the rest of its life: every hand-off afterwards is
    # recorded as a change of owner. The page needs it to look up what became of the pick, since
    # "via" on its own is only where this one trade got it from.
    origin_team = teams_by_id.get(pick.get("roster_id"))
    return {
        "round": round_number,
        "season": season,
        "label": label,
        "full": full,
        "via": via_name,
        "has_via": bool(via_name),
        "origin_id": pick.get("roster_id"),
        "origin": str(origin_team["name"]) if origin_team else "",
        # Set when a pick search is what put this trade on the page, so the page can mark the
        # pick that was actually asked for rather than every pick the trade moved.
        "matched": False,
    }


def _shape_trade(tx: dict, teams_by_id: dict, player_index: dict,
                 my_roster_id) -> dict:
    roster_ids = [roster_id for roster_id in (tx.get("roster_ids") or [])
                  if roster_id in teams_by_id]
    adds = tx.get("adds") or {}
    sides = []
    for roster_id in roster_ids:
        players = [_player_entry(player_id, player_index)
                   for player_id, receiving_roster in adds.items()
                   if receiving_roster == roster_id]
        # A name sits next to its position everywhere on this page, so keep the list in
        # lineup order rather than the order Sleeper happened to return.
        players.sort(key=lambda player: (player["position"], player["name"].casefold()))
        picks = [_pick_entry(pick, teams_by_id)
                 for pick in (tx.get("draft_picks") or [])
                 if pick.get("owner_id") == roster_id]
        picks.sort(key=lambda pick: (pick["season"], pick["round"]))
        sides.append({
            "roster_id": roster_id,
            "team": teams_by_id[roster_id],
            "players": players,
            "picks": picks,
            "is_mine": roster_id == my_roster_id,
        })
    sides.sort(key=lambda side: not side["is_mine"])
    return {
        "transaction_id": str(tx.get("transaction_id") or ""),
        "week": int(tx.get("leg") or 0),
        "created": int(tx.get("created") or 0),
        "sides": sides,
        "is_mine": my_roster_id is not None and my_roster_id in roster_ids,
        "player_count": sum(len(side["players"]) for side in sides),
        "pick_count": sum(len(side["picks"]) for side in sides),
    }


def _own_team(teams: list[dict], manager_id: str, username: str) -> dict | None:
    """The roster the connected manager held in one season's list of teams.

    Sleeper's user id belongs to the manager and survives a rename, so it is asked for
    first; the username is the fallback for a connection that only ever carried one. A
    roster id is worth nothing outside the season that issued it, so this is asked once per
    season rather than once per page.
    """
    manager_id = str(manager_id).strip()
    want_username = str(username).strip().casefold()
    team = next((team for team in teams
                 if manager_id and str(team.get("owner_id")) == manager_id), None)
    if team is None and want_username:
        team = next((team for team in teams
                     if str(team.get("username") or "").casefold() == want_username), None)
    return team


def _read_transactions(league_id: str, week: int) -> tuple[list[dict], bool]:
    """Read one week's transactions. The flag says Sleeper would not answer for it."""
    try:
        return get_json(f"/league/{league_id}/transactions/{week}") or [], False
    except SleeperError:
        return [], True


def _read_weeks(jobs: list[tuple[str, str, int]]) -> tuple[dict[str, list[dict]], list[str]]:
    """Read many weeks of transactions at once: ``(season, league id, week)`` jobs.

    Returns each season's rows, its weeks in the order they were asked for, and the labels of
    the weeks Sleeper would not answer for -- a history that is one week short is still worth
    showing, so a failed read is reported rather than raised.
    """
    rows_by_season: dict[str, list[dict]] = {}
    failed: list[str] = []
    if not jobs:
        return rows_by_season, failed
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        reads = list(executor.map(lambda job: _read_transactions(job[1], job[2]), jobs))
    for (season, _, week), (rows, bad) in zip(jobs, reads):
        if bad:
            failed.append(f"{season} Wk{week}")
        rows_by_season.setdefault(season, []).extend(rows)
    failed.sort()
    return rows_by_season, failed


def _settled_trades(rows: list[dict], teams_by_id: dict, player_index: dict,
                    my_roster_id) -> list[dict]:
    """Shape one season's transaction rows into the trades it filed, oldest first.

    A transaction can appear under the week it was filed and the week its leg belongs to, so
    it is counted once; only a settled trade moved anything, and only those come back.
    """
    trades: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("type") != "trade" or row.get("status") not in SETTLED_STATUSES:
            continue
        transaction_id = str(row.get("transaction_id") or "")
        if not transaction_id or transaction_id in seen:
            continue
        seen.add(transaction_id)
        trade = _shape_trade(row, teams_by_id, player_index, my_roster_id)
        if trade["sides"]:
            trades.append(trade)
    trades.sort(key=lambda trade: (trade["week"], trade["created"]))
    return trades


def _week_note(count: int, focus_count: int, whose: str) -> str:
    """Say what a week's tab offers before it has been opened.

    ``whose`` names the manager the left column is showing as a possessive ("yours", "Silky J,
    Playa Hater's"), because the dot on the tab is read against that column rather than against
    whoever is connected: a week the league traded in and the viewed manager traded in is a
    different week from one only the league traded in.
    """
    note = f"{count} trade{'s' if count != 1 else ''} this week"
    if not focus_count:
        return note
    return f"{note} · {whose}" if focus_count == 1 else f"{note} · {focus_count} of them {whose}"


def _owner_of(side: dict) -> str:
    """The manager behind one side of a trade: his Sleeper user id, which survives a rename."""
    return str((side.get("team") or {}).get("owner_id") or "").strip()


def _mark_focus(trades: list[dict], owner_id: str) -> None:
    """Point every trade at the manager whose dealings the left column is showing.

    Ownership is read off the manager's user id rather than off a roster, because a roster id
    means nothing outside the season that issued it and the column can be pointed at anyone the
    league has ever had -- including the seasons before a manager renamed, which his id follows
    him through. An empty ``owner_id`` marks nobody as the focus, which is what a league that
    cannot name its own teams gets rather than a wrong guess.
    """
    for trade in trades:
        for side in trade["sides"]:
            side["is_focus"] = bool(owner_id) and _owner_of(side) == owner_id
        trade["is_focus"] = any(side["is_focus"] for side in trade["sides"])
        trade["focus_side"] = next((side for side in trade["sides"] if side["is_focus"]), None)
        trade["focus_partners"] = [side for side in trade["sides"] if not side["is_focus"]]


def _owner_options(pairs: list[tuple[str, str]], focus_owner_id: str,
                   my_owner_id: str) -> list[dict]:
    """Every manager the page can open, once each, under the name he trades under.

    A manager is keyed by his user id, so one who renamed -- or whose team was called something
    else three seasons ago -- is still one entry. The names seen first win, so a caller that
    offers the newest names before an older season's wins for that manager. The connected
    manager sorts first, because that is the page's own column, and the one being shown is
    marked so the list can say which entry is the open one.
    """
    seen: dict[str, dict] = {}
    for owner_id, name in pairs:
        owner_id = str(owner_id or "").strip()
        if not owner_id or owner_id in seen:
            continue
        seen[owner_id] = {
            "owner_id": owner_id,
            "label": str(name or "").strip() or "Unnamed team",
            "is_me": owner_id == my_owner_id,
            "is_focus": owner_id == focus_owner_id,
        }
    return sorted(seen.values(),
                  key=lambda entry: (not entry["is_me"], entry["label"].casefold()))


def _season_weeks(league: dict) -> int:
    """How many weeks of transactions to walk: every week the season has reached."""
    try:
        end_week = int(league.get("season_end_week") or league.get("last_week") or 0)
    except (TypeError, ValueError):
        end_week = 0
    try:
        nfl_state = get_json("/state/nfl") or {}
        current_week = int(nfl_state.get("week") or nfl_state.get("display_week") or 0)
    except (SleeperError, TypeError, ValueError):
        current_week = 0
    # A trade is filed against the week it was processed, so the week in progress can
    # still gain one and has to be walked too.
    return max(end_week, current_week, 1) if current_week else max(end_week, 1)


def _seasons_label(seasons: list[str]) -> str:
    """Name the seasons a search read, oldest first, the way the history page names a span."""
    if not seasons:
        return ""
    ordered = sorted(seasons)
    return ordered[0] if len(ordered) == 1 else f"{ordered[0]}–{ordered[-1]}"


def _matched_label(names: list[str]) -> str:
    """Name the players a search settled on, in a sentence's worth of words.

    Searching a surname can honestly mean several players, so the first few are named and the
    rest are counted rather than listed.
    """
    if len(names) <= NAMED_MATCHES:
        return ", ".join(names)
    return f"{', '.join(names[:NAMED_MATCHES])} and {len(names) - NAMED_MATCHES} more"


def _traded_players(trades: list[dict]) -> list[dict]:
    """Every player these trades moved, each once: the pool a name search reads."""
    players: dict[str, dict] = {}
    for trade in trades:
        for side in trade["sides"]:
            for player in side["players"]:
                players.setdefault(player["id"], player)
    return sorted(players.values(), key=lambda player: player["name"].casefold())


def _involves(trade: dict, player_ids: set[str]) -> bool:
    """Whether a trade moved any of these players."""
    return any(player["id"] in player_ids
               for side in trade["sides"] for player in side["players"])


def _traded_teams(trades: list[dict]) -> list[dict]:
    """Every team these trades involved, once per manager, under every name it has carried.

    The manager's user id is the identity, because it survives a rename while a roster id does
    not even survive a season: the names a team traded under in 2019 and the names it traded
    under in 2026 pool under the one id. That is what lets a search follow a team through a
    rename instead of only finding the seasons it went by the searched name.

    The trades arrive newest first, so the first name seen for a manager is its current one:
    that becomes the team's label and the rest are kept as the names it used to carry.
    """
    teams: dict[str, dict] = {}
    for trade in trades:
        for side in trade["sides"]:
            team = side.get("team") or {}
            owner_id = str(team.get("owner_id") or "").strip()
            # A roster Sleeper does not name an owner for cannot be followed across seasons,
            # and a roster id means nothing outside its own season, so it is keyed by both.
            key = owner_id or f"{trade.get('season', '')}#{side.get('roster_id')}"
            entry = teams.get(key)
            if entry is None:
                entry = {"owner_id": owner_id, "names": [], "label": "", "label_season": ""}
                teams[key] = entry
            for value in (team.get("name"), team.get("owner"), team.get("username")):
                text = str(value or "").strip()
                if text and text not in entry["names"]:
                    entry["names"].append(text)
            if not entry["label"]:
                entry["label"] = str(team.get("name") or team.get("owner") or "Team").strip()
                entry["label_season"] = str(trade.get("season") or "")
    return sorted(teams.values(), key=lambda entry: entry["label"].casefold())


def _team_score(query: str, entry: dict) -> float:
    """How well one team answers a search, read against every name it has carried."""
    return max((name_search.score(query, name) for name in entry["names"]), default=0.0)


def _team_closeness(query: str, entry: dict) -> float:
    """The same measure with the gate left open: what a near miss at a team looks like."""
    return max((name_search.closeness(query, name) for name in entry["names"]), default=0.0)


def _match_teams(query: str, teams: list[dict]) -> tuple[list[dict], list[str]]:
    """Split candidate teams into the ones a search means and the names nearest to it.

    The team counterpart of :func:`services.name_search.match`, and it answers the same way: a
    team comes back only when the search settled on one, and a search that settled on nobody is
    told the names it came closest to.
    """
    scored = [(_team_score(query, entry), entry) for entry in teams]
    found = sorted((pair for pair in scored if pair[0] > 0),
                   key=lambda pair: (-pair[0], pair[1]["label"].casefold()))
    if found:
        return [entry for _, entry in found], []
    nearest = sorted(((_team_closeness(query, entry), entry["label"]) for entry in teams),
                     key=lambda pair: (-pair[0], pair[1].casefold()))
    return [], [label for value, label in nearest[:NEAR_MISSES] if value > 0]


def _involves_owner(trade: dict, owner_ids: set[str]) -> bool:
    """Whether a trade involved any of these managers."""
    return any(str((side.get("team") or {}).get("owner_id") or "") in owner_ids
               for side in trade["sides"])


def _read_chain_trades(league_id: str, manager_id: str, username: str) -> tuple[list[dict], list[str], list[str]]:
    """Read every trade this league has filed, newest first: the pool both searches read.

    Every season of the chain is read, because a player's trades are a career and a pick's life
    runs across seasons too, and each season is asked for its own rosters: the roster id a
    manager held in 2024 means nothing in 2026. Returns the trades, the weeks that could not be
    read, and the seasons that were.
    """
    prepared: list[dict] = []
    jobs: list[tuple[str, str, int]] = []
    for season in get_league_season_history(league_id):
        try:
            dashboard = get_league_dashboard(season["league_id"])
        except SleeperError:
            continue  # A season that cannot be read is left out rather than failing the search.
        league = dashboard["league"]
        own_team = _own_team(dashboard["teams"], manager_id, username)
        weeks = min(_season_weeks(league), MAX_NFL_WEEK)
        prepared.append({
            "season": str(season["season"]),
            "teams": {team["roster_id"]: team for team in dashboard["teams"]},
            "my_roster_id": own_team["roster_id"] if own_team else None,
            "league_id": season["league_id"],
        })
        jobs += [(str(season["season"]), season["league_id"], week)
                 for week in range(1, weeks + 1)]

    rows_by_season, failed_weeks = _read_weeks(jobs)
    player_index = get_player_index()
    trades: list[dict] = []
    for entry in prepared:
        for trade in _settled_trades(rows_by_season.get(entry["season"], []), entry["teams"],
                                     player_index, entry["my_roster_id"]):
            trade["season"] = entry["season"]
            trades.append(trade)
    # Newest season first, and inside a season the newest week, so a career reads like a log.
    trades.sort(key=lambda trade: (trade["season"], trade["week"], trade["created"]),
                reverse=True)
    return trades, failed_weeks, [entry["season"] for entry in prepared]


def _search_chain(league_id: str, query: str, manager_id: str, username: str) -> dict:
    """Find the trades a typed name was in, across every season the league has played.

    The name is read as a player's first and a team's only when no player answers to it, because
    a team is often named after a player and the player is the more particular reading of a
    name. Players are matched against only the players these trades moved and teams against only
    the teams they involved, which is what stops a mistyped name landing on somebody -- or
    somebody's team -- that has never been traded at all.

    Comes back with the trades, the weeks that could not be read, the seasons that were read,
    whichever reading the name settled on (``players`` or ``teams``), and the nearest names to
    it when it settled on neither.
    """
    trades, failed_weeks, seasons = _read_chain_trades(league_id, manager_id, username)
    players, nearest_players = name_search.match(query, _traded_players(trades))
    if players:
        wanted = {player["id"] for player in players}
        return {"trades": [trade for trade in trades if _involves(trade, wanted)],
                "failed_weeks": failed_weeks, "seasons": seasons, "players": players,
                "teams": [], "nearest": []}
    teams, nearest_teams = _match_teams(query, _traded_teams(trades))
    owners = {team["owner_id"] for team in teams if team["owner_id"]}
    return {"trades": [trade for trade in trades
                       if owners and _involves_owner(trade, owners)],
            "failed_weeks": failed_weeks, "seasons": seasons, "players": [], "teams": teams,
            "nearest": [] if teams else (nearest_players + nearest_teams)[:NEAR_MISSES]}


def _pick_matches(pick: dict, spec: dict) -> bool:
    """Whether one pick is the pick a search asked for.

    Season and round is all Sleeper records about a traded pick, so that is all this matches on.
    A slot in a typed pick ("2.02") names a place in a draft, not a pick that was traded: a team
    holding three second-round picks used three places with them, and Sleeper never paired a
    place with a pick. Matching on the round is therefore the honest thing to do, and the page
    says so next to the results.

    A search with no round -- a bare year like "2024" -- means every pick of that draft, so it
    matches each pick by its season alone. A search has to name a season or a round; one that
    names neither is not a pick and matches nothing here.
    """
    season = str(spec.get("season") or "")
    if season and str(pick.get("season") or "") != season:
        return False
    round_number = spec.get("round")
    if round_number is None:
        return bool(season)
    try:
        return int(pick.get("round") or 0) == int(round_number)
    except (TypeError, ValueError):
        return False


def _search_picks(league_id: str, spec: dict, manager_id: str,
                  username: str) -> tuple[list[dict], list[str], list[str]]:
    """Find the trades that moved a pick of a given season and round, or a whole draft.

    A round is searched for in every season the league has played unless one was named, and a
    season with no round looks for every pick of that draft. The picks that matched are marked
    on the trades themselves, so the page can show which pick of the ones that moved is the one
    being asked about.
    """
    trades, failed_weeks, seasons = _read_chain_trades(league_id, manager_id, username)
    found = []
    for trade in trades:
        hit = False
        for side in trade["sides"]:
            for pick in side["picks"]:
                if _pick_matches(pick, spec):
                    pick["matched"] = True
                    hit = True
        if hit:
            found.append(trade)
    return found, failed_weeks, seasons


def get_trades_summary(league_id: str, username: str, manager_id: str = "",
                       week: int = 0, player: str = "", pick: str = "",
                       chain_league_id: str = "", owner: str = "") -> dict:
    """Build the trades page for one league's season, one week of it, or one search's results.

    ``week`` shows only the trades filed against that week, which is what a week tab asks
    for; ``0`` is the whole season. ``player`` searches every season the league has played for
    the trades a typed name was moved in -- a player's name first, and a team's name or owner's
    username when no player answers to it -- matched loosely so a misspelling still finds it,
    and ``pick`` does the same for a draft pick -- "2025 1st", "2.02", or a year on its own
    ("2024") for every pick of that draft. ``chain_league_id`` is the newest season of the
    league, which is where a search is anchored: the page can be open on an older season, and a
    search that read the chain from there would stop at it and miss every trade since. ``owner``
    names the manager whose trades the left column shows, by his Sleeper user id; empty is the
    connected manager, which is the column the page opens on. Both read the whole
    chain, because a career is not a season and a pick's life runs across seasons too, and
    ``week`` then narrows the results to one week of any season. Either way the weeks that
    filed trades are counted and named, so the tabs can show which ones are worth opening.
    """
    spec = pick_query.read(pick) if str(pick or "").strip() else None
    query = str(player or "").strip() or (str(spec["text"]) if spec else "")
    dashboard = get_league_dashboard(league_id)
    league = dashboard["league"]
    season = str(league.get("season") or "")
    teams_by_id = {team["roster_id"]: team for team in dashboard["teams"]}
    own_team = _own_team(dashboard["teams"], manager_id, username)
    my_roster_id = own_team["roster_id"] if own_team else None
    my_owner_id = str((own_team or {}).get("owner_id") or "").strip()
    # Whose dealings the left column shows. Anyone the league has had can be pointed at, by his
    # own user id rather than by a roster, and nothing named means the connected manager -- the
    # column the page opens on.
    focus_owner_id = str(owner or "").strip() or my_owner_id

    matched: list[dict] = []
    matched_teams: list[dict] = []
    nearest: list[str] = []
    searched: list[str] = []
    # A search reads every season, so it is read from the newest one. Everything else on the
    # page -- its trades, its teams, its week tabs -- is the season that was asked for.
    chain_root = str(chain_league_id or "").strip() or league_id
    if spec:
        trades, failed_weeks, searched = _search_picks(chain_root, spec, manager_id, username)
        shown_week = week
    elif query:
        found = _search_chain(chain_root, query, manager_id, username)
        trades, failed_weeks, searched = found["trades"], found["failed_weeks"], found["seasons"]
        matched, matched_teams, nearest = found["players"], found["teams"], found["nearest"]
        shown_week = week
    else:
        rows, failed_weeks = _read_weeks([(season, league_id, week)
                                          for week in range(1, min(_season_weeks(league),
                                                                   MAX_NFL_WEEK) + 1)])
        # _settled_trades hands back the season in order; the page reads it newest first.
        trades = list(reversed(_settled_trades(rows.get(season, []), teams_by_id,
                                               get_player_index(), my_roster_id)))
        for trade in trades:
            trade["season"] = season
        shown_week = week

    # The column's manager is marked on every trade first, so both the week strip's dots and
    # the column itself read off one answer rather than two that could drift apart.
    _mark_focus(trades, focus_owner_id)
    # Whoever the column can be pointed at: every name the page's trades were filed under, so a
    # manager who has since left the league is still one click away, then the league's own seats,
    # so a team that never traded can still be opened. The newest names come first and win.
    owner_options = _owner_options(
        [(entry["owner_id"], entry["label"]) for entry in _traded_teams(trades)]
        + [(team.get("owner_id"), team.get("name")) for team in dashboard["teams"]],
        focus_owner_id, my_owner_id)
    focus_is_me = focus_owner_id == my_owner_id
    focus_label = next((option["label"] for option in owner_options if option["is_focus"]), "")
    # The column's own words. A manager the league cannot name -- an id typed into the address
    # that nothing on the page mentions -- is still shown, just without a name to show.
    focus_heading = ("Your Trades" if focus_is_me else
                     f"{focus_label}'s Trades" if focus_label else "Trades")
    focus_subject = "You" if focus_is_me else (focus_label or "That manager")
    focus_possessive = "your" if focus_is_me else (f"{focus_label}'s" if focus_label else
                                                   "that manager's")
    # "Yours" rather than "your" where it opens a sentence: the possessive stands alone there.
    focus_capital = "Yours" if focus_is_me else focus_possessive
    focus_have = "have" if focus_is_me else "has"

    week_counts: dict[int, int] = {}
    focus_week_counts: dict[int, int] = {}
    for trade in trades:
        week_counts[trade["week"]] = week_counts.get(trade["week"], 0) + 1
        if trade["is_focus"]:
            focus_week_counts[trade["week"]] = focus_week_counts.get(trade["week"], 0) + 1

    shown = [trade for trade in trades if trade["week"] == shown_week] if shown_week else trades
    focus_trades = [trade for trade in shown if trade["is_focus"]]
    matched_names = [player["name"] for player in matched]
    matched_team_labels = [team["label"] for team in matched_teams]
    # The label is the name a team goes by now, so the rest are the names it used to carry.
    # They are worth naming: a trade from a season under an old name is otherwise unexplained.
    matched_team_aliases = [name for team in matched_teams for name in team["names"]
                            if name.casefold()
                            not in {label.casefold() for label in matched_team_labels}]
    # A searched strip drops the playoff names (P1, SB): those name a round inside one
    # season, and a week filtered across seasons is only a week number. It also gains any week
    # the results were filed in that this season's own weeks do not reach -- a search can land
    # on week 18 of some earlier season.
    week_tabs = league["week_tabs"]
    if query:
        numbers = sorted({tab["week"] for tab in week_tabs}
                         | {trade["week"] for trade in trades})
        week_tabs = [{"week": number, "label": f"Wk{number}", "playoff": False}
                     for number in numbers]
    return {
        "league": league,
        "username": username,
        "manager_id": manager_id,
        "team": own_team,
        "season": season,
        "week": shown_week,
        "week_name": (f"Week {shown_week}" if query else week_label(league, shown_week))
                     if shown_week else "",
        "week_tabs": week_tabs,
        "week_counts": week_counts,
        "focus_week_counts": focus_week_counts,
        "week_notes": {trade_week: _week_note(count, focus_week_counts.get(trade_week, 0),
                                              "yours" if focus_is_me else
                                              (f"{focus_label}'s" if focus_label else "theirs"))
                       for trade_week, count in week_counts.items()},
        # Whose trades the left column is showing, and every owner it could be pointed at. The
        # ``owner`` is left empty when it is the connected manager, so the default address stays
        # the plain one and a link back to your own trades needs no id.
        "owner": "" if focus_is_me else focus_owner_id,
        "owner_options": owner_options,
        "focus_is_me": focus_is_me,
        "focus_label": focus_label,
        "focus_heading": focus_heading,
        "focus_subject": focus_subject,
        "focus_possessive": focus_possessive,
        "focus_capital": focus_capital,
        "focus_have": focus_have,
        "focus_trades": focus_trades,
        "focus_total": len(focus_trades),
        "all_trades": shown,
        "total": len(shown),
        "weeks_walked": min(_season_weeks(league), MAX_NFL_WEEK),
        "failed_weeks": failed_weeks,
        # The search: what was typed, what it settled on, and what was read to answer it. A
        # name search says which reading it settled on -- a player's or a team's -- so the page
        # can describe the results as what they are. A pick search carries what it will and will
        # not match, so the page can say that plainly too.
        "query": query,
        "query_kind": "pick" if spec else ("team" if matched_teams else
                                            ("player" if query else "")),
        "pick": spec,
        "pick_label": pick_query.label(spec) if spec else "",
        "pick_describe": pick_query.describe(spec) if spec else "",
        "matched": matched,
        "matched_names": matched_names,
        "matched_teams": matched_teams,
        "matched_team_label": _matched_label(matched_team_labels) if matched_team_labels else "",
        "matched_team_aliases": (_matched_label(matched_team_aliases)
                                 if matched_team_aliases else ""),
        # Whichever reading the search settled on, named: a sentence about "what was found"
        # reads the same whether it found a player or a team.
        "matched_label": (_matched_label(matched_names) if matched_names
                          else (_matched_label(matched_team_labels)
                                if matched_team_labels else "")),
        "nearest": nearest,
        "searched_seasons": searched,
        "searched_label": _seasons_label(searched),
    }
