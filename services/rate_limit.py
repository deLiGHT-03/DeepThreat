"""Request limits, so a served copy of the app cannot be used up by whoever finds the address.

Run from source the app answers one person on one machine and a limit would be a solution to a
problem that does not exist. Served from a public URL the same app answers anyone who has the
link: a crawler walks every page it can guess, a script holds the process busy reading Sleeper
for a league nobody is watching, and one visitor with a loop in it spends the Gemini quota that
pays for Ask AI.

Every limit is kept twice, and the pair is the point. A *visitor* limit counts one address's
requests, so a noisy visitor is slowed down without anyone else noticing. A *total* limit counts
everybody's together, and it is the one that still holds when the visitor cannot be identified
-- a program rotating addresses, or a proxy whose headers are not trusted -- because it does not
depend on that identification at all.

The counts live in this process, in memory: a timestamp per request, dropped once it is older
than that limit's window (a minute, an hour, a day for Ask AI), which is also exactly how long
an address is remembered. Nothing is written down and nothing is shared between processes; this
is a small app on one worker, and a limit that survived a restart would not be worth a database.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque

#: The most identities one limit remembers before it starts forgetting the quietest. A visitor
#: limit is per address, so a program that invents addresses could otherwise grow this map
#: without end; the limit that actually stops such a program is the total one.
_MAX_IDENTITIES = 2048

#: How long a throttled visitor is left out of the log for, in seconds. The log line is what
#: tells whoever runs the app that it is being walked or looped, so it must not itself become
#: the flood.
_LOG_PAUSE = 60.0


def _count(variable: str, default: int) -> int:
    """A limit's count: this default, or the environment's number when it is a usable one.

    Negative means no limit at all, and zero means nothing is allowed -- both are deliberate,
    because a limit is also how the feature is switched off.
    """
    raw = (os.environ.get(variable) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


#: Page renders one address may ask for in a minute. A person reading arrives nowhere near this;
#: the layout checker walks every page mode in about that long and stays inside it; a walker
#: looking for anything worth keeping is what it is aimed at.
PAGE_PER_MINUTE = _count("DEEPTHREAT_PAGE_PER_MINUTE", 120)
#: Page renders everybody together may ask for in a minute. Every one of these can read Sleeper
#: and render a league, so this is the ceiling that does not care who is asking.
PAGE_TOTAL_PER_MINUTE = _count("DEEPTHREAT_PAGE_TOTAL_PER_MINUTE", 400)
#: JSON reads one address may ask for in a minute. The Preload button watches its own load every
#: 600ms and the week check runs every five minutes, so this is set by the button, not by a page.
DATA_PER_MINUTE = _count("DEEPTHREAT_DATA_PER_MINUTE", 180)
#: CDN images one address may relay in a minute. A snapshot embeds a roster's worth of them.
MEDIA_PER_MINUTE = _count("DEEPTHREAT_MEDIA_PER_MINUTE", 300)
#: Preloads one address may start in a minute. Each one reads a whole league from Sleeper, so it
#: is the most expensive single request the app answers.
PRELOAD_PER_MINUTE = _count("DEEPTHREAT_PRELOAD_PER_MINUTE", 12)
#: Questions one address may ask in an hour, and in a day. One question is up to ten model calls
#: plus the lookups they ask for, which is what makes this the tightest limit in the file.
ASK_PER_HOUR = _count("DEEPTHREAT_ASK_PER_HOUR", 6)
ASK_PER_DAY = _count("DEEPTHREAT_ASK_PER_DAY", 20)
#: Questions everybody together may ask in an hour, and in a day. The day is the one that keeps a
#: free Gemini key's quota whole: a question the key cannot answer spends it anyway.
ASK_TOTAL_PER_HOUR = _count("DEEPTHREAT_ASK_TOTAL_PER_HOUR", 15)
ASK_TOTAL_PER_DAY = _count("DEEPTHREAT_ASK_TOTAL_PER_DAY", 100)

#: Trust the first entry of ``X-Forwarded-For`` as the visitor's address. Off by default because a
#: header is request input: a proxy that appends its own values leaves a client's invented ones
#: in front of them, so anyone can hand out fresh identities and walk past the visitor limits.
#: Turn it on only where the proxy replaces the header with the address it saw the request come
#: from, and where nothing else can reach the app directly.
TRUST_FORWARDED_FOR = bool((os.environ.get("DEEPTHREAT_TRUST_FORWARDED_FOR") or "").strip())

#: Which limits apply to a bucket: (visitor count, window in seconds, everybody count). A visitor
#: count below zero is a rule about everybody only, and a total of None is a rule about one
#: visitor only. A request is allowed only when every rule for its bucket allows it, and each
#: rule counts in its own window -- an hour and a day are two counts of the same questions, not
#: one count written down twice.
_RULES: dict[str, tuple[tuple[int, float, int | None], ...]] = {
    "page": ((PAGE_PER_MINUTE, 60.0, PAGE_TOTAL_PER_MINUTE),),
    "data": ((DATA_PER_MINUTE, 60.0, None),),
    "media": ((MEDIA_PER_MINUTE, 60.0, None),),
    "preload": ((PRELOAD_PER_MINUTE, 60.0, None),),
    "ask": (
        (ASK_PER_HOUR, 3600.0, ASK_TOTAL_PER_HOUR),
        (ASK_PER_DAY, 86400.0, None),
        (-1, 86400.0, ASK_TOTAL_PER_DAY),
    ),
}

_lock = threading.RLock()
_hits: dict[tuple[str, str], deque[float]] = {}
_totals: dict[str, deque[float]] = {}
_logged: dict[tuple[str, str], float] = {}


def visitor(headers, peer: str) -> str:
    """Which address a request is counted against.

    Cloudflare sits in front of the platform this is written for and sets ``CF-Connecting-IP``
    to the visitor's address, replacing anything the visitor sent under that name; it is the only
    address in the chain that is a fact rather than an assertion, so it is read first.

    ``X-Forwarded-For`` is deliberately *not* read by default. A proxy that appends rather than
    replaces leaves an invented value in front of the real one, and a header that can be made up
    is worse than no identity at all: it would turn every visitor limit into a limit on honest
    visitors. With nothing better to go on, the address the socket came from is used -- which is
    the proxy itself, so everybody shares one bucket and the total limits do the work. That is
    the safe way round to be wrong.
    """
    for header in ("CF-Connecting-IP", "True-Client-IP"):
        value = (headers.get(header) or "").strip()
        if value:
            return value
    if TRUST_FORWARDED_FOR:
        forwarded = (headers.get("X-Forwarded-For") or "").split(",")
        own = forwarded[0].strip() if forwarded else ""
        if own:
            return own
    return (peer or "").strip() or "unknown"


def describe_wait(seconds: int) -> str:
    """A wait in words, for the sentence shown to whoever was throttled."""
    if seconds >= 120:
        return f"about {max(1, round(seconds / 60))} minutes"
    if seconds > 60:
        return "about a minute"
    return f"about {seconds} seconds"


def _evict_oldest(store: dict) -> None:
    """Forget the identity least recently heard from, so a map of them cannot grow forever."""
    if not store:
        return
    quietest = min(store, key=lambda key: store[key][-1] if store[key] else 0.0)
    del store[quietest]


def _record(store: dict, key, count: int, window: float) -> tuple[bool, int]:
    """Count one request against one limit, and say whether it is allowed.

    The second half of the answer is how long the caller has to wait: the age of the oldest
    request still inside the window is exactly how long it takes for room to appear. A rejected
    request is not counted, so being turned away never extends the wait it is told about.
    """
    now = time.monotonic()
    with _lock:
        if key not in store and len(store) >= _MAX_IDENTITIES:
            _evict_oldest(store)
        hits = store.setdefault(key, deque())
        while hits and now - hits[0] >= window:
            hits.popleft()
        if count < 0:
            return True, 0
        if len(hits) >= count:
            oldest = hits[0] if hits else now
            return False, max(1, int(math.ceil(window - (now - oldest))))
        hits.append(now)
    return True, 0


def check(bucket: str, identity: str) -> tuple[bool, int]:
    """Count one request in ``bucket`` from one visitor. Returns (allowed, seconds to wait).

    A visitor is counted before anybody else is, so a request that is turned away for being one
    person's twentieth in a minute does not also spend everybody else's allowance.
    """
    rules = _RULES.get(bucket)
    if not rules:
        return True, 0
    for allowed_for_one, window, _ in rules:
        allowed, wait = _record(_hits, (bucket, identity, window), allowed_for_one, window)
        if not allowed:
            return False, wait
    for _, window, allowed_together in rules:
        if allowed_together is None:
            continue
        allowed, wait = _record(_totals, (bucket, window), allowed_together, window)
        if not allowed:
            return False, wait
    return True, 0


def first_block(bucket: str, identity: str) -> bool:
    """Whether this refusal is the first from this visitor in a while, so it earns a log line."""
    now = time.monotonic()
    key = (bucket, identity)
    with _lock:
        for stale in [k for k, at in _logged.items() if now - at >= _LOG_PAUSE]:
            del _logged[stale]
        if key in _logged:
            return False
        _logged[key] = now
    return True
