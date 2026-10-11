"""Rebuild the brand logo the pages load: ``static/deepthreat-arc-logo.webp``.

``static/deepthreat-arc-logo.png`` is the master -- ``packaging/make_icon.py`` draws the
Windows icon from it -- and it is 820 KB of PNG for a 1519x670 mark that is 69% empty. WebP
stores the same picture losslessly in about a third of that, which is what makes swapping it
into the pages safe: no pixel and no dimension changes, so nothing about where the logo sits or
how sharp it looks is different at any window width or screen density.

The check at the end is the point of the script as much as the encoding is. A logo is the one
image whose loss of quality nobody would notice in a diff and everybody would notice on the
page, so this refuses to write a file that decodes to anything other than the master's own
pixels: every alpha value identical, and every channel identical wherever the artwork draws.
Fully transparent pixels may differ, because a WebP encoder discards the colour under them --
nothing composites those, so nothing changes.

Run it after changing the master:

    python tools/make_web_logo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MASTER_PATH = STATIC_DIR / "deepthreat-arc-logo.png"
WEB_PATH = STATIC_DIR / "deepthreat-arc-logo.webp"


def visible_difference(master: Image.Image, candidate: Image.Image) -> tuple[int, int]:
    """Return the worst alpha and colour difference a viewer could see, or (0, 0) for none.

    Colour is compared only where both pictures draw something, and as the channels themselves
    rather than premultiplied, because the alpha verdict is reported separately: a pixel with
    alpha 0 draws nothing whatever colour is stored behind it.
    """
    master_pixels, candidate_pixels = master.load(), candidate.load()
    worst_alpha = worst_colour = 0
    for y in range(master.height):
        for x in range(master.width):
            here, there = master_pixels[x, y], candidate_pixels[x, y]
            worst_alpha = max(worst_alpha, abs(here[3] - there[3]))
            if here[3] > 0 and there[3] > 0:
                for channel in range(3):
                    worst_colour = max(worst_colour, abs(here[channel] - there[channel]))
    return worst_alpha, worst_colour


def main() -> int:
    master = Image.open(MASTER_PATH).convert("RGBA")
    before = MASTER_PATH.stat().st_size
    master.save(WEB_PATH, "WEBP", lossless=True, quality=100, method=6)
    after = WEB_PATH.stat().st_size

    reloaded = Image.open(WEB_PATH).convert("RGBA")
    if reloaded.size != master.size:
        print(f"{WEB_PATH.name} is {reloaded.size}, master is {master.size}", file=sys.stderr)
        return 1
    worst_alpha, worst_colour = visible_difference(master, reloaded)
    if worst_alpha or worst_colour:
        print(f"{WEB_PATH.name} is not identical: alpha off by {worst_alpha}, "
              f"colour off by {worst_colour}", file=sys.stderr)
        return 1

    print(f"wrote {WEB_PATH.name} ({reloaded.width}x{reloaded.height}, "
          f"{after:,} bytes, down from {before:,} -- {before / after:.1f}x smaller, "
          f"pixel-identical)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
