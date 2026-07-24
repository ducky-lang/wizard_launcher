"""The launcher's visual language, in one place.

Colours, fonts and the handful of building blocks every screen is made of.
The values come from ``launcher_core/data/theme.json`` so the look can be
changed without touching Python, with the previous hardcoded palette kept
here as the fallback - a missing data file must never produce an unstyled
window.

Everything in this module is pure construction: it builds controls and
returns them. Nothing here touches the page, opens a dialog or knows what
the launcher is doing, which is what makes it safe to import from anywhere
in ``ui/``.
"""

import flet as ft

from launcher_core.content import theme as _theme_data

_DATA = _theme_data()


def _palette(key, default):
    palette = _DATA.get("palette")
    value = palette.get(key) if isinstance(palette, dict) else None
    return value if isinstance(value, str) and value.startswith("#") else default


def _colors(key, default):
    value = _DATA.get(key)
    if isinstance(value, list):
        picked = [c for c in value if isinstance(c, str) and c.startswith("#")]
        if picked:
            return picked
    return default


def _anim(key, default):
    section = _DATA.get("animation")
    value = section.get(key) if isinstance(section, dict) else None
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def _font(key, family, url):
    section = _DATA.get("typography")
    entry = section.get(key) if isinstance(section, dict) else None
    if isinstance(entry, dict) and entry.get("family") and entry.get("url"):
        return str(entry["family"]), str(entry["url"])
    return family, url


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG = _palette("bg", "#08080c")
BG_DEEP = _palette("bg_deep", "#050508")
PANEL_BG = _palette("panel_bg", "#111118")
PANEL_BG_ALT = _palette("panel_bg_alt", "#141420")
CARD_BORDER = _palette("card_border", "#26262f")
GOLD = _palette("gold", "#f2c14e")
GOLD_BRIGHT = _palette("gold_bright", "#ffe08a")
GOLD_DIM = _palette("gold_dim", "#3a2f13")
GOLD_WASH = _palette("gold_wash", "#1a1608")
ARCANE = _palette("arcane", "#8b7ae8")
ARCANE_DIM = _palette("arcane_dim", "#241f3d")
DANGER = _palette("danger", "#ef4444")
DANGER_WASH = _palette("danger_wash", "#1a1214")
DANGER_BORDER = _palette("danger_border", "#3a1f22")
SUCCESS = _palette("success", "#7de3b0")
WARN = _palette("warn", "#fbbf24")
TEXT_MAIN = _palette("text_main", "#f4f4f8")
TEXT_SUB = _palette("text_sub", "#a8a8b8")
TEXT_FAINT = _palette("text_faint", "#6c6c7c")

FLAME_COLORS = _colors("flame_colors", ["#f2c14e", "#f7d476", "#ff9a3c", "#ff6f3c", "#ffe08a"])
SPARK_COLORS = _colors("spark_colors", ["#ffe08a", "#f2c14e", "#8b7ae8", "#ffffff", "#7de3b0"])

def _strings(key, default):
    value = _DATA.get(key)
    if isinstance(value, list):
        picked = [s for s in value if isinstance(s, str) and s.strip()]
        if picked:
            return picked
    return default


RUNES = _strings("runes", ["ᚱ", "ᚠ", "ᛒ", "ᛞ", "ᛁ", "ᛦ", "ᚨ", "ᛗ"])

