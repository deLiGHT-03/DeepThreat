"""The league's own facts, as the functions Ask AI is allowed to answer from.

The model is never handed the league; it is handed a handful of questions it can ask about it,
and every one of them is answered here out of the same cached Sleeper responses the pages read.
That is what keeps an answer about a specific league true of that league: the numbers come from
the league's own weekly matchup records and its own traded draft picks, not from anything the
model happens to know about football. Buckle the tools to one league id and the model can only
ever answer about that league.

Two rules shape every tool:

* **Filter here, not there.** A result is already narrowed to what was asked -- one player's
  games, one pick's history -- because a small model reading a wide table of numbers picks the
  wrong row. Each result also carries a ``note`` that says its own point in words, so the figure
  being quoted has been written down once already by the code that knows it for certain.
* **Say what is not known.** A week the league has not scored, a draft that has not happened, a
  player this league never rostered and a pick two teams could each have used are all reported
  as such rather than guessed at.

Everything here is read-only, and every read is cached by :mod:`services.sleeper`, so the second
question about a league costs almost nothing.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from services import draft_picks
from services import name_search
from services import pick_query
from services import trades as trades_service
from services.matchups import _number
from services.player_card import _appearances, _weekly_stats
from services.player_pace import (SEASON_GAMES, SCORED_STATS, _STAT_KEYS, get_player_pace,
                                  pace_figure, pace_rows, stat_order, stat_table)
from services.sleeper import (FIRST_GAME_LOG_SEASON, MAX_NFL_WEEK, SleeperError,
                              get_json, get_league_dashboard, get_league_season_history,
                              get_player_index, get_player_season_games, season_week_count)

#: How many weeks are read at once when a tool walks a season.
FETCH_WORKERS = 8

#: How many rows a list result will name before it says the rest were left out. A tool result
#: is read by a small model, and a long list is where a number gets quoted from the wrong line.
MAX_ROWS = 60

#: How many trades a search will return before it says how many more there were.
MAX_TRADES = 40

#: The figures every position carries whatever it is measured by, in the order a pace table leads
#: with them: points, then points a game. Read from the pace module so the two cannot drift.
SCORED_ABBRS = tuple(abbreviation for abbreviation, _label, _decimals in SCORED_STATS)

#: Every NFL club a game log can name: the code Sleeper writes for it now, its full name, its
#: nickname, and the codes an older season writes for the same club. A club that moved also
#: changed code, so a question about the Raiders has to find the games Sleeper filed under OAK
#: before 2020 and the Chargers' under SD before 2017. The codes are Sleeper's own; the names
#: are what a person types.
NFL_FRANCHISES = (
    ("ARI", "Arizona Cardinals", "Cardinals", ("Cards",)),
    ("ATL", "Atlanta Falcons", "Falcons", ()),
    ("BAL", "Baltimore Ravens", "Ravens", ()),
    ("BUF", "Buffalo Bills", "Bills", ()),
    ("CAR", "Carolina Panthers", "Panthers", ()),
    ("CHI", "Chicago Bears", "Bears", ()),
    ("CIN", "Cincinnati Bengals", "Bengals", ()),
    ("CLE", "Cleveland Browns", "Browns", ()),
    ("DAL", "Dallas Cowboys", "Cowboys", ()),
    ("DEN", "Denver Broncos", "Broncos", ()),
    ("DET", "Detroit Lions", "Lions", ()),
    ("GB", "Green Bay Packers", "Packers", ()),
    ("HOU", "Houston Texans", "Texans", ()),
    ("IND", "Indianapolis Colts", "Colts", ()),
    ("JAX", "Jacksonville Jaguars", "Jaguars", ("JAC", "Jags")),
    ("KC", "Kansas City Chiefs", "Chiefs", ()),
    ("LAC", "Los Angeles Chargers", "Chargers", ("SD", "SDG")),
    ("LAR", "Los Angeles Rams", "Rams", ("STL",)),
    ("LV", "Las Vegas Raiders", "Raiders", ("OAK", "LVR")),
    ("MIA", "Miami Dolphins", "Dolphins", ()),
    ("MIN", "Minnesota Vikings", "Vikings", ()),
    ("NE", "New England Patriots", "Patriots", ()),
    ("NO", "New Orleans Saints", "Saints", ()),
    ("NYG", "New York Giants", "Giants", ()),
    ("NYJ", "New York Jets", "Jets", ()),
    ("PHI", "Philadelphia Eagles", "Eagles", ()),
    ("PIT", "Pittsburgh Steelers", "Steelers", ()),
    ("SEA", "Seattle Seahawks", "Seahawks", ()),
    ("SF", "San Francisco 49ers", "49ers", ("Niners", "SF 49ers")),
    ("TB", "Tampa Bay Buccaneers", "Buccaneers", ("TAM", "Bucs")),
    ("TEN", "Tennessee Titans", "Titans", ()),
    ("WAS", "Washington Commanders", "Commanders", ("WSH", "Redskins", "Football Team")),
)


# --------------------------------------------------------------------------------------
# reading the league
# --------------------------------------------------------------------------------------

def _chain(league_id: str) -> list[dict]:
    """This league's seasons, newest first, as Sleeper's own chain of leagues."""
    try:
        return get_league_season_history(str(league_id))
    except SleeperError:
        return []


def _state() -> dict:
    try:
        return get_json("/state/nfl") or {}
    except SleeperError:
        return {}


def _playing_weeks(league: dict) -> int:
    """How many weeks of this season hold a final score.

    A season being played has scored the weeks behind it and the week in progress has not
    finished, so counting to the week before is what stops a half-finished week being quoted as
    a game. A season that is over has scored all of them.
    """
    from services.history import _season_window
    regular, end, played = _season_window(league, _state())
    return max(0, min(played, end, MAX_NFL_WEEK))


def _season_entries(league_id: str, season: str = "") -> tuple[list[dict], str]:
    """The seasons a question is about: one named season, or every season in the chain.

    Returns the seasons oldest first, so a career reads in the order it happened, and a note
    naming what was actually read.
    """
    chain = _chain(league_id)
    wanted = str(season or "").strip()
    if wanted:
        found = [entry for entry in chain if str(entry["season"]) == wanted]
        if not found:
            return [], (f"This league has no {wanted} season. It has played "
                        f"{_seasons_label(chain)}.")
        return list(reversed(found)), ""
    return list(reversed(chain)), ""


def _seasons_label(chain: list[dict]) -> str:
    seasons = sorted(str(entry["season"]) for entry in chain)
    if not seasons:
        return "no seasons"
    return seasons[0] if len(seasons) == 1 else f"{seasons[0]}-{seasons[-1]}"


def _universe(league_id: str) -> dict[str, dict]:
    """Every player this league has ever held, and the seasons each one was on a team.

    Built from each season's own final rosters, which is the set of players this league can
    meaningfully be asked about. Someone who was never on a team here is not in it, and the
    answer to a question about him says so rather than inventing a line.
    """
    players: dict[str, dict] = {}
    for entry in _chain(league_id):
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        for team in dashboard["teams"]:
            for player in team["players"]:
                record = players.setdefault(player["player_id"], {
                    "id": player["player_id"],
                    "name": player["name"],
                    "position": player["position"],
                    "nfl_team": player["team"],
                    "seasons": {},
                })
                record["seasons"][str(entry["season"])] = team["name"]
    return players


def _resolve_franchise(text: str) -> tuple[tuple[str, ...], list[str]]:
    """Match a typed NFL team against the clubs a game log can name.

    Returns the codes the club is filed under -- the one it wears now and any it wore in an
    older season -- and, when the text names no club or more than one, nothing plus the clubs it
    could have meant. Every word typed has to be one of the club's own words, so "Houston",
    "Texans" and "the Houston Texans" all reach the same club, "CAR" is Carolina rather than
    every club with those letters in its name, and "New York" reaches both clubs it could be.
    """
    typed = {word for word in re.split(r"[^a-z0-9]+", str(text or "").casefold())
             if word and word not in {"the", "nfl"}}
    if not typed:
        return (), []
    hits: list[tuple[tuple[str, ...], str]] = []
    for code, full, nickname, older in NFL_FRANCHISES:
        spelled = set()
        for name in (full, nickname, code, *older):
            spelled.update(word for word in re.split(r"[^a-z0-9]+", str(name).casefold()) if word)
        if typed <= spelled:
            hits.append(((code,) + tuple(str(alias).upper() for alias in older),
                         f"{full} ({code})"))
    if len(hits) == 1:
        return hits[0][0], []
    return (), [label for _, label in hits]


def _league_points(scoring: dict, position: str, line: dict) -> float:
    """One week's official stat line scored the way this league scores, to two decimals.

    Sleeper computes a league's weekly points from its own stat line, and the keys it computes
    them with are the league's scoring settings, so the same sum over the same line reproduces
    the league's own figure -- which is what lets a game the league never scored be quoted on
    the same scale as one it did. The one setting that is not a stat is the bonus a league may
    pay a tight end per reception: Sleeper keeps it as ``bonus_rec_te`` and it is the reception
    count that earns it, so it is added here by hand.
    """
    scoring = scoring or {}
    total = 0.0
    for key, weight in scoring.items():
        if key == "bonus_rec_te" or isinstance(weight, bool):
            continue
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            continue
        value = line.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value) * weight
    if str(position).upper() == "TE":
        try:
            bonus = float(scoring.get("bonus_rec_te") or 0.0)
        except (TypeError, ValueError):
            bonus = 0.0
        receptions = line.get("rec")
        if bonus and isinstance(receptions, (int, float)) and not isinstance(receptions, bool):
            total += float(receptions) * bonus
    return round(total, 2)


def _resolve_player(league_id: str, text: str) -> tuple[list[dict], list[str], dict[str, dict]]:
    """Match a typed name against the players this league has held."""
    universe = _universe(league_id)
    candidates = sorted(universe.values(), key=lambda player: player["name"].casefold())
    matched, nearest = name_search.match(str(text or ""), candidates)
    return matched, nearest, universe


def _resolve_team(league_id: str, text: str,
                  reader: dict | None = None) -> tuple[int | None, str, list[str]]:
    """Match a typed team name to the roster id it holds, and name the teams when it cannot.

    A team name is matched against the league's team names, its owners' display names and their
    usernames, because a manager is as likely to be named by his handle as by his team. The
    roster id is asked of every season the league has played and has to agree to be used: a
    name that is two different teams in two different seasons is not a safe thing to trace.
    """
    want = str(text or "").strip().casefold()
    if not want:
        return None, "", []
    # The reader asking about his own team says "me", "my team" or "mine", and there is only
    # one honest way to read that: the team the connected manager holds.
    if want in {"me", "my team", "mine", "my", "i", "myself", "my squad"}:
        roster_id, team_name = _reader_roster_id(league_id, reader)
        return (roster_id, team_name, []) if roster_id is not None else (None, "", [])
    ids: list[int] = []
    names: list[str] = []
    for entry in _chain(league_id):
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        for team in dashboard["teams"]:
            names.append(team["name"])
            haystack = {str(team["name"]).casefold(), str(team.get("owner", "")).casefold(),
                        str(team.get("username", "")).casefold()}
            if want in haystack or any(want in value for value in haystack if value):
                ids.append(team["roster_id"])
    names = sorted(set(names))
    if not ids:
        return None, "", names
    counts = Counter(ids)
    roster_id, _ = counts.most_common(1)[0]
    if len(counts) > 1:
        return None, "", names
    return roster_id, next((name for name in names if name.casefold() == want), str(text)), names


def _read_scores(league_id: str, weeks: int) -> dict[int, dict[str, tuple[int, float]]]:
    """Read this league's weekly records: ``week -> player id -> (roster id, points)``.

    These are the league's own scored points, which is what makes a number here agree with the
    Matchups page and with a player's card.
    """
    def fetch(week: int) -> tuple[int, list]:
        try:
            rows = get_json(f"/league/{league_id}/matchups/{week}") or []
        except SleeperError:
            return week, []
        return week, rows if isinstance(rows, list) else []

    if weeks < 1:
        return {}
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        results = list(executor.map(fetch, range(1, weeks + 1)))
    scored: dict[int, dict[str, tuple[int, float]]] = {}
    for week, rows in results:
        table: dict[str, tuple[int, float]] = {}
        for row in rows:
            roster_id = row.get("roster_id")
            for player_id, value in (row.get("players_points") or {}).items():
                points = _number(value)
                if points is not None:
                    table[str(player_id)] = (roster_id, points)
        scored[week] = table
    return scored


# Reading what became of a traded pick, and a season's whole draft board, lives in
# :mod:`services.draft_picks` -- the Trades page opens a pick with the same code Ask AI reads it
# with, so a pick the page explains and a pick the answer explains can never disagree.


# --------------------------------------------------------------------------------------
# the tools
# --------------------------------------------------------------------------------------

def league_overview(league_id: str, reader: dict | None = None) -> dict:
    """This league's shape: its seasons, its teams, its rules, and the reader's own team."""
    chain = _chain(league_id)
    if not chain:
        return {"error": "This league's seasons could not be read from Sleeper."}
    newest = chain[0]
    try:
        dashboard = get_league_dashboard(newest["league_id"])
    except SleeperError:
        return {"error": "This league could not be read from Sleeper."}
    league = dashboard["league"]
    scoring = dashboard.get("scoring") or {}
    positions = [str(slot) for slot in (dashboard.get("roster_positions") or [])]
    notable = {}
    for key, label in (("rec", "points per reception"), ("pass_td", "points per passing TD"),
                       ("rush_td", "points per rushing TD"), ("bonus_rec_te", "TE reception bonus"),
                       ("pass_int", "points per interception thrown")):
        if scoring.get(key) not in (None, 0, 0.0):
            notable[label] = scoring[key]
    reception = scoring.get("rec")
    if reception in (1, 1.0):
        format_name = "full PPR"
    elif reception in (0.5,):
        format_name = "half PPR"
    elif reception in (0, 0.0):
        format_name = "standard (no points per reception)"
    else:
        format_name = "custom reception scoring"
    teams = [{"team": team["name"], "owner": team.get("owner", ""),
              "username": team.get("username", ""), "record": team.get("record", "")}
             for team in dashboard["teams"]]
    weeks = league.get("regular_season_weeks")
    mine = _reader_rosters(league_id, reader)
    this_season = str(league.get("season") or "")
    return {
        "your_team": (mine.get(this_season) or {}).get("team", ""),
        "your_team_by_season": {season: holder["team"] for season, holder in sorted(mine.items())},
        "league_name": str(league.get("name") or ""),
        "seasons_played": sorted(str(entry["season"]) for entry in chain),
        "current_season": str(league.get("season") or ""),
        "teams": teams,
        "team_count": len(teams),
        "scoring_format": format_name,
        "scoring_notes": notable,
        "starting_lineup": positions,
        "regular_season_weeks": weeks,
        "playoff_rounds": _playoff_rounds(league),
        "note": (f"{len(teams)}-team league, {format_name}, seasons "
                 f"{_seasons_label(chain)}. "
                 f"Weeks are the league's own scoring weeks; the numbers every tool returns "
                 f"come from this league's weekly matchup records."),
    }


def _playoff_rounds(league: dict) -> int:
    settings = league.get("settings") or {}
    try:
        start = int(settings.get("playoff_week_start") or 0)
    except (TypeError, ValueError):
        start = 0
    try:
        last = int(league.get("season_end_week") or league.get("last_week") or 0)
    except (TypeError, ValueError):
        last = 0
    if start and last:
        return max(0, last - start + 1)
    return 0


def find_players(league_id: str, name: str) -> dict:
    """Look a player up in this league by a loosely typed name."""
    matched, nearest, universe = _resolve_player(league_id, name)
    if not matched:
        return {
            "matches": [],
            "nearest_names": nearest,
            "note": (f"No player in this league's history matches \"{name}\"."
                     + (f" Closest names: {', '.join(nearest)}." if nearest else "")),
        }
    rows = []
    for player in matched[:8]:
        seasons = player.get("seasons") or {}
        latest = max(seasons) if seasons else ""
        rows.append({
            "player_id": player["id"],
            "name": player["name"],
            "position": player["position"],
            "nfl_team": player["nfl_team"],
            "seasons_in_league": sorted(seasons),
            "last_team": seasons.get(latest, ""),
        })
    return {
        "matches": rows,
        "nearest_names": [],
        "note": ("Exactly one player matches." if len(rows) == 1
                 else f"{len(rows)} players match \"{name}\"; the caller should say which."),
    }


def player_game_log(league_id: str, player: str, season: str = "", above: float | None = None,
                    below: float | None = None) -> dict:
    """One player's scored games in this league, newest first, optionally over or under a total.

    Only weeks the league has actually scored are counted, and a week he was on a roster but
    never took the field -- a bye, or a week he was inactive -- is not a game, because the
    league records a zero for him either way. Points are the league's own scoring for its own
    lineup rules, so the number matches his card on the pages.
    """
    matched, nearest, universe = _resolve_player(league_id, player)
    if not matched:
        return {"games": [], "note": (f"No player in this league matches \"{player}\"."
                                      + (f" Closest names: {', '.join(nearest)}." if nearest else ""))}
    if len(matched) > 1:
        return {
            "games": [],
            "matches": [{"name": p["name"], "position": p["position"]} for p in matched[:8]],
            "note": f"\"{player}\" matches more than one player; ask again with the full name.",
        }
    who = matched[0]
    entries, problem = _season_entries(league_id, season)
    if problem:
        return {"games": [], "note": problem}
    state = _state()
    games: list[dict] = []
    seasons_read: list[str] = []
    weeks_read = 0
    for entry in entries:
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        league = dashboard["league"]
        weeks = _playing_weeks(league)
        if weeks < 1:
            continue
        seasons_read.append(str(entry["season"]))
        weeks_read += weeks
        scored = _read_scores(entry["league_id"], weeks)
        on_field = _appearances(str(entry["season"]), list(range(1, weeks + 1)))
        teams = {team["roster_id"]: team["name"] for team in dashboard["teams"]}
        for week in sorted(scored):
            recorded = scored[week].get(who["id"])
            if not recorded:
                continue
            roster_id, points = recorded
            played = on_field.get(week)
            if played is not None and who["id"] not in played:
                continue  # on a roster, but he was not on the field that week
            games.append({
                "season": str(entry["season"]),
                "week": week,
                "points": round(points, 2),
                "fantasy_team": teams.get(roster_id, ""),
            })
    games.sort(key=lambda game: (game["season"], game["week"]), reverse=True)
    filtered = [game for game in games
                if (above is None or game["points"] > float(above))
                and (below is None or game["points"] < float(below))]
    applied = []
    if above is not None:
        applied.append(f"more than {above} points")
    if below is not None:
        applied.append(f"less than {below} points")
    if not games:
        return {"games": [], "note": (f"{who['name']} has no scored game in "
                                      f"{_seasons_label(_chain(league_id))} for this league.")}
    shot = filtered if applied else games
    listed = shot[:MAX_ROWS]
    rows = [{**game} for game in listed]
    lines = [f"{game['season']} Wk{game['week']}: {game['points']} pts ({game['fantasy_team']})"
             for game in listed]
    if applied and listed:
        note = (f"{who['name']} ({who['position']}, {who['nfl_team']}) -- the most recent game "
                f"{' and '.join(applied)}, newest first: {listed[0]['season']} "
                f"Wk{listed[0]['week']} with {listed[0]['points']} points. "
                f"{len(filtered)} of his {len(games)} scored games match.")
    elif applied:
        note = (f"None of {who['name']}'s {len(games)} scored games "
                f"({' and '.join(applied)}) match.")
    else:
        note = (f"{who['name']} ({who['position']}, {who['nfl_team']}): "
                f"{len(games)} scored games, newest first.")
    if len(filtered if applied else games) > len(listed):
        note += f" The {len(listed)} newest are listed."
    if weeks_read:
        note += (" Only weeks the league has scored are counted, so the current week is not "
                 "included until it is final.")
    return {
        "player": {"name": who["name"], "position": who["position"], "nfl_team": who["nfl_team"]},
        "seasons_read": seasons_read,
        "games": rows,
        "listed_lines": lines,
        "match_count": len(filtered) if applied else len(games),
        "note": note,
    }


def player_vs_opponent(league_id: str, player: str, opponent: str = "",
                       season: str = "") -> dict:
    """How one player has scored against one NFL team, over every season Sleeper has for him.

    The league's own weekly records only reach the weeks it scored, and they never say which NFL
    team a player faced, so this reads Sleeper's own game log for the player instead. That log
    names the team he played for and the opponent in each game, back before this league's first
    season, which is what makes a question about one NFL opponent answerable at all. Every game
    is then scored with this league's own scoring settings, so a figure here is on the same
    scale as the weeks this league scored him -- the same numbers, not a different format.

    With no opponent named, the same games are added up per NFL team: how he has done against
    everybody he has faced. Both read regular-season games only, because those are the weeks
    this league plays.
    """
    matched, nearest, _ = _resolve_player(league_id, player)
    if not matched:
        return {"games": [], "note": (f"No player in this league matches \"{player}\"."
                                      + (f" Closest names: {', '.join(nearest)}." if nearest else ""))}
    if len(matched) > 1:
        return {
            "games": [],
            "matches": [{"name": p["name"], "position": p["position"]} for p in matched[:8]],
            "note": f"\"{player}\" matches more than one player; ask again with the full name.",
        }
    who = matched[0]
    codes, candidates = _resolve_franchise(opponent)
    wanted = str(opponent or "").strip()
    if wanted and not codes:
        note = (f"\"{opponent}\" is not an NFL team I can place."
                + (f" Did you mean {', '.join(candidates)}?" if candidates else ""))
        return {"games": [], "note": note}

    seasons, problem = _game_log_seasons(league_id, season)
    if problem:
        return {"games": [], "note": problem}
    failed: list[str] = []

    def fetch(year: str) -> tuple[str, dict | None]:
        try:
            return year, get_player_season_games(who["id"], year)
        except SleeperError:
            return year, None

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        results = dict(executor.map(fetch, seasons))
    games: list[dict] = []
    for year in seasons:
        log = results.get(year)
        if log is None:
            failed.append(year)
            continue
        for week, game in log.items():
            stats = game.get("stats")
            if not isinstance(stats, dict) or not stats:
                continue  # A week he did not play: a bye, an injury, or a game still to come.
            if not (_number(stats.get("gp")) or 0.0) > 0:
                continue
            faced = str(game.get("opponent") or "").strip().upper()
            if codes and faced not in codes:
                continue
            played = str(game.get("team") or "").strip().upper()
            try:
                week_number = int(week)
            except (TypeError, ValueError):
                continue
            games.append({
                "season": str(game.get("season") or year),
                "week": week_number,
                "nfl_team": played,
                "opponent": faced,
                "home": not bool(game.get("is_away_team")),
                "points": _league_points(_scoring(league_id), who["position"], stats),
            })
    games.sort(key=lambda game: (game["season"], game["week"]), reverse=True)

    if not games:
        where = f" against the {_franchise_label(codes)}" if codes else ""
        wanted_season = str(season).strip()
        if wanted_season:
            window = f" in {wanted_season}"
        elif seasons:
            window = f" between {seasons[0]} and {seasons[-1]}"
        else:
            window = ""
        note = (f"{who['name']} ({who['position']}) has no recorded game{where}{window}. "
                "Only games Sleeper recorded him playing are counted.")
        if failed:
            note += f" Seasons {', '.join(failed)} could not be read."
        return {"games": [], "opponents": [], "note": note}

    # The span the games themselves cover, not the span read: seasons a player was not in the
    # NFL yet would otherwise be quoted as part of his record.
    read_label = _season_span(sorted({game["season"] for game in games}))
    basis = ("Points are this league's own scoring, applied to Sleeper's official stat line for "
             "each game, so they are on the same scale as the weeks this league scored him.")
    if codes:
        listing = games[:MAX_ROWS]
        points = [game["points"] for game in games]
        best = max(games, key=lambda game: (game["points"], game["season"], game["week"]))
        worst = min(games, key=lambda game: (game["points"], game["season"], game["week"]))
        teams = sorted({game["nfl_team"] for game in games if game["nfl_team"]})
        label = _franchise_label(codes)
        note = (f"{who['name']} ({who['position']}, {'/'.join(teams) or 'NFL'}) has played "
                f"{len(games)} game{'s' if len(games) != 1 else ''} against the {label}, "
                f"{read_label}: {round(sum(points) / len(points), 2)} points a game, from "
                f"{min(points)} ({worst['season']} Wk{worst['week']}) to {max(points)} "
                f"({best['season']} Wk{best['week']}). {basis} NFL regular-season games only.")
        if len(listing) < len(games):
            note += f" The {len(listing)} newest are listed; the average is over all {len(games)}."
        if failed:
            note += f" Seasons {', '.join(failed)} could not be read."
        return {
            "player": {"name": who["name"], "position": who["position"]},
            "opponent": {"codes": list(codes), "name": label},
            "seasons_read": seasons,
            "games": listing,
            "listed_lines": [_game_line(game) for game in listing],
            "match_count": len(games),
            "average_points": round(sum(points) / len(points), 2),
            "best_game": best,
            "worst_game": worst,
            "note": note,
        }

    # No opponent named: the same games, totalled per NFL team.
    totals: dict[str, dict] = {}
    for game in games:
        row = totals.setdefault(game["opponent"], {"opponent": game["opponent"], "games": 0,
                                                   "points": 0.0})
        row["games"] += 1
        row["points"] += game["points"]
    opponents = []
    for row in totals.values():
        codes_for = _resolve_franchise(row["opponent"])[0]
        opponents.append({
            "opponent": row["opponent"],
            "opponent_name": _franchise_label(codes_for) or row["opponent"],
            "games": row["games"],
            "average_points": round(row["points"] / row["games"], 2),
        })
    opponents.sort(key=lambda row: (row["average_points"], row["games"]), reverse=True)
    listed = opponents[:MAX_ROWS]
    best, worst = opponents[0], opponents[-1]
    note = (f"{who['name']} ({who['position']}) has faced {len(opponents)} NFL teams, "
            f"{read_label}, across {len(games)} games: his best average is "
            f"{best['average_points']} against the {best['opponent_name']} in {best['games']} "
            f"game{'s' if best['games'] != 1 else ''}, his lowest {worst['average_points']} "
            f"against the {worst['opponent_name']} in {worst['games']}. "
            f"{basis} NFL regular-season games only.")
    if len(listed) < len(opponents):
        note += f" The {len(listed)} highest averages are listed."
    if failed:
        note += f" Seasons {', '.join(failed)} could not be read."
    return {
        "player": {"name": who["name"], "position": who["position"]},
        "seasons_read": seasons,
        "opponents": listed,
        "listed_lines": [f"{row['opponent']}: {row['games']} games, "
                         f"{row['average_points']} points a game" for row in listed],
        "match_count": len(games),
        "note": note,
    }


def player_pace(league_id: str, player: str, season: str = "", player_id: str = "") -> dict:
    """How one player is pacing over a full season, in the stats his position is judged by.

    This is the projection a player card's own Generate Pace button draws, put into words
    instead: each of the season's figures averaged over the games he has actually played and
    stretched over seventeen of them. What counts as a game is the card's rule as well -- a week
    the league scored and that Sleeper recorded him taking the field in -- so a bye, a week he
    was inactive and a week still to come are none of them a game, and a player who has missed
    time is not quietly averaged down by weeks he never played.

    The points are this league's own scoring rather than Sleeper's default, which is what lets a
    projection be read beside the weeks it was earned in. A pace the season is too young to have
    -- a player with no game yet -- is said in the note rather than returned as a failure.

    ``player_id`` names the player outright instead of by his name, which is how a card asks
    about the player it is already showing: this league may well hold two players who read the
    same, and which of them the card belongs to is not something a lookup should have to guess.
    """
    # A card knows its own player's id, so his name is never resolved -- and never mistaken for
    # somebody else's.
    given_id = str(player_id or "").strip()
    if given_id:
        record = _universe(league_id).get(given_id)
        matched, nearest = ([record] if record else []), []
    else:
        matched, nearest, _ = _resolve_player(league_id, player)
    if not matched:
        return {"rows": [], "note": (
            f"No player in this league matches \"{player or given_id}\"."
            + (f" Closest names: {', '.join(nearest)}." if nearest else ""))}
    if len(matched) > 1:
        return {
            "rows": [],
            "matches": [{"name": p["name"], "position": p["position"]} for p in matched[:8]],
            "note": f"\"{player}\" matches more than one player; ask again with the full name.",
        }
    who = matched[0]
    entries, problem = _season_entries(league_id, season)
    if problem:
        return {"rows": [], "note": problem}
    # Those arrive oldest first, so the newest season -- the one a question with no season in it
    # means -- is the last of them.
    entry = entries[-1]
    played = str(entry["season"])
    try:
        pace = get_player_pace(str(entry["league_id"]), who["id"])
    except SleeperError as exc:
        # Nothing to project, or a position this app has no pace table for. Either way it is an
        # answer about this player, not a lookup that broke.
        return {
            "player": {"name": who["name"], "position": who["position"]},
            "season": played,
            "rows": [],
            "note": f"{who['name']} ({who['position']}) in {played}: {exc}",
        }

    rows = [{
        "stat": row["label"],
        "abbr": row["abbr"],
        "pace": row["pace"],
        "figure": pace_figure(row["pace"], row["decimals"]),
        "is_rate": bool(row.get("rate")),
    } for row in pace["rows"]]
    games = pace["games"]
    over = pace["season_games"]
    projected = ", ".join(f"{row['figure']} {row['abbr']}" for row in rows if not row["is_rate"])
    rate = next((row for row in rows if row["is_rate"]), None)
    note = (f"{who['name']} ({who['position']}, {who['nfl_team']}) is on pace over {over} games, "
            f"from the {games} game{'s' if games != 1 else ''} he has played in {played}, for "
            f"{projected}.")
    if rate:
        note += (f" His {rate['stat'].lower()} is {rate['figure']}, which is a rate rather than a "
                 f"projection of one.")
    note += (" Points are this league's own scoring. Only weeks this league scored and that "
             "Sleeper recorded him on the field for are counted, so byes and weeks he sat out "
             "are not games.")
    return {
        "player": {"id": who["id"], "name": who["name"], "position": who["position"],
                   "nfl_team": who["nfl_team"]},
        "season": played,
        "games_played": games,
        "season_games": over,
        "rows": rows,
        "listed_lines": [f"{row['stat']} ({row['abbr']}) | {row['figure']}" for row in rows],
        "note": note,
    }


def compare_paces(league_id: str, player: str, other: str, season: str = "",
                  player_id: str = "") -> dict:
    """Two players' 17-game paces beside each other, for weighing one against the other.

    This is the trade question: not how each of them has scored, but what each is on course for,
    and on which of the stats that matter they are ahead. Both sides are projected by
    :func:`player_pace` -- the same call behind the button on a player's card -- so the two
    columns are the two cards, and a comparison cannot disagree with either of them.

    Two players of different positions do not carry the same stats, and a table of two columns
    needs rows that mean something under both headings: the stats both positions carry (points,
    points a game and total touchdowns, always) are compared, and the stats only one of them has
    are reported beside the comparison rather than left out of it. Every figure already exists in
    both halves, so nothing here is derived, averaged or combined.
    """
    sides = [player_pace(league_id, player, season, player_id),
             player_pace(league_id, other, season)]
    projected = [side for side in sides if side.get("rows")]
    if len(projected) < len(sides):
        # One side has nothing to project, or a name that matched nothing or more than one
        # player. Either way the honest answer is the sentence saying which side it was.
        trouble = [str(side.get("note") or "no pace") for side in sides if not side.get("rows")]
        return {
            "players": [side.get("player") or {} for side in sides],
            "compared": [],
            "note": ("No comparison to draw: " + " ".join(trouble)
                     + " Ask for two players this league has scored this season."),
        }
    if sides[0]["player"]["id"] == sides[1]["player"]["id"]:
        return {
            "players": [sides[0]["player"]],
            "compared": [],
            "note": (f"{sides[0]['player']['name']} is the same player on both sides, so there"
                     " is nothing to compare him with."),
        }

    names = [side["player"]["name"] for side in sides]
    looked_up = [{row["abbr"]: row for row in side["rows"]} for side in sides]
    shared = [row["abbr"] for row in sides[0]["rows"] if row["abbr"] in looked_up[1]]
    compared = []
    for abbr in shared:
        here, there = looked_up[0][abbr], looked_up[1][abbr]
        tie = here["pace"] == there["pace"]
        compared.append({
            "stat": here["stat"],
            "abbr": abbr,
            "values": [here["figure"], there["figure"]],
            "leads": "" if tie else (names[0] if here["pace"] > there["pace"] else names[1]),
            "is_rate": here["is_rate"],
        })
    alone = [[row for row in side["rows"] if row["abbr"] not in shared] for side in sides]

    played = sides[0]["season"]
    over = sides[0]["season_games"]
    games = [side["games_played"] for side in sides]
    where = [f"{side['player']['position']}, {side['player']['nfl_team']}" for side in sides]
    note = (f"{names[0]} ({where[0]}) against {names[1]} ({where[1]}), both in {played}: each "
            f"projected over {over} games from the {games[0]} and {games[1]} games he has "
            f"played. {names[0]}'s figure is given first, {names[1]}'s second. They share "
            f"{len(compared)} stat{'s' if len(compared) != 1 else ''} -- "
            + "; ".join(f"{row['abbr']} {row['values'][0]} to {row['values'][1]}"
                        for row in compared) + ".")
    for side, rows in zip(names, alone):
        if rows:
            note += (f" Only on {side}'s side: "
                     + ", ".join(f"{row['abbr']} {row['figure']}" for row in rows) + ".")
    note += (" Points are this league's own scoring, and a pace counts only the weeks this "
             "league scored and that Sleeper recorded the player on the field for.")
    return {
        "players": [side["player"] for side in sides],
        "season": played,
        "season_games": over,
        "games_played": games,
        "compared": compared,
        "only": [rows for rows in alone],
        "listed_lines": [f"{row['stat']} ({row['abbr']}) | {row['values'][0]} | "
                         f"{row['values'][1]}" for row in compared],
        "note": note,
    }


def _scoring(league_id: str) -> dict:
    """This league's own scoring settings, from its newest season."""
    chain = _chain(league_id)
    if not chain:
        return {}
    try:
        return get_league_dashboard(chain[0]["league_id"]).get("scoring") or {}
    except SleeperError:
        return {}


