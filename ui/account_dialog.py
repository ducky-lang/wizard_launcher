"""Signing in, signing out, and explaining the difference.

Guest mode and Microsoft sign-in are both legitimate ways to play this map,
and the launcher previously presented that as one sentence above a button.
This lays the two out side by side with what each one actually gets you -
the same data the first-run guide uses - so the choice is informed rather
than guessed.

Signing out is its own labelled, destructive-styled button rather than a
"Confirm" on a generic dialog, because "am I about to lose my session?" is
not a question anybody should have to answer from context.
"""

import flet as ft

from launcher_core import content, crypto

from . import theme


def show(app):
    if app.account:
        _show_signed_in(app)
    else:
        _show_signed_out(app)


def _mode_card(mode, action):
    is_signin = mode.get("id") == "signin"
    return theme.card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(theme.icon_name(mode.get("icon")), size=17,
                                color=theme.GOLD if is_signin else theme.TEXT_SUB),
                        ft.Text(mode["title"], size=13.5, color=theme.TEXT_MAIN,
                                font_family=theme.FONT_BODY_SEMIBOLD),
                    ],
                    spacing=8,
                ),
                theme.hint(mode.get("summary", ""), size=11.5),
                ft.Container(height=6),
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.icons.CHECK_ROUNDED, size=12, color=theme.SUCCESS),
                                ft.Text(point, size=11.5, color=theme.TEXT_SUB, expand=True),
                            ],
                            spacing=7, vertical_alignment=ft.CrossAxisAlignment.START,
                        )
                        for point in mode.get("points") or []
                    ],
                    spacing=5, tight=True,
                ),
                ft.Container(height=10),
                action,
            ],
            spacing=4, tight=True,
        ),
        padding=14,
        border_color=theme.GOLD_DIM if is_signin else theme.CARD_BORDER,
        expand=True,
    )


def _microsoft_logo():
    return ft.Column(
        [
            ft.Row([ft.Container(width=9, height=9, bgcolor="#f25022"),
                    ft.Container(width=9, height=9, bgcolor="#7fba00")], spacing=2),
            ft.Row([ft.Container(width=9, height=9, bgcolor="#00a4ef"),
                    ft.Container(width=9, height=9, bgcolor="#ffb900")], spacing=2),
        ],
        spacing=2,
    )


def _show_signed_out(app):
    def do_signin(e=None):
        app.close_dialog(dlg)
        app.start_microsoft_login()

    def stay_guest(e=None):
        app.close_dialog(dlg)
        app.toast("Playing as a guest. Type the name you want on the main screen.")

    signin_action = ft.Container(
        content=ft.Row(
            [_microsoft_logo(),
             ft.Text("Sign in with Microsoft", size=13, weight=ft.FontWeight.W_600,
                     color=theme.TEXT_MAIN, font_family=theme.FONT_BODY_SEMIBOLD)],
            spacing=10, alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.symmetric(vertical=11), border_radius=10, bgcolor="#1c1c26",
        border=ft.border.all(0.8, theme.CARD_BORDER), ink=True, on_click=do_signin,
        alignment=ft.alignment.center,
    )
    guest_action = ft.Container(
        content=ft.Text("Continue as a guest", size=13, color=theme.TEXT_SUB,
                        font_family=theme.FONT_BODY_MEDIUM),
        padding=ft.padding.symmetric(vertical=11), border_radius=10,
        bgcolor=theme.PANEL_BG, border=ft.border.all(0.8, theme.CARD_BORDER),
        ink=True, on_click=stay_guest, alignment=ft.alignment.center,
    )

    modes = content.account_modes()
    cards = []
    for mode in modes:
        cards.append(_mode_card(mode, signin_action if mode.get("id") == "signin"
                                else guest_action))
    if not cards:
        cards = [signin_action, guest_action]

    body = ft.Column(
        [
            theme.body("Both work with this map. Sign in to be recognised by your real "
                       "Minecraft name and skin, or play as a guest under any name."),
            ft.Container(height=12),
            ft.Row(cards, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=12),
            ft.Row(
                [
                    ft.Icon(ft.icons.LOCK_ROUNDED, size=13, color=theme.SUCCESS),
                    theme.hint("Your password is never sent to or stored by this launcher. "
                               f"Tokens are encrypted with {crypto.describe_backend()}.",
                               size=11),
                ],
                spacing=7, vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ],
        tight=True, spacing=0,
    )

    dlg = theme.dialog(
        "Enter the Wizarding World", body,
        [theme.ghost_button("Maybe later", lambda e: app.close_dialog(dlg))],
        width=560,
    )
    app.open_dialog(dlg)


def _show_signed_in(app):
    account = app.account

    def sign_out(e=None):
        app.close_dialog(dlg)
        app.sign_out()

    body = ft.Column(
        [
            ft.Row(
                [
                    ft.Container(
                        content=ft.Image(
                            src=f"https://crafatar.com/avatars/{account.uuid}?size=96&overlay",
                            width=48, height=48, fit=ft.ImageFit.COVER, border_radius=12,
                            error_content=ft.Icon(ft.icons.PERSON_ROUNDED, size=22,
                                                  color=theme.GOLD),
                        ),
                        width=48, height=48, border_radius=12,
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                        border=ft.border.all(1, theme.GOLD_DIM),
                    ),
                    ft.Column(
                        [
                            ft.Text(account.username, size=16, color=theme.TEXT_MAIN,
                                    font_family=theme.FONT_BODY_SEMIBOLD),
                            theme.hint("Signed in with Microsoft"),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=14,
            ),
            ft.Container(height=14),
            theme.card(
                ft.Row(
                    [
                        ft.Icon(ft.icons.SHIELD_ROUNDED, size=17, color=theme.SUCCESS),
                        theme.hint(
                            f"Your session is encrypted with {crypto.describe_backend()} and "
                            "kept on this computer only. It cannot be read from another "
                            "account or another machine.", size=11.5),
                    ],
                    spacing=10, vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                padding=12,
            ),
            ft.Container(height=14),
            theme.section_label("Sign out"),
            theme.hint("Removes your saved session from this computer. You can keep playing "
                       "as a guest, and sign back in whenever you like."),
            ft.Container(height=10),
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.icons.LOGOUT_ROUNDED, size=15, color=theme.DANGER),
                        ft.Text("Sign out", size=13, color=theme.DANGER,
                                font_family=theme.FONT_BODY_MEDIUM),
                    ],
                    spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                ),
                padding=ft.padding.symmetric(vertical=10, horizontal=18),
                border_radius=10, bgcolor=theme.DANGER_WASH,
                border=ft.border.all(0.8, theme.DANGER_BORDER),
                ink=True, on_click=sign_out, alignment=ft.alignment.center,
            ),
        ],
        tight=True, spacing=0,
    )

    dlg = theme.dialog(
        "Your account", body,
        [ft.ElevatedButton("Done", on_click=lambda e: app.close_dialog(dlg),
                           bgcolor=theme.GOLD, color="#161616")],
        width=460,
    )
    app.open_dialog(dlg)
