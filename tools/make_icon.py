#!/usr/bin/env python3
"""Generate the application icon.

    python tools/make_icon.py

Writes ``installer/app.ico`` (Windows), ``installer/linux/app.png`` (Linux
AppImage) and ``installer/macos/app.iconset`` plus ``app.icns`` when run on
macOS, where ``iconutil`` is available.

**Source of the artwork.** If a logo file exists at ``assets/floo-logo.png``
(or ``launcher_core/data/floo-logo.png``, which is what a packaged build
carries), every icon is rendered from it and it is also the mark the UI shows
- see :mod:`ui.brand`. Drop a square, transparent PNG in and re-run this; no
code changes.

With no such file the script falls back to drawing the previous mark, a gold
four-point sparkle on the launcher's panel colour. That fallback exists so a
checkout without the asset still produces a working installer rather than
failing to compile.

Written against zlib and struct rather than Pillow on purpose: this runs in
the build environment, and adding an imaging library as a build dependency to
draw four shapes is a poor trade. Pillow *is* used when it happens to be
installed, because its resampling of a real logo beats the box filter below;
the pure-Python path is there so the build never depends on it.
"""

import math
import os
import struct
import subprocess
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOGO_FILENAME = "floo-logo.png"
LOGO_CANDIDATES = (
    os.path.join(ROOT, "assets", LOGO_FILENAME),
    os.path.join(ROOT, "launcher_core", "data", LOGO_FILENAME),
)

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


def _drawn(size):
    """Return RGBA bytes for one square icon of `size` pixels, drawn."""
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
# Rendering from a logo file
# ---------------------------------------------------------------------------
def logo_source():
    """The first logo file that exists, or ``""``."""
    for path in LOGO_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