def _game_log_seasons(league_id: str, season: str) -> tuple[list[str], str]:
    """Which NFL seasons a game log is read for, oldest first, and why not when it cannot be.

    One named season, or every season from the oldest Sleeper has a log for through the season
    in progress: a career is read whole, because the question is about the player rather than
    about this league's seasons. A season Sleeper has nothing for comes back empty and drops
    out of the result on its own.
    """
    wanted = str(season or "").strip()
    if wanted:
        if not wanted.isdigit():
            return [], f"\"{season}\" is not a season year."
        return [wanted], ""
    newest = str((_state() or {}).get("season") or "").strip()
    chain = _chain(league_id)
    for entry in chain:
        if str(entry["season"]).isdigit() and int(entry["season"]) > int(newest or 0):
            newest = str(entry["season"])
    if not newest.isdigit():
        return [], "This league's seasons could not be read from Sleeper."
    first = min(FIRST_GAME_LOG_SEASON, int(newest))
    return [str(year) for year in range(first, int(newest) + 1)], ""


def _franchise_label(codes) -> str:
    """Name a club the table's way -- "Houston Texans (HOU)" -- whichever code filed the games.

    A club that moved wears a different code in an older season, and the games keep it, so the
    label is read from whichever of the club's codes the caller passed rather than from the
    games themselves.
    """
    wanted = {str(code).upper().strip() for code in codes or () if str(code).strip()}
    for code, full, _nickname, older in NFL_FRANCHISES:
        aliases = {str(alias).upper() for alias in older}
        if code in wanted or aliases & wanted:
            return f"{full} ({code})"
    return sorted(wanted)[0] if wanted else ""


