"""What each finished season already added up to, kept so a cold page does not add it up again.

The History page is the one page that reads a league's whole past: every week of matchups and
every week of transactions, for every season in the chain, before it can say who won the most or
which score was the highest. Read cold -- a process holding nothing, which is what a restarted
desktop app and a woken instance both are -- that is a few hundred upstream responses, and every
one of them parsed and added up before a single number reaches the page.

None of that arithmetic changes once a season is over, so it is done once and kept. What is kept
is derived, not copied: the rows the season produced -- its games, team totals, streaks, moves,
waiver pickups, draft and rookies -- with no upstream payload in them, because a kept payload
would only have to be parsed and added up again, which is the work this exists to avoid.

The summary is deliberately *not* told whether a row is the reader's: a row carries the roster it
belongs to, so one league's summary is the same summary for everybody looking at it, and the page
marks its own team when it draws it.

Names are the exception, and a deliberate one: a row keeps the manager or player it was read
with, so a summary made while a manager was in the league says his handle even if he has since
left it. The page still reads the current league, so the career table and the record cards are
named from today's accounts; it is the rows of a season already played that keep the name they
were played under, which is a small price for not re-reading the season to find a rename.

Three places are looked in, cheapest first:

* this process's memory, so a page opened twice adds up once;
* ``%APPDATA%\\DeepThreat\\history`` -- a file, on a copy allowed to keep one (see
  :func:`services.local_settings.keeps_files`), which is what survives a desktop app restart;
* ``data/history`` beside the app, which is where the snapshot a *deployment* ships lives.

That last one is the one that matters on a host. A hosted instance sleeps and wakes into an
empty process, and on Render's free plan its filesystem is thrown away too, so a summary written
during a previous life is not there to be found; the file in the image is. Keeping the snapshot
current is ``python tools/build_history_snapshot.py``, which is also where the sizes, the reads
saved and the page-identity check are reported.

A summary is only ever used for a season that has *finished* -- every week it covers is scored,
which is the point at which its numbers stop moving -- and only while the facts it was built from
still agree: the version of this code, the league and season it names, and the shape of the season
(its regular-season length, the last week it covers, the weeks it had scored, and where its
playoffs begin). A season that has since scored another week, or whose settings were changed,
fails that check and is read again from Sleeper. Splitting hairs about what may have changed is
why the check is on the season's shape rather than on a timestamp: a finished season is finished,
and the escape hatch for the rare case where Sleeper corrects one after the fact is to delete the
summary (or raise :data:`DIGEST_VERSION`).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from services.local_settings import keeps_files
from services.sleeper import playoff_week_start

#: The shape of the rows written below. Bumped when they change, which is what stops an older
#: build's summary from being read back as if it were this one's.
DIGEST_VERSION = 1

#: The summaries shipped with the app: written by ``tools/build_history_snapshot.py`` and
#: committed, so a deployment carries them in its image. A served copy has this and nothing else.
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data" / "history"

_memory: dict[str, dict] = {}
_lock = threading.RLock()
_chosen_dir: Path | None = None
_counts = {"memory": 0, "file": 0, "bundle": 0, "built": 0, "saved": 0}


def stats() -> dict:
    """What this process has done with summaries: where each one came from, and how many it built.

    Counted since the process started, like the app's upstream read count, so a copy that has just
    restarted reports zeroes -- which is both how a served copy can be asked what it is doing and
    how a restart can be seen from outside.
    """
    with _lock:
        return dict(_counts)


def forget() -> None:
    """Drop what is held in memory, so the next read has to come off a disk or a snapshot."""
    with _lock:
        _memory.clear()


def set_cache_dir(path) -> Path:
    """Write summaries under ``path`` instead of this copy's own data directory.

    The snapshot tool's hook, and the only caller: it points the cache at the repository's own
    data directory, so the summaries a deployment ships are written by the same code that writes
    them anywhere else rather than by a second implementation of it.
    """
    global _chosen_dir
    with _lock:
        _chosen_dir = Path(path)
        return _chosen_dir


def cache_dir() -> Path:
    """Where written summaries are kept when this copy is allowed to keep a file at all."""
    with _lock:
        chosen = _chosen_dir
    if chosen is not None:
        return chosen
    app_data = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    return root / "DeepThreat" / "history"


def _path_in(directory: Path, season_id: str) -> Path:
    return directory / f"{season_id}.json"


def _shape(entry: dict) -> dict:
    """The facts about a season that a summary has to still agree with to be worth trusting.

    All four come from the season's own league record, which the page has already read by the
    time it asks: a season that has scored a further week, or whose regular season or playoff
    start was changed, answers differently here and is read again rather than reused.
    """
    league = entry.get("league") or {}
    return {
        "regular": int(entry.get("regular") or 0),
        "end": int(entry.get("end") or 0),
        "played": int(entry.get("played") or 0),
        "playoff_start": int(playoff_week_start(league) or 0),
    }


def _usable(kept, entry: dict) -> bool:
    """Whether a summary that was found describes this season as it stands now."""
    if not isinstance(kept, dict) or kept.get("version") != DIGEST_VERSION:
        return False
    if str(kept.get("league_id") or "") != str(entry.get("league_id") or ""):
        return False
    if str(kept.get("season") or "") != str(entry.get("season") or ""):
        return False
    return kept.get("shape") == _shape(entry)


def _read_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load(entry: dict) -> dict | None:
    """Return the summary kept for one season of a chain, or None when there is none to use.

    ``entry`` is a season plan entry (:func:`services.history.season_plan`), which is what says
    which season this is and what shape it currently has. A season still being played never
    returns one, however recently it was read: its rows are moving.
    """
    if entry.get("in_progress"):
        return None
    season_id = str(entry.get("league_id") or "")
    if not season_id:
        return None
    with _lock:
        kept = _memory.get(season_id)
        if kept is not None:
            if _usable(kept, entry):
                _counts["memory"] += 1
                return kept
            del _memory[season_id]
    for directory, source in ((cache_dir(), "file"), (BUNDLED_DIR, "bundle")):
        kept = _read_file(_path_in(directory, season_id))
        if kept is not None and _usable(kept, entry):
            with _lock:
                _memory[season_id] = kept
                _counts[source] += 1
            return kept
    return None


def save(entry: dict, summary: dict) -> Path | None:
    """Keep a summary that was just built, and return the file it went into if any.

    A season that is still being played is not kept: what it adds up to will be different next
    week. A copy that keeps no files keeps it in memory only -- which still saves the work when
    the same league is opened again by the same process -- and everywhere else it is written
    beside the app's other data, where it survives the restart that would otherwise read the
    whole chain again. A write that fails is not an error: the summary is in memory either way,
    and a read-only filesystem is a documented way to run this app.
    """
    season_id = str(entry.get("league_id") or "")
    if not season_id:
        return None
    summary = {**summary, "version": DIGEST_VERSION, "shape": _shape(entry),
               "built": int(time.time())}
    with _lock:
        _counts["built"] += 1
        if entry.get("in_progress"):
            return None
        _memory[season_id] = summary
    if not keeps_files():
        return None
    directory = cache_dir()
    path = _path_in(directory, season_id)
    temporary = directory / f"{path.name}.{os.getpid()}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(summary, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    with _lock:
        _counts["saved"] += 1
    return path