def _decode_png(path):
    """Minimal PNG reader: ``(width, height, rgba_bytes)``.

    Handles 8-bit greyscale/RGB/palette/alpha, non-interlaced - which is what
    any logo exported from a design tool will be. Anything else raises, and
    the caller falls back to the drawn icon rather than producing a mangled
    one. Pillow is used first when available; this is the no-dependency path.
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")

    pos = 8
    header = None
    palette = b""
    transparency = b""
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"tRNS":
            transparency = payload
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break

    if header is None:
        raise ValueError("PNG has no header")
    width, height, depth, colour_type, compression, filter_method, interlace = header
    if depth != 8 or interlace != 0 or compression != 0 or filter_method != 0:
        raise ValueError(f"unsupported PNG (depth={depth}, interlace={interlace})")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour_type)
    if channels is None:
        raise ValueError(f"unsupported PNG colour type {colour_type}")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(width * height * channels)

    # Undo the per-scanline filters. This is the whole of PNG decoding once
    # zlib has done the hard part.
    previous = bytearray(stride)
    offset = 0
    for row in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        for index in range(stride):
            left = line[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            value = line[index]
            if filter_type == 1:
                value += left
            elif filter_type == 2:
                value += up
            elif filter_type == 3:
                value += (left + up) // 2
            elif filter_type == 4:
                # Paeth
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                value += left if (pa <= pb and pa <= pc) else (up if pb <= pc else upper_left)
            elif filter_type != 0:
                raise ValueError(f"unknown PNG filter {filter_type}")
            line[index] = value & 0xFF
        out[row * stride:(row + 1) * stride] = line
        previous = line

    # Expand whatever colour type this was into straight RGBA.
    rgba = bytearray(width * height * 4)
    for pixel in range(width * height):
        source = pixel * channels
        target = pixel * 4
        if colour_type == 0:
            grey = out[source]
            rgba[target:target + 4] = bytes((grey, grey, grey, 255))
        elif colour_type == 2:
            rgba[target:target + 3] = out[source:source + 3]
            rgba[target + 3] = 255
        elif colour_type == 3:
            index = out[source]
            rgba[target:target + 3] = palette[index * 3:index * 3 + 3]
            rgba[target + 3] = transparency[index] if index < len(transparency) else 255
        elif colour_type == 4:
            grey, alpha = out[source], out[source + 1]
            rgba[target:target + 4] = bytes((grey, grey, grey, alpha))
        else:
            rgba[target:target + 4] = out[source:source + 4]

    return width, height, bytes(rgba)


def _load_logo_rgba(path):
    """``(width, height, rgba)`` for the logo, preferring Pillow if present."""
    try:
        from PIL import Image
    except ImportError:
        return _decode_png(path)
    with Image.open(path) as image:
        image = image.convert("RGBA")
        return image.width, image.height, image.tobytes()


def _box_resize(source, source_width, source_height, size):
    """Average each destination pixel over its source footprint.

    A box filter rather than nearest-neighbour: a logo reduced to 16x16 by
    point sampling loses whole strokes, which is exactly the size where the
    icon has to stay recognisable. Alpha is premultiplied during the average
    so transparent edge pixels do not drag colour into the result.
    """
    out = bytearray(size * size * 4)
    for y in range(size):
        y0 = y * source_height // size
        y1 = max(y0 + 1, (y + 1) * source_height // size)
        for x in range(size):
            x0 = x * source_width // size
            x1 = max(x0 + 1, (x + 1) * source_width // size)
            r = g = b = a = 0
            count = 0
            for sy in range(y0, y1):
                row = sy * source_width
                for sx in range(x0, x1):
                    index = (row + sx) * 4
                    alpha = source[index + 3]
                    r += source[index] * alpha
                    g += source[index + 1] * alpha
                    b += source[index + 2] * alpha
                    a += alpha
                    count += 1
            target = (y * size + x) * 4
            if a:
                out[target] = min(255, r // a)
                out[target + 1] = min(255, g // a)
                out[target + 2] = min(255, b // a)
                out[target + 3] = a // count
            # else: leave fully transparent black
    return bytes(out)


def _fit_square(rgba, width, height):
    """Pad a non-square logo to a square canvas so it is not distorted."""
    if width == height:
        return rgba, width
    side = max(width, height)
    out = bytearray(side * side * 4)
    left = (side - width) // 2
    top = (side - height) // 2
    for y in range(height):
        source = y * width * 4
        target = ((y + top) * side + left) * 4
        out[target:target + width * 4] = rgba[source:source + width * 4]
    return bytes(out), side


def _stage_logo_for_packaging(source):
    """Copy the logo into ``launcher_core/data`` so a built app still has it.

    PyInstaller is told ``--collect-data=launcher_core``, which carries that
    folder into the bundle; a top-level ``assets/`` directory is not collected
    by anything. Staging it here means "drop the file in, run this script,
    build" works, instead of the logo showing up in a source run and quietly
    reverting to the drawn mark in the installer.
    """
    destination = os.path.join(ROOT, "launcher_core", "data", LOGO_FILENAME)
    if os.path.abspath(source) == os.path.abspath(destination):
        return
    try:
        with open(source, "rb") as src:
            payload = src.read()
        if os.path.isfile(destination):
            with open(destination, "rb") as existing:
                if existing.read() == payload:
                    return
        with open(destination, "wb") as out:
            out.write(payload)
        print(f"  staged {os.path.relpath(destination, ROOT)} for packaging")
    except OSError as e:
        print(f"  ! could not stage the logo for packaging: {e}")


class _Renderer:
    """Produces icon pixels, from the logo file when there is one."""

    def __init__(self):
        self.source = logo_source()
        self._square = None
        self._side = 0
        if self.source:
            try:
                width, height, rgba = _load_logo_rgba(self.source)
                self._square, self._side = _fit_square(rgba, width, height)
            except Exception as e:
                print(f"  ! could not read {os.path.relpath(self.source, ROOT)}: {e}")
                print("    falling back to the drawn mark.")
                self.source = ""

    @property
    def describes(self):
        return (os.path.relpath(self.source, ROOT) if self.source
                else "the built-in drawn mark")

    def __call__(self, size):
        if self._square is None:
            return _drawn(size)
        if size == self._side:
            return self._square
        return _box_resize(self._square, self._side, self._side, size)


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

    render = _Renderer()
    print(f"Source: {render.describes}")
    if not render.source:
        print(f"  (put a square PNG at assets/{LOGO_FILENAME} to use your own logo)")
    else:
        _stage_logo_for_packaging(render.source)

    print("Rendering icon sizes:", ", ".join(str(s) for s in ICO_SIZES))
    images = []
    for size in ICO_SIZES:
        images.append((size, encode_png(render(size), size)))
        print(f"  {size}x{size} ok")

    ico_bytes = encode_ico(images)
    ico_path = os.path.join(installer_dir, "app.ico")
    with open(ico_path, "wb") as f:
        f.write(ico_bytes)
    print(f"Wrote {os.path.relpath(ico_path, ROOT)} ({len(ico_bytes) / 1024:.1f} KB)")

    # A second copy inside the package data. PyInstaller is told to collect
    # launcher_core's data, so this one ships with the app and is what
    # platform_utils.set_window_icon() hands to Win32 at runtime. The
    # installer/ copy is build input and is gitignored; this one is not.
    runtime_ico = os.path.join(ROOT, "launcher_core", "data", "app.ico")
    with open(runtime_ico, "wb") as f:
        f.write(ico_bytes)
    print(f"Wrote {os.path.relpath(runtime_ico, ROOT)} (runtime window icon)")

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