def _season_span(seasons) -> str:
    """The seasons a set of games falls in, as a span: "2014-2026", or "2024" for one."""
    years = sorted({int(year) for year in seasons if str(year).isdigit()})
    if not years:
        return "no seasons"
    return str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"


def _game_line(game: dict) -> str:
    """One game as a line, so the figure that is quoted is one the code wrote down."""
    where = "home" if game.get("home") else "away"
    return (f"{game['season']} Wk{game['week']}: {game['points']} pts vs {game['opponent']} "
            f"({game.get('nfl_team') or 'NFL'}, {where})")


def find_trades(league_id: str, player: str = "", team: str = "", season: str = "",
                pick: str = "", reader: dict | None = None) -> dict:
    """Trades in this league's history, narrowed by a player, a team, a season or a draft pick.

    ``pick`` takes a pick as a manager says it -- "2025 1st", "2.02" -- and finds the trades
    that moved one. A pick is only ever recorded by its year, round and original team, so a
    search matches on the year and round and the tool says which of the picks that moved is the
    one being asked about.
    """
    pick_spec = pick_query.read(pick) if str(pick or "").strip() else None
    entries, problem = _season_entries(league_id, season)
    if problem:
        return {"trades": [], "note": problem}
    player_ids: set[str] = set()
    if player:
        matched, nearest, _ = _resolve_player(league_id, player)
        if not matched:
            return {"trades": [], "note": (f"No player in this league matches \"{player}\"."
                                           + (f" Closest: {', '.join(nearest)}." if nearest else ""))}
        if len(matched) > 1:
            return {"trades": [], "matches": [p["name"] for p in matched[:8]],
                    "note": f"\"{player}\" matches more than one player; use the full name."}
        player_ids = {matched[0]["id"]}
    roster_id, team_name, all_names = (None, "", [])
    if team:
        roster_id, team_name, all_names = _resolve_team(league_id, team, reader)
        if roster_id is None:
            return {"trades": [], "note": (f"No single team in this league matches \"{team}\"."
                                           + (f" Teams: {', '.join(all_names)}." if all_names else ""))}

    prepared = []
    jobs: list[tuple[str, str, int]] = []
    for entry in entries:
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        league = dashboard["league"]
        weeks = min(trades_service._season_weeks(league), MAX_NFL_WEEK)
        prepared.append((str(entry["season"]), {team["roster_id"]: team for team in dashboard["teams"]}))
        jobs += [(str(entry["season"]), entry["league_id"], week) for week in range(1, weeks + 1)]
    if not jobs:
        return {"trades": [], "note": "No weeks of this league could be read."}
    rows_by_season, failed = trades_service._read_weeks(jobs)
    player_index = get_player_index()
    trades: list[dict] = []
    for season_name, teams in prepared:
        for trade in trades_service._settled_trades(rows_by_season.get(season_name, []), teams,
                                                    player_index, None):
            trade["season"] = season_name
            trades.append(trade)
    trades.sort(key=lambda trade: (trade["season"], trade["week"]), reverse=True)

    def involves(trade: dict) -> bool:
        if player_ids and not any(item["id"] in player_ids
                                  for side in trade["sides"] for item in side["players"]):
            return False
        if roster_id is not None and not any(side["roster_id"] == roster_id for side in trade["sides"]):
            return False
        if pick_spec is not None:
            hits = [item for side in trade["sides"] for item in side["picks"]
                    if trades_service._pick_matches(item, pick_spec)]
            if not hits:
                return False
            for item in hits:
                item["matched"] = True
        return True

    found = [trade for trade in trades if involves(trade)]
    listed = found[:MAX_TRADES]
    shaped = []
    for trade in listed:
        shaped.append({
            "season": trade["season"],
            "week": trade["week"],
            "trade_id": trade["transaction_id"],
            "sides": [{
                "team": side["team"]["name"],
                "received_players": [f"{item['name']} ({item['position']})" for item in side["players"]],
                "received_picks": [f"{item['full']}" + (f" (originally {item['via']})"
                                                        if item["has_via"] else "")
                                   + (" [this is the pick searched for]"
                                      if item.get("matched") else "")
                                   for item in side["picks"]],
            } for side in trade["sides"]],
        })
    what = []
    if player:
        what.append(f"involving {player}")
    if team:
        what.append(f"involving {team_name or team}")
    if pick_spec is not None:
        what.append(f"that moved a {pick_query.label(pick_spec)}")
    if season:
        what.append(f"in the {season} season")
    note = (f"{len(found)} trade{'s' if len(found) != 1 else ''} "
            f"{' '.join(what) if what else 'in this league'}.")
    if len(found) > len(listed):
        note += f" The {len(listed)} most recent are listed."
    if failed:
        note += f" Weeks that could not be read: {', '.join(failed)}."
    return {"trades": shaped, "trade_count": len(found), "note": note}


