"""About, and the update notice.

Both live here because they answer the same question from opposite ends -
"what am I running?" and "what would I be running?" - and both render the
changelog, which is data rather than prose written twice.

The update flow is deliberately conservative: it tells the player a version
exists, shows them what changed, and opens the download page in their
browser. It never downloads or runs an installer by itself. A launcher that
silently fetches and executes a binary is exactly the mechanism a
supply-chain attacker wants, and the convenience is not worth handing it to
them.
"""

import flet as ft

from launcher_core import content, crypto
from launcher_core.constants import RELEASES_URL
from launcher_core.platform_utils import platform_name
from launcher_core.version import (
    ABOUT_TEXT, APP_NAME, CREDITS, LEGAL_NOTICE, VERSION, VERSION_STRING,
)

from . import brand, theme

# The mark lives in ui.brand now, so the logo can be swapped by replacing an
# asset rather than by editing a drawing. Re-exported because this is where
# the rest of the UI has always imported it from.
wand_mark = brand.logo_mark


def _changelog_block(release, dense=False):
    if not release:
        return ft.Container()

    header = ft.Row(
        [
            ft.Text(f"v{release.get('version', '')}", size=12.5, color=theme.GOLD,
                    font_family=theme.FONT_BODY_SEMIBOLD),
            ft.Text(release.get("title", ""), size=12.5, color=theme.TEXT_MAIN, expand=True),
            ft.Text(release.get("date", ""), size=11, color=theme.TEXT_FAINT),
        ],
        spacing=10,
    )

    lines = release.get("highlights" if dense else "notes") or release.get("notes") or []
    bullets = [
        ft.Row(
            [
                ft.Icon(ft.icons.CHEVRON_RIGHT_ROUNDED, size=13, color=theme.TEXT_FAINT),
                ft.Text(str(line), size=11.5, color=theme.TEXT_SUB, expand=True),
            ],
            spacing=6, vertical_alignment=ft.CrossAxisAlignment.START,
        )
        for line in lines
    ]
    return theme.card(ft.Column([header, ft.Container(height=6)] + bullets,
                                spacing=4, tight=True), padding=13)


def show(app):
    content_column = ft.Column(
        [
            ft.Row(
                [
                    wand_mark(),
                    ft.Column(
                        [
                            ft.Text(APP_NAME, size=19, color=theme.TEXT_MAIN,
                                    font_family=theme.FONT_DISPLAY,
                                    weight=ft.FontWeight.W_600),
                            ft.Text(f"{VERSION_STRING}  ·  {platform_name()}",
                                    size=12, color=theme.GOLD,
                                    font_family=theme.FONT_BODY_MEDIUM),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=14,
            ),
            ft.Container(height=14),
            theme.body(ABOUT_TEXT),
            theme.divider(),

            theme.section_label("What's new"),
            ft.Container(height=6),
            _changelog_block(content.release_for(VERSION)),
            theme.divider(),

            theme.section_label("Credits"),
            *[
                ft.Row([
                    ft.Text(role, size=12, color=theme.TEXT_FAINT, width=130),
                    ft.Text(who, size=12, color=theme.TEXT_MAIN, expand=True),
                ])
                for role, who in CREDITS
            ],
            theme.divider(),

            theme.section_label("Privacy & security"),
            theme.body(
                "Your Microsoft password is never seen or stored by this launcher - sign-in "
                "happens on Microsoft's own page and only tokens come back.\n\n"
                f"Saved tokens and your profile are encrypted with {crypto.describe_backend()} "
                "and cannot be read from another user account or another computer.\n\n"
                "The world server is bound to this computer only unless you turn on LAN play "
                "in Settings. Log files are scrubbed of tokens before anything is written, "
                "and nothing is ever uploaded anywhere.",
                size=12,
            ),
            theme.divider(),

            theme.section_label("Legal"),
            ft.Text(LEGAL_NOTICE, size=11, color=theme.TEXT_FAINT),
        ],
        tight=True, spacing=6, scroll=ft.ScrollMode.AUTO, height=440,
    )

    actions = []
    if RELEASES_URL:
        actions.append(theme.ghost_button(
            "All releases", lambda e: app.open_url(RELEASES_URL),
            icon=ft.icons.OPEN_IN_NEW_ROUNDED))
    actions.append(ft.ElevatedButton(
        "Close", on_click=lambda e: app.close_dialog(dlg),
        bgcolor=theme.GOLD, color="#161616"))

    dlg = theme.dialog("About", content_column, actions, width=540)
    app.open_dialog(dlg)


def show_update(app, update):
    """Offer the update; never install it automatically."""
    newer = content.release_notes_since(VERSION)
    changelog = [_changelog_block(release, dense=True) for release in newer]
    if not changelog and update.get("notes"):
        changelog = [theme.card(theme.body(update["notes"], size=11.5), padding=13)]

    body = ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.icons.SYSTEM_UPDATE_ALT_ROUNDED, size=22, color=theme.GOLD),
                    ft.Column(
                        [
                            ft.Text(f"Version {update['version']} is available",
                                    size=14, color=theme.TEXT_MAIN,
                                    font_family=theme.FONT_BODY_SEMIBOLD),
                            theme.hint(f"You are running {VERSION_STRING}."),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=12,
            ),
            ft.Container(height=12),
            theme.section_label("What's new"),
            ft.Container(height=6),
            *changelog,
            ft.Container(height=10),
            theme.hint("The download page opens in your browser - nothing is installed "
                       "automatically, and you can keep playing on this version."),
        ],
        tight=True, spacing=0, scroll=ft.ScrollMode.AUTO, height=340,
    )

    def open_page(e=None):
        app.close_dialog(dlg)
        if update.get("url"):
            app.open_url(update["url"])

    dlg = theme.dialog(
        "An update is available", body,
        [
            theme.ghost_button("Not now", lambda e: app.close_dialog(dlg)),
            ft.ElevatedButton("Open download page", on_click=open_page,
                              bgcolor=theme.GOLD, color="#161616"),
        ],
        width=520,
    )
    app.open_dialog(dlg)
