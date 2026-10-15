"""Ask both deployed copies whether they are serving the revision that was pushed, and answer once.

``tools/check_deployed_revisions.py`` answers the first half of that question by asking each host
which commit it is running. This is the whole of it, because a copy can name the right revision and
still be showing somebody something else: a build that did not ship an asset, an edge cache still
holding yesterday's stylesheet, or a revision variable set by a build whose pages never arrived.
Nothing on a page betrays that -- a stale page renders exactly like a fresh one -- so this asks the
copies for the things a name cannot prove:

* **the revision** -- each host's own health check, asked by ``check_deployed_revisions.py``, which
  is where the hosts are known and the answer is judged;
* **the page** -- a real page from each copy, which must answer 200 and name every style sheet and
  script at a URL carrying a stamp;
* **the bytes** -- every asset that page names, fetched from that copy and compared with the file
  in the pushed commit, byte for byte, together with the stamp in its own URL and the year of
  caching that stamp is what makes safe;
* **the words** -- the newest release in the notebook must be in the page a copy serves, read out
  of the markup as a reader reads it rather than as the markup writes it, which is the one thing
  here that only the pushed commit's own code and templates can say.

A copy that is behind or that cannot be asked is not asked the rest: its answer is already "not
this revision", and comparing its assets would only report that again as noise.

    python tools/postflight.py                                  # both hosts, against the push
    python tools/postflight.py --wait 600                       # until the build lands, up to 10m
    python tools/postflight.py --league-id 1313... --username someone --manager-id 376...
    python tools/postflight.py --host Render=https://...        # a host that was added since
    python tools/postflight.py --json postflight.json

The page it asks for is a league page where a league is known -- the saved connection, or the
``--league-id``, ``--username`` and ``--manager-id`` the other tools take -- because a served copy
holds no connection of its own and a league page needs none: the league is in the address. With no
league to name it asks for ``/``, which is the connection page, and then the notebook's own words
cannot be read there; the run says so rather than counting the copy as current on that alone.

Exit codes: 0 every copy answered as the pushed revision and served the pushed page and bytes, 1 a
copy is behind, or is serving something that is not the pushed revision, 2 nothing failed but
something could not be asked -- a host that did not answer, was gated, does not know its revision,
or an asset this could not compare. A copy nobody could ask is never counted as serving.

This is the sibling of ``tools/preflight.py``: that one asks the checks before a commit, this one
asks the deploys after a push, and neither is the other's business.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: How each copy is asked which revision it runs, and how long one may take to answer. A free
#: instance wakes from nothing in tens of seconds, measured, so this is longer than a page fetch.
REVISIONS_TOOL = "check_deployed_revisions.py"

#: Where a page names a static file, whether or not it carries a stamp.
STATIC_URL = re.compile(r"""(?:src|href)=["'](/static/[^"']+)["']""")

#: The newest release's headline, read out of the pushed commit's own changelog rather than out of
#: this checkout: a release written here and not yet pushed is a release no copy can be serving.
NEWEST_TITLE = re.compile(r'RELEASES\s*=\s*\[\s*\{.*?"title":\s*"([^"]+)"', re.S)

#: The stamp a URL carries and the caching it is what makes safe. Both are the app's own numbers
#: (``asset_stamp`` and the ``after_request`` hook), restated here so a copy is judged by what it
#: sends rather than by what this repository believes it sends.
STAMP_LENGTH = 12
YEAR = 31536000

#: A page of this app, its assets and the copies' own answers. Generous enough for a free instance
#: that has gone to sleep and is waking, short enough that a host which is not answering is
#: reported rather than waited on.
TIMEOUT = 60

#: How long to keep asking while a build lands, when nothing says otherwise, and how often. A push
#: reaches a host in a minute or two and a build takes longer, so no waiting is the default.
DEFAULT_INTERVAL = 20.0


def _git(*arguments: str) -> tuple[int, bytes]:
    """One read-only git command in this checkout, answering with its status and its raw output."""
    try:
        finished = subprocess.run(("git", "-C", str(ROOT), *arguments), capture_output=True,
                                  timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return 1, b""
    return finished.returncode, finished.stdout


def blob_at(commit: str, path: str) -> bytes | None:
    """A file as the pushed commit holds it, or None when that commit does not hold it.

    The comparison is against the commit and not against this working tree on purpose: the tree a
    copy was built from is the push, and the two differ by line endings on a Windows checkout --
    which would otherwise report every style sheet as changed.
    """
    code, output = _git("show", f"{commit}:{path}")
    return output if code == 0 and output else None


def newest_title(commit: str) -> str:
    """The newest release headline in the pushed commit's changelog, or "" when it cannot be read."""
    code, output = _git("show", f"{commit}:services/changelog.py")
    if code != 0:
        return ""
    found = NEWEST_TITLE.search(output.decode("utf-8", errors="replace"))
    return found.group(1) if found else ""


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:STAMP_LENGTH]