def trace_draft_pick(league_id: str, season: str, round: int, team: str = "",
                     reader: dict | None = None) -> dict:
    """Follow one traded draft pick from the team that started with it to the player it became.

    ``team`` is the team the pick originally belonged to -- the manager who says "my second" --
    and the trace itself is :mod:`services.draft_picks`, which is the same code the Trades page
    opens a pick with. That is the point of it living there: a pick explained here and the same
    pick clicked on the page cannot come out differently.
    """
    try:
        round_number = int(round)
    except (TypeError, ValueError):
        return {"note": f"\"{round}\" is not a draft round."}
    original = None
    if team:
        original, _team_name, all_names = _resolve_team(league_id, team, reader)
        if original is None:
            return {"note": (f"No single team in this league matches \"{team}\"."
                             + (f" Teams: {', '.join(all_names)}." if all_names else ""))}
    return draft_picks.trace(league_id, str(season), round_number, original)


def team_roster(league_id: str, season: str, team: str, reader: dict | None = None) -> dict:
    """One team's roster in one season, with the starters marked."""
    roster_id, team_name, all_names = _resolve_team(league_id, team, reader)
    if roster_id is None:
        return {"note": (f"No single team in this league matches \"{team}\"."
                         + (f" Teams: {', '.join(all_names)}." if all_names else ""))}
    entry = next((item for item in _chain(league_id) if str(item["season"]) == str(season)), None)
    if not entry:
        return {"note": f"This league has not played a {season} season."}
    try:
        dashboard = get_league_dashboard(entry["league_id"])
    except SleeperError:
        return {"note": f"The {season} season could not be read."}
    found = next((item for item in dashboard["teams"] if item["roster_id"] == roster_id), None)
    if not found:
        return {"note": f"{team_name} has no roster in {season}."}
    starters = set(found.get("starters") or [])
    players = [{"name": player["name"], "position": player["position"],
                "nfl_team": player["team"],
                "starter": player["player_id"] in starters}
               for player in found["players"]]
    return {
        "team": found["name"],
        "owner": found.get("owner", ""),
        "season": str(season),
        "record": found.get("record", ""),
        "players": players,
        "note": (f"{found['name']} ({found.get('owner', '')}) in {season}, {found.get('record', '')}: "
                 f"{len(players)} players held, starters marked."),
    }


def season_standings(league_id: str, season: str = "") -> dict:
    """Where every team finished in one season, and who won it."""
    entries, problem = _season_entries(league_id, season)
    if problem:
        return {"standings": [], "note": problem}
    rows = []
    for entry in entries:
        try:
            rosters = get_json(f"/league/{entry['league_id']}/rosters") or []
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        names = {team["roster_id"]: team["name"] for team in dashboard["teams"]}
        for roster in rosters:
            settings = roster.get("settings") or {}
            scored = _number(settings.get("fpts")) or 0.0
            scored_decimal = _number(settings.get("fpts_decimal")) or 0.0
            conceded = _number(settings.get("fpts_against")) or 0.0
            conceded_decimal = _number(settings.get("fpts_against_decimal")) or 0.0
            # Sleeper keeps a team's season points in two fields, the whole number and the
            # hundredths, and they are summed here rather than read as one: the whole number on
            # its own silently drops the fraction (2385 and 24 is 2385.24, not 2385). These are
            # the REGULAR season's totals, which is what Sleeper records in a roster's settings
            # -- a team's playoff weeks are not in them -- so every row says so.
            rows.append({
                "season": str(entry["season"]),
                "team": names.get(roster.get("roster_id"), f"roster {roster.get('roster_id')}"),
                "wins": settings.get("wins", 0),
                "losses": settings.get("losses", 0),
                "ties": settings.get("ties", 0),
                "regular_season_points_for": round(scored + scored_decimal / 100, 2),
                "regular_season_points_against": round(conceded + conceded_decimal / 100, 2),
            })
        champion, runner_up = _final(entry["league_id"], names)
        for row in rows:
            if row["season"] == str(entry["season"]):
                row["champion"] = bool(champion and row["team"] == champion)
                row["runner_up"] = bool(runner_up and row["team"] == runner_up)
    rows.sort(key=lambda row: (row["season"], -(row["wins"] or 0),
                               -(row["regular_season_points_for"] or 0)), reverse=True)
    listed = rows[:MAX_ROWS]
    champion_names = [row["team"] for row in rows if row.get("champion")]
    seasons_seen = len(set(row["season"] for row in rows))
    note = (f"{seasons_seen} season(s) of standings. Wins, losses and points are the REGULAR "
            f"SEASON's, as Sleeper records them in each team's settings; a team's playoff weeks "
            f"are not counted in them. The champion is the winner of the playoff final, which "
            f"is a different thing from the best regular-season record."
            + (f" Champions: {', '.join(champion_names)}." if champion_names else ""))
    return {"standings": listed, "note": note}


