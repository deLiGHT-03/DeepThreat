"""Run every check worth running before a commit, and answer with one verdict.

The checks in this repository are separate tools on purpose -- each asks something different of a
different place -- but that means a commit can break any of six things, and remembering to run six
commands (two of which want a browser and a live league) is how one of them quietly stops being run
at all. This runs them in the order that fails fastest and cheapest first:

* **syntax** -- every Python file in the project compiles, before anything tries to import it;
* **app** -- the app builds: every template compiles, and the notebook's releases are a list this
  app can serve, which is checked at startup because a release filed under a kind that is not one
  of :data:`services.changelog.KINDS` stops the app rather than drawing a blank chip;
* **javascript** -- every script in ``static/`` parses, asked only where ``node`` is installed,
  since the app itself needs no Node anywhere;
* **stamps** -- every style sheet and script is fetched at a URL made of the file's own bytes,
  which is what the year of caching they carry is safe on (``tools/check_asset_stamps.py``);
* **slots** -- the lineup slot assignment reconciles with Sleeper's own weekly figures, the same
  check CI runs and ``render.yaml`` waits for (``tools/check_lineup_slots.py``);
* **layout** -- every page mode the app links to is measured in a real browser at nine widths,
  for a logo nothing is drawn through, a page no wider than its window, and a narrow toolbar that
  reads its controls on one line (``tools/check_layout.py``).

    python tools/preflight.py                       # everything, against the saved connection
    python tools/preflight.py --fast                # skip the browser pass
    python tools/preflight.py --only stamps,layout  # only these, in the order they run
    python tools/preflight.py --skip slots          # everything but these
    python tools/preflight.py --league-id 1313... --username someone --manager-id 376...
    python tools/preflight.py --base-url http://127.0.0.1:5099
    python tools/preflight.py --json preflight.json

The two checks that read a league take ``--league-id`` and friends the way the app takes a
connection: from the saved settings unless they are named here, which is also why a machine with no
connection saved reports those two as checks that could not be asked rather than as passes.

What is deliberately not run: ``tools/check_deployed_revisions.py``. It asks the hosts which
revision they are serving, which is a question about a deploy -- worth asking after a push, and
unanswerable about a commit that has not been pushed. ``tools/postflight.py`` is the sibling on the
other side of a push: it asks the copies whether they are serving what was pushed, which is the
revision they report and the page and the bytes they hand over.

Exit codes: 0 every check that was asked ran and passed, 1 a check failed, 2 nothing failed but
something could not be asked -- a check that ended 2 for its own reasons (no league to read, no
browser, an upstream that did not answer), or one that did not finish inside the time it was given.
A check left out because you said to (``--fast``, ``--skip``, ``--only``) is named in the verdict
and does not decide the exit code, because leaving it out was the request.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Directories that hold no project source. Walked past rather than filtered afterwards, so a
#: virtualenv with thousands of files costs this nothing.
SKIP_DIRECTORIES = {
    ".git", ".venv", "venv", ".build-venv", ".build-tools", "__pycache__", "build", "dist",
    "release", "node_modules",
}

#: How long any one check may take. The two that read a whole league over the network are given
#: minutes, because a cold league read is minutes of work; the rest get enough to start a server
#: (and, for the layout check, a browser) and not much more. A check that overruns is reported as
#: one that could not be asked, never as one that passed.
TIMEOUTS = {"syntax": 180, "app": 180, "javascript": 180, "stamps": 300, "slots": 1500,
            "layout": 1500}

#: What each check is for, printed beside its name so a failure reads as the promise it broke
#: rather than as a file name.
TITLES = {
    "syntax": "every Python file compiles",
    "app": "the app builds: every template compiles and the notebook's releases are valid",
    "javascript": "every script in static/ parses",
    "stamps": "every style sheet and script is fetched at a URL made of its own bytes",
    "slots": "the lineup slots reconcile with Sleeper's own weekly figures",
    "layout": "no page runs off its width and nothing is drawn through the brand logo",
}

#: How many lines of a failed check's own output to show. Enough for a traceback's point and the
#: line it points at; the whole of it is what the check prints when it is run by hand.
TAIL_LINES = 24


def project_python_files() -> list[Path]:
    """Every Python file in the project, with the directories that hold other people's code left out."""
    return [
        path for path in sorted(ROOT.rglob("*.py"))
        if not any(part in SKIP_DIRECTORIES for part in path.relative_to(ROOT).parts)
    ]


