"""Prove that the URL a page gives a stylesheet or a script changes when the file's contents do.

Every style sheet and script here is asked for at a URL carrying a digest of the file's own bytes
(``asset_stamp`` in ``app.py``), which is what makes a year of caching honest: the browser is told
to keep what it fetched until the URL changes, and the URL is the contents. The promise is
invisible from a page -- a fresh stylesheet and a stale one render the same markup -- so it is
asked after here, from three sides:

* **the stamp is the bytes** -- every static file the templates load carries a stamp, and that
  stamp is the first twelve characters of the sha1 of that file's own bytes, recomputed here
  rather than taken from the app's word for it;
* **contents move the URL, and nothing else does** -- against a scratch folder holding one file a
  scenario, the same contents answer the same stamp, new contents answer a new one (a longer file,
  a shorter one and an edit that leaves the length alone alike), and rewriting the file's
  modification time by itself moves nothing, because a checkout rewrites every modification time
  and a deploy must not throw away a cache a visitor already has;
* **the URL is what a visitor is served** -- through HTTP: a stamped URL's bytes hash back to the
  stamp in its URL, the same file asked for without a stamp keeps revalidating instead of the year,
  and every static URL a real page emits is one of those.

Nothing is inferred from a page's own word for anything else. What a page says about its assets is
read out of it, and each of those URLs is then fetched.

    python tools/check_asset_stamps.py                          # start a server and ask it
    python tools/check_asset_stamps.py --base-url http://127.0.0.1:5000
    python tools/check_asset_stamps.py --json stamps.json

Exit codes: 0 every asset's URL is the digest of the bytes the app reads, for every change a real
edit produces, 1 one of them is not, 2 something could not be measured -- a server that would not
start, a file that could not be fetched, a static file a template names that the folder does not
hold. An asset this could not ask about is never counted as one that held.

One case is printed as a bound rather than asserted either way, because the app's memory of a file
is a deliberate trade rather than a bug: inside one live process, a rewrite that leaves the file's
size and its modification time exactly as they were -- which is what two writes of the same length
landing in the same clock tick look like to a file system -- is answered from the digest read
before it.
No editor, checkout or build leaves a modification time alone, and a fresh process -- which is
what a deploy is -- reads the file again, so the URL a visitor is given is still about the bytes
they are served; what this cannot claim is that the app notices a rewrite made to look untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"

#: Where a server this starts for itself is asked. Loopback only: the check has no business on a
#: network, and a copy bound to every interface would be one.
HOST = "127.0.0.1"

#: How a template asks for one of these files. Both quote styles are matched, because a check that
#: saw only one of them would call a file unreferenced and report a clean run over half the pages.
ASSET_CALL = re.compile(r"""asset_url\(\s*['"]([^'"]+)['"]\s*\)""")

#: How a page names a static file, whether or not it carries a stamp.
STATIC_URL = re.compile(r"""(?:src|href)=["'](/static/[^"']+)["']""")

#: The stamp's own length: ``asset_stamp`` reports the first twelve hex characters of a sha1, and
#: this is the only place the check assumes anything about how a stamp is made.
STAMP_LENGTH = 12

#: A year in seconds, which is the caching this whole check exists to keep honest.
YEAR = 31536000

#: Long enough for a page of a local server, short enough that a server which is not answering is
#: reported rather than waited on.
TIMEOUT = 30

#: The page whose own URLs are read. The connection page and a league page carry the same asset
#: URLs, so which one answers does not matter -- what matters is that a real page was read.
PAGE = "/"


def referenced_assets() -> list[str]:
    """Every static file the templates ask for by name.

    Read from the templates rather than from a page, because a page only shows the assets it needs
    and this is about all of them: a stylesheet that no page happens to load today is still a file
    whose URL has to be about its contents the day one does.
    """
    names: set[str] = set()
    for template in sorted(TEMPLATES.glob("*.html")):
        names.update(ASSET_CALL.findall(template.read_text(encoding="utf-8", errors="replace")))
    return sorted(names)


def digest_of(path: Path) -> str:
    """The stamp a file's own bytes call for."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:STAMP_LENGTH]


def without_query(url: str) -> str:
    """A static URL's path, so a stamped URL can be matched to the file it names."""
    return url.split("?", 1)[0]