def _final(league_id: str, names: dict) -> tuple[str, str]:
    """One season's champion and runner-up, from the match Sleeper settles first place with.

    Sleeper keys a bracket match in single letters -- ``p`` is the placement and ``w``/``l`` the
    winning and losing roster ids -- and a season still being played has its final named but not
    yet decided, which comes back as nobody at all.
    """
    try:
        bracket = get_json(f"/league/{league_id}/winners_bracket") or []
    except SleeperError:
        return "", ""
    if not isinstance(bracket, list):
        return "", ""
    for match in bracket:
        if not isinstance(match, dict) or match.get("p") != 1:
            continue
        try:
            winner = names.get(int(match["w"]), "")
        except (KeyError, TypeError, ValueError):
            return "", ""
        try:
            runner_up = names.get(int(match["l"]), "")
        except (KeyError, TypeError, ValueError):
            runner_up = ""
        return winner, runner_up
    return "", ""


def _reader_rosters(league_id: str, reader: dict | None) -> dict[str, dict]:
    """The team the reader held in each season of this league.

    A question about "my team" or "the pick I traded" is asked by the manager the app is
    connected as, and only the season's own rosters can say which team that was: a roster id
    means nothing outside the season that issued it. Sleeper's user id belongs to the manager and
    survives a rename, so it is asked for first and the username is the fallback.
    """
    manager_id = str((reader or {}).get("manager_id") or "")
    username = str((reader or {}).get("username") or "")
    if not manager_id and not username:
        return {}
    mine: dict[str, dict] = {}
    for entry in _chain(league_id):
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        team = trades_service._own_team(dashboard["teams"], manager_id, username)
        if team:
            mine[str(entry["season"])] = {"roster_id": team["roster_id"], "team": team["name"],
                                           "owner": team.get("owner", "")}
    return mine


def _reader_roster_id(league_id: str, reader: dict | None, season: str = "") -> tuple[int | None, str]:
    """The reader's roster id in a season, falling back to the newest season they played.

    Roster ids are usually carried from one season to the next, so a fallback is worth having;
    what it is not worth is pretending, so the season the id came from is returned with it.
    """
    mine = _reader_rosters(league_id, reader)
    if not mine:
        return None, ""
    wanted = str(season or "")
    if wanted and wanted in mine:
        return mine[wanted]["roster_id"], mine[wanted]["team"]
    newest = max(mine)
    return mine[newest]["roster_id"], mine[newest]["team"]


def draft_board(league_id: str, season: str, round: str = "") -> dict:
    """One season's whole draft, in order, optionally for a single round.

    This is the question a draft board answers and nothing else can: who went where. Rounds are
    named the way a manager names them -- a round number, and the place within it ("3rd round",
    and each pick's own "3.05").
    """
    wanted = str(season or "").strip()
    if not wanted:
        seasons = sorted(str(entry["season"]) for entry in _chain(league_id))
        return {"picks": [], "note": ("Name a season for a draft board"
                                       + (f": this league has drafted {', '.join(seasons)}."
                                          if seasons else "; no seasons could be read."))}
    try:
        round_number = int(str(round).strip()) if str(round or "").strip() else None
    except (TypeError, ValueError):
        return {"picks": [], "note": f"\"{round}\" is not a round number."}
    board = draft_picks.draft_board(league_id, wanted, round_number)
    board["season"] = wanted
    return board


#: A season's rookie draft is its short one; a startup runs about twice as deep. The History page
#: reads its own drafts by the same rule, so a tool and the page never call one draft two things.
ROOKIE_ROUND_LIMIT = 8


def player_draft(league_id: str, player: str) -> dict:
    """Where this league drafted one player, exactly: the season, the round and the pick.

    A player's draft position is not a fact about a traded pick, so the trades cannot answer it.
    It is read off every draft the league has held instead -- the rookie class, and the startup a
    first season ran -- and answered with all three numbers a manager means by "what pick was
    he": the place in the round ("3.04"), the round, and the number overall.

    Whose pick it was rides along, because the two halves are one question: "where was he taken"
    is answered by the pick, and "where did that pick come from" by the trades that moved it and
    what they cost either side. A manager asked the first way and answered only the first way has
    to ask again, so the trade is part of this answer rather than a second lookup.
    """
    entries = _chain(league_id)
    if not entries:
        return {"drafted": [], "note": "This league's seasons could not be read."}
    drafted: dict[str, dict] = {}
    boards: dict[str, list[dict]] = {}
    for entry in entries:
        season = str(entry["season"])
        boards[season] = draft_picks.season_boards(league_id, season)
        for board in boards[season]:
            for pick in board["picks"]:
                if not pick["player_id"]:
                    continue
                drafted.setdefault(pick["player_id"], {"id": pick["player_id"],
                                                        "name": pick["player"],
                                                        "position": pick["position"]})
    if not drafted:
        return {"drafted": [], "note": "No draft in this league's history has a player on it."}
    candidates = sorted(drafted.values(), key=lambda row: row["name"].casefold())
    matched, nearest = name_search.match(str(player or ""), candidates)
    if not matched:
        # The search runs over the players this league has actually spent a pick on, so a miss
        # means no draft here took him -- which is worth saying, because a manager on the wire is
        # the other way a player joins a league.
        return {"drafted": [], "nearest_names": nearest,
                "note": (f"No pick in this league's drafts is a player matching \"{player}\" -- "
                         f"no draft here took him."
                         + (f" Closest drafted names: {', '.join(nearest)}." if nearest else ""))}
    if len(matched) > 1:
        return {"drafted": [], "matches": [row["name"] for row in matched[:8]],
                "note": f"\"{player}\" matches more than one drafted player; use the full name."}
    who = matched[0]
    hits: list[dict] = []
    for season, season_boards in boards.items():
        names = draft_picks.team_names(league_id, season)
        for board in season_boards:
            rounds = max([pick["round"] for pick in board["picks"]] + [0])
            kind = "rookie draft" if 0 < rounds <= ROOKIE_ROUND_LIMIT else "startup draft"
            for pick in board["picks"]:
                if pick["player_id"] != who["id"]:
                    continue
                hits.append({"season": season, "draft": kind, "name": who["name"],
                             "position": who["position"], "round": pick["round"],
                             "round_label": trades_service.round_label(pick["round"]),
                             "place": pick["slot_label"], "overall": pick["pick_no"],
                             "team": names.get(pick["roster_id"], f"roster {pick['roster_id']}"),
                             # The board is named because a season that held two drafts has two
                             # picks of the same number, and this one is known here.
                             "pick_origin": draft_picks.provenance(
                                 league_id, season, pick["round"], pick["pick_no"],
                                 draft_id=board["draft_id"])})
    hits.sort(key=lambda row: (row["season"], row["overall"] or 0))
    taken = "; ".join(f"{row['place']} -- {row['round_label']} pick {row['overall']} -- "
                      f"by {row['team']} in the {row['season']} {row['draft']}" for row in hits)
    note = f"{who['name']} ({who['position']}) was taken at {taken}."
    # The pick's own history belongs to this answer, so it is stated here rather than left to a
    # second question the reader should not have to ask.
    origins = [row["pick_origin"]["note"] for row in hits
               if (row.get("pick_origin") or {}).get("note")]
    if origins:
        note += " " + " ".join(origins)
    return {"player": who["name"], "position": who["position"], "drafted": hits,
            "note": note}


def _team_scores(league_id: str, weeks: int) -> dict[int, dict[int, float]]:
    """Read a team's own weekly score: ``week -> roster id -> points``."""
    def fetch(week: int) -> tuple[int, list]:
        try:
            rows = get_json(f"/league/{league_id}/matchups/{week}") or []
        except SleeperError:
            return week, []
        return week, rows if isinstance(rows, list) else []

    if weeks < 1:
        return {}
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        results = list(executor.map(fetch, range(1, weeks + 1)))
    scored: dict[int, dict[int, float]] = {}
    for week, rows in results:
        table: dict[int, float] = {}
        for row in rows:
            points = _number(row.get("points"))
            if points is not None:
                table[row.get("roster_id")] = points
        scored[week] = table
    return scored


def weekly_scores(league_id: str, season: str, week: int, reader: dict | None = None) -> dict:
    """Every team's score in one week of one season, highest first, with who they played.

    The scores are the league's own, which is what the Matchups page shows, so a number here and
    the number on that page are the same number.
    """
    if not str(season or "").strip():
        return {"scores": [], "note": ("Name a season: a week belongs to one season. This league "
                                       f"has played {_seasons_label(_chain(league_id))}.")}
    entries, problem = _season_entries(league_id, season)
    if problem:
        return {"scores": [], "note": problem}
    entry = entries[0]
    try:
        week_number = int(week)
    except (TypeError, ValueError):
        return {"scores": [], "note": f"\"{week}\" is not a week number."}
    try:
        dashboard = get_league_dashboard(entry["league_id"])
    except SleeperError:
        return {"scores": [], "note": f"The {season} season could not be read from Sleeper."}
    played = _playing_weeks(dashboard["league"])
    if not 1 <= week_number <= played:
        return {"scores": [], "note": (f"The {season} season has scored {played} week(s), so week "
                                       f"{week_number} has no final scores.")}
    try:
        rows = get_json(f"/league/{entry['league_id']}/matchups/{week_number}") or []
    except SleeperError:
        return {"scores": [], "note": f"{season} week {week_number} could not be read."}
    names = {team["roster_id"]: team["name"] for team in dashboard["teams"]}
    mine, _name = _reader_roster_id(league_id, reader, str(season))
    paired: dict = {}
    for row in rows:
        paired.setdefault(row.get("matchup_id"), []).append(row)
    scores = []
    for row in rows:
        points = _number(row.get("points"))
        if points is None:
            continue
        others = [other for other in paired.get(row.get("matchup_id"), [])
                  if other.get("roster_id") != row.get("roster_id")]
        opponent_points = _number(others[0].get("points")) if others else None
        scores.append({
            "team": names.get(row.get("roster_id"), f"roster {row.get('roster_id')}"),
            "points": round(points, 2),
            "opponent": names.get(others[0].get("roster_id"), "") if others else "",
            "opponent_points": round(opponent_points, 2) if opponent_points is not None else None,
            "is_you": mine is not None and row.get("roster_id") == mine,
        })
    scores.sort(key=lambda item: -(item["points"] or 0))
    top = scores[0] if scores else None
    note = f"{season} week {week_number}: {len(scores)} teams scored, highest first."
    if top:
        note += (f" {top['team']} scored the most with {top['points']}"
                 + (f", against {top['opponent']}" if top["opponent"] else "") + ".")
        your = next((item for item in scores if item["is_you"]), None)
        if your:
            note += (f" The reader's team scored {your['points']}"
                     + (f" against {your['opponent']}." if your["opponent"] else "."))
    return {"scores": scores[:MAX_ROWS], "note": note}


