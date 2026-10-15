"""Fail when the brand logo is drawn through anything, or a page runs off its own width.

Every page used to open with its title in the largest type on it and, on some of them, a
sentence under that. The brand logo floated above that band off the toolbar's right, so the
two had to be kept apart: a ``max-width`` on ``.matchups-heading>div`` reserved the logo's
lane and the heading copy stayed inside what was left. None of that is there any more -- the
titles and their sentences are gone and the logo stands at the left of the band in flow (see
``templates/_page_head.html``) -- but the ways a header can go wrong did not go with it, and
none of them is loud:

* the band holds the logo alone. A page that grows a title back, or a control moved up into
  the band, is a logo with something else in its row;
* the toolbar's controls -- the tabs, the notebook button and the season-and-league column --
  stay on their own row below it rather than across it; and
* every page fits the window it is read in, with nothing hanging off the side of it.

This check drives a real browser over every page mode the app links to, at nine widths either
side of the desktop breakpoint, and measures what is drawn in the logo's row against the
logo's own box.

    python tools/check_layout.py                 # starts its own server, uses your saved league
    python tools/check_layout.py --base-url http://127.0.0.1:8790
    python tools/check_layout.py --widths 640,980,981,1024,1440 --json layout.json

Needs Playwright (``pip install -r requirements-dev.txt``) and a Chromium-family browser;
it drives the installed Edge/Chrome first so no browser download is required. Measured text
widths depend on the Google-hosted webfonts, so the report says so when they did not load.

Exit codes: 0 clean, 1 something overlaps or overflows, 2 something could not be measured.

Note: every page view saves the connection (that is the app's own behaviour), so run this
against the league already in your settings, or the check will leave its own values there.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import threading
import urllib.parse
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

HOST = "127.0.0.1"

#: Widths either side of the 981px breakpoint, plus the widths where the page's 1024px
#: content box is the binding constraint. The reserve only has to hold at >= 981px; the
#: narrower widths are here to prove the logo is back in normal flow there.
DEFAULT_WIDTHS = (640, 900, 980, 981, 1000, 1100, 1280, 1600, 1920)

#: Sub-pixel slop. Boxes that merely touch are not an overlap.
DEFAULT_SLACK = 0.5

VIEWPORT_HEIGHT = 900

#: Measure after two frames: one for the resize to lay out, one for the paint to settle.
SETTLE = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"

#: Runs inside the page. Returns the logo box, every heading text line box, and the worst
#: horizontal relationship between them.
MEASURE_JS = r"""
() => {
  const rect = (box) => ({
    left: +box.left.toFixed(2), top: +box.top.toFixed(2),
    right: +box.right.toFixed(2), bottom: +box.bottom.toFixed(2),
    width: +box.width.toFixed(2), height: +box.height.toFixed(2),
  });
  const describe = (element) => {
    const name = element.tagName.toLowerCase();
    const classes = typeof element.className === "string" && element.className.trim()
      ? "." + element.className.trim().split(/\s+/).join(".")
      : "";
    return name + classes;
  };
  const shown = (element) => {
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && +style.opacity > 0;
  };

  const image = document.querySelector(".page-brand-logo");
  const usable = image && shown(image) && image.complete && image.naturalWidth > 0;
  const logo = usable ? image.getBoundingClientRect() : null;

  // Everything the logo could be drawn through. The band it stands in is measured as text --
  // the band holds the logo alone, so a line of text in it is a page's own heading come back --
  // and the toolbar under it as the boxes of what it holds, because that is what a control
  // moved up into the band would collide with.
  const texts = [];
  const band = document.querySelector(".page-head");
  if (band) {
    const walker = document.createTreeWalker(band, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.nodeValue || !node.nodeValue.trim()) continue;
      const host = node.parentElement;
      if (!host || !shown(host)) continue;
      // Skip labels hidden for screen readers and the text inside a select: those are measured
      // through the control's own box instead, because a range inside a select has no layout
      // of its own.
      if (host.closest('.visually-hidden, select, option, script, style, [aria-hidden="true"]')) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const line of range.getClientRects()) {
        if (line.width < 0.5 || line.height < 0.5) continue;
        texts.push(Object.assign(rect(line), { source: describe(host) }));
      }
    }
    // And the boxes of anything that is a control rather than a word -- a select, a button, a
    // picture -- because a control moved into the band brings no text this walk would read
    // (a select's own text has no layout) and it is the thing the logo would be drawn through.
    for (const child of band.querySelectorAll("select, button, input, img, a")) {
      if (child === image || child.contains(image) || !shown(child)) continue;
      const box = child.getBoundingClientRect();
      if (box.width < 0.5 || box.height < 0.5) continue;
      texts.push(Object.assign(rect(box), { source: describe(child) }));
    }
  }
  // The toolbar's own boxes and the controls inside them. The wrappers are measured as well as
  // the controls, because a wrapper is what a control moved up into the band would take with it;
  // the controls are measured in their own right because one of those wrappers carries no box at
  // all once the narrow layout reads them on a single row (``display:contents``), and a control
  // that is only measured through a wrapper would stop being measured at all.
  const toolbar = document.querySelector(".feature-toolbar");
  const toolbarBoxes = toolbar
    ? toolbar.querySelectorAll(".mode-switch, .changelog, .page-controls, select, button")
    : [];
  for (const control of toolbarBoxes) {
    if (!shown(control)) continue;
    const box = control.getBoundingClientRect();
    if (box.width < 0.5 || box.height < 0.5) continue;
    texts.push(Object.assign(rect(box), { source: describe(control) }));
  }

  // The four controls a reader uses at any width -- the notebook, the season being read, the
  // button that loads the league and the theme -- are one row while the toolbar is narrow: the
  // whole point of the narrow toolbar is that they are read on a line rather than stacked into
  // six. Above that layout's own breakpoint the season stands over the league it is read in,
  // which is the toolbar's design rather than a stack, so the row is only asked for below it.
  // Two controls share a line when their boxes overlap vertically, which is also what tells a
  // row apart from a stack: the season is shorter than the buttons beside it, so their tops are
  // never the same number.
  const rowControls = [".changelog-button", ".season-switcher select", ".preload-button",
                      ".skin-picker-trigger"]
    .map((selector) => (toolbar ? toolbar.querySelector(selector) : null))
    .filter((element) => element && shown(element));
  let oneRow = null;
  if (window.innerWidth <= 980 && rowControls.length > 1) {
    const boxes = rowControls.map((element) => element.getBoundingClientRect());
    oneRow = boxes.every((box) => boxes.every(
      (other) => Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top) > 0.5));
  }

  // A gap is the horizontal distance between the logo and a box; a negative gap is how far the
  // two overlap. Only boxes that share vertical space with the logo count.
  let clearance = null;
  let worst = null;
  let beside = 0;
  if (logo && texts.length) {
    for (const text of texts) {
      const across = Math.min(text.bottom, logo.bottom) - Math.max(text.top, logo.top);
      if (across <= 0) continue;
      beside += 1;
      const gap = Math.max(logo.left - text.right, text.left - logo.right);
      if (clearance === null || gap < clearance) clearance = +gap.toFixed(2);
      if (gap < 0 && (!worst || gap < worst.gap)) {
        worst = { gap: +gap.toFixed(2), across: +across.toFixed(2), text: text, logo: rect(logo) };
      }
    }
  }

  // The logo is the first thing on the page's own left edge, so that is where it should start.
  const page = document.querySelector(".page");
  let edge_gap = null;
  if (page && image) {
    const padding = parseFloat(getComputedStyle(page).paddingLeft) || 0;
    edge_gap = +(image.getBoundingClientRect().left - (page.getBoundingClientRect().left + padding)).toFixed(2);
  }

  // Nothing may hang off the side of the window: a table too wide for the wrapper that scrolls
  // it is the usual way this happens, and on a phone it is the whole page that slides sideways.
  const overflow = +(document.documentElement.scrollWidth - document.documentElement.clientWidth).toFixed(2);

  return {
    logo: logo && rect(logo),
    logo_src: image ? image.getAttribute("src") : null,
    text_count: texts.length,
    beside: beside,
    clearance: clearance,
    worst: worst,
    edge_gap: edge_gap,
    overflow: overflow,
    toolbar_row: {rows: rowControls.length, one_row: oneRow},
    web_fonts: document.fonts.status === "loaded"
      && document.fonts.check("700 38px Manrope") && document.fonts.check("400 16px 'DM Sans'"),
  };
}
"""


class Unmeasured(Exception):
    """A page that could not be measured, so it cannot be reported as clean."""


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


def start_server(port: int) -> tuple[object, str]:
    """Serve the app in this process so the check needs nothing already running."""
    from werkzeug.serving import make_server

    from app import create_app

    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # one line per request buries the report
    server = make_server(HOST, port, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{HOST}:{server.server_port}"


def launch(browser_type, headed: bool):
    """Use an installed Edge or Chrome before falling back to Playwright's own Chromium."""
    attempts = []
    for channel in ("msedge", "chrome", None):
        try:
            if channel is None:
                return browser_type.launch(headless=not headed), "bundled chromium"
            return browser_type.launch(channel=channel, headless=not headed), channel
        except Exception as exc:  # noqa: BLE001 - the next channel is the whole point
            attempts.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
    raise SystemExit("No browser available for the layout check.\n  " + "\n  ".join(attempts))