def stamp_of(url: str) -> str:
    """The stamp a static URL carries, or "" when it carries none."""
    if "v=" not in url:
        return ""
    return url.split("v=", 1)[1].split("&", 1)[0]


def ask_revisions(hosts: list[str], expect: str, wait: float, interval: float,
                  timeout: float) -> tuple[int, dict]:
    """Ask each host which revision it runs, through the tool that already knows how to ask.

    A commit named here is passed along rather than only used for the pages and the bytes: asking
    the hosts about one commit and their assets about another would have a run call a copy current
    in one line and not-current in the next.
    """
    argv = [sys.executable, str(ROOT / "tools" / REVISIONS_TOOL), "--timeout", str(timeout),
            "--interval", str(interval), "--wait", str(wait)]
    if expect:
        argv += ["--expect", expect]
    for host in hosts:
        argv += ["--host", host]
    with tempfile.TemporaryDirectory() as folder:
        report_path = Path(folder) / "revisions.json"
        argv += ["--json", str(report_path)]
        try:
            finished = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                      errors="replace", timeout=max(120.0, wait + 180.0),
                                      check=False)
        except subprocess.TimeoutExpired:
            return 2, {"error": "the revision check did not finish in the time it was given"}
        output = (finished.stdout or "") + (finished.stderr or "")
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 2, {"error": f"the revision check wrote no report:\n{output.rstrip()}",
                       "output": output}
    payload["exit_code"] = finished.returncode
    payload["output"] = output
    # Its closing sentence is what this shows, and the line it writes about its own report is
    # about a file name in a temporary folder rather than about a copy.
    payload["last_line"] = next((line.strip() for line in reversed(output.splitlines())
                                 if line.strip() and not line.strip().startswith("Wrote ")), "")
    return finished.returncode, payload


