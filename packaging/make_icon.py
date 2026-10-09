"""Rebuild DeepThreat.ico: the arc logo on a dark rounded tile.

The logo is a wide, pale strip of thin strokes with nothing behind it. On a light desktop
wallpaper that reads as almost nothing — at 16 px the strokes thin out to a pixel or two of
pale green on pale grey. Windows honours the icon's transparency, so the fix is to stop
relying on the wallpaper: draw a dark rounded tile the full size of the canvas and lay the
artwork on it, which is what app tiles do.

Each size is rendered from its own supersampled master rather than downscaling one big
image, so the strokes stay crisp at 16 px and the artwork can take up proportionally more of
the tile as the tile shrinks.

Run it with Pillow available (``pip install pillow``); it rewrites ``DeepThreat.ico`` next to
this file. Add ``--preview PATH`` to also write a PNG sheet for eyeballing sizes.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PIL import Image, ImageDraw

SPEC_DIR = Path(__file__).resolve().parent
LOGO_PATH = SPEC_DIR.parent / "static" / "deepthreat-arc-logo.png"
ICON_PATH = SPEC_DIR / "DeepThreat.ico"

#: Sizes Windows asks for, largest first so the ICO holds a frame for each.
SIZES = (256, 128, 64, 48, 32, 24, 16)
#: How far each size's master is rendered above its final resolution before shrinking.
SUPERSAMPLE = 4
#: Corner radius as a fraction of the tile, near Windows' own app-tile rounding.
CORNER_RADIUS = 0.21
#: The plate: the app's own page colours, its radial glow at the top fading to the page black.
PLATE_TOP = (38, 55, 44)
PLATE_BOTTOM = (14, 18, 16)
PLATE_EDGE = (61, 81, 66)
#: Share of the tile's width the artwork spans. Thin strokes need more room as the tile shrinks.
ART_WIDTH = {16: 0.99, 24: 0.97, 32: 0.94, 48: 0.90, 64: 0.88, 128: 0.86, 256: 0.86}


def load_artwork() -> Image.Image:
    """Return the arc logo cropped to the ink, so centring it centres what is drawn."""
    logo = Image.open(LOGO_PATH).convert("RGBA")
    return logo.crop(logo.getchannel("A").getbbox())


def plate(size: int) -> Image.Image:
    """Return a rounded, dark, softly graded tile filling a square canvas of ``size``."""
    radius = round(size * CORNER_RADIUS)
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        mix = y / max(1, size - 1)
        gradient.putpixel((0, y), tuple(
            round(PLATE_TOP[channel] + (PLATE_BOTTOM[channel] - PLATE_TOP[channel]) * mix)
            for channel in range(3)
        ) + (255,))
    tile = gradient.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    tile.putalpha(mask)
    # A hairline edge keeps the tile from blurring into a dark wallpaper, where a plain black
    # square leaves the artwork floating with no shape of its own. Drawn after the mask so it
    # follows the rounding and leaves the corners outside it transparent.
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, outline=PLATE_EDGE + (255,),
        width=max(1, round(size * 0.008)),
    )
    return tile


def frame(size: int, artwork: Image.Image) -> Image.Image:
    """Return one finished icon size: the plate with the artwork centred on it."""
    big = size * SUPERSAMPLE
    canvas = plate(big)
    width = round(big * ART_WIDTH[size])
    height = round(artwork.height * width / artwork.width)
    art = artwork.resize((width, height), Image.LANCZOS)
    canvas.alpha_composite(art, ((big - width) // 2, (big - height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def write_ico(path: Path, frames: list[Image.Image]) -> None:
    """Write the frames as one .ico, each stored the way Windows reads them.

    Pillow's own ICO writer PNG-compresses every frame, and Windows' icon loader turns out
    to reject the larger ones written that way — ``System.Drawing.Icon`` refuses the 128 and
    256 frames with a range error, which would leave Explorer falling back to a small frame.
    The classic encoding is a BMP: a 32-bit DIB followed by a 1-bit AND mask. Writing that
    by hand keeps the frames readable, and the masks stay correct on paths that ignore alpha.
    """
    header = struct.pack("<HHH", 0, 1, len(frames))
    directory, blobs = b"", []
    offset = len(header) + 16 * len(frames)
    for frame in frames:
        pixels = frame.load()
        rows = b""
        for y in range(frame.height - 1, -1, -1):
            # A DIB stores bottom-up rows of BGRA, not the RGBA Pillow hands back. Writing the
            # channels in the wrong order swaps red and blue, which is invisible in the tile but
            # turns the logo's green arc teal.
            for x in range(frame.width):
                red, green, blue, alpha = pixels[x, y]
                rows += bytes((blue, green, red, alpha))
        # The AND mask is one bit per pixel, bottom-up, each row padded to four bytes.
        mask = b""
        for y in range(frame.height - 1, -1, -1):
            bits = bytearray()
            for x in range(frame.width):
                if x % 8 == 0:
                    bits.append(0)
                if pixels[x, y][3] < 128:
                    bits[-1] |= 0x80 >> (x % 8)
            bits += bytes(-len(bits) % 4)
            mask += bytes(bits)
        dib = struct.pack("<IiiHHIIiiII", 40, frame.width, frame.height * 2, 1, 32, 0,
                          len(rows), 0, 0, 0, 0)
        blob = dib + rows + mask
        dimension = 0 if frame.width == 256 else frame.width
        directory += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                                 len(blob), offset)
        blobs.append(blob)
        offset += len(blob)
    path.write_bytes(header + directory + b"".join(blobs))


def write_preview(frames: dict[int, Image.Image], path: Path) -> None:
    """Write the sizes onto light and dark bands, magnified, so the result can be judged."""
    missing = [size for size in (256, 64, 48, 32, 16) if size not in frames]
    if missing:
        raise ValueError(f"preview needs frames for {missing}")
    scale, pad, label = 4, 24, 22
    columns = [(size, frames[size].resize((size * scale, size * scale), Image.NEAREST))
               for size in (256, 64, 48, 32, 16)]
    width = pad + sum(column.width + pad for _, column in columns)
    height = pad * 2 + max(column.height for _, column in columns) + label
    sheet = Image.new("RGBA", (width, height * 2), (0, 0, 0, 255))
    for band, background in enumerate(((240, 237, 228, 255), (16, 21, 19, 255))):
        x = pad
        for _, column in columns:
            sheet.alpha_composite(
                Image.new("RGBA", column.size, background), (x, band * height + pad)
            )
            sheet.alpha_composite(column, (x, band * height + pad))
            x += column.width + pad
    sheet.convert("RGB").save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", help="also write a PNG sheet of the sizes to this path")
    args = parser.parse_args()

    artwork = load_artwork()
    frames = {size: frame(size, artwork) for size in SIZES}
    write_ico(ICON_PATH, [frames[size] for size in SIZES])
    if args.preview:
        write_preview(frames, Path(args.preview))
    print(f"wrote {ICON_PATH} ({', '.join(f'{size}x{size}' for size in SIZES)})")


if __name__ == "__main__":
    main()
