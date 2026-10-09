"""Read-only access to Sleeper's public NFL league API."""

from __future__ import annotations

import mimetypes
import re

import requests

from services.data_cache import MISS
from services.data_cache import count as cache_count
from services.data_cache import drop as cache_drop
from services.data_cache import lookup as cache_lookup
from services.data_cache import store as cache_store

BASE_URL = "https://api.sleeper.app/v1"
SCHEDULE_URL = "https://api.sleeper.com/schedule/nfl/regular"
TIMEOUT = 20
# Team avatars, team logos and player headshots live on Sleeper's CDN, whose responses reach
# a page with no Access-Control-Allow-Origin header, so a page cannot read those bytes back to
# embed them in a screenshot. Relaying them from this app's own origin is what makes them
# readable — and it keeps the feature from depending on the CDN's own CORS behaviour.
CDN_URL = "https://sleepercdn.com"
CDN_PATHS = ("avatars/", "content/", "images/team_logos/")
CDN_SECONDS = 24 * 60 * 60
# One path segment onwards, and nothing that could name a different host.
_CDN_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
DEFAULT_REGULAR_SEASON_WEEKS = 14
MAX_NFL_WEEK = 18

# How long a cached response stays valid. Player names and weekly statistics are large
# and effectively frozen once a week is over, so they refresh on their own slow clock.
# Live data has to keep up with games in progress, so it is re-checked constantly.
PLAYER_INDEX_SECONDS = 6 * 60 * 60
STATS_SECONDS = 6 * 60 * 60
LIVE_SECONDS = 60
SCHEDULE_SECONDS = 5 * 60

_MATCHUPS_PATH = re.compile(r"^/league/(?P<league_id>[^/]+)/matchups/(?P<week>\d+)$")
_BRACKET_PATH = re.compile(r"^/league/(?P<league_id>[^/]+)/(?:winners|losers)_bracket$")


