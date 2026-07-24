"""Help: getting started, FAQ, and step-by-step fixes.

The old help dialog was a single column of four setup steps and five short
answers. It was honest about what it covered and useless for anything else,
because there was no way to look something up - you read all of it or none
of it.

This is three tabs over the same data file the rest of the app uses, with a
search box that filters the FAQ and the troubleshooting entries together.
The point is that somebody typing "port" finds the port answer in two
seconds, rather than reading nine paragraphs about resource packs first.

Two things at the bottom of every tab, because they are what somebody who
got this far actually needs: a button that builds the diagnostic report,
and one that opens a GitHub issue with the details already filled in.
"""

import threading

import flet as ft

from launcher_core import content, diagnostics
from launcher_core.constants import EXTERNAL_LINKS_ENABLED, ISSUES_URL

from . import theme


def show(app, focus=None):
    """Open help. ``focus`` names a troubleshooting entry to expand first -
    the error dialog uses it to jump straight to the relevant fix."""
    HelpDialog(app, focus=focus).open()


class HelpDialog:
    def __init__(self, app, focus=None):
        self.app = app
        self.focus = focus
        self.query = ""

        self.faq_column = ft.Column(spacing=10, tight=True, scroll=ft.ScrollMode.AUTO)
        self.fix_column = ft.Column(spacing=10, tight=True, scroll=ft.ScrollMode.AUTO)

        self.search_field = ft.TextField(
            hint_text="Search help", height=38, text_size=12.5, border_radius=10,
            bgcolor=theme.PANEL_BG_ALT, border_color=theme.CARD_BORDER,
            focused_border_color=theme.GOLD, color=theme.TEXT_MAIN, dense=True,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=6),
            prefix_icon=ft.icons.SEARCH_ROUNDED,
            on_change=self._on_search,
        )

        self.tabs = ft.Tabs(
            selected_index=1 if focus else 0, animation_duration=200,
            indicator_color=theme.GOLD, label_color=theme.GOLD,
            unselected_label_color=theme.TEXT_FAINT, divider_color=theme.CARD_BORDER,
            expand=True,
            tabs=[
                ft.Tab(text="Getting started", icon=ft.icons.FLAG_ROUNDED,
                       content=self._pad(self._getting_started())),
                ft.Tab(text="Fixes", icon=ft.icons.BUILD_ROUNDED,
                       content=self._pad(self.fix_column)),
                ft.Tab(text="Questions", icon=ft.icons.HELP_OUTLINE_ROUNDED,
                       content=self._pad(self.faq_column)),
            ],
        )

        self._render_lists()

        actions = [
            theme.ghost_button("Setup guide", self._replay_onboarding,
                               icon=ft.icons.SCHOOL_ROUNDED),
            theme.ghost_button("Diagnostic report", self._build_report,
                               icon=ft.icons.BUG_REPORT_ROUNDED),
        ]
        if EXTERNAL_LINKS_ENABLED:
            actions.append(theme.ghost_button(
                "Report an issue", lambda e: self.app.report_issue(),
                icon=ft.icons.OPEN_IN_NEW_ROUNDED,
                color=theme.GOLD if ISSUES_URL else theme.TEXT_FAINT))
        actions.append(ft.ElevatedButton(
            "Got it", on_click=lambda e: self.app.close_dialog(self.dialog),
            bgcolor=theme.GOLD, color="#161616"))

        self.dialog = theme.dialog(
            "Help & Setup Guide",
            ft.Column(
                [self.search_field, ft.Container(content=self.tabs, height=400)],
                spacing=10, tight=True,
            ),
            actions,
            width=620,
        )

    def open(self):
        self.app.open_dialog(self.dialog)

    # ------------------------------------------------------------------
    @staticmethod
    def _pad(control):
        return ft.Container(content=control,
                            padding=ft.padding.only(top=14, left=2, right=8))

    def _on_search(self, e):
        self.query = (e.control.value or "").strip().lower()
        self._render_lists()
        # Searching means "find me the answer", so put the user on a tab that
        # has results rather than leaving them on Getting Started.
        if self.query and self.tabs.selected_index == 0:
            self.tabs.selected_index = 1 if self.fix_column.controls else 2
        self.app.refresh_dialog(self.dialog)

    def _matches(self, *fields):
        if not self.query:
            return True
        haystack = " ".join(str(f).lower() for f in fields if f)
        return all(word in haystack for word in self.query.split())

    def _render_lists(self):
        self.fix_column.controls = self._fixes()
        self.faq_column.controls = self._faq()

    # ------------------------------------------------------------------
    def _getting_started(self):
        steps = content.getting_started()
        controls = [theme.section_label("Four steps to the castle"), ft.Container(height=8)]
        controls += [
            theme.numbered_step(index + 1, step["title"], step["body"])
            for index, step in enumerate(steps)
        ]
        controls += [
            theme.divider(),
            theme.section_label("Where your files live"),
            ft.Text(self.app.base_dir, size=11.5, color=theme.TEXT_SUB, selectable=True),
            ft.Container(height=6),
            ft.Row([
                theme.ghost_button("Open game folder", lambda e: self.app.open_data_folder(),
                                   icon=ft.icons.FOLDER_ROUNDED),
                theme.ghost_button("Open log folder", lambda e: self.app.logger.open_folder(),
                                   icon=ft.icons.FOLDER_OPEN_ROUNDED),
            ]),
        ]
        return ft.Column(controls, spacing=10, tight=True, scroll=ft.ScrollMode.AUTO)

    def _fixes(self):
        entries = [
            entry for entry in content.troubleshooting()
            if self._matches(entry.get("symptom"), entry.get("cause"),
                             " ".join(entry.get("steps") or []))
        ]
        if not entries:
            return [theme.hint("Nothing matches that. Try a shorter word, or use "
                               "\"Report an issue\" below.")]

        tiles = []
        for entry in entries:
            body = []
            if entry.get("cause"):
                body.append(ft.Row(
                    [
                        ft.Icon(ft.icons.INFO_OUTLINE_ROUNDED, size=14, color=theme.TEXT_FAINT),
                        ft.Text(entry["cause"], size=11.5, color=theme.TEXT_FAINT, expand=True),
                    ],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                ))
                body.append(ft.Container(height=4))
            body += [
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Text(str(number + 1), size=10, color=theme.GOLD,
                                            font_family=theme.FONT_BODY_SEMIBOLD),
                            width=18, height=18, border_radius=9, bgcolor=theme.GOLD_WASH,
                            alignment=ft.alignment.center,
                        ),
                        ft.Text(step, size=12, color=theme.TEXT_SUB, expand=True,
                                selectable=True),
                    ],
                    spacing=9, vertical_alignment=ft.CrossAxisAlignment.START,
                )
                for number, step in enumerate(entry.get("steps") or [])
            ]

            tiles.append(ft.ExpansionTile(
                title=ft.Text(entry["symptom"], size=13, color=theme.TEXT_MAIN,
                              font_family=theme.FONT_BODY_MEDIUM),
                leading=ft.Icon(ft.icons.BUILD_CIRCLE_ROUNDED, size=18, color=theme.GOLD),
                # Expanded when the caller pointed at this entry, or when a
                # search narrowed it down to a handful - in both cases the
                # answer should already be on screen.
                initially_expanded=(entry.get("id") == self.focus
                                    or (bool(self.query) and len(entries) <= 3)),
                collapsed_bgcolor=theme.PANEL_BG_ALT, bgcolor=theme.PANEL_BG_ALT,
                collapsed_icon_color=theme.TEXT_FAINT, icon_color=theme.GOLD,
                controls=[ft.Container(
                    content=ft.Column(body, spacing=6, tight=True),
                    padding=ft.padding.only(left=16, right=16, bottom=14),
                )],
            ))
        return tiles

    def _faq(self):
        entries = [
            entry for entry in content.faq()
            if self._matches(entry.get("question"), entry.get("answer"),
                             " ".join(entry.get("tags") or []))
        ]
        if not entries:
            return [theme.hint("No question matches that. Try the Fixes tab, or "
                               "\"Report an issue\" below.")]
        return [
            theme.card(
                ft.Column(
                    [
                        ft.Text(entry["question"], size=12.5, color=theme.TEXT_MAIN,
                                font_family=theme.FONT_BODY_MEDIUM),
                        ft.Text(entry["answer"], size=11.5, color=theme.TEXT_SUB,
                                selectable=True),
                    ],
                    spacing=4, tight=True,
                ),
                padding=13,
            )
            for entry in entries
        ]

    # ------------------------------------------------------------------
    def _replay_onboarding(self, e=None):
        self.app.close_dialog(self.dialog)
        self.app.show_onboarding(force=True)

    def _build_report(self, e=None):
        def work():
            path = diagnostics.build_report(
                self.app.logger, self.app.settings, self.app.java_manager,
            )
            self.app.schedule_ui(self.app.report_created, path)

        threading.Thread(target=work, daemon=True, name="diag-report").start()
        self.app.toast("Building the report...")
