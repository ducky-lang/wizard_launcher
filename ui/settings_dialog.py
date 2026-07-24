"""Settings, in categories.

The previous version was one scrolling column: two memory sliders, a LAN
switch, three behaviour toggles and a pair of folder buttons, all under
headings in the same column. It worked, but finding "turn off animations"
meant reading past everything else, and there was nowhere to put anything
new without making that worse.

Five tabs, each answering one question:

* **Memory** - how much RAM the world and the game get.
* **Graphics** - what the launcher animates, for people on laptops.
* **Network** - LAN play, which is the only setting here with a security
  consequence, so it gets a tab where it cannot be missed.
* **Behaviour** - what happens on Play, on a crash, on startup.
* **Diagnostics** - the system check, the folders, and the report bundle.

Nothing is written until Save. The Cancel button therefore genuinely cancels,
which was not true of the toggles before.
"""

import threading

import flet as ft

from launcher_core import diagnostics, system_check
from launcher_core.config import auto_client_ram_mb, auto_server_ram_mb
from launcher_core.constants import EXTERNAL_LINKS_ENABLED

from . import theme

RAM_MIN, RAM_MAX, RAM_DIVISIONS = 1024, 8192, 14


def show(app):
    SettingsDialog(app).open()


class SettingsDialog:
    def __init__(self, app):
        self.app = app
        self.settings = app.settings

        server_ram = self.settings.get("server_ram_mb") or auto_server_ram_mb()
        client_ram = self.settings.get("client_ram_mb") or auto_client_ram_mb()

        self.server_label = ft.Text(f"World server: {server_ram} MB", size=13,
                                    color=theme.TEXT_MAIN)
        self.client_label = ft.Text(f"Minecraft client: {client_ram} MB", size=13,
                                    color=theme.TEXT_MAIN)
        self.server_slider = ft.Slider(
            min=RAM_MIN, max=RAM_MAX, divisions=RAM_DIVISIONS, value=server_ram,
            active_color=theme.GOLD,
            on_change=lambda e: self._set_label(self.server_label, "World server", e),
        )
        self.client_slider = ft.Slider(
            min=RAM_MIN, max=RAM_MAX, divisions=RAM_DIVISIONS, value=client_ram,
            active_color=theme.GOLD,
            on_change=lambda e: self._set_label(self.client_label, "Minecraft client", e),
        )

        self.switches = {
            key: ft.Switch(value=bool(self.settings.get(key)), active_color=theme.GOLD)
            for key in ("allow_lan", "auto_restart_server", "animations", "spell_effects",
                        "intro_animation", "close_launcher_on_play", "check_updates",
                        "keyboard_shortcuts", "backup_before_reset")
        }

        self.check_column = ft.Column(
            [theme.hint("Press \"Run system check\" to see how this computer is doing.")],
            spacing=10, tight=True,
        )

        self.dialog = theme.dialog(
            "Settings",
            ft.Container(content=self._tabs(), height=430),
            [
                theme.ghost_button("Cancel", lambda e: self.app.close_dialog(self.dialog)),
                ft.ElevatedButton("Save", on_click=self._save,
                                  bgcolor=theme.GOLD, color="#161616"),
            ],
            width=580,
        )

    def open(self):
        self.app.open_dialog(self.dialog)

    # ------------------------------------------------------------------
    def _tabs(self):
        def tab(label, icon, body):
            return ft.Tab(
                text=label, icon=icon,
                content=ft.Container(
                    content=ft.Column(body, spacing=10, tight=True,
                                      scroll=ft.ScrollMode.AUTO),
                    padding=ft.padding.only(top=14, left=2, right=8),
                ),
            )

        return ft.Tabs(
            selected_index=0, animation_duration=200,
            indicator_color=theme.GOLD, label_color=theme.GOLD,
            unselected_label_color=theme.TEXT_FAINT, divider_color=theme.CARD_BORDER,
            expand=True,
            tabs=[
                tab("Memory", ft.icons.MEMORY_ROUNDED, self._memory_tab()),
                tab("Graphics", ft.icons.AUTO_AWESOME_ROUNDED, self._graphics_tab()),
                tab("Network", ft.icons.WIFI_ROUNDED, self._network_tab()),
                tab("Behaviour", ft.icons.TUNE_ROUNDED, self._behaviour_tab()),
                tab("Diagnostics", ft.icons.MONITOR_HEART_ROUNDED, self._diagnostics_tab()),
            ],
        )

    # ------------------------------------------------------------------
    def _memory_tab(self):
        return [
            theme.section_label("How much memory to use"),
            theme.hint("The world server and Minecraft each get their own share. Together "
                       "they should stay comfortably below your total RAM - the rest of "
                       "your computer still needs some."),
            ft.Container(height=6),
            self.server_label, self.server_slider,
            self.client_label, self.client_slider,
            ft.Row([
                theme.ghost_button("Reset to recommended for this PC", self._reset_memory,
                                   icon=ft.icons.RESTART_ALT_ROUNDED, color=theme.GOLD),
            ]),
            theme.divider(),
            theme.hint("Changes apply the next time you press Play. Lowering the world "
                       "server is usually the fix for stuttering; raising the client helps "
                       "with a high render distance."),
        ]

    def _graphics_tab(self):
        return [
            theme.section_label("Launcher visuals"),
            theme.hint("These affect the launcher window only - never the game."),
            ft.Container(height=6),
            theme.setting_row("Drifting embers",
                              "The animated background. Turn off to use less CPU on a laptop.",
                              self.switches["animations"]),
            theme.setting_row("Spell effects",
                              "The spark burst when a launch begins.",
                              self.switches["spell_effects"]),
            theme.setting_row("Opening flourish",
                              "The short animation after you press Begin the Journey.",
                              self.switches["intro_animation"]),
            theme.divider(),
            theme.section_label("Game graphics"),
            theme.hint("Render distance, shaders and video settings live inside Minecraft "
                       "itself. Sodium and Iris are already installed - open Video Settings "
                       "in game to use them."),
        ]

    def _network_tab(self):
        rows = [
            theme.section_label("Who can reach your world"),
            ft.Container(height=4),
            theme.setting_row("Allow friends on my network to join",
                              "Off keeps the world reachable only from this computer.",
                              self.switches["allow_lan"]),
            ft.Container(height=8),
            theme.card(
                ft.Row(
                    [
                        ft.Icon(ft.icons.SHIELD_ROUNDED, size=18, color=theme.SUCCESS),
                        ft.Text(
                            "With this off the server binds to 127.0.0.1 and nothing "
                            "outside this computer can connect. Turning it on opens it to "
                            "your local network - your home Wi-Fi is fine; a café or a "
                            "campus network is not.",
                            size=11.5, color=theme.TEXT_SUB, expand=True,
                        ),
                    ],
                    spacing=10, vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                padding=12,
            ),
        ]
        # The update check itself is switched off (EXTERNAL_LINKS_ENABLED in
        # constants.py) - a toggle that controls nothing is worse than no
        # toggle, so the whole section is hidden rather than left inert.
        if EXTERNAL_LINKS_ENABLED:
            rows += [
                theme.divider(),
                theme.section_label("Updates"),
                theme.setting_row("Check for updates at startup",
                                  "Only checks. Nothing is ever downloaded or installed for you.",
                                  self.switches["check_updates"]),
            ]
        return rows

    def _behaviour_tab(self):
        return [
            theme.section_label("While you play"),
            theme.setting_row("Restart the server if it crashes",
                              "Brings the world back without closing the game.",
                              self.switches["auto_restart_server"]),
            theme.setting_row("Hide the launcher after Play",
                              "Minimises the window once Minecraft opens.",
                              self.switches["close_launcher_on_play"]),
            theme.divider(),
            theme.section_label("Safety"),
            theme.setting_row("Back up player data before resetting it",
                              "Keeps a copy of your inventory and progress so a mis-click "
                              "is recoverable.",
                              self.switches["backup_before_reset"]),
            theme.divider(),
            theme.section_label("Keyboard"),
            theme.setting_row("Keyboard shortcuts",
                              "P to play, S for settings, F1 for help, "
                              "Escape to close.",
                              self.switches["keyboard_shortcuts"]),
        ]

    def _diagnostics_tab(self):
        return [
            theme.section_label("This computer"),
            ft.Container(height=4),
            self.check_column,
            ft.Row([
                theme.ghost_button("Run system check", self._run_check,
                                   icon=ft.icons.PLAY_ARROW_ROUNDED, color=theme.GOLD),
            ]),
            theme.divider(),
            theme.section_label("Folders"),
            ft.Row([
                theme.ghost_button("Open log folder", lambda e: self.app.logger.open_folder(),
                                   icon=ft.icons.FOLDER_OPEN_ROUNDED),
                theme.ghost_button("Open game folder", lambda e: self.app.open_data_folder(),
                                   icon=ft.icons.FOLDER_ROUNDED),
            ]),
            theme.divider(),
            theme.section_label("Bug reports"),
            theme.hint("The report bundles your logs, settings and system details into one "
                       "file. Tokens and passwords are removed. Nothing is uploaded."),
            ft.Row([
                theme.ghost_button("Create diagnostic report", self._build_report,
                                   icon=ft.icons.BUG_REPORT_ROUNDED, color=theme.GOLD),
            ] + ([
                theme.ghost_button("Report an issue", lambda e: self.app.report_issue(),
                                   icon=ft.icons.OPEN_IN_NEW_ROUNDED),
            ] if EXTERNAL_LINKS_ENABLED else [])),
        ]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    @staticmethod
    def _set_label(label, prefix, e):
        label.value = f"{prefix}: {int(e.control.value)} MB"
        try:
            label.update()
        except Exception:
            pass

    def _reset_memory(self, e=None):
        self.server_slider.value = auto_server_ram_mb()
        self.client_slider.value = auto_client_ram_mb()
        self.server_label.value = f"World server: {int(self.server_slider.value)} MB"
        self.client_label.value = f"Minecraft client: {int(self.client_slider.value)} MB"
        self.app.refresh_dialog(self.dialog)

    def _run_check(self, e=None):
        self.check_column.controls = [
            ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2, color=theme.GOLD),
                    theme.body("Checking...")], spacing=10)
        ]
        self.app.refresh_dialog(self.dialog)

        def work():
            try:
                checks = system_check.run_all(java_manager=self.app.java_manager)
            except Exception as err:
                self.app.schedule_ui(self._show_check_error, str(err))
                return
            self.app.schedule_ui(self._show_checks, checks)

        threading.Thread(target=work, daemon=True, name="settings-check").start()

    def _show_checks(self, checks):
        self.check_column.controls = [theme.status_row(check) for check in checks]
        self.app.refresh_dialog(self.dialog)

    def _show_check_error(self, message):
        self.check_column.controls = [theme.body(f"The check could not run: {message}")]
        self.app.refresh_dialog(self.dialog)

    def _build_report(self, e=None):
        def work():
            path = diagnostics.build_report(
                self.app.logger, self.settings, self.app.java_manager,
            )
            self.app.schedule_ui(self.app.report_created, path)

        threading.Thread(target=work, daemon=True, name="diag-report").start()
        self.app.toast("Building the report...")

    def _save(self, e=None):
        self.settings.update(
            server_ram_mb=int(self.server_slider.value),
            client_ram_mb=int(self.client_slider.value),
            **{key: switch.value for key, switch in self.switches.items()},
        )
        self.app.apply_settings_changes()
        self.app.close_dialog(self.dialog)
        self.app.toast("Settings saved. They apply the next time you press Play.")
