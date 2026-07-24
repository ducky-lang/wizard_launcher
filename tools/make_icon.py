#!/usr/bin/env python3
"""Generate the application icon.

    python tools/make_icon.py

Writes ``installer/app.ico`` (Windows), ``installer/linux/app.png`` (Linux
AppImage) and ``installer/macos/app.iconset`` plus ``app.icns`` when run on
macOS, where ``iconutil`` is available.

Written against zlib and struct rather than Pillow on purpose: this runs in
the build environment, and adding an imaging library as a build dependency to
draw four shapes is a poor trade. The drawing is analytic and supersampled 4x,
which is what keeps the small sizes legible.

The motif matches the launcher's own UI - a gold four-point sparkle on the
same near-black panel colour, the AUTO_AWESOME icon the title bar uses.
"""

import math
import os
import struct
import subprocess
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Straight from ui/main_window.py's palette.
BG_OUTER = (8, 8, 12)
BG_PANEL = (26, 22, 8)
BORDER = (58, 47, 19)
GOLD = (242, 193, 78)
GOLD_LIGHT = (255, 224, 138)

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)

SUPERSAMPLE = 4


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _rounded_rect_coverage(x, y, half, radius):
    """Exact signed distance to a rounded square centred on the origin.

    Negative inside, zero on the edge. The naive "distance to the corner
    circle plus distance to the box" version leaves a visible notch at each
    corner, which at 32px reads as a chipped icon.
    """
    qx = abs(x) - half + radius
    qy = abs(y) - half + radius
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - radius


def _star_coverage(x, y, radius, power=0.5):
    """A four-point sparkle: the astroid |x|^p + |y|^p <= r^p.

    p = 0.5 gives the concave-sided star used by the launcher's UI icon; the
    closer p gets to 1 the more it becomes a plain diamond.
    """
    if radius <= 0:
        return 1.0
    value = (abs(x) / radius) ** power + (abs(y) / radius) ** power
    return value - 1.0


def _blend(base, top, alpha):
    return tuple(int(round(b + (t - b) * alpha)) for b, t in zip(base, top))


def render(size):
    """Return RGBA bytes for one square icon of `size` pixels."""
    ss = SUPERSAMPLE if size <= 256 else 2
    big = size * ss
    half = big / 2.0
    centre = half

    # Geometry as fractions of the icon, so every size looks like the same icon.
    panel_half = big * 0.42
    panel_radius = big * 0.13
    border_width = max(big * 0.012, ss * 0.75)
    star_radius = big * 0.30
    small_star_radius = big * 0.10
    small_star_offset = big * 0.245

    accumulator = [[0, 0, 0, 0] for _ in range(size * size)]

    for py in range(big):
        y = py + 0.5 - centre
        row_index = (py // ss) * size
        for px in range(big):
            x = px + 0.5 - centre

            colour = None
            alpha = 0.0

            panel = _rounded_rect_coverage(x, y, panel_half, panel_radius)
            if panel <= 0:
                # Vertical gradient so the tile does not look flat at 256px.
                mix = (y + panel_half) / (2 * panel_half)
                colour = _blend(BG_OUTER, BG_PANEL, max(0.0, min(1.0, mix)))
                alpha = 1.0
                if panel >= -border_width:
                    colour = BORDER

            # Glow, then the sparkle itself, drawn over the panel.
            star = _star_coverage(x, y, star_radius)
            if star <= 0:
                colour = GOLD_LIGHT if star < -0.45 else GOLD
                alpha = 1.0
            elif star < 0.35 and colour is not None:
                glow = 1.0 - (star / 0.35)
                colour = _blend(colour, GOLD, glow * 0.28)

            small = _star_coverage(
                abs(x) - small_star_offset, abs(y) - small_star_offset,
                small_star_radius,
            )
            if small <= 0 and x > 0 and y < 0:
                colour = GOLD_LIGHT
                alpha = 1.0

            if colour is None:
                continue

            cell = accumulator[row_index + (px // ss)]
            cell[0] += colour[0] * alpha
            cell[1] += colour[1] * alpha
            cell[2] += colour[2] * alpha
            cell[3] += 255 * alpha

    samples = float(ss * ss)
    out = bytearray()
    for cell in accumulator:
        out += bytes((
            int(round(cell[0] / samples)),
            int(round(cell[1] / samples)),
            int(round(cell[2] / samples)),
            int(round(cell[3] / samples)),
        ))
    return bytes(out)


# ---------------------------------------------------------------------------
# PNG / ICO encoding
# ---------------------------------------------------------------------------
def _chunk(tag, payload):
    data = tag + payload
    return struct.pack(">I", len(payload)) + data + struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)


def encode_png(rgba, size):
    raw = bytearray()
    stride = size * 4
    for row in range(size):
        raw.append(0)  # filter type 0 (None) - the images are tiny
        raw += rgba[row * stride:(row + 1) * stride]

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def encode_ico(images):
    """images: list of (size, png_bytes). Modern Windows reads PNG-in-ICO."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    offset = 6 + 16 * count
    for size, png in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0, 0, 1, 32, len(png), offset,
        )
        offset += len(png)
    return header + entries + b"".join(png for _size, png in images)


# ---------------------------------------------------------------------------
def main():
    installer_dir = os.path.join(ROOT, "installer")
    os.makedirs(installer_dir, exist_ok=True)

    print("Rendering icon sizes:", ", ".join(str(s) for s in ICO_SIZES))
    images = []
    for size in ICO_SIZES:
        images.append((size, encode_png(render(size), size)))
        print(f"  {size}x{size} ok")

    ico_path = os.path.join(installer_dir, "app.ico")
    with open(ico_path, "wb") as f:
        f.write(encode_ico(images))
    print(f"Wrote {os.path.relpath(ico_path, ROOT)} ({os.path.getsize(ico_path) / 1024:.1f} KB)")

    # Linux: a single 256x256 PNG, the size AppImage/.desktop icons expect.
    linux_dir = os.path.join(installer_dir, "linux")
    os.makedirs(linux_dir, exist_ok=True)
    png_path = os.path.join(linux_dir, "app.png")
    with open(png_path, "wb") as f:
        f.write(encode_png(render(256), 256))
    print(f"Wrote {os.path.relpath(png_path, ROOT)}")

    # macOS: an .iconset folder that iconutil turns into an .icns.
    macos_dir = os.path.join(installer_dir, "macos")
    iconset = os.path.join(macos_dir, "app.iconset")
    os.makedirs(iconset, exist_ok=True)
    for size in ICNS_SIZES:
        png = encode_png(render(size), size)
        if size <= 512:
            with open(os.path.join(iconset, f"icon_{size}x{size}.png"), "wb") as f:
                f.write(png)
        if size >= 32:
            half = size // 2
            with open(os.path.join(iconset, f"icon_{half}x{half}@2x.png"), "wb") as f:
                f.write(png)
    print(f"Wrote {os.path.relpath(iconset, ROOT)}")

    if sys.platform == "darwin":
        icns = os.path.join(macos_dir, "app.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
        print(f"Wrote {os.path.relpath(icns, ROOT)}")
    else:
        print("Run this again on macOS (or `iconutil -c icns installer/macos/app.iconset`)"
              " to produce app.icns.")


if __name__ == "__main__":
    main()
