"""The first-run guide.

A player who double-clicks a launcher for the first time is deciding, in
about ninety seconds, whether this is going to work. Previously they were
shown a Play button and nothing else: no idea what the map was, whether
their PC could run it, how long the first launch would take, or what "Guest
Wizard" in the corner meant. The ones whose disk was full found out eleven
minutes into a download.

This is five screens that answer those questions before anything is
downloaded:

1. **Welcome** - what this map is and who made it.
2. **System check** - disk, memory, Java, network. Live, and honest about
   what it cannot fix.
3. **Account** - Guest and Microsoft sign-in laid out side by side with what
   each one actually gets you.
4. **Performance** - the memory the launcher picked, and a one-tap way to
   change its mind.
5. **Ready** - what the first launch will do, and how long it will take.

The content is data (``launcher_core/data/content.json``); this module is
only the presentation. The wizard can be dismissed at any point - "Skip
setup" is a first-class button, not a hidden escape - and it records the
version that completed it so a later release can reintroduce it if the setup
genuinely changes.
"""

import threading

import flet as ft

from launcher_core import content, system_check
from launcher_core.config import auto_client_ram_mb, auto_server_ram_mb
from launcher_core.version import MAP_CREDIT, VERSION

from . import theme

# Memory presets offered on the performance step, as multipliers of the
# automatically detected value. "Balanced" is what the launcher would have
# chosen on its own.
MEMORY_PRESETS = [
    ("light", "Lighter", 0.75, "Leaves more room for everything else you have open."),
    ("balanced", "Balanced", 1.0, "What the launcher recommends for this computer."),
    ("performance", "Performance", 1.35, "Smoother world, less room for other programs."),
]


def should_show(settings):
    """True when the guide has never been completed on this install."""
    return not (settings.get("onboarding_completed_version") or "").strip()


def mark_complete(settings):
    settings.update(onboarding_completed_version=VERSION)


