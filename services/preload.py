"""Load a league's Sleeper data in one background pass, before the pages ask for it.

Navigating between pages used to refetch the same league records, rosters and weekly
matchups every time, so every click paid full network latency. A preload fetches all of
it once — settings, every matchup week up to the end of the league's season, and the
official weekly stats behind GP — leaving the pages to read from the cache instead.

A preload runs on its own thread so the page that started it still renders immediately,
and its progress is readable through :func:`preload_status` for the toolbar indicator.

Only the season being viewed is loaded. Other seasons are left untouched until one of
them is chosen, which selects a league from that season and preloads it in turn.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.sleeper import (SleeperError, cached_response_count, get_json,
                              get_league_dashboard, get_nfl_schedule,
                              get_nfl_week_stats, get_user,
                              get_user_account_leagues, refresh_league,
                              season_end_week)

IDLE = "idle"
LOADING = "loading"
READY = "ready"
ERROR = "error"

#: A failed load should not be retried on every page view, only when asked.
FAILED_RETRY_SECONDS = 60

_lock = threading.RLock()
_states: dict[str, dict] = {}


def _update(league_id: str, **fields) -> None:
    with _lock:
        _states.setdefault(league_id, {}).update(fields)


def preload_status(league_id: str) -> dict:
    """Report what the last preload for this league did, for the page indicator."""
    with _lock:
        state = dict(_states.get(str(league_id), {}))
    return {
        "status": state.get("status", IDLE),
        "progress": round(float(state.get("progress", 0.0)), 3),
        "detail": state.get("detail", ""),
        "seconds": state.get("seconds"),
        "error": state.get("error", ""),
        "entries": state.get("entries", cached_response_count()),
    }


def start_preload(league_id: str, username: str = "", manager_id: str = "",
                  force: bool = False) -> dict:
    """Begin loading this league's data, or return the run already in progress.

    Repeat calls join the existing run rather than starting a second one. ``force``
    discards what is cached first, which is what the Preload Data button does when it
    is used to reload.
    """
    league_id = str(league_id)
    with _lock:
        state = _states.get(league_id, {})
        status = state.get("status", IDLE)
        if status == LOADING:
            return preload_status(league_id)
        if status == READY and not force:
            return preload_status(league_id)
        if (status == ERROR and not force
                and time.monotonic() - state.get("attempted_at", 0) < FAILED_RETRY_SECONDS):
            return preload_status(league_id)
        _states[league_id] = {"status": LOADING, "progress": 0.0, "detail": "Starting…",
                              "error": "", "seconds": None, "attempted_at": time.monotonic()}
    threading.Thread(target=_load, args=(league_id, username, manager_id, force),
                     name=f"preload-{league_id}", daemon=True).start()
    return preload_status(league_id)


def _warm_league_list(username: str, manager_id: str, season: str) -> None:
    """Load the league dropdown for the season in view, and nothing from any other season.

    Seasons you have not chosen are left alone, including their history: choosing a season
    navigates to a league from that season, which preloads that season in turn. The list of
    the seasons a league played in is read by the page itself, because it has to be walked
    to be rendered, so it is already loaded by the time a preload starts.
    """
    connected = username.strip()
    if not connected and manager_id:
        connected = str(get_user(manager_id).get("username") or "")
    if connected and season:
        get_user_account_leagues(connected, season)


def _completed_weeks(season: str, max_week: int, state: dict) -> int:
    """Count the weeks whose official stats are final, mirroring the player data page."""
    if str(state.get("season") or "") != str(season):
        return max_week
    try:
        current_week = int(state.get("week") or state.get("display_week") or 1)
    except (TypeError, ValueError):
        return max_week
    return min(max_week, max(0, current_week - 1))


def _load(league_id: str, username: str, manager_id: str, force: bool) -> None:
    started = time.monotonic()
    try:
        if force:
            refresh_league(league_id)
        _update(league_id, progress=0.05, detail="League settings")
        dashboard = get_league_dashboard(league_id)
        league = dashboard["league"]
        season = str(league.get("season") or "")
        # The seasonal player totals run past the regular season into the league's playoff
        # weeks, so those are warmed as well as the regular-season weeks the tabs show.
        weeks = max(1, season_end_week(league))

        _update(league_id, progress=0.1, detail="NFL state")
        state = get_json("/state/nfl") or {}
        try:
            _warm_league_list(username, manager_id, season)
            get_nfl_schedule(season)
        except SleeperError:
            pass  # Projections and the league list fall back on their own; keep loading.

        stats_weeks = range(1, _completed_weeks(season, weeks, state) + 1)
        jobs = [("matchups", week) for week in range(1, weeks + 1)]
        jobs += [("stats", week) for week in stats_weeks]
        total = len(jobs)
        _update(league_id, progress=0.15, detail=f"0 of {total}")

        def fetch(kind: str, week: int):
            if kind == "matchups":
                return get_json(f"/league/{league_id}/matchups/{week}") or []
            return get_nfl_week_stats(season, week)

        done = 0
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(fetch, kind, week) for kind, week in jobs]
            for future in as_completed(futures):
                try:
                    future.result()
                except SleeperError:
                    pass  # The pages report their own failed weeks; keep loading the rest.
                done += 1
                _update(league_id, progress=0.15 + 0.85 * done / total,
                        detail=f"{done} of {total}")

        _update(league_id, status=READY, progress=1.0,
                detail=f"{total} loads", seconds=round(time.monotonic() - started, 2),
                entries=cached_response_count(), error="")
    except Exception as exc:  # Report the failure to the toolbar instead of losing the thread.
        _update(league_id, status=ERROR, error=str(exc) or exc.__class__.__name__,
                detail="", seconds=round(time.monotonic() - started, 2))