def regular_season_week_count(league: dict) -> int:
    """Return the regular-season length from Sleeper settings, with a safe fallback."""
    settings = league.get("settings") or {}
    for key in ("regular_season_weeks", "reg_season_weeks"):
        try:
            value = int(settings.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if 1 <= value <= MAX_NFL_WEEK:
            return value
    try:
        playoff_start = int(settings.get("playoff_week_start") or 0)
    except (TypeError, ValueError):
        playoff_start = 0
    if 2 <= playoff_start <= MAX_NFL_WEEK:
        return playoff_start - 1
    return DEFAULT_REGULAR_SEASON_WEEKS


def season_end_week(league: dict) -> int:
    """Return the last week this league's season covers, playoffs included.

    A league plays its playoffs inside the NFL's regular season, so a league whose playoffs
    start in week 15 and run three rounds ends in week 17, while one that plays through the
    NFL's last week ends in week 18. Sleeper records the furthest week it has scored in the
    settings: for a finished season that is the final week of its playoffs, and for a season
    still being played it is how far its scoring has actually got.
    """
    end = regular_season_week_count(league)
    settings = league.get("settings") or {}
    for key in ("last_scored_leg", "leg"):
        try:
            value = int(settings.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if 1 <= value <= MAX_NFL_WEEK:
            end = max(end, value)
    return end


def playoff_week_start(league: dict) -> int | None:
    """Return the week this league plays its first playoff round in, or None if it has none.

    Sleeper states it outright in the settings. A league that does not is one whose playoffs
    the app cannot place, and which therefore has no rounds to show.
    """
    settings = league.get("settings") or {}
    for key in ("playoff_week_start", "playoff_start_week"):
        try:
            value = int(settings.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if 2 <= value <= MAX_NFL_WEEK:
            return value
    return None


def season_week_tabs(league: dict, playoff_rounds: int | None = None) -> list[dict]:
    """List every week the week tabs offer: the regular season, then its playoff rounds.

    A league plays its playoffs in the NFL weeks that follow its regular season, one round to
    a week, and the pages name those rounds P1, P2 and so on, with the last round - the final -
    named SB. How many there are comes from the league's own bracket when Sleeper has published
    one, which is how the rounds can be listed before any of them has been played, and from how
    far the league has actually scored when it has not.
    """
    regular = regular_season_week_count(league)
    tabs = [{"week": week, "label": f"Wk{week}", "playoff": False}
            for week in range(1, regular + 1)]
    start = playoff_week_start(league)
    if start is None or start <= regular:
        return tabs
    # The final round is the later of what the bracket says and what the league has scored.
    # With no bracket to count rounds from there is nothing saying a round exists before it is
    # played, so only the weeks already scored count - otherwise a league whose playoffs have
    # not started would be handed a round it never played.
    last = season_end_week(league)
    if playoff_rounds:
        last = max(last, start + playoff_rounds - 1)
    last = min(last, MAX_NFL_WEEK)
    for index, week in enumerate(range(start, last + 1), start=1):
        tabs.append({"week": week, "label": "SB" if week == last else f"P{index}",
                     "playoff": True})
    return tabs


def last_week_of(league: dict, playoff_rounds: int | None = None) -> int:
    """Return the last week of this league's season, its playoff rounds included."""
    tabs = season_week_tabs(league, playoff_rounds)
    return tabs[-1]["week"] if tabs else regular_season_week_count(league)


def week_label(league: dict, week: int) -> str:
    """Name a week the way its tab does, so a page says which round it is showing.

    A regular-season week is its own number; a playoff round reads as "P1 (Week 15)", which
    keeps the round and the NFL week it is played in from being mistaken for each other.
    """
    tab = next((tab for tab in league.get("week_tabs") or [] if tab["week"] == week), None)
    if tab is not None and tab.get("playoff"):
        return f"{tab['label']} (Week {week})"
    return f"Week {week}"


def get_playoff_round_count(league_id: str) -> int | None:
    """Count the rounds in a league's winners bracket, or None when it has no bracket.

    The bracket exists before the playoffs are played - its teams and weeks are filled in as
    the season runs - so its rounds say how many playoff weeks there will be. A league that
    keeps no bracket has no playoff rounds to list.
    """
    try:
        bracket = get_json(f"/league/{league_id}/winners_bracket")
    except SleeperError:
        return None
    rounds = []
    for match in bracket or []:
        if not isinstance(match, dict):
            continue
        try:
            rounds.append(int(match.get("r")))
        except (TypeError, ValueError):
            continue
    return max(rounds) if rounds else None


def get_playoff_brackets(league_id: str) -> dict[str, list[dict]]:
    """Return the league's championship and consolation brackets, each a list of matches.

    Sleeper keeps the two apart: the winners bracket plays for the title and the losers
    bracket plays out the places below it. A league that keeps no losers bracket, or whose
    bracket cannot be read, comes back empty rather than failing the page that asked.
    """
    brackets = {}
    for name, path in (("championship", "winners_bracket"), ("consolation", "losers_bracket")):
        try:
            matches = get_json(f"/league/{league_id}/{path}")
        except SleeperError:
            matches = None
        brackets[name] = [match for match in matches or [] if isinstance(match, dict)]
    return brackets


def _bracket_pair(match: dict) -> frozenset | None:
    """Return the two roster ids a bracket match is played between, or None if undecided.

    A round later than the first names its entrants by the earlier matches that feed it
    (``t1_from``/``t2_from``) until those are played, so a match with unknown teams names no
    pair - and a pair is what ties the bracket to a week's matchup rows.
    """
    try:
        first, second = int(match["t1"]), int(match["t2"])
    except (KeyError, TypeError, ValueError):
        return None
    return frozenset({first, second})


def _bracket_round(match: dict) -> int | None:
    try:
        round_number = int(match.get("r"))
    except (TypeError, ValueError):
        return None
    return round_number if round_number >= 1 else None


def _placement(match: dict) -> int | None:
    """Return the place a bracket match settles (1 is the title), or None if it settles none."""
    try:
        return int(match["p"])
    except (KeyError, TypeError, ValueError):
        return None


def _super_bowl(bracket: list[dict], final_round: int | None) -> frozenset | None:
    """Return the pair playing the title game, or None when the bracket does not say.

    Sleeper marks the places its final round settles, and the first of them is the final
    itself. A bracket that marks no places falls back to the one match of its last round, if
    it holds only one. Nothing is guessed beyond that: a card is better left unnamed than
    labelled as a final it may not be.
    """
    last = [match for match in bracket if _bracket_round(match) == final_round]
    for match in last:
        if _placement(match) == 1:
            return _bracket_pair(match)
    if len(last) == 1:
        return _bracket_pair(last[0])
    return None


def playoff_matchup_stages(league: dict, brackets: dict[str, list[dict]]) -> dict:
    """Name every bracket match the app can place, by the week it is played in and its pair.

    Returns ``{week: {frozenset({a, b}): stage}}``. A playoff round is played in its own NFL
    week, one round to a week, so a match's round places its week. Each stage reads as the
    championship bracket's final (the Super Bowl), its other matches (a playoff match), or
    the consolation bracket. Only matches whose teams the bracket has already decided appear:
    an undecided round names no pair, so it labels nothing.
    """
    start = playoff_week_start(league)
    if start is None:
        return {}
    championship = brackets.get("championship") or []
    rounds = [round_number for round_number in (_bracket_round(match) for match in championship)
              if round_number is not None]
    final_round = max(rounds) if rounds else None
    super_bowl = _super_bowl(championship, final_round)
    stages: dict = {}
    for name, matches in (("championship", championship),
                          ("consolation", brackets.get("consolation") or [])):
        for match in matches:
            pair = _bracket_pair(match)
            round_number = _bracket_round(match)
            if pair is None or round_number is None:
                continue
            if name == "championship" and pair == super_bowl:
                stage = {"key": "final", "label": "Super Bowl"}
            elif name == "championship":
                stage = {"key": "playoff", "label": "Playoff"}
            else:
                stage = {"key": "consolation", "label": "Consolation"}
            stages.setdefault(start + round_number - 1, {}).setdefault(pair, stage)
    return stages


class SleeperError(Exception):
    """A readable error from the Sleeper API or its response data."""


def _avatar_urls(*avatar_ids: str | None) -> list[str]:
    """Return de-duplicated Sleeper thumbnail and full-size avatar URLs."""
    urls = []
    for avatar_id in avatar_ids:
        if not avatar_id:
            continue
        avatar = str(avatar_id).strip()
        if not avatar:
            continue
        if avatar.startswith(("https://", "http://")):
            candidates = [avatar]
        else:
            avatar = avatar.strip("/")
            candidates = [
                f"https://sleepercdn.com/avatars/thumbs/{avatar}",
                f"https://sleepercdn.com/avatars/{avatar}",
            ]
        for candidate in candidates:
            if candidate not in urls:
                urls.append(candidate)
    return urls


def get_cdn_file(path: str) -> tuple[bytes, str]:
    """Return the bytes and content type of a file on Sleeper's CDN.

    Only the paths this app actually shows are relayed, so a crafted path cannot turn the
    route into a way to fetch somewhere else.
    """
    clean = str(path or "").strip().lstrip("/")
    if not _CDN_PATH.match(clean) or ".." in clean or not clean.startswith(CDN_PATHS):
        raise SleeperError("That is not an image this app serves.")
    url = f"{CDN_URL}/{clean}"

    def fetch() -> tuple[bytes, str]:
        try:
            response = requests.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SleeperError("Could not load that image from Sleeper.") from exc
        declared = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        # The bytes travel on to a data URL, so an unhelpful header would leave the picture
        # with nothing to decode: fall back to what the file name says.
        guessed = mimetypes.guess_type(clean)[0] or "image/png"
        return response.content, declared if declared.startswith("image/") else guessed

    return _cached(url, CDN_SECONDS, fetch)


def _fetch_json(url: str, message: str):
    try:
        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SleeperError(message) from exc


def _cached(url: str, ttl: float | None, fetch):
    """Return the stored response for ``url``, otherwise fetch it and keep it for ``ttl``."""
    stored = cache_lookup(url)
    if stored is not MISS:
        return stored
    payload = fetch()
    cache_store(url, payload, ttl)
    return payload


def _nfl_state() -> dict:
    """Read the cached NFL state without going to the network, for TTL decisions only."""
    state = cache_lookup(f"{BASE_URL}/state/nfl")
    return state if isinstance(state, dict) else {}


def _current_week_of(season: str) -> int | None:
    """Return the NFL week in progress for ``season``, or None for a past season."""
    state = _nfl_state()
    if not state or str(state.get("season") or "") != str(season):
        return None
    try:
        return int(state.get("week") or state.get("display_week") or 1)
    except (TypeError, ValueError):
        return None


def _matchups_seconds(league_id: str, week: int) -> float | None:
    """Completed weeks never change; a week still being played is re-checked often."""
    league = cache_lookup(f"{BASE_URL}/league/{league_id}")
    season = str((league or {}).get("season") or "") if isinstance(league, dict) else ""
    if not season or not _nfl_state():
        # Nothing tells us the week is over yet, so keep re-checking it.
        return LIVE_SECONDS
    current_week = _current_week_of(season)
    if current_week is None:
        return None  # A season that has finished cannot change again.
    return LIVE_SECONDS if week >= current_week else None


def _bracket_seconds(league_id: str) -> float | None:
    """A bracket is fixed once its season is over; while the season runs it can gain rounds."""
    league = cache_lookup(f"{BASE_URL}/league/{league_id}")
    season = str((league or {}).get("season") or "") if isinstance(league, dict) else ""
    if not season or not _nfl_state():
        return LIVE_SECONDS
    return None if _current_week_of(season) is None else LIVE_SECONDS


def _response_seconds(url: str) -> float | None:
    """Decide how long a response may be reused, based on how stable its data is."""
    path = url.split(BASE_URL, 1)[-1] if url.startswith(BASE_URL) else url
    if path.startswith("/players/nfl"):
        return PLAYER_INDEX_SECONDS
    if path.startswith("/stats/"):
        return STATS_SECONDS
    if path.startswith("/state/"):
        return LIVE_SECONDS
    match = _MATCHUPS_PATH.match(path)
    if match:
        return _matchups_seconds(match.group("league_id"), int(match.group("week")))
    match = _BRACKET_PATH.match(path)
    if match:
        return _bracket_seconds(match.group("league_id"))
    return None


def get_json(path: str):
    url = f"{BASE_URL}{path}"
    return _cached(url, _response_seconds(url), lambda: _fetch_json(
        url, "Sleeper could not be reached. Check the username and try again."
    ))


def get_nfl_schedule(season: str) -> list[dict]:
    """Fetch Sleeper's per-game NFL schedule, including live game statuses."""
    url = f"{SCHEDULE_URL}/{season}"
    current_week = _current_week_of(str(season))
    ttl = SCHEDULE_SECONDS if current_week is not None else None
    payload = _cached(url, ttl, lambda: _fetch_json(
        url, "Sleeper's NFL schedule could not be reached."
    ))
    return payload if isinstance(payload, list) else []


def get_nfl_week_stats(season: str, week: int) -> dict[str, dict]:
    """Fetch official weekly NFL player stats, keyed by Sleeper player ID.

    Sleeper returns an object keyed by player ID whose values are the stat
    dictionaries, including ``gp`` (games played). A player is absent from a
    week he did not appear in, and team defenses use their team abbreviation
    (for example ``SF``) as the key.
    """
    url = f"{BASE_URL}/stats/nfl/regular/{season}/{int(week)}"
    payload = _cached(url, _response_seconds(url), lambda: _fetch_json(
        url, "Sleeper's weekly NFL player stats could not be reached."
    ))
    if not isinstance(payload, dict):
        return {}
    return {
        str(player_id): stats
        for player_id, stats in payload.items()
        if isinstance(stats, dict)
    }


def refresh_league(league_id: str) -> int:
    """Drop this league's cached responses so the next read fetches them again.

    The player index and weekly NFL statistics are deliberately kept: they are large,
    effectively immutable, and already refresh on their own schedule. Everything that
    describes the league — settings, rosters, users, weekly matchups — is cleared, along
    with the shared NFL state and schedule that decide how those are displayed.
    """
    league_id = str(league_id)
    return cache_drop((f"{BASE_URL}/league/{league_id}",
                       f"{BASE_URL}/state/nfl",
                       SCHEDULE_URL))


def cached_response_count() -> int:
    """Return how many Sleeper responses are currently held in memory."""
    return cache_count()


def get_user(username: str) -> dict:
    user = get_json(f"/user/{username}")
    if not user or not user.get("user_id"):
        raise SleeperError(f"Sleeper user '{username}' was not found.")
    return user


def get_user_account_leagues(username: str, season: str) -> tuple[dict, list[dict]]:
    user = get_user(username)
    leagues = get_json(f"/user/{user['user_id']}/leagues/nfl/{season}") or []
    return user, sorted(leagues, key=lambda league: league.get("name", "").lower())


def get_user_leagues(username: str, season: str) -> list[dict]:
    """Return leagues for a Sleeper username (compatibility helper)."""
    return get_user_account_leagues(username, season)[1]


def get_league_season_history(league_id: str) -> list[dict]:
    """Follow Sleeper's previous-league links to list this league's seasons."""
    history = []
    seen = set()
    current_id = str(league_id)
    # Sleeper marks "no previous season" with "0", and asking for it only returns a 404,
    # so the walk stops there instead of spending a round trip on every page.
    while current_id and current_id != "0" and current_id not in seen and len(history) < 25:
        seen.add(current_id)
        try:
            league = get_json(f"/league/{current_id}")
        except SleeperError:
            if history:
                break
            raise
        if not league:
            break
        history.append({
            "league_id": str(league.get("league_id") or current_id),
            "season": str(league.get("season") or ""),
            "name": league.get("name") or "League",
        })
        current_id = str(league.get("previous_league_id") or "")
    return sorted(history, key=lambda item: item["season"], reverse=True)


def get_player_index() -> dict:
    return get_json("/players/nfl")


def get_league_dashboard(league_id: str) -> dict:
    league = get_json(f"/league/{league_id}")
    if not league:
        raise SleeperError("That Sleeper league was not found.")
    league["regular_season_weeks"] = regular_season_week_count(league)
    league["season_end_week"] = season_end_week(league)
    # The week tabs run past the regular season into the league's playoff rounds, so anything
    # asking which weeks exist - the tabs themselves and the week a page may be opened on -
    # reads them from here rather than stopping at the regular season.
    tabs = season_week_tabs(league, get_playoff_round_count(league_id))
    league["week_tabs"] = tabs
    league["last_week"] = tabs[-1]["week"] if tabs else league["regular_season_weeks"]
    rosters = get_json(f"/league/{league_id}/rosters") or []
    users = get_json(f"/league/{league_id}/users") or []
    player_index = get_player_index()
    user_by_id = {user.get("user_id"): user for user in users}

    teams = []
    for roster in rosters:
        owner = user_by_id.get(roster.get("owner_id"), {})
        metadata = owner.get("metadata") or {}
        roster_players = []
        for player_id in roster.get("players") or []:
            player = player_index.get(str(player_id), {})
            first = player.get("first_name", "")
            last = player.get("last_name", "")
            roster_players.append({
                "player_id": str(player_id),
                "name": (f"{first} {last}".strip() or str(player_id)),
                "position": player.get("position") or "—",
                "team": player.get("team") or "FA",
                "status": player.get("injury_status") or "",
            })
        roster_players.sort(key=lambda p: (p["position"], p["name"]))
        settings = roster.get("settings") or {}
        roster_metadata = roster.get("metadata") or {}
        avatar_urls = _avatar_urls(owner.get("avatar"), metadata.get("avatar"),
                                   roster_metadata.get("avatar"))
        teams.append({
            "roster_id": roster.get("roster_id"),
            "owner_id": owner.get("user_id") or roster.get("owner_id"),
            "name": metadata.get("team_name") or owner.get("display_name") or "Team",
            "owner": owner.get("display_name") or "Unknown owner",
            "username": owner.get("username") or "",
            "record": f"{settings.get('wins', 0)}-{settings.get('losses', 0)}",
            "logo_url": avatar_urls[0] if avatar_urls else "",
            "logo_fallback_urls": avatar_urls[1:],
            "players": roster_players,
            "starters": [str(player_id) for player_id in (roster.get("starters") or [])],
        })
    teams.sort(key=lambda team: team["name"].lower())
    return {
        "league": league,
        "teams": teams,
        "roster_count": len(rosters),
        "season": league.get("season", ""),
        "roster_positions": league.get("roster_positions") or [],
        "scoring": league.get("scoring_settings") or {},
    }