class OnboardingWizard:
    """Builds and drives the first-run dialog.

    ``app`` is the :class:`~ui.main_window.LauncherApp`; the wizard uses its
    dialog plumbing, its settings and its logger rather than owning any of
    them, so closing the launcher mid-guide leaves nothing behind.
    """

    def __init__(self, app, on_finish=None, start_index=0):
        self.app = app
        self.settings = app.settings
        self.on_finish = on_finish or (lambda play_now: None)

        self.steps = content.onboarding_steps() or [
            {"id": "ready", "title": "Ready", "body": "", "kind": "ready"}
        ]
        self.index = max(0, min(start_index, len(self.steps) - 1))

        self._checks = []
        self._checks_running = False
        self._memory_preset = "balanced"

        self.body_area = ft.Container(height=310)
        self.dots_row = ft.Row(spacing=6, alignment=ft.MainAxisAlignment.CENTER)
        self.step_title = theme.heading("", size=20)
        self.step_caption = theme.hint("")

        self.back_btn = theme.ghost_button("Back", self._back, icon=ft.icons.ARROW_BACK_ROUNDED)
        self.skip_btn = theme.ghost_button("Skip setup", self._skip)
        self.next_btn = ft.ElevatedButton("Next", on_click=self._next,
                                          bgcolor=theme.GOLD, color="#161616")

        self.dialog = theme.dialog(
            "Welcome to Wizard Launcher",
            ft.Column(
                [
                    ft.Row([self.step_title], alignment=ft.MainAxisAlignment.START),
                    self.step_caption,
                    ft.Container(height=10),
                    self.body_area,
                    ft.Container(height=6),
                    self.dots_row,
                ],
                tight=True, spacing=2,
            ),
            [self.skip_btn, self.back_btn, self.next_btn],
            width=560,
            subtitle=f"Setup guide  ·  step 1 of {len(self.steps)}",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def open(self):
        self._render()
        self.app.open_dialog(self.dialog)

    def _close(self):
        self.app.close_dialog(self.dialog)

    def _skip(self, e=None):
        mark_complete(self.settings)
        self.app.log("Setup guide skipped - you can reopen it from Help.")
        self._close()
        self.on_finish(False)

    def _back(self, e=None):
        if self.index > 0:
            self.index -= 1
            self._render()

    def _next(self, e=None):
        if self.index >= len(self.steps) - 1:
            self._finish(play_now=True)
            return
        self.index += 1
        self._render()

    def _finish(self, play_now):
        mark_complete(self.settings)
        self._apply_memory_preset()
        self._close()
        self.on_finish(play_now)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(self):
        step = self.steps[self.index]
        kind = step.get("kind") or step.get("id")

        self.step_title.value = step.get("title", "")
        self.step_caption.value = step.get("body", "")
        self.dialog.title.controls[1].value = (
            f"Setup guide  ·  step {self.index + 1} of {len(self.steps)}"
        )

        self.body_area.content = {
            "story": self._story_body,
            "system_check": self._system_body,
            "account": self._account_body,
            "memory": self._memory_body,
            "ready": self._ready_body,
        }.get(kind, self._story_body)()

        self.dots_row.controls = [
            ft.Container(
                width=18 if i == self.index else 7, height=7, border_radius=4,
                bgcolor=theme.GOLD if i == self.index else theme.CARD_BORDER,
                animate=ft.Animation(220, ft.AnimationCurve.EASE_OUT),
            )
            for i in range(len(self.steps))
        ]

        self.back_btn.visible = self.index > 0
        self.next_btn.text = "Let's go" if self.index == len(self.steps) - 1 else "Next"

        self.app.refresh_dialog(self.dialog)

    # ------------------------------------------------------------------
    # Step bodies
    # ------------------------------------------------------------------
    def _story_body(self):
        story = content.story()
        controls = [
            ft.Row(
                [
                    theme.rune_badge(theme.RUNES[0], size=40),
                    ft.Column(
                        [
                            ft.Text(story["title"], size=20, color=theme.TEXT_MAIN,
                                    font_family=theme.FONT_DISPLAY,
                                    weight=ft.FontWeight.W_600),
                            theme.hint(story["tagline"] or f"A map by {MAP_CREDIT}"),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=14,
            ),
            ft.Container(height=12),
        ]
        for paragraph in story["paragraphs"]:
            controls.append(theme.body(paragraph, size=12.5))
            controls.append(ft.Container(height=8))

        if story["facts"]:
            controls.append(theme.card(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(fact["label"], size=11.5, color=theme.TEXT_FAINT,
                                        width=150),
                                ft.Text(fact["value"], size=11.5, color=theme.TEXT_MAIN,
                                        expand=True),
                            ],
                        )
                        for fact in story["facts"]
                    ],
                    spacing=6, tight=True,
                ),
                padding=14,
            ))

        return ft.Column(controls, tight=True, spacing=0, scroll=ft.ScrollMode.AUTO,
                         height=310)

    def _system_body(self):
        self.system_column = ft.Column(
            [ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2, color=theme.GOLD),
                     theme.body("Checking your computer...")], spacing=10)],
            tight=True, spacing=12, scroll=ft.ScrollMode.AUTO, height=310,
        )
        self.system_summary = None
        if not self._checks_running:
            self._checks_running = True
            threading.Thread(target=self._run_checks, daemon=True,
                             name="onboarding-check").start()
        elif self._checks:
            self._paint_checks()
        return self.system_column

    def _run_checks(self):
        try:
            checks = system_check.run_all(java_manager=self.app.java_manager)
        except Exception as e:  # a check that crashes must not strand the guide
            self.app.logger.write(f"System check failed: {e}", level="WARN")
            checks = []
        self._checks = checks
        self._checks_running = False
        self.app.schedule_ui(self._paint_checks)

    def _paint_checks(self):
        if not self._checks:
            self.system_column.controls = [
                theme.body("The system check could not run. That will not stop you "
                           "from playing - press Next.")
            ]
            self.app.refresh_dialog(self.dialog)
            return

        summary = system_check.summary_line(self._checks)
        status = system_check.worst_status(self._checks)
        self.system_column.controls = [
            theme.card(
                ft.Row(
                    [
                        ft.Icon(theme.STATUS_ICONS.get(status), size=20,
                                color=theme.STATUS_COLORS.get(status, theme.SUCCESS)),
                        ft.Text(summary, size=12.5, color=theme.TEXT_MAIN, expand=True),
                    ],
                    spacing=10,
                ),
                padding=12,
            ),
            ft.Container(height=2),
        ] + [theme.status_row(check) for check in self._checks] + [
            ft.Container(height=4),
            ft.Row([theme.ghost_button("Check again", self._recheck,
                                       icon=ft.icons.REFRESH_ROUNDED)]),
        ]
        self.app.refresh_dialog(self.dialog)

    def _recheck(self, e=None):
        self._checks = []
        self.system_column.controls = [
            ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2, color=theme.GOLD),
                    theme.body("Checking again...")], spacing=10)
        ]
        self.app.refresh_dialog(self.dialog)
        self._checks_running = True
        threading.Thread(target=self._run_checks, daemon=True, name="onboarding-recheck").start()

    def _account_body(self):
        modes = content.account_modes()
        cards = []
        for mode in modes:
            is_signin = mode.get("id") == "signin"
            action = (
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.icons.LOGIN_ROUNDED, size=15, color=theme.TEXT_MAIN),
                            ft.Text("Sign in now", size=12.5, color=theme.TEXT_MAIN,
                                    font_family=theme.FONT_BODY_MEDIUM),
                        ],
                        spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(vertical=9), border_radius=10,
                    bgcolor="#1c1c26", border=ft.border.all(0.8, theme.CARD_BORDER),
                    ink=True, on_click=self._request_signin,
                )
                if is_signin else
                # A *new* TextField, never the main window's one. Flet stamps
                # a control with a uid the first time it is rendered; moving
                # that same object into a dialog leaves the Flutter-side
                # control map holding one id in two places, and the window
                # goes blank with no Python traceback to show for it.
                ft.TextField(
                    value=self.app.player_name, hint_text="Player name",
                    height=40, text_size=13, border_radius=10,
                    bgcolor=theme.PANEL_BG, border_color=theme.CARD_BORDER,
                    focused_border_color=theme.GOLD, color=theme.TEXT_MAIN,
                    content_padding=ft.padding.symmetric(horizontal=12, vertical=6),
                    disabled=self.app.account is not None,
                    on_change=lambda e: self.app.set_player_name(e.control.value),
                )
            )

            cards.append(
                theme.card(
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(theme.icon_name(mode.get("icon")),
                                            size=17,
                                            color=theme.GOLD if is_signin else theme.TEXT_SUB),
                                    ft.Text(mode["title"], size=13.5, color=theme.TEXT_MAIN,
                                            font_family=theme.FONT_BODY_SEMIBOLD,
                                            expand=True),
                                ],
                                spacing=8,
                            ),
                            theme.hint(mode.get("summary", ""), size=11.5),
                            ft.Container(height=4),
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(ft.icons.CHECK_ROUNDED, size=12,
                                                    color=theme.SUCCESS),
                                            ft.Text(point, size=11.5, color=theme.TEXT_SUB,
                                                    expand=True),
                                        ],
                                        spacing=7,
                                        vertical_alignment=ft.CrossAxisAlignment.START,
                                    )
                                    for point in mode.get("points") or []
                                ],
                                spacing=5, tight=True,
                            ),
                            ft.Container(height=8),
                            action,
                        ],
                        spacing=4, tight=True,
                    ),
                    padding=14,
                    border_color=theme.GOLD_DIM if is_signin else theme.CARD_BORDER,
                    expand=True,
                )
            )

        if not cards:
            cards = [theme.body("You can sign in from the top right of the launcher at any time.")]

        return ft.Column(
            [
                ft.Row(cards, spacing=12,
                       vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Container(height=8),
                theme.hint("You can switch between these at any time - click your name in "
                           "the top right corner of the launcher."),
            ],
            tight=True, spacing=0, scroll=ft.ScrollMode.AUTO, height=310,
        )

    def _request_signin(self, e=None):
        """Hand off to the real sign-in flow and come back afterwards.

        Two modal dialogs stacked on top of each other is a reliable way to
        make Flet's overlay unhappy, so the guide steps aside and is reopened
        on the next step once authentication settles either way.
        """
        resume_at = min(self.index + 1, len(self.steps) - 1)
        self._close()
        self.app.start_microsoft_login(
            on_settled=lambda: self.app.reopen_onboarding(resume_at)
        )

    def _memory_body(self):
        total_note = next(
            (check.detail for check in self._checks if check.key == "memory"),
            "The launcher sizes memory from the RAM it detects.",
        )
        server_base, client_base = auto_server_ram_mb(), auto_client_ram_mb()

        self.preset_labels = {}
        cards = []
        for key, label, factor, description in MEMORY_PRESETS:
            server = int(server_base * factor)
            client = int(client_base * factor)
            selected = key == self._memory_preset
            value_text = ft.Text(f"World {server} MB  ·  Game {client} MB",
                                 size=11, color=theme.TEXT_FAINT)
            self.preset_labels[key] = value_text
            cards.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(label, size=13, color=theme.TEXT_MAIN,
                                    font_family=theme.FONT_BODY_SEMIBOLD),
                            ft.Text(description, size=11, color=theme.TEXT_SUB),
                            ft.Container(height=4),
                            value_text,
                        ],
                        spacing=2, tight=True,
                    ),
                    padding=14, border_radius=12, expand=True,
                    bgcolor=theme.GOLD_WASH if selected else theme.PANEL_BG_ALT,
                    border=ft.border.all(1 if selected else 0.6,
                                         theme.GOLD if selected else theme.CARD_BORDER),
                    ink=True, on_click=lambda e, k=key: self._choose_preset(k),
                )
            )

        return ft.Column(
            [
                theme.card(ft.Row([ft.Icon(ft.icons.MEMORY_ROUNDED, size=18,
                                           color=theme.GOLD),
                                   ft.Text(total_note, size=12, color=theme.TEXT_SUB,
                                           expand=True)], spacing=10), padding=12),
                ft.Container(height=12),
                ft.Row(cards, spacing=10, vertical_alignment=ft.CrossAxisAlignment.STRETCH),
                ft.Container(height=12),
                theme.hint("Not a permanent decision - Settings has a slider for each of "
                           "these, and a button that puts them back to the recommendation."),
            ],
            tight=True, spacing=0, scroll=ft.ScrollMode.AUTO, height=310,
        )

    def _choose_preset(self, key):
        self._memory_preset = key
        self._render()

    def _apply_memory_preset(self):
        factor = next((f for k, _l, f, _d in MEMORY_PRESETS if k == self._memory_preset), 1.0)
        if factor == 1.0:
            # 0 means "decide automatically", which is what Balanced is - and
            # storing 0 rather than a snapshot means the value follows the
            # machine if the player adds RAM later.
            self.settings.update(server_ram_mb=0, client_ram_mb=0)
            return
        self.settings.update(
            server_ram_mb=int(auto_server_ram_mb() * factor),
            client_ram_mb=int(auto_client_ram_mb() * factor),
        )

    def _ready_body(self):
        installed = next((c for c in self._checks if c.key == "install"), None)
        first_run = not (installed and installed.value)

        timeline = [
            ("Download the castle and the resource pack", "about 1.4 GB, once only")
            if first_run else
            ("Check the castle is intact", "a second or two"),
            ("Prepare the Minecraft client and its mods",
             "a few minutes on the first run" if first_run else "already done"),
            ("Start the world server", "around 10 seconds"),
            ("Open the portal and launch Minecraft", "a few seconds"),
        ]

        return ft.Column(
            [
                theme.card(
                    ft.Row(
                        [
                            ft.Icon(ft.icons.AUTO_FIX_HIGH_ROUNDED, size=20, color=theme.GOLD),
                            ft.Text(
                                "The first launch takes a few minutes. Every launch after "
                                "that is about fifteen seconds."
                                if first_run else
                                "Everything is already installed - this launch will take "
                                "about fifteen seconds.",
                                size=12.5, color=theme.TEXT_MAIN, expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                    padding=12,
                ),
                ft.Container(height=14),
                theme.section_label("What happens when you press play"),
                ft.Container(height=8),
                ft.Column(
                    [theme.numbered_step(i + 1, title, note)
                     for i, (title, note) in enumerate(timeline)],
                    spacing=10, tight=True,
                ),
                ft.Container(height=14),
                theme.hint("Press Escape or the Stop button at any time. Nothing is "
                           "installed outside the launcher's own folder."),
            ],
            tight=True, spacing=0, scroll=ft.ScrollMode.AUTO, height=310,
        )
