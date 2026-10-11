"""In-memory store for Sleeper responses so a page stops re-fetching what it already has.

Every page used to ask Sleeper for the same league records, rosters, weekly matchups and
season statistics, so each navigation paid the full network latency again. Responses are
kept here instead: a page only fetches what it has not loaded yet, and the Preload Data
button decides when to load everything up front. Callers that arrive while a response is
already being fetched share that one fetch rather than starting their own -- see
:func:`get_or_load`.

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

#: How many responses this process has fetched from upstream since it started. Counted where a
#: fetch is decided rather than where one is asked for, so two callers that share a fetch add one
#: between them: the number is the work actually done, not the number of callers wanting it. That
#: is what lets duplicated work be seen from outside a served copy, where nothing can be attached
#: to the process — a page read by two visitors at once reports the same number as the page read
#: by one, and a number that doubles says the sharing did not happen.
_loads = 0


def _count_load() -> None:
    """Record a fetch as this process's own: counted as it starts, so a failed one counts too."""
    global _loads
    with _lock:
        _loads += 1


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


#: One lock per key that somebody is loading right now, with a count of who is using it. Kept
#: only while it is in use: the last one out removes both entries, so a run of keys nobody asks
#: for twice cannot grow this map without end.
_locks: dict[str, threading.RLock] = {}
_users: dict[str, int] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.RLock:
    """Return this key's lock, counting the caller as a user of it."""
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        _users[key] = _users.get(key, 0) + 1
        return lock


def _release(key: str) -> None:
    """Stop counting one caller as a user, forgetting the lock once nobody is left."""
    with _locks_guard:
        remaining = _users.get(key, 1) - 1
        if remaining > 0:
            _users[key] = remaining
            return
        _users.pop(key, None)
        _locks.pop(key, None)


def get_or_load(key: str, ttl: float | None, load):
    """Return the stored value, loading it **once** however many callers are asking for it.

    A lookup followed by a fetch is not enough on its own: two callers that miss the same key at
    the same moment both then fetch it, because neither has stored anything yet. That is not a
    rare race in an app served to several people at once -- three simultaneous views of one
    league turned its seventeen upstream reads into fifty-one, and each of the extra ones is a
    whole league or, for the player index, fourteen megabytes. Waiting for the caller that is
    already loading replaces all of that with one read that everybody shares.

    The lock is re-entrant because a load can be nested: a page's own read may ask for something
    that read depends on, and a plain lock would deadlock the thread that did it. ``load`` is
    called with no locks held other than this key's, so a load that reads a different key cannot
    be blocked by, or block, another one.
    """
    value = lookup(key)
    if value is not MISS:
        return value
    lock = _lock_for(key)
    try:
        with lock:
            value = lookup(key)  # the caller we waited for may have just stored it
            if value is not MISS:
                return value
            _count_load()
            value = load()
            store(key, value, ttl)
            return value
    finally:
        _release(key)


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


def load_count() -> int:
    """Return how many responses this process has fetched upstream since it started.

    Read alongside :func:`count`, this is how much work the cache has saved rather than what it
    holds: callers answering from it add nothing here.
    """
    with _lock:
        return _loads
