"""The launcher's mark, in one place.

The logo is an *asset* rather than something drawn in code, so it can be
replaced by dropping a file in without touching Python. Put a square PNG at
``assets/floo-logo.png`` (transparent background, 512px or larger) and it
becomes the mark in the title bar, in About, and - once
``python tools/make_icon.py`` is run - the Windows/macOS/Linux application
icon too.

When no file is present the previous drawn mark is used instead. That
fallback is not decoration: a build that shipped without the asset would
otherwise show an empty box where the logo goes, which looks like a broken
install rather than a missing file.

The image is read once and embedded as base64 rather than served from an
assets folder. Flet's ``assets_dir`` would need the folder to survive
PyInstaller's packaging and to be found relative to a frozen executable;
base64 needs neither, and the file is a few kilobytes.
"""

import base64
import os
import threading

import flet as ft

from launcher_core.paths import get_app_root

from . import theme

LOGO_FILENAME = "floo-logo.png"

_CACHE = {}
_LOCK = threading.Lock()


def _candidate_paths():
    """Where the logo can be, in order of preference.

    ``launcher_core/data`` is first because ``--collect-data=launcher_core``
    already carries that folder into a packaged build; ``assets/`` next to
    the source tree is the convenient place to drop one during development.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    paths = [
        os.path.join(root, "launcher_core", "data", LOGO_FILENAME),
        os.path.join(root, "assets", LOGO_FILENAME),
    ]
    try:
        app_root = get_app_root()
        paths.append(os.path.join(app_root, "launcher_core", "data", LOGO_FILENAME))
        paths.append(os.path.join(app_root, "assets", LOGO_FILENAME))
    except Exception:
        pass
    return paths


def logo_path():
    """The first logo file that exists, or ``""``."""
    for path in _candidate_paths():
        if os.path.isfile(path):
            return path
    return ""


def icon_path():
    """The ``.ico`` used for the Windows window and taskbar icon, or ``""``.

    Written by ``tools/make_icon.py`` into the package data so it ships with
    a build; the ``installer/`` copy is build input and is not installed.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    candidates = [
        os.path.join(root, "launcher_core", "data", "app.ico"),
        os.path.join(root, "installer", "app.ico"),
    ]
    try:
        app_root = get_app_root()
        candidates.insert(0, os.path.join(app_root, "launcher_core", "data", "app.ico"))
    except Exception:
        pass
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def logo_base64():
    """The logo as base64, or ``""`` when there is no file to use."""
    with _LOCK:
        if "b64" in _CACHE:
            return _CACHE["b64"]
        encoded = ""
        path = logo_path()
        if path:
            try:
                with open(path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("ascii")
            except OSError:
                encoded = ""
        _CACHE["b64"] = encoded
        return encoded


def has_logo():
    return bool(logo_base64())


def drawn_mark(size=52, icon_size=24):
    """The fallback mark: a wand striking a spark.

    Built from primitives so it scales cleanly, tints with the palette, and
    adds nothing to the download. Kept as the fallback rather than deleted -
    a build without the logo asset still needs *something* in the corner.
    """
    return ft.Container(
        content=ft.Stack(
            [
                ft.Container(
                    content=ft.Icon(ft.icons.AUTO_FIX_HIGH_ROUNDED, color=theme.GOLD,
                                    size=icon_size),
                    alignment=ft.alignment.center, width=size, height=size,
                ),
                ft.Container(
                    content=ft.Icon(ft.icons.AUTO_AWESOME_ROUNDED, color=theme.GOLD_BRIGHT,
                                    size=icon_size * 0.42),
                    left=size * 0.60, top=size * 0.14, opacity=0.95,
                ),
                ft.Container(
                    content=ft.Icon(ft.icons.AUTO_AWESOME_ROUNDED, color=theme.ARCANE,
                                    size=icon_size * 0.28),
                    left=size * 0.14, top=size * 0.60, opacity=0.75,
                ),
            ],
            width=size, height=size,
        ),
        width=size, height=size, border_radius=size * 0.27,
        bgcolor=theme.GOLD_WASH, border=ft.border.all(0.8, theme.GOLD_DIM),
        alignment=ft.alignment.center,
    )


def logo_mark(size=52, icon_size=24):
    """The application mark.

    Returns a FRESH control every call. Never share one instance between two
    places in the tree: Flet stamps a control with a uid the first time it is
    rendered, and the same object under a second parent leaves the
    Flutter-side control map with one id in two places - at which point the
    window goes blank with no Python traceback to explain it.
    """
    encoded = logo_base64()
    if not encoded:
        return drawn_mark(size=size, icon_size=icon_size)

    return ft.Container(
        content=ft.Image(
            src_base64=encoded, width=size, height=size,
            fit=ft.ImageFit.CONTAIN,
            # A fresh fallback for the same reason as above.
            error_content=drawn_mark(size=size, icon_size=icon_size),
        ),
        width=size, height=size, border_radius=size * 0.27,
        alignment=ft.alignment.center,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
    )