def league_records(league_id: str, reader: dict | None = None) -> dict:
    """This league's record book: the biggest and smallest team weeks, the blowouts and the
    near misses, the best and worst season totals, and every season's champion.

    Read from the league's own weekly scores rather than from any summary, so a record here is
    one the Matchups page would show too. It reads every scored week of every season the league
    has played, which is the widest read any tool here makes -- worth it for a question like
    "what is the highest score in this league's history".
    """
    entries = _season_entries(league_id)[0]
    if not entries:
        return {"records": {}, "note": "This league's seasons could not be read."}
    seasons: list[dict] = []
    for entry in entries:
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        league = dashboard["league"]
        played = _playing_weeks(league)
        if played < 1:
            continue
        names = {team["roster_id"]: team["name"] for team in dashboard["teams"]}
        champion, runner_up = _final(entry["league_id"], names)
        per_week = _team_scores(entry["league_id"], played)
        totals: dict[int, float] = {}
        weeks: list[dict] = []
        for week, table in per_week.items():
            for roster_id, points in table.items():
                totals[roster_id] = totals.get(roster_id, 0.0) + points
                weeks.append({"season": str(entry["season"]), "week": week,
                              "team": names.get(roster_id, f"roster {roster_id}"),
                              "points": round(points, 2)})
        best_total = max(totals.items(), key=lambda item: item[1], default=None)
        worst_total = min(totals.items(), key=lambda item: item[1], default=None)
        seasons.append({
            "season": str(entry["season"]), "weeks": played, "champion": champion,
            "runner_up": runner_up,
            "most_points": round(best_total[1], 2) if best_total else None,
            "most_points_team": names.get(best_total[0], "") if best_total else "",
            "fewest_points": round(worst_total[1], 2) if worst_total else None,
            "fewest_points_team": names.get(worst_total[0], "") if worst_total else "",
            "weeks_scored": weeks,
        })
    if not seasons:
        return {"records": {}, "note": "No season of this league has a scored week yet."}
    every_week = [week for season in seasons for week in season["weeks_scored"]]
    every_week.sort(key=lambda week: -week["points"])
    highest = every_week[:5]
    lowest = sorted(every_week, key=lambda week: week["points"])[:3]

    def describe(week: dict) -> str:
        return (f"{week['season']} Wk{week['week']}: {week['team']} {week['points']}")

    records = {
        "highest_team_weeks": highest,
        "lowest_team_weeks": lowest,
        "seasons": [{key: value for key, value in season.items() if key != "weeks_scored"}
                    for season in seasons],
    }
    note = (f"Read {len(every_week)} scored team weeks across {len(seasons)} season(s). "
            f"Highest single week: {describe(highest[0])}."
            f" Lowest: {describe(lowest[0])}."
            " Each season's own line carries its champion and its most and fewest points."
            " Only weeks the league has scored are counted.")
    return {"records": records, "note": note}


def _round2(value) -> float:
    """Two decimal places, under a name a tool's own ``round`` argument cannot shadow."""
    return round(value, 2)


def _drafted_players(league_id: str) -> dict[str, dict]:
    """Every player this league has spent a pick on, by player id, with the picks that took him.

    Read off every draft of every season, so a player taken in a startup and again in a rookie
    draft carries both, and "a 3rd round pick" means a pick of any draft this league has held
    rather than of whichever one a season happened to run.
    """
    drafted: dict[str, dict] = {}
    for entry in _chain(league_id):
        season = str(entry["season"])
        for board in draft_picks.season_boards(league_id, season):
            rounds = max([pick["round"] for pick in board["picks"]] + [0])
            kind = "rookie" if 0 < rounds <= ROOKIE_ROUND_LIMIT else "startup"
            for pick in board["picks"]:
                if not pick["player_id"]:
                    continue
                record = drafted.setdefault(pick["player_id"], {
                    "name": pick["player"], "position": pick["position"] or "", "picks": []})
                record["picks"].append({
                    "season": season, "kind": kind, "round": pick["round"],
                    "place": pick["slot_label"] or f"pick {pick['pick_no']}",
                    "overall": pick["pick_no"],
                })
    for record in drafted.values():
        record["picks"].sort(key=lambda pick: (pick["season"], pick["overall"] or 0))
    return drafted


def _matching_pick(record: dict | None, wanted_round, wanted_draft: str) -> dict | None:
    """The pick a filter is about: the round, and the year, the player was taken in.

    With no filter asked for this is the first pick that took him, which is what a row names when
    it is only there to say where a player came from.
    """
    if not record:
        return None
    for pick in record["picks"]:
        if wanted_round is not None and pick["round"] != wanted_round:
            continue
        if wanted_draft and pick["season"] != wanted_draft:
            continue
        return pick
    return None


def _draft_label(pick: dict | None) -> str:
    """Name the pick that took a player, or say this league never drafted him."""
    if not pick:
        return "not drafted in this league"
    return (f"{pick['season']} {pick['kind']} draft, {trades_service.round_label(pick['round'])} "
            f"({pick['place']})")


def best_player_seasons(league_id: str, season: str = "", round: str = "", draft_season: str = "",
                        position: str = "", limit: int = 10) -> dict:
    """Rank players by their best single season, over the whole set rather than a sample of it.

    A question like "which third-round picks have scored the most in a season" is a ranking of an
    entire set -- every player this league drafted in that round -- and a handful of game logs
    cannot answer it. The set is assembled first, each player's weeks are added into season
    totals, and only then is the best of those totals put in order. That order, and not the
    players the answer happens to name, is the answer.

    ``season`` narrows the scores to one season (empty is every season the league has played);
    ``round`` and ``draft_season`` narrow the players to the ones drafted then; ``position``
    narrows to one position. A week he scored nothing adds nothing and is not counted as a game
    -- a bye, or a week he was inactive -- and a week the season has not finished is not read.
    """
    entries, problem = _season_entries(league_id, season)
    if problem:
        return {"players": [], "note": problem}
    try:
        wanted_round = int(str(round).strip()) if str(round or "").strip() else None
    except (TypeError, ValueError):
        return {"players": [], "note": f'"{round}" is not a draft round.'}
    wanted_draft = str(draft_season or "").strip()
    wanted_position = str(position or "").strip().upper()
    try:
        wanted_rows = max(1, min(int(limit or 10), MAX_ROWS))
    except (TypeError, ValueError):
        wanted_rows = 10

    index = get_player_index()
    drafted = _drafted_players(league_id)
    totals: dict[tuple[str, str], float] = {}
    games: dict[tuple[str, str], int] = {}
    seasons_read: list[str] = []
    weeks_read = 0
    for entry in entries:
        try:
            dashboard = get_league_dashboard(entry["league_id"])
        except SleeperError:
            continue
        weeks = _playing_weeks(dashboard["league"])
        if weeks < 1:
            continue
        label = str(entry["season"])
        seasons_read.append(label)
        weeks_read += weeks
        for table in _read_scores(entry["league_id"], weeks).values():
            for player_id, (_roster_id, points) in table.items():
                key = (str(player_id), label)
                totals[key] = totals.get(key, 0.0) + points
                if points > 0:
                    games[key] = games.get(key, 0) + 1
    if not totals:
        return {"players": [], "note": "No week of this league has been scored yet."}

    best: dict[str, dict] = {}
    for (player_id, label), total in totals.items():
        if total <= 0:
            continue
        held = best.get(player_id)
        # Seasons are read oldest first, so an equal total leaves the first time he did it.
        if held is None or total > held["points"]:
            best[player_id] = {"season": label, "points": _round2(total),
                               "games": games.get((player_id, label), 0)}
    rows: list[dict] = []
    for player_id, row in best.items():
        record = drafted.get(player_id)
        pick = _matching_pick(record, wanted_round, wanted_draft)
        if (wanted_round is not None or wanted_draft) and pick is None:
            continue
        who = index.get(player_id) or {}
        name = (f"{who.get('first_name', '')} {who.get('last_name', '')}".strip()
                or (record or {}).get("name") or player_id)
        place = str(who.get("position") or (record or {}).get("position") or "-")
        if wanted_position and place.upper() != wanted_position:
            continue
        rows.append({"player": name, "position": place, "season": row["season"],
                     "points": row["points"], "games": row["games"],
                     "drafted": _draft_label(pick)})
    rows.sort(key=lambda item: (-item["points"], item["player"].casefold()))
    listed = rows[:wanted_rows]

    what = "every player this league has scored"
    if wanted_round is not None and wanted_draft:
        what = f"every player the {wanted_draft} draft took in round {wanted_round}"
    elif wanted_round is not None:
        what = f"every player this league drafted in round {wanted_round}"
    elif wanted_draft:
        what = f"every player the {wanted_draft} draft took"
    if wanted_position:
        what += f" at {wanted_position}"
    label = (seasons_read[0] if len(seasons_read) == 1
             else f"{seasons_read[0]}-{seasons_read[-1]}" if seasons_read else "no season")
    if not rows:
        return {"players": [], "note": (
            f"Read {weeks_read} scored weeks of {label} and added up {len(totals)} player-seasons, "
            f"but none of {what} has scored a point.")}
    note = (f"Added up {len(totals)} player-seasons across {weeks_read} scored weeks of {label} "
            f"and ranked {len(rows)} players ({what}) by their best single season. "
            "Highest first: " + "; ".join(
                f"{row['player']} {row['points']} ({row['season']}, {row['games']} games)"
                for row in listed[:5]) + ".")
    if len(rows) > len(listed):
        note += f" {len(rows) - len(listed)} more were ranked below these."
    return {"players": listed, "ranked": len(rows), "note": note}


def _stat_choices() -> str:
    """Every stat a pace can be ranked by, as the model is told them: the abbreviation and name."""
    table = stat_table()
    return ", ".join(f"{abbr} ({table[abbr]['label']})" for abbr in sorted(table))


def _name_positions(positions) -> str:
    """A set of positions as a reader reads them: ``QB, RB, WR and TE``."""
    places = sorted(positions)
    if len(places) == 1:
        return f"{places[0]}s"
    return ", ".join(places[:-1]) + f" and {places[-1]}"


def pace_lines(league_id: str, season: str = "") -> dict:
    """Every player this league has scored in one season, and what each is on pace for.

    One season's weeks are read once -- the league's own scored points, and Sleeper's official
    stats for the week, which say both who took the field and what he did with it -- and every
    player's whole pace line is worked out from them by
    :func:`services.player_pace.pace_rows`. That is the same arithmetic, on the same games, as
    the pace button on a player's card, so the two cannot disagree about a figure.

    This is the read behind both the pace ranking Ask AI answers by and the Pace board page:
    a player's line is the whole of what he is on course for in the stats his position is judged
    by, and each caller decides what to do with it -- rank it by one stat, or lay it all out.
    The totals behind the lines are returned as well, because a caller that ranks by a stat needs
    the season's own figure for it, which a projected one cannot be turned back into.

    A week whose official stats could not be read is named in ``unread`` and counted in no
    player's games, because a pace built on the wrong number of games is worse than one that
    says which week is missing. Raises :class:`~services.sleeper.SleeperError` when there is no
    season to read at all.
    """
    entries, problem = _season_entries(league_id, season)
    if problem:
        raise SleeperError(problem)
    # The chain arrives oldest first, so the season being played is the newest of them -- the one
    # a read with no season in it means.
    entry = entries[-1]
    played = str(entry["season"])
    try:
        dashboard = get_league_dashboard(entry["league_id"])
    except SleeperError as exc:
        raise SleeperError(f"The {played} season could not be read: {exc}") from exc
    weeks = season_week_count(dashboard["league"])
    if weeks < 1:
        raise SleeperError(f"No game of this league's {played} season has been played yet.")

    scored = _read_scores(entry["league_id"], weeks)
    weekly = _weekly_stats(played, list(range(1, weeks + 1)))
    unread = sorted(week for week, lines in weekly.items() if lines is None)

    index = get_player_index()
    # Every stat a pace line can hold is summed for every player, because a line is the whole of
    # what a pace is and not only the one stat a caller happens to rank by.
    totals: dict[str, dict[str, float]] = {}
    points: dict[str, float] = {}
    games: dict[str, int] = {}
    places: dict[str, str] = {}
    for week in range(1, weeks + 1):
        lines = weekly.get(week)
        if lines is None:
            continue
        for player_id, (_roster_id, scored_week) in (scored.get(week) or {}).items():
            line = lines.get(player_id)
            # ``gp`` is what says he took the field: a bye and an inactive week both record none,
            # and the league recording him is the other half of what makes a week a game.
            if not isinstance(line, dict) or not (_number(line.get("gp")) or 0.0) > 0:
                continue
            ran_up = totals.setdefault(player_id, {})
            for key in _STAT_KEYS:
                value = _number(line.get(key))
                if value is not None:
                    ran_up[key] = ran_up.get(key, 0.0) + value
            points[player_id] = points.get(player_id, 0.0) + scored_week
            games[player_id] = games.get(player_id, 0) + 1
            places[player_id] = str((index.get(player_id) or {}).get("position") or "-")

    players: list[dict] = []
    for player_id, ran_up in totals.items():
        played_games = games.get(player_id, 0)
        if played_games < 1:
            continue
        who = index.get(player_id) or {}
        place = places.get(player_id) or "-"
        scored_points = points.get(player_id, 0.0)
        players.append({
            "id": player_id,
            "name": (f"{who.get('first_name', '')} {who.get('last_name', '')}".strip()
                     or player_id),
            "position": place,
            "games": played_games,
            "totals": ran_up,
            "points": scored_points,
            "line": pace_rows(place, ran_up, scored_points, played_games),
        })
    return {
        "season": played,
        "season_games": SEASON_GAMES,
        "weeks": weeks,
        "weeks_read": weeks - len(unread),
        "unread": unread,
        "players": players,
    }


