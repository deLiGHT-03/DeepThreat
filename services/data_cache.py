"""In-memory store for Sleeper responses so a page stops re-fetching what it already has.

Every page used to ask Sleeper for the same league records, rosters, weekly matchups and
season statistics, so each navigation paid the full network latency again. Responses are
kept here instead: a page only fetches what it has not loaded yet, and the Preload Data
button decides when to load everything up front.

Values are stored and handed back by reference. Callers must treat them as read-only —
nothing is copied on the way in or out, because the player index alone is over ten
megabytes. Entries carrying a TTL are re-fetched once they go stale; entries without one
stay until :func:`drop` clears them (a refresh or a league switch).
"""

from __future__ import annotations

import threading
import time

#: Returned by :func:`lookup` for a key that is absent or stale.
MISS = object()

_lock = threading.RLock()
_entries: dict[str, tuple[float, float | None, object]] = {}


def store(key: str, value, ttl: float | None = None) -> None:
    """Keep ``value`` under ``key`` for ``ttl`` seconds, or forever when ``ttl`` is None."""
    with _lock:
        _entries[key] = (time.monotonic(), ttl, value)


def lookup(key: str):
    """Return the stored value, or :data:`MISS` when it is absent or past its TTL."""
    with _lock:
        entry = _entries.get(key)
    if entry is None:
        return MISS
    stored_at, ttl, value = entry
    if ttl is not None and time.monotonic() - stored_at > ttl:
        return MISS
    return value


def drop(prefixes: tuple[str, ...] | list[str]) -> int:
    """Remove every entry whose key starts with one of ``prefixes`` and return how many."""
    with _lock:
        doomed = [key for key in _entries if key.startswith(tuple(prefixes))]
        for key in doomed:
            del _entries[key]
    return len(doomed)


def drop_expired() -> int:
    """Remove stale entries so long-expired payloads do not sit in memory."""
    now = time.monotonic()
    with _lock:
        doomed = [key for key, (stored_at, ttl, _) in _entries.items()
                  if ttl is not None and now - stored_at > ttl]
        for key in doomed:
            del _entries[key]
    return len(doomed)


def count() -> int:
    """Return how many responses are currently cached."""
    with _lock:
        return len(_entries)