def first_line(exc: BaseException) -> str:
    """The first line of an exception, which is the line that says what went wrong."""
    return str(exc).splitlines()[0] if str(exc).strip() else exc.__class__.__name__


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


def start_server(port: int) -> tuple[object, str]:
    """Serve the app in this process, so the check needs nothing already running."""
    import logging

    from werkzeug.serving import make_server

    from app import create_app

    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # one line per request buries the report
    server = make_server(HOST, port, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{HOST}:{server.server_port}"


def check_contents(names: list[str]) -> tuple[list[dict], list[dict]]:
    """Ask the app for each asset's stamp and compare it with the bytes on disk.

    The app is asked rather than the digest recomputed alone, because what is being checked is the
    app's own answer: a stamp that agreed with this file's arithmetic and not with the app's would
    pass a check that did its own hashing.
    """
    from app import asset_stamp

    results: list[dict] = []
    unmeasured: list[dict] = []
    for name in names:
        path = STATIC / name
        if not path.is_file():
            unmeasured.append({
                "what": name,
                "detail": "a template asks for it and the static folder has no such file",
            })
            continue
        wanted = digest_of(path)
        given = asset_stamp(name, str(STATIC))
        results.append({"asset": name, "stamp": given, "wanted": wanted, "ok": given == wanted})
    return results, unmeasured


def check_sensitivity() -> list[dict]:
    """What moves the stamp, and what does not, on a scratch folder holding one file a scenario.

    Each scenario gets a file name of its own because the app remembers a stamp against a file's
    name: asking two scenarios about one name would measure the app's memory of the first, which is
    the warm cache the last scenario here is deliberately about.
    """
    from app import asset_stamp

    scenarios: list[dict] = []
    with tempfile.TemporaryDirectory() as folder:
        def ask(name: str, text: str, when: float | None = None) -> str:
            """Write one file and ask the app what stamp it carries."""
            path = Path(folder) / name
            path.write_bytes(text.encode("utf-8"))
            if when is not None:
                os.utime(path, (when, when))
            return asset_stamp(name, folder)

        def scenario(title: str, first: str, second: str, expected: bool) -> None:
            scenarios.append({"title": title, "ok": (first == second) is expected,
                              "detail": f"{first or '(none)'} -> {second or '(none)'}"})

        # The same contents, asked twice: the answer cannot drift between two reads of one file.
        scenario("the same contents, asked again", ask("scratch-again.css", "a{}\n"),
                 ask("scratch-again.css", "a{}\n"), True)

        # The same contents written with a new modification time. A checkout rewrites every one of
        # these, so a stamp that moved with the clock would invalidate every cache a deploy by hand.
        scenario("the same contents, a new modification time",
                 ask("scratch-clock.css", "b{}\n", 1_000_000_000),
                 ask("scratch-clock.css", "b{}\n", 1_700_000_000), True)

        # New contents of a different length, which is what editing a stylesheet usually does.
        scenario("new contents, three bytes longer", ask("scratch-longer.css", "c{}\n"),
                 ask("scratch-longer.css", "c{} /* x */\n"), False)
        scenario("new contents, three bytes shorter", ask("scratch-shorter.css", "d{} /* x */\n"),
                 ask("scratch-shorter.css", "d{}\n"), False)

        # New contents of exactly the same length, which is the case a size comparison alone cannot
        # see: the length is the file's whole visible shape to anything but its bytes. The second
        # write is given a later modification time on purpose, because a later modification time is
        # what a real edit leaves behind: relying on two writes falling a clock tick apart would be
        # this check racing the file system, and which tick they land in is not what is asked here.
        scenario("new contents, the same length",
                 ask("scratch-samelen.css", "/* one */\n"),
                 ask("scratch-samelen.css", "/* two */\n", time.time() + 5), False)

        # A file the app has not been asked about yet is read from the file: the memory above is
        # kept against a file's own name, so one file's answer can never be handed to another.
        scenario("a name it has not been asked about, the same size as one it has",
                 ask("scratch-ninth.css", "/* nine */\n"),
                 ask("scratch-tenth.css", "/* ten! */\n"), False)

        # A file that is not there has no stamp, rather than a stamp for something that is not the
        # file: a URL nothing has ever cached is the honest answer for a file that is not served.
        missing = asset_stamp("scratch-absent-does-not-exist.css", folder)
        scenarios.append({"title": "a file that is not there", "ok": missing == "",
                          "detail": f"stamp {missing or '(none)'}"})
    return scenarios


def check_warm_cache() -> list[dict]:
    """What the app's own memory of a file can and cannot see, inside one live process.

    ``asset_stamp`` keeps the digest it read against the file's size and modification time, so a
    page does one ``stat`` an asset instead of reading every file it loads. That shortcut has one
    blind spot, and it is measured here rather than assumed away: a rewrite that leaves both the
    size and the modification time exactly as they were is answered from the memory of the older
    bytes. Nothing that edits a file -- an editor, a checkout, a build -- leaves the modification
    time alone, so this is the bound of the trade rather than a case a visitor meets.
    """
    from app import asset_stamp

    measured: list[dict] = []
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "scratch-warm.css"
        path.write_bytes(b"/* one */\n")
        info = os.stat(path)
        before = asset_stamp(path.name, folder)
        again = asset_stamp(path.name, folder)
        measured.append({"title": "the same contents, asked again while the process lives",
                         "ok": again == before, "detail": before})

        # The blind spot itself: the same length, and the modification time put back exactly as it
        # was. The app answers from its memory of the older bytes, and so does -- for as long as
        # that answer is kept -- a page that had already drawn the older URL. Measured, not
        # assumed, and printed whether or not it shows.
        path.write_bytes(b"/* two */\n")  # the same length as the line above
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        stamp = asset_stamp(path.name, folder)
        measured.append({
            "title": "a rewrite that leaves the size and the time alone",
            "ok": True,
            "bound": stamp == before,
            "detail": (f"the process answers {stamp}, still the digest of the older bytes, while the "
                       f"file is now {digest_of(path)}"
                       if stamp == before else
                       f"the process answers {stamp}, the digest of the newer bytes"),
        })
    return measured


def check_served(base: str, names: list[str]) -> tuple[list[dict], list[dict]]:
    """What a visitor is handed: the file at the URL a page gives it, and the same file without one."""
    import requests

    from app import asset_stamp

    served: list[dict] = []
    unmeasured: list[dict] = []
    session = requests.Session()

    def get(url: str):
        try:
            return session.get(base + url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            unmeasured.append({"what": url, "detail": first_line(exc)})
            return None

    # The page's own URLs, which is the part a browser acts on: a stamp this check builds and the
    # app does not draw is a stamp nobody would ever fetch.
    page = get(PAGE)
    page_urls: list[dict] = []
    if page is not None and page.status_code == 200:
        for url in STATIC_URL.findall(page.text):
            name = without_query(url).rsplit("/", 1)[-1]
            stamp = url.split("v=", 1)[1].split("&", 1)[0] if "v=" in url else ""
            on_disk = (STATIC / name)
            page_urls.append({
                "url": url, "name": name, "stamp": stamp,
                "ok": bool(stamp) and on_disk.is_file() and stamp == digest_of(on_disk),
            })
        if not page_urls:
            unmeasured.append({"what": PAGE, "detail": "the page linked no static file at all"})
    elif page is not None:
        unmeasured.append({"what": PAGE, "detail": f"HTTP {page.status_code}"})

    for name in names:
        path = STATIC / name
        if not path.is_file():
            continue  # reported by the contents check, and nothing can be fetched for it here
        stamp = asset_stamp(name, str(STATIC)) or digest_of(path)
        url = f"/static/{name}?v={stamp}"
        answer = get(url)
        if answer is None:
            continue
        body = answer.content
        cache = answer.headers.get("Cache-Control", "")
        served.append({
            "asset": name, "url": url, "status": answer.status_code, "bytes": len(body),
            "hash": hashlib.sha1(body).hexdigest()[:STAMP_LENGTH],
            "cache": cache or "(none)",
            "ok": (answer.status_code == 200
                   and body == path.read_bytes()
                   and hashlib.sha1(body).hexdigest()[:STAMP_LENGTH] == stamp
                   and "immutable" in cache and str(YEAR) in cache),
        })

    # The same file without its stamp. What makes the year safe is that it is only ever said about a
    # URL that changes with the file, so a request that names no digest must keep revalidating.
    bare = f"/static/{names[0]}" if names else ""
    if bare:
        answer = get(bare)
        if answer is not None:
            cache = answer.headers.get("Cache-Control", "")
            served.append({
                "asset": f"{names[0]} with no stamp", "url": bare, "status": answer.status_code,
                "bytes": len(answer.content), "hash": "",
                "cache": cache or "(none)",
                "ok": answer.status_code == 200 and "immutable" not in cache and str(YEAR) not in cache,
            })

    return served, unmeasured, page_urls


def render(contents: list[dict], scenarios: list[dict], warm: list[dict], served: list[dict],
           page_urls: list[dict], unmeasured: list[dict]) -> int:
    broken = [entry for entry in contents if not entry["ok"]]
    broken += [entry for entry in served if not entry["ok"]]
    broken += [entry for entry in page_urls if not entry["ok"]]
    broken += [entry for entry in scenarios if not entry["ok"]]

    print(f"\nThe stamp of every asset is the sha1 of its own bytes ({len(contents)} assets)")
    for entry in contents:
        print(f"  {entry['asset']:<24} {entry['stamp'] or '(none)'}"
              f"{'' if entry['ok'] else '  NOT ' + entry['wanted']}")

    print("\nWhat moves the stamp, on a scratch folder (a file a scenario):")
    for entry in scenarios:
        print(f"  {'ok  ' if entry['ok'] else 'BROKEN'} {entry['title']}  [{entry['detail']}]")

    print("\nWhat the app's memory of a file can see, inside one live process "
          "(measured, not asserted):")
    for entry in warm:
        mark = "BOUND" if entry.get("bound") else ("ok  " if entry["ok"] else "BROKEN")
        print(f"  {mark} {entry['title']}  [{entry['detail']}]")

    print(f"\nWhat a visitor is served ({len(served)} URLs asked for):")
    for entry in served:
        print(f"  {'ok  ' if entry['ok'] else 'BROKEN'} {entry['url']}  HTTP {entry['status']}, "
              f"{entry['bytes']}B, {entry['cache']}"
              + (f", sha1 {entry['hash']}" if entry["hash"] else ""))

    print(f"\nThe URLs a page emits ({len(page_urls)}):")
    for entry in page_urls:
        print(f"  {'ok  ' if entry['ok'] else 'BROKEN'} {entry['url']}")

    if unmeasured:
        print(f"\nCOULD NOT MEASURE ({len(unmeasured)}) — what could not be asked is not a pass:")
        for entry in unmeasured:
            print(f"  {entry['what']}: {entry['detail']}")

    if broken:
        print(f"\nBROKEN ({len(broken)}) — a URL that does not change with its file:")
        for entry in broken:
            print(f"  {entry.get('asset') or entry.get('url') or entry.get('title')}")
        print("\nThese files are cached for a year on the URL they are fetched from, so a URL that "
              "outlives its contents is a fix that reaches nobody who has already been served the "
              "old one. The stamp is made from the file's bytes; keep it that way.")
        return 1

    if unmeasured:
        return 2

    print("\nEvery stylesheet and script a page loads is fetched at a URL made of its own contents, "
          "and the year of caching arrives with that stamp or not at all.")
    print("The bound printed above is the app's stat shortcut, not a URL that outlived its file: "
          "every change an editor, a checkout or a build makes moves the stamp.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", help="check an already running instance instead of starting one")
    parser.add_argument("--json", help="write every measurement to this file")
    args = parser.parse_args(argv)

    names = referenced_assets()
    if not names:
        print("No template asks for a static file, which cannot be right.", file=sys.stderr)
        return 2
    print(f"Asking {len(names)} stylesheets, scripts and pictures whether their URL is their contents")

    server = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        try:
            server, base = start_server(free_port())
        except Exception as exc:  # noqa: BLE001 - a server that will not start is not a pass
            print(f"Could not start a server to ask: {first_line(exc)}", file=sys.stderr)
            return 2

    try:
        contents, unmeasured = check_contents(names)
        scenarios = check_sensitivity()
        warm = check_warm_cache()
        served, unreachable, page_urls = check_served(base, names)
        unmeasured += unreachable
        code = render(contents, scenarios, warm, served, page_urls, unmeasured)
    finally:
        if server is not None:
            server.shutdown()

    if args.json:
        Path(args.json).write_text(json.dumps({
            "base_url": base, "assets": len(names), "stamps": contents, "scenarios": scenarios,
            "warm_cache": warm, "served": served, "page_urls": page_urls,
            "unmeasured": unmeasured, "exit_code": code,
        }, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