# Status colour by system-check severity, so one mapping serves the first-run
# guide, the settings panel and the error dialog.
STATUS_COLORS = {"ok": SUCCESS, "warn": WARN, "fail": DANGER, "pending": TEXT_FAINT}
STATUS_ICONS = {
    "ok": ft.icons.CHECK_CIRCLE_ROUNDED,
    "warn": ft.icons.WARNING_AMBER_ROUNDED,
    "fail": ft.icons.ERROR_OUTLINE_ROUNDED,
    "pending": ft.icons.HOURGLASS_EMPTY_ROUNDED,
}


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
FONT_DISPLAY, _URL_DISPLAY = _font(
    "display", "Cinzel",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf")
FONT_DISPLAY_DECO, _URL_DISPLAY_DECO = _font(
    "display_decorative", "Cinzel Decorative",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/cinzeldecorative/CinzelDecorative-Bold.ttf")
FONT_BODY, _URL_BODY = _font(
    "body", "Poppins",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Regular.ttf")
FONT_BODY_MEDIUM, _URL_BODY_MEDIUM = _font(
    "body_medium", "Poppins Medium",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Medium.ttf")
FONT_BODY_SEMIBOLD, _URL_BODY_SEMIBOLD = _font(
    "body_semibold", "Poppins SemiBold",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-SemiBold.ttf")

FONT_MONO = "Consolas, monospace"

FONTS = {
    FONT_DISPLAY: _URL_DISPLAY,
    FONT_DISPLAY_DECO: _URL_DISPLAY_DECO,
    FONT_BODY: _URL_BODY,
    FONT_BODY_MEDIUM: _URL_BODY_MEDIUM,
    FONT_BODY_SEMIBOLD: _URL_BODY_SEMIBOLD,
}


# ---------------------------------------------------------------------------
# Animation budget
#
# The old loop ran 55 particles at ~22 fps unconditionally, which kept a core
# busy even when the launcher was sitting in the background.
# ---------------------------------------------------------------------------
PARTICLE_COUNT = _anim("particle_count", 34)
SPARK_COUNT = _anim("spark_count", 26)
FRAME_INTERVAL_FOCUSED = _anim("frame_interval_focused", 0.055)
FRAME_INTERVAL_BACKGROUND = _anim("frame_interval_background", 0.6)
RUNE_ROTATION_SECONDS = _anim("rune_rotation_seconds", 24)


def background_gradient():
    """The slow wash behind everything.

    Flat ``#08080c`` reads as "unfinished" on a big monitor; a barely-there
    vertical gradient with a warm bias at the bottom reads as candlelight
    without costing a frame to animate.
    """
    section = _DATA.get("background_gradient")
    colors = ["#0b0a14", BG, "#0d0a06"]
    stops = [0.0, 0.55, 1.0]
    if isinstance(section, dict):
        raw_colors = [c for c in section.get("colors") or [] if isinstance(c, str)]
        raw_stops = [s for s in section.get("stops") or [] if isinstance(s, (int, float))]
        if len(raw_colors) >= 2 and len(raw_stops) == len(raw_colors):
            colors, stops = raw_colors, [float(s) for s in raw_stops]
    return ft.LinearGradient(
        begin=ft.alignment.top_center, end=ft.alignment.bottom_center,
        colors=colors, stops=stops,
    )


def icon_name(name, fallback=ft.icons.CIRCLE_ROUNDED):
    """Resolve an icon named in a data file.

    Flet icon constants are plain lowercase strings, so the JSON can name one
    directly - but a typo would otherwise reach Flutter as an unknown icon
    and render as nothing at all, which looks like a layout bug.
    """
    if not isinstance(name, str) or not name:
        return fallback
    return getattr(ft.icons, name.upper(), fallback)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def heading(text, size=19, color=None):
    return ft.Text(text, size=size, weight=ft.FontWeight.W_600,
                   color=color or TEXT_MAIN, font_family=FONT_DISPLAY)


def section_label(text):
    return ft.Text(text.upper(), size=11, weight=ft.FontWeight.W_700, color=TEXT_FAINT,
                   font_family=FONT_BODY_SEMIBOLD,
                   style=ft.TextStyle(letter_spacing=2))


def body(text, size=13, color=None, **kwargs):
    return ft.Text(text, size=size, color=color or TEXT_SUB,
                   font_family=FONT_BODY, **kwargs)


def hint(text, size=11.5):
    return ft.Text(text, size=size, color=TEXT_FAINT, font_family=FONT_BODY)


def icon_button(icon, tooltip=None, on_click=None, color=None):
    return ft.Container(
        content=ft.Icon(icon, color=color or TEXT_SUB, size=17),
        width=38, height=38, border_radius=10,
        bgcolor=PANEL_BG_ALT, border=ft.border.all(0.6, CARD_BORDER),
        alignment=ft.alignment.center, tooltip=tooltip,
        on_click=on_click, ink=True,
    )


def action_button(text, icon, on_click, color=DANGER,
                  border=None, bg=None):
    """A bordered, tinted text button. Returns the container with its icon and
    label stashed in ``.data`` so :func:`set_enabled` can recolour them."""
    icon_ctrl = ft.Icon(icon, size=15, color=color)
    text_ctrl = ft.Text(text, size=13, color=color, weight=ft.FontWeight.W_500,
                        font_family=FONT_BODY_MEDIUM)
    btn = ft.Container(
        content=ft.Row([icon_ctrl, text_ctrl], spacing=8,
                       alignment=ft.MainAxisAlignment.CENTER),
        padding=ft.padding.symmetric(horizontal=16, vertical=10),
        border_radius=10,
        border=ft.border.all(0.6, border or DANGER_BORDER),
        bgcolor=bg or DANGER_WASH,
        ink=True, on_click=on_click,
    )
    btn.data = {"icon": icon_ctrl, "text": text_ctrl, "color": color}
    return btn


def primary_button(text, on_click, icon=None, width=None, height=44):
    content = [ft.Text(text, size=14, weight=ft.FontWeight.W_600, color="#161616",
                       font_family=FONT_BODY_SEMIBOLD)]
    if icon:
        content.insert(0, ft.Icon(icon, color="#161616", size=17))
    return ft.Container(
        content=ft.Row(content, spacing=9, alignment=ft.MainAxisAlignment.CENTER),
        width=width, height=height, border_radius=12, bgcolor=GOLD,
        alignment=ft.alignment.center, ink=True, on_click=on_click,
    )


def ghost_button(text, on_click, icon=None, color=None):
    return ft.TextButton(
        text, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(color=color or TEXT_SUB),
    )


def card(content, padding=16, bgcolor=None, border_color=None, **kwargs):
    return ft.Container(
        content=content, padding=padding,
        bgcolor=bgcolor or PANEL_BG_ALT, border_radius=12,
        border=ft.border.all(0.8, border_color or CARD_BORDER),
        **kwargs,
    )


def rune_badge(glyph, color=None, size=34):
    """A small circular sigil. Used as the marker for a wizard-themed list
    item, in place of a generic bullet or a numbered circle."""
    return ft.Container(
        content=ft.Text(glyph, size=size * 0.45, color=color or GOLD,
                        font_family=FONT_DISPLAY),
        width=size, height=size, border_radius=size / 2,
        bgcolor=GOLD_WASH, border=ft.border.all(0.8, GOLD_DIM),
        alignment=ft.alignment.center,
    )


def numbered_step(number, title, text):
    return ft.Row(
        [
            ft.Container(
                content=ft.Text(str(number), size=12, color=GOLD,
                                font_family=FONT_BODY_SEMIBOLD),
                width=26, height=26, border_radius=13, bgcolor=GOLD_WASH,
                border=ft.border.all(0.6, GOLD_DIM), alignment=ft.alignment.center,
            ),
            ft.Column(
                [
                    ft.Text(title, size=13, color=TEXT_MAIN, font_family=FONT_BODY_MEDIUM),
                    hint(text),
                ],
                spacing=2, expand=True,
            ),
        ],
        spacing=12, vertical_alignment=ft.CrossAxisAlignment.START,
    )


def setting_row(label, description, control):
    return ft.Row(
        [
            ft.Column(
                [
                    ft.Text(label, size=13, color=TEXT_MAIN, font_family=FONT_BODY),
                    hint(description, size=11),
                ],
                spacing=1, expand=True,
            ),
            control,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def status_row(check):
    """One line of a system check, coloured by severity."""
    color = STATUS_COLORS.get(check.status, TEXT_SUB)
    children = [
        ft.Text(check.title, size=13, color=TEXT_MAIN, font_family=FONT_BODY_MEDIUM),
        ft.Text(check.detail, size=11.5, color=TEXT_SUB),
    ]
    if check.remedy:
        children.append(ft.Text(check.remedy, size=11.5, color=color))

    return ft.Row(
        [
            ft.Icon(STATUS_ICONS.get(check.status, ft.icons.CIRCLE_OUTLINED),
                    color=color, size=18),
            ft.Column(children, spacing=2, expand=True),
        ],
        spacing=12, vertical_alignment=ft.CrossAxisAlignment.START,
    )


def dialog(title, content, actions, width=460, subtitle=None):
    """The launcher's one dialog shell.

    Every dialog in the app goes through here so the heading font, the corner
    radius and the action alignment cannot drift apart between screens.
    """
    heading_controls = [ft.Text(title, color=TEXT_MAIN, weight=ft.FontWeight.W_700,
                                font_family=FONT_DISPLAY, size=19)]
    if subtitle:
        heading_controls.append(ft.Text(subtitle, color=TEXT_FAINT, size=12,
                                        font_family=FONT_BODY))

    return ft.AlertDialog(
        modal=True, bgcolor=PANEL_BG,
        title=ft.Column(heading_controls, spacing=2, tight=True),
        content=ft.Container(content=content, width=width),
        actions=actions, shape=ft.RoundedRectangleBorder(radius=16),
        actions_alignment=ft.MainAxisAlignment.END,
    )


def set_enabled(btn, enabled):
    """Enable/disable a control built by :func:`action_button`."""
    btn.disabled = not enabled
    btn.opacity = 1.0 if enabled else 0.35
    color = btn.data["color"] if enabled else TEXT_FAINT
    btn.data["icon"].color = color
    btn.data["text"].color = color


def divider(height=24):
    return ft.Divider(color=CARD_BORDER, height=height)
