"""Ask every copy of the app which revision it is running, and say which one is behind.

A copy that has not released the newest commit answers exactly the same pages as one that has:
both render the same league, and the only thing that tells them apart from outside the app is the
revision the running copy reports for itself at ``/healthz``. This asks each host for that and
compares it with the commit that was pushed, so a deploy that quietly did not land -- or landed
and then fell over, or never started because the checks that gate it did not pass -- is a line of
output here rather than a page somebody has to remember to open and read.

    python tools/check_deployed_revisions.py                    # every known host, against the push
    python tools/check_deployed_revisions.py --expect 49eaf8a   # against a commit you name
    python tools/check_deployed_revisions.py --wait 600         # until a deploy lands, up to 10m
    python tools/check_deployed_revisions.py --host Render=https://deepthreat.onrender.com
    python tools/check_deployed_revisions.py --json revisions.json

Each host gets one of five answers, and only the first one is quiet:

* **current** -- it named the commit that was expected.
* **behind** -- it named a different one. The deploy did not land, is still building, or was held
  back by the check that gates it.
* **unknown** -- it answered as the app and said it does not know which revision it is (its
  ``source`` is ``unknown``). That is not a pass and is not a failure of the deploy either: a copy
  that cannot say what it is cannot be told from one that is months behind, and which variable
  that host has to hand over is in DEPLOY.md ("Which commit is live").
* **blocked** -- something answered in the app's place: a sign-in gate in front of the host, or a
  host that is not this app at all. The health check sits behind whatever gates the host, so the
  app was never reached and nothing can be concluded about the copy behind it. Which of the two it
  is has to be settled at the host -- this cannot tell them apart from outside.
* **unreachable** -- nothing answered, or what answered was not this app: a timeout, a refused
  connection, an HTML page, or a status that is neither a redirect nor a healthy answer.

Nothing is inferred from a page, because there is nothing on one to infer from: a page from a
stale copy and a page from a current one are the same bytes. A host that cannot be asked is
reported as unasked, never counted as current.

The commit it asks for is the pushed one -- the tip of this branch's upstream, not this HEAD --
because work that has not been pushed is work no host can have, and asking for it would report
every host as behind. When the two differ the run says so before it asks anything.

The two hosts it knows are the ones this repository deploys to, and both were set up by hand, so a
host added somewhere else is a host this cannot see until it is named in ``HOSTS`` or passed with
``--host``.

Exit codes: 0 every host answered for itself and is current, 1 a host is behind, 2 a host could
not be asked or could not say. Only the first is the run that means every copy is the commit that
was pushed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent

#: The copies of the app this repository deploys, as (name, base URL). Named rather than derived
#: because each was set up by hand on its own platform, and a host nobody listed is a host nobody
#: is checking -- which is the situation this tool exists to end.
#:
#: Only a host that has been seen to answer as this app belongs here: every run is judged by the
#: names in this tuple, and a hostname that answers with somebody else's app turns a check into a
#: false report. The Render service `render.yaml` defines has no URL recorded anywhere in this
#: repository, so it is named on the command line instead of guessed at from the service name:
#: ``--host Render=https://…``. Write the URL down here once it is known.
HOSTS = (
    ("Vercel", "https://deep-threat.vercel.app"),
)

#: Where a copy says which revision it is. Answered without reading Sleeper, so asking it never
#: waits on a league and never costs the host more than a dictionary.
HEALTH_PATH = "/healthz"

#: A free instance sleeps when nothing is asking, and the first request after that waits for it to
#: wake: tens of seconds, measured. This is long enough for that and still short enough that a
#: host which is not answering at all is reported rather than waited on.
DEFAULT_TIMEOUT = 45

#: How often to ask again while waiting for a deploy to land, and how long to keep at it when
#: nothing says otherwise. A push reaches a host in a minute or two and a build takes longer than
#: that, so the default wait is nothing (ask once) and --wait names the budget.
DEFAULT_INTERVAL = 20

#: Two names of one commit can differ in length -- HEAD is abbreviated here and a host reports the
#: full sha it was built from -- so they are compared by prefix. Below this, a prefix says too
#: little to be worth matching: "abc" is not a revision, and a host that reports one is not
#: current, it is unintelligible.
MINIMUM_PREFIX = 7

#: What this says it is when it asks. A host's own logs should name the asker rather than leaving
#: an operator to guess at an anonymous request.
USER_AGENT = "DeepThreat-deploy-check"

#: The answers a host can come back with, in the order the summary prints them.
STATES = ("current", "behind", "unknown", "blocked", "unreachable")


def _git(*arguments: str) -> str:
    """One read-only git command in this checkout, or "" when git cannot answer it."""
    try:
        finished = subprocess.run(("git", "-C", str(ROOT), *arguments),
                                  capture_output=True, text=True, errors="replace",
                                  timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return finished.stdout.strip() if finished.returncode == 0 else ""


def _expected() -> tuple[str, str]:
    """Which commit the hosts should be at, and what to say when that is not this HEAD.

    The commit a host can be running is the one that was **pushed**, not the one in the working
    tree: work that has not been pushed is work no host can have, and asking for it would report
    every host as behind for a deploy that could not have happened. A check that cries wolf is a
    check nobody reads, so the pushed commit is what this asks for, and the note says so plainly
    when the two differ.
    """
    head = _git("rev-parse", "HEAD")
    if not head:
        return "", "this checkout has no HEAD to compare against"
    upstream = _git("rev-parse", "@{upstream}")
    if not upstream or upstream == head:
        return head, ""
    branch = (_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
              or "the upstream branch")
    pushed = upstream[:MINIMUM_PREFIX]
    ahead = _git("rev-list", "--count", "@{upstream}..HEAD")
    behind = _git("rev-list", "--count", "HEAD..@{upstream}")
    if ahead.isdigit() and int(ahead):
        count = int(ahead)
        return upstream, (f"{count} commit{'' if count == 1 else 's'} in this checkout "
                          f"{'is' if count == 1 else 'are'} not pushed yet, so no host can be "
                          f"running {'it' if count == 1 else 'them'}: asking against {pushed} "
                          f"on {branch} instead")
    if behind.isdigit() and int(behind):
        count = int(behind)
        return upstream, (f"this checkout is {count} commit{'' if count == 1 else 's'} behind "
                          f"{branch}: asking against the pushed one, {pushed}, instead")
    return upstream, (f"this checkout is not at {branch}: asking against the pushed commit "
                      f"{pushed} instead")


def _same_commit(reported: str, expected: str) -> bool:
    """Whether two commit names are the same object, allowing either to be the short form."""
    left, right = str(reported or "").strip().casefold(), str(expected or "").strip().casefold()
    if len(left) < MINIMUM_PREFIX or len(right) < MINIMUM_PREFIX:
        return left == right and bool(left)
    return left.startswith(right) or right.startswith(left)


def _hosts(pairs: list[str]) -> list[tuple[str, str]]:
    """The hosts to ask: the ones named on the command line, or the ones in :data:`HOSTS`."""
    if not pairs:
        return list(HOSTS)
    hosts = []
    for pair in pairs:
        name, _, url = str(pair).partition("=")
        name, url = name.strip(), url.strip()
        if not name or not url:
            raise ValueError(f"{pair!r} is not NAME=URL")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        hosts.append((name, url.rstrip("/")))
    return hosts


def ask(name: str, base: str, expected: str, timeout: float) -> dict:
    """Ask one host which revision it is running, and say what its answer amounts to.

    Every way this can come back is an answer of its own: a copy that is behind, a copy that does
    not know, a gate that answered instead of the app, and a host that never answered. None of
    them is treated as a pass, and the detail says which one it was.
    """
    url = base.rstrip("/") + HEALTH_PATH
    report: dict[str, object] = {"host": name, "url": url, "state": "unreachable",
                                 "commit": None, "branch": None, "source": None, "detail": ""}
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=False,
                                headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        report["detail"] = f"nothing answered ({exc.__class__.__name__})"
        return report

    if 300 <= response.status_code < 400:
        # A sign-in gate answers before the app does, so this is the host being unaskable rather
        # than the copy being wrong: the app was never reached. A host that is not this app
        # answers the same way, and nothing out here tells the two apart -- which is why the
        # detail names both possibilities rather than guessing at one.
        location = str(response.headers.get("Location") or "another address")
        report["state"] = "blocked"
        report["detail"] = (f"answered {response.status_code} to {location}, so something is "
                            "standing in front of the app — a sign-in gate, or a host that is "
                            "not this app")
        return report

    if response.status_code != 200:
        report["detail"] = f"answered {response.status_code}"
        return report

    try:
        body = response.json()
    except ValueError:
        report["detail"] = "answered with something that is not a health check"
        return report

    if not isinstance(body, dict) or body.get("ok") is not True or body.get("service") != "deepthreat":
        report["detail"] = "answered 200 without this app's health check"
        return report

    commit = str(body.get("commit") or "").strip()
    report["commit"] = commit or None
    report["branch"] = str(body.get("branch") or "").strip() or None
    report["source"] = str(body.get("source") or "").strip() or None
    if not commit:
        report["state"] = "unknown"
        report["detail"] = (f"the copy does not know which revision it is "
                            f"(source {report['source']})")
        return report

    report["state"] = "current" if _same_commit(commit, expected) else "behind"
    return report


def _counts(reports: list[dict]) -> dict[str, int]:
    counts = {state: 0 for state in STATES}
    for report in reports:
        counts[str(report["state"])] += 1
    return counts


def _summary_line(counts: dict[str, int]) -> str:
    return ", ".join(f"{counts[state]} {state}" for state in STATES)


def _print_reports(reports: list[dict], expected: str) -> None:
    """One block per host, then what the run as a whole amounts to."""
    width = max((len(str(report["host"])) for report in reports), default=0)
    for report in reports:
        print(f"  {str(report['host']):<{width}}  {report['url']}")
        if report["state"] == "current":
            revision = f"{str(report['commit'])[:MINIMUM_PREFIX]}"
            extras = [str(value) for value in (report["branch"], report["source"]) if value]
            print(f"  {'':<{width}}  current — {revision}"
                  + (f" ({', '.join(extras)})" if extras else ""))
        elif report["state"] == "behind":
            extras = [str(value) for value in (report["branch"], report["source"]) if value]
            print(f"  {'':<{width}}  behind — it is running "
                  f"{str(report['commit'])[:MINIMUM_PREFIX]}"
                  + (f" ({', '.join(extras)})" if extras else "")
                  + f", not {expected[:MINIMUM_PREFIX]}")
        else:
            print(f"  {'':<{width}}  {report['state']} — {report['detail']}")

    counts = _counts(reports)
    print(f"\n{_summary_line(counts)}")
    behind = [report for report in reports if report["state"] == "behind"]
    unasked = [report for report in reports
               if report["state"] in ("unknown", "blocked", "unreachable")]
    if counts["current"] == len(reports):
        print("Every copy answered for itself and is the commit that was pushed.")
    elif behind:
        print("A copy behind the commit that was pushed answers exactly like one that is not, "
              "so these are the copies that are showing somebody an older app:")
        for report in behind:
            print(f"  {report['host']}: running {str(report['commit'])[:MINIMUM_PREFIX]}, "
                  f"expected {expected[:MINIMUM_PREFIX]} ({report['source'] or 'no source'})")
    if unasked and not behind:
        print("No host said it is behind, but these could not be asked, so nothing can be "
              "concluded about what they are running:")
        for report in unasked:
            print(f"  {report['host']}: {report['state']} — {report['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect", default="",
                        help="the commit every host should be running (default: the pushed "
                             "commit, which is this branch's upstream)")
    parser.add_argument("--host", action="append", default=[], metavar="NAME=URL",
                        help="ask only the hosts you name (repeatable); default: the known ones")
    parser.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                        help="keep asking until every host is current, up to this many seconds")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, metavar="SECONDS",
                        help=f"how often to ask while waiting (default: {DEFAULT_INTERVAL:.0f})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                        help=f"how long one host may take to answer (default: "
                             f"{DEFAULT_TIMEOUT:.0f})")
    parser.add_argument("--json", default="", metavar="PATH",
                        help="write the report here as well as printing it")
    args = parser.parse_args()

    if args.expect:
        expected, note = args.expect.strip(), ""
    else:
        expected, note = _expected()
    if not expected:
        print(note or "could not work out which commit to compare against", file=sys.stderr)
        return 2
    try:
        hosts = _hosts(args.host)
    except ValueError as exc:
        print(f"could not read the hosts to ask: {exc}", file=sys.stderr)
        return 2
    if args.wait and args.interval <= 0:
        print("--interval has to be more than nothing to keep asking.", file=sys.stderr)
        return 2

    plural = len(hosts) != 1
    print(f"Asking {len(hosts)} host{'' if not plural else 's'} which revision "
          f"{'they are' if plural else 'it is'} running, against {expected[:MINIMUM_PREFIX]}")
    if note:
        print(f"  {note}")
    started = time.monotonic()
    deadline = started + args.wait if args.wait else None
    asked = 0
    while True:
        asked += 1
        reports = [ask(name, base, expected, args.timeout) for name, base in hosts]
        if deadline is None or _counts(reports)["current"] == len(reports):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # While waiting, one line per poll: the full report is for the end, where it is read.
        print(f"  poll {asked} at {time.monotonic() - started:.0f}s: "
              f"{_summary_line(_counts(reports))}")
        time.sleep(min(args.interval, remaining))

    print()
    _print_reports(reports, expected)
    if deadline is not None:
        print(f"\nAsked {asked} time{'' if asked == 1 else 's'} over "
              f"{time.monotonic() - started:.0f}s.")

    if args.json:
        payload = {
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expected": expected,
            "hosts": reports,
            "counts": _counts(reports),
        }
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")

    counts = _counts(reports)
    if counts["behind"]:
        return 1
    return 0 if counts["current"] == len(reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
