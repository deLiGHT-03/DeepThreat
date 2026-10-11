"""Write the season summaries a deployed copy rebuilds its History page from.

The History page reads a league's whole past -- every week of matchups and every week of
transactions of every season it has played -- and adds it up. Read cold that is a few hundred
upstream responses; the summaries kept by ``services/history_digest.py`` replace them with a row
of games, totals, moves and rookies per finished season, and that is what makes the page cheap on
a copy that holds nothing. On a *hosted* copy nothing is what it holds: the process is new every
time it wakes, and on Render's free plan the filesystem is thrown away with it, so the only place
a summary can survive to be found is the image itself. That is what this writes.

    python tools/build_history_snapshot.py                    # the league in your settings
    python tools/build_history_snapshot.py --league-id 1313... --username someone
    python tools/build_history_snapshot.py --out ../scratch/history

It does its own checking, because the risk in a snapshot is not that it is slow but that it is
*stale*: it builds the page cold (reads and wall clock reported), then throws away everything held
in memory and builds the same page again -- with the upstream cache dropped, so the second build
has to come off the snapshot -- and refuses to be happy unless the two pages are byte-identical
and the second one read less. Commit the directory it writes and the deployed copy gets the
second column.

Only finished seasons are written. A season still being played changes every week, so its summary
would be wrong by the next one, and the page reads those weeks itself.

Exit codes: 0 written and verified, 1 no league to build for, 2 written but not verified.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The report uses · and – for width, so do not let a cp1252 console mangle it.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from app import create_app  # noqa: E402
from services import data_cache, history, history_digest, local_settings  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "history"


def _build(app, league_id: str, username: str, manager_id: str, season: str = "") -> dict:
    """Render the page cold and return what it cost and what it said."""
    data_cache.drop(("https://",))
    reads_before = data_cache.load_count()
    started = time.perf_counter()
    summary = history.get_history_summary(league_id, username, manager_id=manager_id,
                                          season=season)
    wall = time.perf_counter() - started
    body = app.test_client().get(f"/league/{league_id}/history",
                                query_string={"username": username, "manager_id": manager_id,
                                              **({"season": season} if season else {})}).data
    return {"wall": wall, "reads": data_cache.load_count() - reads_before, "body": body,
            "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league-id", default="", help="default: the league in your settings")
    parser.add_argument("--username", default="", help="default: the manager in your settings")
    parser.add_argument("--manager-id", default="", help="default: the id in your settings")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where the summaries are written")
    args = parser.parse_args()

    saved = local_settings.load_connection()
    league_id = args.league_id or str(saved.get("league_id") or "")
    username = args.username or str(saved.get("username") or "")
    manager_id = args.manager_id or str(saved.get("manager_id") or "")
    if not league_id:
        print("no league: pass --league-id, or connect a league in the app first", file=sys.stderr)
        return 1

    out = Path(args.out).resolve()
    history_digest.set_cache_dir(out)
    app = create_app()

    print(f"league {league_id}, manager {username or '(not given)'}")
    print(f"summaries will be written to {out}\n")

    fresh = _build(app, league_id, username, manager_id)
    plan = history.season_plan(league_id)
    written = history_digest.stats()["saved"]
    kept = [entry for entry in plan if not entry["in_progress"]]
    print(f"built cold:     {fresh['wall']:>5.2f}s  {fresh['reads']:>3} upstream reads  "
          f"{len(fresh['body']):>6} page bytes")
    print(f"                {len(kept)} finished season(s) of {len(plan)} in the chain, "
          f"{written} summar{'y' if written == 1 else 'ies'} written")

    # Everything held in memory goes, and so does every Sleeper response: what the second build
    # reads now can only come off the snapshot on disk.
    history_digest.forget()
    again = _build(app, league_id, username, manager_id)
    stats = history_digest.stats()
    print(f"rebuilt from it: {again['wall']:>4.2f}s  {again['reads']:>3} upstream reads  "
          f"{len(again['body']):>6} page bytes  ({stats['file']} from file, "
          f"{stats['bundle']} from the shipped copy, {stats['memory']} from memory)")

    files = sorted(out.glob("*.json"))
    total = sum(path.stat().st_size for path in files)
    print(f"\n{len(files)} file(s) in {out}, {total / 1024:.0f} KB in all:")
    for path in files:
        season = next((entry["season"] for entry in plan
                       if entry["league_id"] == path.stem), "?")
        print(f"    {path.name:<26} {path.stat().st_size / 1024:>7.1f} KB   season {season}")

    saved_reads = fresh["reads"] - again["reads"]
    print(f"\nthe rebuilt page read {saved_reads} fewer responses "
          f"({fresh['reads']} -> {again['reads']}, "
          f"{saved_reads / max(fresh['reads'], 1) * 100:.0f}% fewer)")
    if again["body"] != fresh["body"]:
        print("the two pages are NOT identical -- do not ship this", file=sys.stderr)
        return 2
    print("the two pages are byte-identical")
    if again["reads"] >= fresh["reads"]:
        print("the second build read no less than the first: nothing was reused", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
