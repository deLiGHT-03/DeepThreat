"""One player's season at a glance, for the hover player card.

The card answers "how has this player scored, and against whom" without leaving the page
you are already on. Weekly fantasy points come from the same league matchup records every
other page reads, so a card always agrees with the numbers around it, and each week's NFL
opponent comes from Sleeper's schedule for the league's season.

The whole regular season is listed, so the card keeps showing a schedule before a week has
been played: a week with no score shows a dash, and a week the player's NFL team does not
play is a bye. A week only counts as a game when Sleeper recorded him taking the field, so
a bye or a week he sat out neither shows a score nor joins the season total.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services.matchups import _number, _player_display_names, _position
from services.sleeper import (SleeperError, get_json, get_league_dashboard,
                              get_nfl_schedule, get_nfl_week_stats, get_player_index,
                              regular_season_week_count)

#: A card is a small panel: one slow week must not hold the rest of it up.
WEEK_WORKERS = 6


def _scoring_weeks(league: dict) -> int:
    """Return the weeks that can hold a score: the completed ones and the live one."""
    max_week = regular_season_week_count(league)
    try:
        state = get_json("/state/nfl") or {}
        if str(state.get("season")) == str(league.get("season")):
            current = int(state.get("week") or state.get("display_week") or 1)
            return min(max_week, max(1, current))
    except (SleeperError, TypeError, ValueError):
        pass
    return max_week


def _points_by_player(league_id: str, weeks: int) -> dict[str, dict[int, float]]:
    """Map each player who was on a roster to the league points he scored every week."""
    def fetch(week: int) -> tuple[int, list[dict]]:
        try:
            rows = get_json(f"/league/{league_id}/matchups/{week}") or []
        except SleeperError:
            return week, []
        return week, rows if isinstance(rows, list) else []

    with ThreadPoolExecutor(max_workers=WEEK_WORKERS) as executor:
        results = list(executor.map(fetch, range(1, weeks + 1)))

    scores: dict[str, dict[int, float]] = {}
    for week, rows in results:
        for row in rows:
            recorded = row.get("players_points") or {}
            if not isinstance(recorded, dict):
                continue
            for player_id, value in recorded.items():
                score = _number(value)
                if score is not None:
                    scores.setdefault(str(player_id), {})[week] = score
    return scores


def _appearances(season: str, weeks: list[int]) -> dict[int, set[str] | None]:
    """Return who Sleeper recorded as playing each week, and None where that is unknown.

    A roster keeps a score for a player whether or not he took the field — a bye week and
    an injury both record zero — so the official weekly stats are what decide whether a
    week counts as a game at all.
    """
    def fetch(week: int) -> tuple[int, set[str] | None]:
        try:
            stats = get_nfl_week_stats(season, week)
        except SleeperError:
            return week, None
        return week, {player_id for player_id, values in stats.items()
                      if isinstance(values, dict) and (_number(values.get("gp")) or 0.0) > 0}

    with ThreadPoolExecutor(max_workers=WEEK_WORKERS) as executor:
        return dict(executor.map(fetch, weeks))


def _opponents(season: str) -> dict[str, dict[int, dict]]:
    """Map each NFL team to the opponent it plays each week, and where."""
    try:
        schedule = get_nfl_schedule(str(season))
    except SleeperError:
        return {}
    opponents: dict[str, dict[int, dict]] = {}
    for game in schedule:
        try:
            week = int(game.get("week"))
        except (TypeError, ValueError):
            continue
        home = str(game.get("home") or "").upper()
        away = str(game.get("away") or "").upper()
        if home and away:
            opponents.setdefault(home, {})[week] = {"opponent": away, "home": True}
            opponents.setdefault(away, {})[week] = {"opponent": home, "home": False}
    return opponents


def _headshot_urls(player_id: str, player: dict) -> list[str]:
    """Return the portrait to try first and the URLs to fall back on.

    Sleeper's CDN serves a headshot per player ID and a team logo per NFL team, which is
    what a defense uses because Sleeper keys a defense by its team abbreviation.
    """
    if _position(player) == "DEF":
        team = str(player.get("team") or player_id).strip().lower()
        return [f"https://sleepercdn.com/images/team_logos/nfl/{team}.png"] if team else []
    urls = [f"https://sleepercdn.com/content/nfl/players/thumb/{player_id}.jpg",
            f"https://sleepercdn.com/content/nfl/players/{player_id}.jpg"]
    espn_id = player.get("espn_id")
    if espn_id:
        urls.append(f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png")
    return urls


def get_player_card(league_id: str, player_id: str) -> dict:
    """Build one player's card: his schedule, his weekly points, and his season total."""
    dashboard = get_league_dashboard(league_id)
    league = dashboard["league"]
    player_id = str(player_id).strip()
    player = get_player_index().get(player_id)
    if not player:
        raise SleeperError("That player is not in Sleeper's player list.")

    season = str(league.get("season") or "")
    max_week = regular_season_week_count(league)
    scoring_weeks = _scoring_weeks(league)
    weekly = _points_by_player(league_id, scoring_weeks).get(player_id, {})
    # Only weeks that recorded a score are worth asking about, and a week whose stats are
    # unavailable keeps its recorded score rather than losing it.
    appeared = _appearances(season, sorted(weekly))
    team_weeks = _opponents(season)
    nfl_team = str(player.get("team") or "").upper()
    scheduled = team_weeks.get(nfl_team, {}) if nfl_team else {}
    name, _ = _player_display_names(player, player_id)
    headshots = _headshot_urls(player_id, player)
    fantasy_team = next((team for team in dashboard["teams"]
                         if any(row["player_id"] == player_id for row in team["players"])), None)

    weeks = []
    for week in range(1, max_week + 1):
        game = scheduled.get(week)
        points = weekly.get(week)
        played = appeared.get(week)
        if points is not None and played is not None and player_id not in played:
            points = None  # on a roster that week, but he never took the field
        weeks.append({
            "week": week,
            "opponent": game["opponent"] if game else None,
            "home": bool(game and game["home"]),
            "points": points,
            # A card with no schedule at all must not call every week a bye.
            "scheduled": bool(game) or bool(scheduled),
        })
    played = [week for week in weeks if week["points"] is not None]
    total = sum(week["points"] for week in played)
    return {
        "id": player_id,
        "name": name,
        "position": _position(player) or "—",
        "nfl_team": nfl_team or "FA",
        "season": season,
        "headshot_url": headshots[0] if headshots else "",
        "headshot_fallbacks": headshots[1:],
        "weeks": weeks,
        "games": len(played),
        "total_points": round(total, 2),
        "points_per_game": round(total / len(played), 2) if played else None,
        "fantasy_team": fantasy_team["name"] if fantasy_team else "",
    }