def check_serving(name: str, base: str, expected: str, page_path: str, title: str,
                  timeout: float) -> dict:
    """Ask one copy for the page it serves and every asset that page names.

    What comes back is a report rather than a verdict: the assets that were the pushed bytes, the
    ones that were not, and the things this could not ask about at all -- which is not the same as
    a pass and is not counted as one.
    """
    import requests

    report: dict = {"host": name, "page": base + page_path, "assets": 0, "same": 0,
                    "problems": [], "unasked": [], "title": title or None, "words": None}
    session = requests.Session()

    def get(url: str) -> bytes | None:
        try:
            answer = session.get(url if url.startswith("http") else base + url,
                                 timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            report["unasked"].append(f"{url}: {str(exc).splitlines()[0]}")
            return None
        if answer.status_code != 200:
            report["unasked"].append(f"{url}: HTTP {answer.status_code}")
            return None
        return answer.content

    page = get(page_path)
    if page is None:
        return report
    text = page.decode("utf-8", errors="replace")
    # What the page says, as a reader reads it rather than as the markup writes it: a title with
    # an apostrophe in it arrives escaped -- "A Pace board's title" is written &#39; there -- and a
    # test against the markup would call a copy that carries the words one that does not, which is
    # what this said about the first release here to have an apostrophe in its headline. An asset
    # address with more than one parameter is escaped the same way, so it is read the same way
    # before anything is fetched at it.
    spoken = html.unescape(text)
    urls = [html.unescape(url) for url in STATIC_URL.findall(text)]
    if not urls:
        report["unasked"].append(f"{page_path} named no static file at all")
    if title:
        report["words"] = title in spoken
        if not report["words"]:
            report["problems"].append(f"the page does not carry the newest release, {title!r}")

    for url in urls:
        asset = url.split("?", 1)[0].rsplit("/", 1)[-1]
        stamp = stamp_of(url)
        # One request an asset, because the body and the header a visitor is given are the same
        # request's answer: asking twice would let a cache serve different things to each half of
        # this check and have the copy pass on one of them.
        try:
            answer = session.get(base + url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            report["unasked"].append(f"{url}: {str(exc).splitlines()[0]}")
            continue
        if answer.status_code != 200:
            report["unasked"].append(f"{url}: HTTP {answer.status_code}")
            continue
        body = answer.content
        report["assets"] += 1
        problems = []
        cache = answer.headers.get("Cache-Control", "")
        if not stamp:
            problems.append("the URL carries no stamp")
        elif digest(body) != stamp:
            problems.append(f"the bytes hash to {digest(body)}, not the {stamp} in its URL")
        if "immutable" not in cache or str(YEAR) not in cache:
            problems.append(f"it is not cached for a year ({cache or 'no Cache-Control'})")
        pushed = blob_at(expected, f"static/{asset}")
        if pushed is None:
            report["unasked"].append(f"static/{asset}: not in {expected[:7]}, so nothing to compare")
        elif body != pushed:
            problems.append(f"it is not the pushed file ({len(body)}B served, "
                            f"{len(pushed)}B in {expected[:7]})")
        if problems:
            report["problems"].append(f"{url}: " + "; ".join(problems))
        else:
            report["same"] += 1
    return report


def render(hosts: list[dict], serving: list[dict], expected: str, revisions: dict) -> int:
    width = max((len(str(host.get("host"))) for host in hosts), default=0)
    for host in hosts:
        name = str(host.get("host"))
        state = str(host.get("state"))
        if state == "current":
            extras = [str(value) for value in (host.get("branch"), host.get("source")) if value]
            print(f"  {name:<{width}}  current - {str(host.get('commit'))[:7]}"
                  + (f" ({', '.join(extras)})" if extras else ""))
        elif state == "behind":
            print(f"  {name:<{width}}  behind - running {str(host.get('commit'))[:7]}, "
                  f"not {expected[:7]}")
        else:
            print(f"  {name:<{width}}  {state} - {host.get('detail')}")

    for report in serving:
        print(f"\n  {report['host']}  {report['page']}")
        print(f"    {'the revision above is what the copy says; this is what it serves'}")
        if report["assets"]:
            print(f"    {report['same']} of {report['assets']} assets are the pushed bytes, at a "
                  f"URL made of them and cached for a year")
        if report["words"] is True:
            print(f"    the page carries the newest release, {report['title']!r}")
        elif report["words"] is None and report["title"]:
            print("    the newest release could not be looked for on this page")
        for problem in report["problems"]:
            print(f"    NOT THE PUSHED REVISION: {problem}")
        for note in report["unasked"]:
            print(f"    could not be asked: {note}")

    # Two different failures, said as two rather than as one: a copy on an older revision is a
    # build that has not landed, and a copy that answered as the pushed revision and then served
    # something else is the reason this asks twice at all. Only the second one is the surprise.
    behind = sorted({str(entry["host"]) for entry in hosts if entry.get("state") == "behind"})
    mismatched = sorted({str(report["host"]) for report in serving if report["problems"]})

    unasked = [str(entry["host"]) for entry in hosts
               if entry.get("state") not in ("current", "behind")]
    unasked += [str(report["host"]) for report in serving if report["unasked"]]
    unasked = sorted(set(unasked))

    print()
    if behind or mismatched:
        print(f"\nPOSTFLIGHT FAILED: {', '.join(sorted(set(behind) | set(mismatched)))}")
        if behind:
            print(f"  {', '.join(behind)}: answering with a revision older than {expected[:7]}")
        if mismatched:
            print(f"  {', '.join(mismatched)}: answered as {expected[:7]} and then served "
                  f"something that is not it, which is the one thing a revision number cannot "
                  f"tell you")
        return 1
    if unasked:
        print(f"\nPOSTFLIGHT NOT PROVEN: "
              f"{', '.join(sorted({str(entry['host']) for entry in unasked}))} "
              f"could not be asked in full. A copy nobody could ask is not a copy that is serving "
              f"the push.")
        return 2
    if revisions.get("last_line"):
        print(f"  {revisions['last_line']}")
    print(f"\nPOSTFLIGHT CLEAN: {len(hosts)} copies are the pushed revision {expected[:7]} and "
          f"serve its own page and bytes.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", action="append", default=[], metavar="NAME=URL",
                        help="ask only the hosts you name (repeatable); default: the known ones")
    parser.add_argument("--expect", default="",
                        help="the commit every host should be serving (default: the push)")
    parser.add_argument("--league-id", help="league whose page to ask for (default: your settings)")
    parser.add_argument("--username", help="Sleeper username (default: your settings)")
    parser.add_argument("--manager-id", help="Sleeper manager id (default: your settings)")
    parser.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                        help="keep asking until every copy is current, up to this many seconds")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, metavar="SECONDS",
                        help=f"how often to ask while waiting (default: {DEFAULT_INTERVAL:.0f})")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, metavar="SECONDS",
                        help=f"how long one copy may take to answer (default: {TIMEOUT:.0f})")
    parser.add_argument("--json", help="write every answer to this file")
    args = parser.parse_args(argv)

    from services.local_settings import load_connection

    saved = load_connection()
    league_id = args.league_id or str(saved.get("league_id") or "")
    username = args.username if args.username is not None else str(saved.get("username") or "")
    manager_id = args.manager_id if args.manager_id is not None else str(saved.get("manager_id") or "")
    if league_id:
        query = "&".join(f"{key}={value}" for key, value in
                         (("username", username), ("manager_id", manager_id)) if value)
        page_path = f"/league/{league_id}/matchups" + (f"?{query}" if query else "")
    else:
        page_path = "/"

    print("DeepThreat postflight - whether the deployed copies are serving what was pushed")
    print(f"  a league page at {page_path}" if league_id else
          "  no league to name, so the connection page: the notebook's own words cannot be read "
          "there\n  (pass --league-id to ask for a league page instead)")
    print()

    started = time.monotonic()
    _, revisions = ask_revisions(args.host, args.expect, args.wait, args.interval, args.timeout)
    if "error" in revisions and not revisions.get("hosts"):
        print(f"The revision check could not run: {revisions['error']}", file=sys.stderr)
        return 2

    expected = args.expect.strip() or str(revisions.get("expected") or "")
    if not expected:
        print("Could not work out which commit to compare against.", file=sys.stderr)
        return 2
    hosts = revisions.get("hosts") or []
    if not hosts:
        print("No host answered, so there is nothing to ask about a page.", file=sys.stderr)
        return 2

    # Only the copies that say they are the pushed revision are asked what they serve: one that
    # says it is behind has already answered, and one that could not be asked has nothing to say.
    current = [host for host in hosts if host.get("state") == "current"]
    # The host's own health URL without its path, which is the address the page is asked at: one
    # place names the hosts (the revision check) and this asks the same ones about what they serve.
    base_of = {str(host.get("host")): str(host.get("url", "")).rsplit("/", 1)[0] for host in hosts}
    title = newest_title(expected)
    status = f"  asking {len(current)} of {len(hosts)} copies what they serve, against "
    print(status + f"{expected[:7]}\n")

    serving = [check_serving(str(host.get("host")), base_of[str(host.get("host"))], expected,
                             page_path, title, args.timeout)
               for host in current]

    code = render(hosts, serving, expected, revisions)
    if args.json:
        Path(args.json).write_text(json.dumps({
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expected": expected, "page": page_path, "newest_release": title or None,
            "revisions": revisions, "serving": serving, "exit_code": code,
        }, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    print(f"\nAsked in {time.monotonic() - started:.0f}s.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