def player_names(league_id: str) -> list[str]:
    """Every player this league has held, ordered for a search box to complete names from.

    The names a box can be answered with are the players this league's own seasons resolved to --
    exactly the set :func:`player_pace` looks a typed name up in -- so a name completed from this
    list is always one the lookup can answer for, and never one it has never heard of. It is the
    same reasoning the Compare page's own matching follows: only the players this league has
    actually held can be found.

    They are ordered by what each player is on pace for in the season being played, highest
    first, because the name a reader is typing is far more often the one who matters now than the
    one that happens to be alphabetically first; a player the league has not scored this season
    follows in name order. A season that cannot be read costs the ordering and nothing else.
    """
    names = {str(who.get("name") or "").strip()
             for who in _universe(league_id).values()
             if str(who.get("name") or "").strip()}
    if not names:
        return []
    scored: dict[str, float] = {}
    try:
        scored = {who["name"]: who["points"] for who in pace_lines(league_id)["players"]}
    except SleeperError:
        pass
    return sorted(names, key=lambda name: (-scored.get(name, 0.0), name))


def pace_ranking(league_id: str, season: str = "", position: str = "", limit: int = 10,
                 stat: str = "PTS") -> dict:
    """Rank this league's players by what each is on pace for in one season, in one stat.

    The question this answers is "who is on pace for the most points this season" and its
    siblings -- the most rushing yards, the most receptions, the most touchdowns -- and the season
    it means is the one being played, not the best season anybody has already had. Every player
    whose position is measured by the stat asked for is projected over a full season at the rate
    he has earned it at, and that whole ranking is the answer, because a handful of game logs
    cannot say who leads a league.

    A ranking is not one column, though: a pace line is the whole of what a player is on course
    for, so each row carries every stat his own position is measured by as well, and the returned
    table can be read across the stats rather than only down the one that was ranked by. The
    columns are what the listed players between them have, and a stat a position is not measured
    by is a dash in his row rather than a zero.

    The games counted are a player card's games, so a row here reads beside the pace button on the
    card it names: a week this league scored and that Sleeper recorded him taking the field in. A
    bye, an inactive week and a week still to come are none of them a game, which is what stops a
    player who has missed time being quietly averaged down by weeks he never played.

    ``stat`` is an abbreviation :func:`services.player_pace.stat_table` lists -- points by
    default -- and only the positions that carry a stat are ranked by it. ``season`` names one
    season to project (empty is the one being played); ``position`` narrows the ranking to a
    single position; ``limit`` is how many rows come back.
    """
    chosen = stat_table().get(str(stat or "PTS").strip().upper() or "PTS")
    if chosen is None:
        return {"players": [], "note": (
            f'"{stat}" is not a stat this app can rank a pace by. Ask for one of: '
            + _stat_choices() + ".")}
    wanted_position = str(position or "").strip().upper()
    # A position this stat is not measured by has nothing to rank, and saying that is a better
    # answer than an empty table the reader has to work out for himself.
    if (wanted_position and chosen["positions"] is not None
            and wanted_position not in chosen["positions"]):
        return {"players": [], "note": (
            f"A {wanted_position} has no {chosen['label'].lower()}: it is measured for "
            + _name_positions(chosen["positions"]) + ".")}
    try:
        wanted_rows = max(1, min(int(limit or 10), MAX_ROWS))
    except (TypeError, ValueError):
        wanted_rows = 10

    try:
        board = pace_lines(league_id, season)
    except SleeperError as exc:
        # A season this league never played, or one with no game scored yet: that is an answer
        # about the league rather than a lookup that broke.
        return {"players": [], "note": str(exc)}
    played, weeks = board["season"], board["weeks"]
    unread, weeks_read = board["unread"], board["weeks_read"]

    decimals = chosen["decimals"]
    rows: list[dict] = []
    figures: dict[str, dict[str, str]] = {}
    for who in board["players"]:
        place = who["position"]
        # Only the positions a stat is measured by are ranked by it, and a position can be asked
        # for on its own; either way this player is not in this ranking at all.
        if chosen["positions"] is not None and place not in chosen["positions"]:
            continue
        if wanted_position and place.upper() != wanted_position:
            continue
        line = {row["abbr"]: row for row in who["line"]}
        ranked = line.get(chosen["abbr"])
        if ranked is None:
            continue  # a stat this position is not measured by is not a row for him
        total = (who["points"] if chosen["scored"]
                 else sum(who["totals"].get(key, 0.0) for key in chosen["keys"]))
        rows.append({
            "player_id": who["id"],
            "player": who["name"],
            "position": place,
            "games": who["games"],
            "stat": chosen["label"],
            "abbr": chosen["abbr"],
            # A whole-number stat is a whole number of it, and points are the league's own and
            # carry the hundredths his card prints; a pace is read at the stat's own precision.
            "total": round(total, 2 if chosen["scored"] else decimals),
            "per_game": round(total / who["games"], decimals),
            # A rate is already a per-game figure, so a full season does not change it: the pace
            # of one is that figure.
            "pace": ranked["pace"],
        })
        # The printed figures are kept beside the rows, so the table a reader is shown and the
        # rows the model is handed are one set of numbers rather than two readings of them.
        figures[who["id"]] = {row["abbr"]: pace_figure(row["pace"], row["decimals"])
                              for row in who["line"]}
    rows.sort(key=lambda item: (-item["pace"], item["player"].casefold()))
    listed = rows[:wanted_rows]

    # The table's columns are the stats the listed players between them carry, so every row shows
    # the stats its own position is measured by, and no column is drawn for a stat nobody here
    # has. The stat that was ranked by leads it and points follow, because those are what a pace
    # chiefly means.
    carried = [abbr for abbr in stat_order()
               if abbr != chosen["abbr"]
               and any(abbr in figures[row["player_id"]] for row in listed)]
    columns = (["Player", "Pos", "GP", chosen["abbr"]]
               + [abbr for abbr in carried if abbr in SCORED_ABBRS]
               + [abbr for abbr in carried if abbr not in SCORED_ABBRS])
    # A stat a position is not measured by is nothing at all for him rather than a zero, and the
    # table says so instead of reading as a figure he scored.
    blank = "—"
    listed_lines = [" | ".join(
        [row["player"], row["position"], str(row["games"])]
        + [figures[row["player_id"]].get(abbr, blank) for abbr in columns[3:]])
        for row in listed]

    stat_label = chosen["label"].lower()
    what = "every player this league has scored" + (
        f" at {wanted_position}" if wanted_position else "")
    if not rows:
        if wanted_position:
            return {"players": [], "note": (
                f"Read {weeks_read} scored weeks of {played} and counted no {wanted_position} "
                f"with a {stat_label} pace to project from.")}
        return {"players": [], "note": (
            f"Read {weeks_read} scored weeks of {played}, but no player this league has scored "
            f"has a {stat_label} pace to project from yet.")}

    def _figure(row: dict) -> str:
        count = f"{row['games']} game{'s' if row['games'] != 1 else ''}"
        detail = count if chosen["rate"] else f"{row['per_game']} a game, {count}"
        return f"{row['player']} {row['pace']} ({detail})"

    note = (f"Projected {len(rows)} players ({what}) over {SEASON_GAMES} games from the "
            f"{weeks_read} weeks of {played} this league has scored, ranked by {stat_label}, "
            "highest first: " + "; ".join(_figure(row) for row in listed[:5]) + ".")
    if listed:
        note += (" Every row is that player's whole pace line, in the columns '"
                 + ", ".join(columns) + "' -- the stats his position is measured by as well as "
                 "the one this ranking is by -- so the ranking is one stat but the table can be "
                 f"read across the others, and a stat a position is not measured by is shown "
                 f"as {blank} rather than as a figure.")
    if len(rows) > len(listed):
        note += f" {len(rows) - len(listed)} more were ranked below these."
    if unread:
        note += (f" Week{'s' if len(unread) != 1 else ''} "
                 + ", ".join(str(week) for week in unread)
                 + " could not be read from Sleeper, so "
                 + ("they are" if len(unread) != 1 else "it is") + " not counted.")
    note += ((f" {chosen['label']} is this league's own scoring" if chosen["scored"] else
              f" {chosen['label']} comes from Sleeper's official weekly stats")
             + ", and a pace counts only the weeks this league scored and that Sleeper recorded "
             "the player on the field for.")
    return {"players": listed, "ranked": len(rows), "stat": chosen["label"],
            "abbr": chosen["abbr"], "season": played, "season_games": SEASON_GAMES,
            "columns": columns, "listed_lines": listed_lines,
            "note": note}


# --------------------------------------------------------------------------------------
# what the model is offered
# --------------------------------------------------------------------------------------

