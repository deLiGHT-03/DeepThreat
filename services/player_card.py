"""One player's season at a glance, for the hover player card.

The card answers "how has this player scored, and against whom" without leaving the page
you are already on. Weekly fantasy points come from the same league matchup records every
other page reads, so a card always agrees with the numbers around it, and each week's NFL
opponent comes from Sleeper's schedule for the league's season.

The league's whole season is listed, its playoff rounds included, so the card keeps showing a
schedule before a week has been played: a week with no score shows a dash, and a week the
player's NFL team does not play is a bye. A week only counts as a game when Sleeper recorded
him taking the field, so a bye or a week he sat out neither shows a score nor joins the
season total.

The weeks counted are the ones the Player Data Season tables count: every week the league has
scored, and the week in progress as far as it has been played, so nothing a player has already
put on the board during a live week is missing from his rate -- and so the rate on a card reads
the same as the rate in the tables it is opened from.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from services.matchups import _number, _player_display_names, _position
from services.sleeper import (SleeperError, get_json, get_league_dashboard,
                              get_nfl_schedule, get_nfl_week_stats, get_player_index,
                              season_end_week, season_week_count)

#: A card is a small panel: one slow week must not hold the rest of it up.
WEEK_WORKERS = 6


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
    # The league's season, not just its regular season: a card's listed weeks are the weeks a
    # finished season was played in, so they add up to the season total printed underneath them.
    max_week = season_end_week(league)
    # The same window the Player Data Season tables count, so a card's rate is the rate in
    # the tables it is opened from (see services.sleeper.season_week_count).
    scoring_weeks = season_week_count(league)
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
            # A week the league has not reached yet, as opposed to one it has scored and he had
            # no part in: only the first is still to come, and only the first is dimmed.
            "pending": week > scoring_weeks,
        })
    played = [week for week in weeks if week["points"] is not None]
    total = sum(week["points"] for week in played)
    # Every figure is rounded to the one decimal it is printed at, here, rather than by whoever
    # prints it. The same numbers are printed in the Player Data tables, and the two would round
    # an exact half-tenth apart if each did its own: a rate of 5.25 reads 5.2 in those tables
    # (Python's "%.1f" sends an exact half to the even digit) and would read 5.3 on a card whose
    # own formatter rounds halves up. The totals are sums of the unrounded weekly points, so a
    # card's total is still the season's total and not the sum of its printed rows.
    for week in weeks:
        if week["points"] is not None:
            week["points"] = round(week["points"], 1)
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
        "total_points": round(total, 1),
        "points_per_game": round(total / len(played), 1) if played else None,
        "fantasy_team": fantasy_team["name"] if fantasy_team else "",
    }
