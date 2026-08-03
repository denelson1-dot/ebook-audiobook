"""Generate the application icons from one drawing.

Run after changing the design:

    python tools/make_icons.py

Outputs land in ``ebook_audiobook/assets/`` and are committed, so neither the
build nor the installer needs Pillow or a rasteriser. This exists because the
icon has to appear in eight sizes and three container formats, and hand-editing
that many files guarantees they drift apart.

Sizes below 48 px get a deliberately coarser drawing. Downsampling the full
design to 16 px turns the spine detail and the outer sound arc into grey mush;
dropping them and thickening what remains is what keeps the icon legible where
it is actually seen most — a taskbar and a title bar.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "ebook_audiobook" / "assets"

# The UI's accent (--accent) and near-black ink (--accent-ink) from
# web/static/app.css, so the icon and the app it opens are the same object.
INK = (10, 11, 15, 255)
ACCENT_TOP = (143, 157, 250, 255)
ACCENT_BOTTOM = (102, 117, 240, 255)

PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# The types a modern macOS .icns needs, and the pixel size each one holds.
#
# icp4/icp5/icp6 (16, 32 and 64 px) are deliberately absent. macOS renders those
# three *scrambled* when they carry a PNG payload and the file is used as an
# application icon — fine in a standalone .icns, garbage in Finder's list view
# and the sidebar. The @2x slots below cover the same point sizes (ic11 is
# 16pt@2x, ic12 is 32pt@2x) and macOS downsamples them cleanly for @1x, so
# dropping them costs nothing.
#
# It also removes a subtler wart: render() switches to the coarse drawing below
# 48 px, so keeping icp5 (32 px, coarse) beside ic12 (64 px, detailed) meant the
# same 32pt slot showed two different pictures depending on the display.
ICNS_TYPES = (
    (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
    (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512),
)

CANVAS = 1024
CORNER = 0.205  # rounded-square radius, as a fraction of the canvas

# One geometry, shared by the PNG/ICO/ICNS renderer and the SVG writer, so the
# scalable copy can never drift from the rasters. Book is (left, top, right,
# bottom); arcs radiate from the book's right edge and sweep +/- SWEEP degrees.
FULL = {
    "book": (140, 270, 500, 754), "radius": 44,
    "spine": (222, 270, 250, 754),
    "arcs": (130, 234, 338), "width": 58, "sweep": 52,
}
SMALL = {
    "book": (160, 285, 490, 739), "radius": 36,
    "spine": None,
    "arcs": (156, 292), "width": 96, "sweep": 54,
}


def _background(size: int) -> Image.Image:
    """A rounded square with a soft vertical gradient."""
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel((0, y), tuple(
            round(a + (b - a) * t) for a, b in zip(ACCENT_TOP, ACCENT_BOTTOM)))
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=round(size * 0.205), fill=255)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(gradient, (0, 0), mask)
    return out


def _draw(img: Image.Image, spec: dict) -> None:
    """A book, with sound arcs radiating from its right edge."""
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(spec["book"], radius=spec["radius"], fill=INK)
    if spec["spine"]:
        # Cut out of the cover rather than drawn on top of it, so the icon stays
        # a two-colour silhouette at every size.
        d.rectangle(spec["spine"], fill=(0, 0, 0, 0))

    cx, cy = spec["book"][2], CANVAS // 2
    sweep = spec["sweep"]
    for radius in spec["arcs"]:
        d.arc((cx - radius, cy - radius, cx + radius, cy + radius),
              start=-sweep, end=sweep, fill=INK, width=spec["width"])


def render(size: int) -> Image.Image:
    """One icon, drawn large and downsampled so the edges are antialiased."""
    img = _background(CANVAS)
    _draw(img, SMALL if size < 48 else FULL)
    return img.resize((size, size), Image.LANCZOS)


def write_svg(path: Path) -> None:
    """The scalable copy, for Linux's hicolor theme and anywhere a PNG won't do.

    Generated from the same FULL geometry as the rasters. Pillow's arc is
    butt-ended and measures angles clockwise from 3 o'clock with y pointing down,
    which is exactly SVG's convention, so the shapes transfer without adjustment.
    """
    import math

    bl, bt, br, bb = FULL["book"]
    sweep, cx, cy = FULL["sweep"], FULL["book"][2], CANVAS // 2

    def arc_path(r: float) -> str:
        a = math.radians(sweep)
        x0, y0 = cx + r * math.cos(-a), cy + r * math.sin(-a)
        x1, y1 = cx + r * math.cos(a), cy + r * math.sin(a)
        # large-arc=0 (the sweep is under 180 degrees), sweep-flag=1 (clockwise).
        return f"M {x0:.1f} {y0:.1f} A {r} {r} 0 0 1 {x1:.1f} {y1:.1f}"

    ink = "#%02x%02x%02x" % INK[:3]
    arcs = "\n".join(
        f'  <path d="{arc_path(r)}" fill="none" stroke="{ink}" '
        f'stroke-width="{FULL["width"]}"/>'
        for r in FULL["arcs"])
    # The spine is a cut-out in the raster; SVG gets the same effect from an
    # even-odd fill rule on a single path, which keeps it one shape.
    spine = ""
    if FULL["spine"]:
        sl, st, sr, sb = FULL["spine"]
        spine = (f'\n  <rect x="{sl}" y="{st}" width="{sr - sl}" '
                 f'height="{sb - st}" fill="url(#bg)"/>')

    path.write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" \
viewBox="0 0 {CANVAS} {CANVAS}" width="{CANVAS}" height="{CANVAS}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#%02x%02x%02x"/>
      <stop offset="1" stop-color="#%02x%02x%02x"/>
    </linearGradient>
  </defs>
  <rect width="{CANVAS}" height="{CANVAS}" rx="{round(CANVAS * CORNER)}" fill="url(#bg)"/>
  <rect x="{bl}" y="{bt}" width="{br - bl}" height="{bb - bt}" \
rx="{FULL['radius']}" fill="{ink}"/>{spine}
{arcs}
</svg>
""" % (ACCENT_TOP[:3] + ACCENT_BOTTOM[:3]), encoding="utf-8")


def write_icns(path: Path, images: dict[int, Image.Image]) -> None:
    """Assemble a PNG-based .icns by hand.

    Pillow can save .icns, but only a fixed set of types and only from a single
    source image. Writing the container directly is a dozen lines and lets each
    slot carry the drawing meant for its size — which is the whole point of
    having a coarse variant.
    """
    import io

    chunks = []
    for type_code, px in ICNS_TYPES:
        buf = io.BytesIO()
        images[px].save(buf, format="PNG")
        payload = buf.getvalue()
        chunks.append(type_code + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    needed = sorted(set(PNG_SIZES) | set(ICO_SIZES) | {px for _, px in ICNS_TYPES})
    images = {size: render(size) for size in needed}

    for size in PNG_SIZES:
        images[size].save(ASSETS / f"icon-{size}.png")
    images[512].save(ASSETS / "icon.ico",
                     sizes=[(s, s) for s in ICO_SIZES])
    write_icns(ASSETS / "icon.icns", images)
    write_svg(ASSETS / "icon.svg")

    print(f"wrote {len(PNG_SIZES)} PNGs, icon.svg, icon.ico and icon.icns to {ASSETS}")


if __name__ == "__main__":
    main()