#: The tools, described for the model. The descriptions say what each one is *for*, because that
#: is what decides which of them gets called for a question.
TOOL_SPECS = [
    {
        "name": "league_overview",
        "description": ("This league's seasons, teams, scoring format, lineup rules and size, "
                        "and which team the person asking holds in every season. Call this first "
                        "for any question about how the league works, and always before "
                        "answering a question that says 'I', 'my' or 'me' -- it is what turns "
                        "'my team' into an actual team name."),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_players",
        "description": ("Look up a player by a loosely typed name in this league only. Returns "
                        "his position, NFL team and the seasons he was on a roster here. Use it "
                        "when a name may be misspelled, or to check a player was ever in this "
                        "league."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The player's name as typed by the reader."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "player_game_log",
        "description": ("One player's scored games in this league, each with the week, season, "
                        "league points and the fantasy team he was on. Call this for any "
                        "question about a player's points, a best or worst game, a streak, or a "
                        "total. Use 'below' and 'above' to get only the games under or over a "
                        "point total -- e.g. below=8 answers 'when did he last score less than "
                        "8'. Answers come newest first."),
        "parameters": {
            "type": "object",
            "properties": {
                "player": {"type": "string", "description": "Player name."},
                "season": {"type": "string",
                           "description": "A season year, or empty for every season the league has played."},
                "below": {"type": "number",
                          "description": "Only games with fewer than this many points."},
                "above": {"type": "number",
                          "description": "Only games with more than this many points."},
            },
            "required": ["player"],
        },
    },
    {
        "name": "player_vs_opponent",
        "description": ("How one player in this league has scored against one NFL team, game "
                        "by game and season by season -- his own NFL games, including seasons "
                        "before this league existed and weeks he was not on a roster here. Call "
                        "this for any question about a player against an opponent: 'how does he "
                        "do against the Texans', 'his average against Dallas', 'who has he "
                        "scored most against'. Name the opponent as a person would ('Texans', "
                        "'Houston', 'HOU') or leave it out for his averages against every NFL "
                        "team he has faced. His points are this league's own scoring, so they "
                        "read on the same scale as his weeks in this league."),
        "parameters": {
            "type": "object",
            "properties": {
                "player": {"type": "string", "description": "Player name."},
                "opponent": {"type": "string",
                             "description": ("The NFL team faced, as a name or code, e.g. "
                                             "'Texans' or 'HOU'. Leave it out to add his games "
                                             "up per NFL team instead.")},
                "season": {"type": "string",
                           "description": ("Only this NFL season, e.g. 2024. Leave it out for "
                                           "every season Sleeper has for him.")},
            },
            "required": ["player"],
        },
    },
    {
        "name": "player_pace",
        "description": ("What one player in this league is on pace for over a full 17-game "
                        "season, stat by stat: his season figures averaged over the games he "
                        "has actually played and stretched over the season, plus his points and "
                        "points a game. Call this for any question about a pace, a projection or "
                        "what a player is on course for -- 'what is Bucky Irving on pace for', "
                        "'is he on pace for 2,000 yards', 'what is he averaging'. The stats are "
                        "the ones his position is judged by: carries, catches, rushing, "
                        "receiving and passing yards, touchdowns, turnovers and points, all "
                        "scored by this league's own rules. Name a season that has already "
                        "been played to project that one; leave it out for the season being "
                        "played."),
        "parameters": {
            "type": "object",
            "properties": {
                "player": {"type": "string", "description": "Player name."},
                "season": {"type": "string",
                           "description": ("The season to project, e.g. 2024. Leave it out for "
                                           "the season being played.")},
            },
            "required": ["player"],
        },
    },
    {
        "name": "compare_paces",
        "description": ("Two players' paces side by side over a full 17-game season, for a "
                        "trade or a lineup decision: each one's projection over the stats both "
                        "positions carry -- points, points a game and total touchdowns -- with "
                        "the stats only one of them has reported beside them. Call this for any "
                        "question weighing two players against each other -- 'should I trade "
                        "Bucky Irving for Drake London', 'who is better, him or him', 'compare "
                        "their paces'. Both sides come from the same lookup a player's card "
                        "uses, so neither column can disagree with his card. Name a season that "
                        "has already been played to compare that one; leave it out for the "
                        "season being played."),
        "parameters": {
            "type": "object",
            "properties": {
                "player": {"type": "string", "description": "The first player's name."},
                "other": {"type": "string", "description": "The player compared against him."},
                "season": {"type": "string",
                           "description": ("The season to project, e.g. 2024. Leave it out for "
                                           "the season being played.")},
            },
            "required": ["player", "other"],
        },
    },
    {
        "name": "find_trades",
        "description": ("Trades in this league's history, and what each side received in players "
                        "and draft picks. Call this for any question about a trade, who traded "
                        "for whom, or what a team gave up. Filter by player, team or season."),
        "parameters": {
            "type": "object",
            "properties": {
                "player": {"type": "string", "description": "Only trades moving this player."},
                "team": {"type": "string",
                         "description": ("Only trades this team was part of. Use 'me' for the "
                                         "team of the person asking.")},
                "season": {"type": "string", "description": "Only trades filed in this season."},
                "pick": {"type": "string",
                         "description": ("Only trades that moved a draft pick, as a manager says "
                                         "it: '2027 1st', '2025 2nd', '2.02'. A slot (2.02) "
                                         "matches the round, because Sleeper never records "
                                         "which of a team's picks in a round was which.")},
            },
            "required": [],
        },
    },
    {
        "name": "trace_draft_pick",
        "description": ("Follow one traded draft pick from the team that started with it to the "
                        "player it became: every hand-off, who holds it now, which rookie was "
                        "drafted with it, and what each of those trades cost both sides. Call this "
                        "for questions like 'what happened to the second round pick I traded "
                        "away'."),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "string", "description": "The draft year the pick is for, e.g. 2025."},
                "round": {"type": "integer", "description": "The draft round, e.g. 2."},
                "team": {"type": "string",
                         "description": ("The team the pick ORIGINALLY belonged to -- the roster "
                                         "it started with, before any trade, never the team "
                                         "that ended up making the pick. Use 'me' for a pick "
                                         "that started with the person asking, e.g. 'my 2nd'. "
                                         "Always pass it when a round was traded by more than "
                                         "one team.")},
            },
            "required": ["season", "round"],
        },
    },
    {
        "name": "draft_board",
        "description": ("One season's whole draft, in order: every pick, the team that made it, "
                        "the player it took, and the pick's place in its round (\"3.05\"). Call "
                        "this for any question about who was drafted, what a round of a draft "
                        "looked like, or where a player was taken."),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "string", "description": "The draft year, e.g. 2025."},
                "round": {"type": "integer",
                          "description": "Only this round. Leave it out for the whole draft."},
            },
            "required": ["season"],
        },
    },
    {
        "name": "player_draft",
        "description": ("Where one player was drafted in this league: the season, the round, his "
                        "place in that round (\"3.04\") and the number overall -- and, with it, "
                        "whose pick that was and the trade that brought it to the team that used "
                        "it, with what each side received. Call this for any question about how "
                        "early or late a player went -- what round, what pick, how high he was "
                        "taken -- and for where the pick that took him came from. Never answer a "
                        "draft position, or a pick's origin, without it."),
        "parameters": {
            "type": "object",
            "properties": {
                "player": {"type": "string",
                           "description": "The player's name, loosely typed; part of a surname is enough."},
            },
            "required": ["player"],
        },
    },
    {
        "name": "weekly_scores",
        "description": ("Every team's score in one week of one season, highest first, with the "
                        "opponent each faced and which team is the person asking. Call this for "
                        "a week's scores, the best score of a week, or how the reader did."),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "string", "description": "Season year, e.g. 2025."},
                "week": {"type": "integer", "description": "Week number within that season."},
            },
            "required": ["season", "week"],
        },
    },
    {
        "name": "best_player_seasons",
        "description": ("Rank players by their best single season ever played in this league -- "
                        "a record already set, not a projection. Each player's weeks are added "
                        "into season totals and the best of those is ranked, over every scored "
                        "week rather than sampled. Filter to the players drafted in a round "
                        "(\"3rd round picks\"), drafted in a year, or of one position. Call this "
                        "for which picks scored the most in a season or the best season anyone "
                        "has had; for anything on pace, or projected, call pace_ranking instead. "
                        "It returns the whole ranking, which is a table; never answer such a "
                        "question from a few players' game logs."),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "string",
                           "description": ("Only this season's scores. Leave it out to rank every "
                                           "season the league has played.")},
                "round": {"type": "integer",
                          "description": ("Only players this league drafted in this round, e.g. 3 "
                                          "for third-round picks.")},
                "draft_season": {"type": "string",
                                 "description": "Only players drafted in this year, e.g. 2023."},
                "position": {"type": "string",
                             "description": "Only this position, e.g. RB, WR, QB, TE."},
                "limit": {"type": "integer",
                          "description": "How many rows to return, up to 60. Defaults to 10."},
            },
            "required": [],
        },
    },
    {
        "name": "pace_ranking",
        "description": ("Rank this league's players by what each is on pace for over a full "
                        "season, highest first, in one stat: points by default, and any stat a "
                        "position is measured by -- " + _stat_choices() + ". Call this for any "
                        "question about who is on pace for the most of something this season, "
                        "this season's pace leaders, or a position's pace leaders -- it projects "
                        "the season being played rather than a season already played, and it is "
                        "the only tool that reads a pace stat other than points. Only the "
                        "positions the stat is measured by are ranked by it. Filter to one "
                        "position; leave the season out for the one being played. It returns "
                        "the whole ranking, which is a table; never answer such a question from "
                        "a few players' game logs. Each row is that player's whole pace line, "
                        "not only the stat ranked by: 'columns' names the table's columns in "
                        "order -- his position's stats, his points and his points a game -- and "
                        "'listed_lines' is its rows, one player per line, with a dash in a "
                        "column his position is not measured by. Print that table, header and "
                        "all, rather than only the ranked stat."),
        "parameters": {
            "type": "object",
            "properties": {
                "stat": {"type": "string",
                         "description": ("Which stat to rank by, as its abbreviation: PTS for "
                                         "points (the default), or one of " + _stat_choices()
                                         + ".")},
                "season": {"type": "string",
                           "description": ("The season to project, e.g. 2025. Leave it out for "
                                           "the season being played.")},
                "position": {"type": "string",
                             "description": "Only this position, e.g. RB, WR, QB, TE."},
                "limit": {"type": "integer",
                          "description": "How many rows to return, up to 60. Defaults to 10."},
            },
            "required": [],
        },
    },
    {
        "name": "league_records",
        "description": ("This league's record book: the highest and lowest team weeks it has "
                        "ever scored, each season's most and fewest points with its champion and "
                        "runner-up, and the teams behind them. Call it for anything superlative "
                        "-- the most points ever scored, the best season, who has won the most."),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "team_roster",
        "description": ("One team's roster in one season, with starters marked. Use it to say "
                        "who a team had, or to check whether a player was on a team that year."),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "string", "description": "Season year, e.g. 2024."},
                "team": {"type": "string", "description": "Team name."},
            },
            "required": ["season", "team"],
        },
    },
    {
        "name": "season_standings",
        "description": ("Every team's record, points for and against, and the champion, for one "
                        "season or all of them. Call it for questions about records, who won a "
                        "season, or how a season finished. The records and points are the "
                        "regular season's; the champion is the winner of the playoff final."),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "string", "description": "A season year, or empty for all seasons."},
            },
            "required": [],
        },
    },
]

#: The tools that are told which team the connected manager holds, so that "my team", "the pick
#: I traded" and "me" resolve to a real roster rather than to nothing.
READER_TOOLS = frozenset({"league_overview", "find_trades", "trace_draft_pick", "team_roster",
                          "weekly_scores", "league_records"})

#: Name to function. Every tool takes the league id first, which is what binds an answer to the
#: league the question was asked in.
TOOLS = {
    "league_overview": league_overview,
    "find_players": find_players,
    "player_game_log": player_game_log,
    "player_vs_opponent": player_vs_opponent,
    "player_pace": player_pace,
    "compare_paces": compare_paces,
    "find_trades": find_trades,
    "trace_draft_pick": trace_draft_pick,
    "draft_board": draft_board,
    "player_draft": player_draft,
    "weekly_scores": weekly_scores,
    "league_records": league_records,
    "best_player_seasons": best_player_seasons,
    "pace_ranking": pace_ranking,
    "team_roster": team_roster,
    "season_standings": season_standings,
}


def run(league_id: str, name: str, arguments: dict | None,
        reader: dict | None = None) -> dict:
    """Run one tool call for one league, turning any failure into something explainable.

    A tool that raises is reported to the model as a failed call with the reason, because a
    question that cannot be answered should come back as an answer that says so.
    """
    function = TOOLS.get(str(name))
    if function is None:
        return {"error": f"There is no tool called {name}."}
    arguments = dict(arguments or {})
    if str(name) in READER_TOOLS:
        arguments["reader"] = reader
    try:
        return function(league_id, **arguments)
    except TypeError as exc:
        return {"error": f"{name} was called with the wrong arguments: {exc}"}
    except SleeperError as exc:
        return {"error": f"Sleeper could not be read: {exc}"}
    except Exception as exc:  # A tool must never take the page down with it.
        return {"error": f"{name} failed: {exc.__class__.__name__}: {exc}"}