def load(page, url: str, timeout: int) -> None:
    """Open a page and wait until its text and fonts have settled."""
    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    status = response.status if response else 0
    if status != 200:
        raise Unmeasured(f"HTTP {status}")
    try:
        page.wait_for_selector(".page-head", timeout=timeout)
    except Exception:  # noqa: BLE001 - a page with no head band cannot be measured
        raise Unmeasured("the page rendered without its head band") from None
    # Text widths are the whole measurement, so the webfonts have to be in place first.
    page.evaluate("() => document.fonts.ready.then(() => true)")


def variant_key(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Identify a page mode: the same path and flags is the same page, whatever the order."""
    parts = urllib.parse.urlsplit(url)
    query = tuple(sorted(
        (key, value) for key, value in urllib.parse.parse_qsl(parts.query)
        if key not in ("history_id",)
    ))
    return parts.path, query


def label(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    page_name = parts.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
    flags = [key for key, _ in urllib.parse.parse_qsl(parts.query)
             if key not in ("username", "manager_id", "week", "history_id")]
    return f"{page_name} · {' '.join(flags) if flags else 'default'}"


def discover(page, seeds: list[str]) -> list[str]:
    """Follow the app's own mode links so a page mode the app adds is covered too."""
    found: list[str] = []
    seen: set[tuple] = set()

    def add(url: str) -> bool:
        key = variant_key(url)
        if key in seen:
            return False
        seen.add(key)
        found.append(url)
        return True

    queue = [url for url in seeds if add(url)]
    while queue:
        url = queue.pop(0)
        load(page, url, timeout=180_000)
        # Modes live in the mode switch; the season-wide Player Data tabs (Team, Season)
        # are week tabs, so follow those too and leave the per-week links to one side.
        hrefs = page.eval_on_selector_all(
            ".mode-switch a[href], .week-tabs a.seasonal-tab[href]",
            "els => els.map((el) => el.href).filter(Boolean)",
        )
        league_prefix = "/".join(urllib.parse.urlsplit(url).path.split("/")[:3])
        for href in hrefs:
            if not urllib.parse.urlsplit(href).path.startswith(league_prefix):
                continue  # another league, or the connection page
            if add(href):
                print(f"  found {label(href)}")
                queue.append(href)
    return found


def render(report: list[dict], widths: list[int], slack: float) -> int:
    print()
    overlaps: list[dict] = []
    unmeasured: list[dict] = []
    warnings: list[str] = []
    overflows: list[dict] = []
    stacked: list[dict] = []
    clearances: list[tuple[float, dict]] = []
    beside_widths = 0
    total = 0

    for entry in report:
        print(f"{label(entry['url'])}")
        print(f"  {entry['url'].split('://', 1)[-1].split('/', 1)[-1]}")
        print("  width   " + "".join(f"{width:>7}" for width in widths))
        cells = []
        for sample in entry["samples"]:
            total += 1
            if sample["error"]:
                cells.append("      ?")
                unmeasured.append({**sample, "url": entry["url"]})
                continue
            result = sample["result"]
            # A missing logo would make every page pass for the wrong reason, so it counts
            # as unmeasured rather than clean.
            if not result["logo"]:
                cells.append("      ?")
                unmeasured.append({**sample, "url": entry["url"], "error": (
                    "the brand logo did not render (it needs the account's leagues from "
                    "Sleeper), so there was nothing to measure")})
                continue
            # A page wider than its window is the other silent header failure: the tabs and the
            # switchers share one row, and a row that cannot fit slides the whole page sideways.
            if result["overflow"] > slack:
                cells.append("     !!")
                overflows.append({**sample, "url": entry["url"], "result": result})
                continue
            # A narrow toolbar that has stacked its controls is the other way a header ends up
            # several rows deep, so it is reported with the same mark as an overlap.
            row = result.get("toolbar_row") or {}
            if row.get("one_row") is False:
                cells.append("     !!")
                stacked.append({**sample, "url": entry["url"], "result": result})
                continue
            if result["worst"] and -result["worst"]["gap"] > slack:
                cells.append("     !!")
                overlaps.append({**sample, "url": entry["url"], "result": result})
                continue
            if result["beside"] == 0:
                cells.append("      –")
                continue
            beside_widths += 1
            cells.append(f"{result['clearance']:>7.0f}")
            clearances.append((result["clearance"], {**sample, "url": entry["url"]}))
            if result["clearance"] < 12:
                warnings.append(
                    f"only {result['clearance']:.0f}px between the logo and what shares its row "
                    f"on {label(entry['url'])} at {sample['width']}px"
                )
            if not result["web_fonts"]:
                warnings.append(
                    f"the Google webfonts did not load, so {label(entry['url'])} at "
                    f"{sample['width']}px was measured with a fallback font"
                )
            if result["edge_gap"] is not None and abs(result["edge_gap"]) > 1:
                warnings.append(
                    f"the logo sits {abs(result['edge_gap']):.0f}px "
                    f"{'past' if result['edge_gap'] > 0 else 'short of'} the page edge on "
                    f"{label(entry['url'])} at {sample['width']}px, so the band no longer "
                    "starts the logo on the page's own left edge"
                )
        print("  gap(px) " + "".join(f"{cell:>7}" for cell in cells))
        print()

    print("-" * 72)
    print(f"{len(report)} page modes x {len(widths)} widths = {total} measurements")
    print("  – = nothing shares the logo's row: the band holds the logo alone")
    print("  number = px of clear space between the logo and what shares its row")
    print("  !! = something overlaps the logo, runs off the page, or leaves the toolbar's "
          "controls stacked, ? = could not be measured")

    if clearances:
        tightest, where = min(clearances, key=lambda item: item[0])
        print(f"  tightest clearance: {tightest:.0f}px on {label(where['url'])} at {where['width']}px")
    print(f"  {beside_widths} widths with something beside the logo, "
          f"{total - beside_widths - len(unmeasured)} with the logo on its own row")

    if unmeasured:
        print(f"\nCOULD NOT MEASURE ({len(unmeasured)}) — a page the check could not read is not a pass:")
        seen = set()
        for sample in unmeasured:
            key = (sample["url"], sample["error"])
            if key in seen:
                continue
            seen.add(key)
            print(f"  {label(sample['url'])} at {sample['width']}px: {sample['error']}")

    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for text in dict.fromkeys(warnings):
            print(f"  {text}")

    if overlaps:
        print(f"\nOVERLAP ({len(overlaps)}) — something is drawn through the brand logo:")
        for sample in overlaps:
            worst = sample["result"]["worst"]
            print(f"  {label(sample['url'])} at {sample['width']}px: "
                  f"{worst['text']['source']} overlaps the logo by {-worst['gap']:.0f}px "
                  f"({worst['across']:.0f}px tall); its right edge "
                  f"{worst['text']['right']:.0f}px vs logo left edge {worst['logo']['left']:.0f}px")
        print("\nThe head band holds the logo alone: keep anything else on the toolbar's own "
              "row below it, or give the band room for the two.")
        return 1

    if overflows:
        print(f"\nOVERFLOW ({len(overflows)}) — the page is wider than the window it is read in:")
        for sample in overflows:
            print(f"  {label(sample['url'])} at {sample['width']}px: the document is "
                  f"{sample['result']['overflow']:.0f}px wider than the window")
        print("\nSomething on the page asks for more width than it has: a row that cannot wrap "
              "is the usual one, and on a header it is the toolbar's own row.")
        return 1

    if stacked:
        print(f"\nTOOLBAR ({len(stacked)}) — the toolbar's controls are not read on one line:")
        for sample in stacked:
            print(f"  {label(sample['url'])} at {sample['width']}px: the notebook, the season, "
                  "loading the league and the theme sit on more than one row")
        print("\nNarrow, those four are one row: the tabs keep a row of their own above them and "
              "everything else on the toolbar is placed on that line, the league taking a row "
              "below it when a phone cannot hold it.")
        return 1

    if unmeasured:
        return 2
    print("\nNothing is drawn through the brand logo, and no page runs off its own width.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league-id", help="league to check (default: the saved connection)")
    parser.add_argument("--username", help="Sleeper username (default: the saved connection)")
    parser.add_argument("--manager-id", help="Sleeper manager id (default: the saved connection)")
    parser.add_argument("--week", help="week to check (default: whatever the app opens on)")
    parser.add_argument("--base-url", help="check an already running instance instead of starting one")
    parser.add_argument("--widths", default=",".join(str(width) for width in DEFAULT_WIDTHS),
                        help="comma-separated viewport widths (default: %(default)s)")
    parser.add_argument("--slack", type=float, default=DEFAULT_SLACK,
                        help="overlap tolerated in px before reporting (default: %(default)s)")
    parser.add_argument("--json", help="write every measurement to this file")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("This check needs Playwright:\n  python -m pip install -r requirements-dev.txt",
              file=sys.stderr)
        return 2

    widths = [int(part) for part in args.widths.replace(" ", "").split(",") if part]

    from services.local_settings import load_connection

    saved = load_connection()
    league_id = args.league_id or str(saved.get("league_id") or "")
    username = args.username if args.username is not None else str(saved.get("username") or "")
    manager_id = args.manager_id if args.manager_id is not None else str(saved.get("manager_id") or "")
    if not league_id:
        print("No league to check. Connect the app to a league, or pass --league-id.",
              file=sys.stderr)
        return 2

    query = {key: value for key, value in
             (("username", username), ("manager_id", manager_id)) if value}
    # Resolve the week the app would open on, so the seeded URLs match the links the pages
    # themselves emit and a mode is not measured twice under two spellings of its week.
    week = args.week
    if week is None:
        try:
            from services.matchups import get_default_week

            week = str(get_default_week(league_id))
        except Exception:  # noqa: BLE001 - the app's default is a nicety, not a requirement
            week = None
    weekly = {"week": str(week)} if week else {}
    seed_paths = [
        f"/league/{league_id}/matchups",
        f"/league/{league_id}/seasonal",
        f"/league/{league_id}/player-data",
    ]

    server = None
    if args.base_url:
        base = args.base_url.rstrip("/")
    else:
        server, base = start_server(free_port())

    def seed(path: str, extra: dict | None = None) -> str:
        pairs = dict(query, **(extra or {}))
        return f"{base}{path}?{urllib.parse.urlencode(pairs)}"

    seeds = [
        seed(seed_paths[0], weekly),
        seed(seed_paths[1]),  # the seasonal page has no week of its own
        seed(seed_paths[2], weekly),
    ]

    print(f"Layout check — the brand logo against what is drawn beside it")
    print(f"  server   {base}{'' if args.base_url else ' (started by this check)'}")
    print(f"  league   {league_id}   username {username or '(none)'}")
    print(f"  widths   {', '.join(str(width) for width in widths)}")
    report: list[dict] = []
    try:
        with sync_playwright() as playwright:
            browser, channel = launch(playwright.chromium, args.headed)
            print(f"  browser  {channel}{' (headless)' if not args.headed else ''}")
            page = browser.new_context(viewport={"width": widths[0], "height": VIEWPORT_HEIGHT},
                                       reduced_motion="reduce").new_page()
            page.set_default_timeout(60_000)

            variants = discover(page, seeds)
            # Two toggles are forms rather than links, and each has its own description, so
            # follow() cannot find them: the Defense toggle, and the Group View checkbox.
            variants.append(seed(seed_paths[1], {"defense": "1"}))
            variants.append(seed(seed_paths[0], dict(weekly, broad="1")))
            variants.append(seed(seed_paths[1], {"broad": "1"}))
            print(f"  pages    {len(variants)} modes, discovered from the app's own links\n")

            measured: set[tuple] = set()
            for url in variants:
                if variant_key(url) in measured:  # a mode the app links to and this file adds
                    continue
                measured.add(variant_key(url))
                entry = {"url": url, "samples": []}
                try:
                    load(page, url, timeout=180_000)
                except Unmeasured as exc:
                    entry["samples"] = [
                        {"width": width, "error": str(exc)} for width in widths
                    ]
                    report.append(entry)
                    print(f"{label(url)}: {exc}")
                    continue
                print(f"{label(url)}")
                for width in widths:
                    page.set_viewport_size({"width": width, "height": VIEWPORT_HEIGHT})
                    page.evaluate(SETTLE)
                    result = page.evaluate(MEASURE_JS)
                    entry["samples"].append({"width": width, "error": None, "result": result})
                report.append(entry)
            browser.close()
    finally:
        if server is not None:
            server.shutdown()

    status = render(report, widths, args.slack)
    if args.json:
        Path(args.json).write_text(json.dumps({"base": base, "league_id": league_id,
                                               "widths": widths, "report": report},
                                              indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