def run(argv: list[str], timeout: int) -> tuple[int | None, str]:
    """Run one command and answer with its exit code and everything it said.

    ``None`` for the code means it never finished or never started: a command that had to be killed,
    or one this machine does not have. Neither is an exit status, and neither is a pass.
    """
    try:
        finished = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, errors="replace",
                                  timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"did not finish inside {timeout}s"
    except OSError as exc:
        return None, str(exc).splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def last_line(output: str) -> str:
    """The last thing a check said, which in every one of them is its own verdict."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def step_syntax() -> tuple[int | None, str, str]:
    files = project_python_files()
    code, output = run([sys.executable, "-m", "py_compile", *[str(path) for path in files]],
                       TIMEOUTS["syntax"])
    return code, f"{len(files)} Python files compile", output


def step_app() -> tuple[int | None, str, str]:
    """Build the app and every template in it, which is what the app does before it serves anything."""
    snippet = (
        "import sys; sys.path.insert(0, '.')\n"
        "from app import create_app\n"
        "from services.changelog import releases\n"
        "app = create_app()\n"
        "names = sorted(app.jinja_env.list_templates())\n"
        "for name in names:\n"
        "    app.jinja_env.get_template(name)\n"
        "print(f'{len(names)} templates compile and {len(releases())} releases are valid')\n"
    )
    code, output = run([sys.executable, "-c", snippet], TIMEOUTS["app"])
    return code, last_line(output) or "the app builds", output


def step_javascript() -> tuple[int | None, str, str]:
    scripts = sorted((ROOT / "static").glob("*.js"))
    node = shutil.which("node")
    if node is None:
        return None, f"node is not installed, so {len(scripts)} scripts were not parsed", ""
    for script in scripts:
        code, output = run([node, "--check", str(script)], TIMEOUTS["javascript"])
        if code != 0:
            return code, f"{script.name} does not parse", output
    return 0, f"{len(scripts)} scripts parse", ""


def step_tool(tool: str, extra: list[str], timeout: int) -> tuple[int | None, str, str]:
    argv = [sys.executable, str(ROOT / "tools" / tool), *extra]
    code, output = run(argv, timeout)
    if code is None:
        return None, output, ""
    return code, last_line(output) or f"{tool} said nothing", output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", metavar="NAME,NAME",
                        help="run only these checks (default: all of them)")
    parser.add_argument("--skip", default="", metavar="NAME,NAME", help="leave these out")
    parser.add_argument("--fast", action="store_true",
                        help="leave out the browser pass, which is the slow one")
    parser.add_argument("--league-id", help="league to read (default: the saved connection)")
    parser.add_argument("--username", help="Sleeper username (default: the saved connection)")
    parser.add_argument("--manager-id", help="Sleeper manager id (default: the saved connection)")
    parser.add_argument("--base-url", help="measure an already running instance instead of starting one")
    parser.add_argument("--json", help="write every check's outcome to this file")
    args = parser.parse_args(argv)

    league = [part for part in (
        ("--league-id", args.league_id) if args.league_id else None,
        ("--username", args.username) if args.username else None,
        ("--manager-id", args.manager_id) if args.manager_id else None,
    ) if part]
    league = [value for pair in league for value in pair]
    base = ["--base-url", args.base_url] if args.base_url else []

    steps = [
        ("syntax", step_syntax),
        ("app", step_app),
        ("javascript", step_javascript),
        ("stamps", lambda: step_tool("check_asset_stamps.py", base, TIMEOUTS["stamps"])),
        ("slots", lambda: step_tool("check_lineup_slots.py", league, TIMEOUTS["slots"])),
        ("layout", lambda: step_tool("check_layout.py", league + base, TIMEOUTS["layout"])),
    ]

    def names(text: str) -> list[str]:
        return [part.strip() for part in text.replace(" ", "").split(",") if part.strip()]

    known = [name for name, _ in steps]
    asked = names(args.only) or known
    unknown = [name for name in set(asked) | set(names(args.skip)) if name not in known]
    if unknown:
        print(f"No such check: {', '.join(sorted(unknown))}. The checks are {', '.join(known)}.",
              file=sys.stderr)
        return 2
    left_out: list[str] = [name for name in names(args.skip) if name in known]
    if args.fast and "layout" not in left_out:
        left_out.append("layout")
    asked = [name for name in asked if name not in left_out]
    if not asked:
        print("Nothing to check.", file=sys.stderr)
        return 2

    print("DeepThreat preflight - every check worth running before a commit")
    print(f"  {len(asked)} of {len(known)} checks"
          + (f"; left out: {', '.join(left_out)}" if left_out else ""))
    print()

    results: list[dict] = []
    for name, step in steps:
        if name not in asked:
            continue
        print(f"  {name:<11} {TITLES[name]} ...", flush=True)
        started = time.monotonic()
        code, detail, output = step()
        seconds = time.monotonic() - started
        results.append({"name": name, "exit_code": code, "seconds": round(seconds, 1),
                        "detail": detail, "output": output})
        mark = {0: "ok", 1: "FAILED", 2: "not asked"}.get(code, "could not run")
        print(f"  {mark:<11} {name:<11} {detail}  [{seconds:.1f}s]", flush=True)

    failed = [entry for entry in results if entry["exit_code"] == 1]
    unasked = [entry for entry in results if entry["exit_code"] not in (0, 1)]

    for entry in failed + unasked:
        print(f"\n--- what {entry['name']} said " + "-" * 40)
        lines = entry["output"].rstrip().splitlines()
        if len(lines) > TAIL_LINES:
            print(f"  (last {TAIL_LINES} lines of {len(lines)})")
            lines = lines[-TAIL_LINES:]
        for line in lines:
            print(f"  {line}")
    print()

    if args.json:
        Path(args.json).write_text(json.dumps({
            "checks": results, "left_out": left_out,
            "exit_code": 1 if failed else (2 if unasked else 0),
        }, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}\n")

    spent = sum(entry["seconds"] for entry in results)
    if failed:
        print(f"PREFLIGHT FAILED: {', '.join(entry['name'] for entry in failed)} "
              f"({len(failed)} of {len(results)} checks, {spent:.1f}s).")
        return 1
    if unasked:
        print(f"PREFLIGHT NOT PROVEN: {', '.join(entry['name'] for entry in unasked)} "
              f"could not be asked ({len(unasked)} of {len(results)} checks, {spent:.1f}s). "
              f"A check nobody could run is not a check that passed.")
        return 2
    plural = "" if len(results) == 1 else "s"
    print(f"PREFLIGHT CLEAN: {len(results)} check{plural} passed in {spent:.1f}s"
          + (f", and {', '.join(left_out)} was not asked ({'--fast' if args.fast else '--skip'})."
             if left_out else ". Nothing was left unproven."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
