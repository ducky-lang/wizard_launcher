"""The launcher window: state, orchestration, and the one screen you see.

This module used to be everything - palette, particle loop, five dialogs,
the launch state machine and the maintenance jobs, in two thousand lines.
The presentation now lives in siblings (:mod:`ui.theme`, :mod:`ui.effects`,
and one module per dialog) and what is left here is the
part that is genuinely about *this application*: what state the launcher is
in, what happens when the player presses Play, and how a background thread
is allowed to touch the UI.

The rules that the rest of ``ui/`` relies on:

* **One thread owns the UI.** Anything running off the event loop goes
  through :meth:`LauncherApp.schedule_ui`.
* **A control lives in exactly one place.** Flet stamps a control with a uid
  the first time it is rendered; re-parenting it later leaves the
  Flutter-side control map holding one id in two places and the window goes
  blank with no Python traceback. Dialogs therefore build fresh controls
  rather than borrowing the main window's.
* **Every launch has a session id.** Background watchers capture the id they
  started with and become no-ops once it changes, which is what makes Stop
  reliable.
"""

import os
import random
import re
import shutil
import threading
import time
import traceback

import flet as ft

from launcher_core import content, diagnostics
from launcher_core.app_log import get_logger
from launcher_core.client_manager import ClientManager
from launcher_core.config import Settings
from launcher_core.constants import (
    EXTERNAL_LINKS_ENABLED, ISSUES_URL, MC_VERSION, NEW_ISSUE_URL,
    PLAYER_DATA_BACKUP_KEEP, PLAYER_DATA_SERVER_FILES, PLAYER_DATA_WORLD_ITEMS,
    PROXY_PORT, RESOURCE_PACK_NAME, MAP_NAME, SERVER_JAVA_MAX, SERVER_JAVA_MIN,
    SERVER_KEEP_ON_CLEAR, SERVER_PORT, SERVER_VERSION_DIR,
)
from launcher_core.exceptions import LauncherError
from launcher_core.install_state import InstallState
from launcher_core.java_manager import JavaManager
from launcher_core.microsoft_auth import MicrosoftAuth
from launcher_core.paths import get_data_root
from launcher_core.platform_utils import IS_MACOS, open_path, set_window_icon
from launcher_core.process_manager import ProcessManager
from launcher_core.proxy_manager import ProxyManager
from launcher_core.server_manager import ServerManager
from launcher_core.updater import check_for_update
from launcher_core.version import (
    APP_AUTHOR, APP_AUTHOR_HANDLE, APP_NAME, MAP_CREDIT, VERSION_STRING,
)

from . import about_dialog, account_dialog, brand, effects, help_dialog, onboarding
from . import settings_dialog, theme

# Launcher lifecycle states.
STATE_IDLE = "idle"
STATE_LAUNCHING = "launching"
STATE_PLAYING = "playing"
STATE_STOPPING = "stopping"
STATE_BUSY = "busy"

IDLE_ROTATE_SECONDS = 3.2
IDLE_TIMEOUT_SECONDS = 10


class _BackgroundResult:
    """Run one callable on a daemon thread and collect its result later.

    Deliberately smaller than a ``Future``: the launch thread needs exactly
    "start this now, give me the value (or re-raise) when I ask", and a
    daemon thread means a half-finished download can never keep the launcher
    alive after the user closes the window.
    """

    def __init__(self, fn, *args, **kwargs):
        self._value = None
        self._error = None
        self._done = threading.Event()

        def _run():
            try:
                self._value = fn(*args, **kwargs)
            except BaseException as exc:  # re-raised in result()
                self._error = exc
            finally:
                self._done.set()

        self._thread = threading.Thread(target=_run, daemon=True, name="launch-prepare")
        self._thread.start()

    def result(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError("Background task did not finish in time.")
        if self._error is not None:
            raise self._error
        return self._value


class LauncherApp:
    def __init__(self, page: ft.Page):
        self.page = page

        # ---- state -----------------------------------------------------
        self.state = STATE_IDLE
        self.account = None
        self._auth_waiting_dialog = None
        self._closing = False
        self._open_dialogs = []
        self._last_failure = None      # (step, error, traceback) for reports

        # Every launch gets a new id. Background watchers capture the id they
        # were started with and exit immediately if it no longer matches, so a
        # watcher left over from a stopped session can never clobber the UI of
        # a newer one. This is what made the old Stop button unreliable.
        self._session_id = 0
        self._session_lock = threading.Lock()

        # ---- paths & infrastructure -----------------------------------
        self.base_dir = get_data_root()
        self.resources_dir = os.path.join(self.base_dir, "resources")

        self.logger = get_logger()
        self.settings = Settings(log=self.logger.write)
        # What provisioning has already been done, so reopening the launcher
        # does not redo a launch's worth of work to arrive at the same state.
        self.install_state = InstallState(log=self.logger.write)

        self.server_dir = os.path.join(self.resources_dir, "servers", SERVER_VERSION_DIR)
        self.map_source_dir = os.path.join(self.resources_dir, "copy", MAP_NAME)
        self.resourcepack_source = os.path.join(self.resources_dir, "copy", RESOURCE_PACK_NAME)
        self.world_dest_dir = os.path.join(self.server_dir, "world")
        self.backup_dir = os.path.join(self.base_dir, "backups")
        self.proxy_dir = os.path.join(self.resources_dir, "proxy")
        self.client_root_dir = os.path.join(self.resources_dir, "client", MC_VERSION)
        self.mc_dir = os.path.join(self.client_root_dir, "minecraft")
        # The downloaded .mrpack lives here rather than in the game folder, so
        # a modpack upgrade can reuse it and "Clear Cache" can drop it.
        self.modpack_cache_dir = os.path.join(self.client_root_dir, "modpack")

        # ---- launch progress model -------------------------------------
        # Weighted rather than equal slices: "Preparing the castle" is a
        # multi-minute download on a first run while "Opening the portal" is
        # under a second, and equal slices made the bar look frozen through
        # the long steps and jump through the short ones.
        self.launch_steps = content.launch_steps()
        self._step_offsets, self._total_weight = self._weigh_steps(self.launch_steps)
        self._step_index = 0

        # ---- window ----------------------------------------------------
        page.title = APP_NAME
        page.window.width = 1400
        page.window.height = 880
        page.window.min_width = 980
        page.window.min_height = 680
        page.window.center()
        page.bgcolor = theme.BG
        page.padding = 0
        page.window.title_bar_hidden = True
        # Keep the native traffic lights on macOS - a Mac window without them
        # cannot be closed by muscle memory, and the OS places them for us.
        page.window.title_bar_buttons_hidden = not IS_MACOS
        page.window.prevent_close = True
        page.fonts = dict(theme.FONTS)
        page.theme = ft.Theme(font_family=theme.FONT_BODY)
        page.theme_mode = ft.ThemeMode.DARK

        # ---- idle status -----------------------------------------------
        self.idle_messages = content.idle_messages()
        self._idle_rotating = True
        self._idle_rotation_index = 0
        self._last_status_time = time.time()
        self._loops_running = True

        # ---- core managers ---------------------------------------------
        self.process_manager = ProcessManager(self.log, self.logger.write_raw)
        self.java_manager = JavaManager(self.log, self.mc_dir)
        self.server_manager = ServerManager(
            self.log, self.map_source_dir, self.world_dest_dir,
            self.resourcepack_source, self.mc_dir, self.server_dir,
            settings=self.settings, progress_fn=self._progress_from_worker,
            state=self.install_state,
        )
        self.proxy_manager = ProxyManager(self.log, self.proxy_dir, settings=self.settings)
        self.client_manager = ClientManager(
            self.log, self.logger.write_raw, self.mc_dir, self.modpack_cache_dir,
            settings=self.settings, progress_fn=self._progress_from_worker,
            state=self.install_state,
        )
        self.ms_auth = MicrosoftAuth(log=self.log, schedule_callback=self.schedule_ui)

        self.build_ui()
        page.window.on_event = self._on_window_event
        page.on_keyboard_event = self._on_keyboard

        threading.Thread(target=self._startup_tasks, daemon=True, name="startup").start()
        # The window does not exist yet, so this polls for it on its own
        # thread rather than blocking the first paint.
        threading.Thread(target=self._apply_window_icon, daemon=True,
                         name="window-icon").start()

    def _apply_window_icon(self):
        """Replace Flet's icon on the window and in the taskbar.

        Only does anything from a source checkout on Windows: a packaged
        build takes its icon from the executable, which the installer script
        already points at the same artwork.
        """
        try:
            icon = brand.icon_path()
            if icon:
                set_window_icon(APP_NAME, icon)
        except Exception as e:
            self.logger.write(f"Could not set the window icon: {e}", level="WARN")

    # ==================================================================
    # Startup
    # ==================================================================
    def _startup_tasks(self):
        """Everything that must not block the first paint."""
        # Reap anything a previous crashed run left holding our ports. Doing
        # this at startup is why the user no longer has to reboot after a
        # hard crash to get "port in use" to go away.
        try:
            self.process_manager.reap_orphans()
        except Exception as e:
            self.logger.write(f"Orphan cleanup failed: {e}", level="WARN")

        # Check whether the Microsoft app registration has been rotated.
        # Before the token round trip, so a build whose registration was
        # retired signs in on the new one instead of failing first. Silent,
        # cached for a day, and a no-op unless the feature is configured.
        try:
            self.ms_auth.client_ids.refresh_from_manifest()
        except Exception as e:
            self.logger.write(f"Provisioning check failed: {e}", level="WARN")

        # Show the cached name/avatar instantly, then validate in the
        # background. The user sees their identity in ~0ms rather than after
        # a four-hop token round trip.
        cached = self.ms_auth.peek_cached_profile()
        if cached:
            self.schedule_ui(self._apply_cached_profile, cached)

        account = self.ms_auth.load_cached_account()
        if account:
            self.schedule_ui(self._apply_signed_in_account, account, True)
        elif cached:
            self.schedule_ui(self._sign_out_ui_only)

        # Warm the Java lookup so the first Play press does not pay for it -
        # and so the first-run guide has an answer ready when it asks.
        try:
            self.java_manager.prewarm()
        except Exception:
            pass

        if onboarding.should_show(self.settings):
            self.schedule_ui(self.show_onboarding)
        elif EXTERNAL_LINKS_ENABLED and self.settings.get("check_updates"):
            # Never both at once: a first-run player meeting an update prompt
            # before they have played is a dialog on top of a dialog.
            try:
                update = check_for_update(self.logger.write)
                if update:
                    self.schedule_ui(about_dialog.show_update, self, update)
            except Exception:
                pass

    # ==================================================================
    # UI construction
    # ==================================================================
    def build_ui(self):
        page = self.page

        self.starfield = effects.Starfield()
        # Background layers, not part of the centre column. A decoration that
        # reserves its own height in the layout punches a hole in whatever it
        # was meant to sit behind - which is what the old rune ring did, with
        # a 430px box between the wordmark and the subtitle. In the root Stack
        # these are layout-neutral.
        self.embers = effects.EmberField(
            page.window.width, page.window.height, self.settings,
            is_minimised=lambda: bool(getattr(page.window, "minimized", False)),
        )
        self.rune_drift = effects.RuneDrift(
            page.window.width, page.window.height, self.settings)
        self.spell_burst = effects.SpellBurst(self.schedule_ui)

        root_stack = ft.Stack(
            [
                effects.background_layer(),
                self.starfield.control,
                ft.Container(content=effects.title_halo(),
                             alignment=ft.alignment.center, expand=True),
                self.rune_drift.control,
                self.embers.control,
                ft.Column(
                    [
                        self._build_top_bar(),
                        self._build_center(),
                        self._build_bottom_bar(),
                    ],
                    expand=True,
                ),
                self.spell_burst.control,
            ],
            expand=True,
        )

        page.add(root_stack)
        page.update()

        self.embers.start()
        self.rune_drift.start()
        keep_running = lambda: self._loops_running  # noqa: E731
        self.play_glow.start(self.schedule_ui, keep_running, self.settings)
        self.play_sheen.start(self.schedule_ui, keep_running, self.settings)
        self.status_pulse.start(self.schedule_ui, keep_running, self.settings)
        threading.Thread(target=self._idle_status_loop, daemon=True, name="idle-status").start()
        threading.Thread(target=self._idle_timeout_watchdog, daemon=True,
                         name="idle-timeout").start()

    # ------------------------------------------------------------------
    def _build_top_bar(self):
        wordmark = ft.Row(
            [
                brand.logo_mark(size=36, icon_size=19),
                ft.Text("W&W ", size=19, weight=ft.FontWeight.W_600, color=theme.TEXT_MAIN,
                        font_family=theme.FONT_DISPLAY),
                ft.Text("LAUNCHER", size=14, color=theme.TEXT_SUB,
                        font_family=theme.FONT_BODY,
                        style=ft.TextStyle(letter_spacing=3)),
                ft.Container(width=4),
                ft.Text(VERSION_STRING, size=10, color=theme.TEXT_FAINT,
                        font_family=theme.FONT_BODY),
            ],
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.account_pill_text = ft.Text("Guest Wizard", size=13, color=theme.TEXT_MAIN,
                                         font_family=theme.FONT_BODY_MEDIUM)
        self.account_avatar_box = ft.Container(
            content=self._guest_avatar(), width=26, height=26, border_radius=13,
            bgcolor="#0e1a2e", border=ft.border.all(0.6, "#1f3a5c"),
            alignment=ft.alignment.center, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        self.guest_pill = ft.Container(
            content=ft.Row([self.account_avatar_box, self.account_pill_text],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            border_radius=20, bgcolor=theme.PANEL_BG_ALT,
            border=ft.border.all(0.6, theme.CARD_BORDER),
            ink=True, on_click=lambda e: account_dialog.show(self),
            tooltip="Account  ·  sign in or out",
        )

        def window_btn(icon, hover_bg, on_click):
            return ft.Container(
                content=ft.Icon(icon, color=theme.TEXT_SUB, size=14),
                width=32, height=32, border_radius=8,
                alignment=ft.alignment.center, ink=True, on_click=on_click,
                on_hover=lambda e: self._window_btn_hover(e, hover_bg),
            )

        self.maximize_icon = ft.Icon(ft.icons.CROP_SQUARE_ROUNDED, color=theme.TEXT_SUB, size=13)
        maximize_btn = ft.Container(
            content=self.maximize_icon, width=32, height=32, border_radius=8,
            alignment=ft.alignment.center, ink=True, on_click=self._toggle_maximize,
            on_hover=lambda e: self._window_btn_hover(e, "#1c1c26"),
        )

        # macOS keeps its own traffic lights (see __init__): drawing a second
        # set of minimise/maximise/close buttons on the opposite corner is the
        # kind of detail that makes an app feel ported rather than written.
        window_controls = (
            ft.Container(width=0)
            if IS_MACOS else
            ft.Row(
                [
                    ft.Container(width=6),
                    ft.Container(width=1, height=22, bgcolor=theme.CARD_BORDER),
                    ft.Container(width=6),
                    ft.Row(
                        [
                            window_btn(ft.icons.REMOVE_ROUNDED, "#1c1c26", self._minimize_window),
                            maximize_btn,
                            window_btn(ft.icons.CLOSE_ROUNDED, "#3a1f22", self._close_window),
                        ],
                        spacing=4,
                    ),
                ],
                spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

        buttons_row = ft.Row(
            [
                theme.icon_button(ft.icons.SETTINGS_ROUNDED, "Settings  (S)",
                                  lambda e: settings_dialog.show(self)),
                theme.icon_button(ft.icons.HELP_ROUNDED, "Help & Setup Guide  (F1)",
                                  lambda e: help_dialog.show(self)),
                theme.icon_button(ft.icons.INFO_ROUNDED, "About",
                                  lambda e: about_dialog.show(self)),
                self.guest_pill,
                window_controls,
            ],
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # WindowDragArea swallows clicks from anything inside it, so only the
        # brand is draggable; the buttons stay outside it.
        # The extra left inset on macOS keeps the brand clear of the traffic
        # lights, which are drawn over the content when the title bar is hidden.
        return ft.Container(
            content=ft.Row(
                [ft.WindowDragArea(content=wordmark, maximizable=True, expand=True), buttons_row],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.padding.only(
                left=86 if IS_MACOS else 32, right=32, top=16, bottom=16,
            ),
        )

    def _build_center(self):
        title = ft.Column(
            [
                ft.Text("WITCHCRAFT", size=52, weight=ft.FontWeight.W_600,
                        color=theme.TEXT_MAIN, font_family=theme.FONT_DISPLAY,
                        style=ft.TextStyle(letter_spacing=4),
                        text_align=ft.TextAlign.CENTER),
                ft.Text("& WIZARDRY", size=52, weight=ft.FontWeight.W_600, color=theme.GOLD,
                        font_family=theme.FONT_DISPLAY,
                        style=ft.TextStyle(letter_spacing=4),
                        text_align=ft.TextAlign.CENTER),
            ],
            spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # A hairline under the wordmark, brightest in the middle and fading to
        # nothing at both ends. Cheap, still, and it gives the title a base to
        # sit on now that the rune ring is not framing it.
        title_rule = ft.Container(
            width=340, height=1,
            gradient=ft.LinearGradient(
                begin=ft.alignment.center_left, end=ft.alignment.center_right,
                colors=["#00f2c14e", theme.GOLD, "#00f2c14e"],
            ),
            opacity=0.5,
        )

        subtitle = ft.Text(
            "Step through the portal into a living wizarding world. Learn spells,\n"
            "explore the castle, and let your adventure begin.",
            size=14.5, color=theme.TEXT_SUB, font_family=theme.FONT_BODY,
            text_align=ft.TextAlign.CENTER, style=ft.TextStyle(letter_spacing=0.3),
        )

        self.username_field = ft.TextField(
            value="Player", hint_text="Player name", text_align=ft.TextAlign.CENTER,
            width=280, height=44, border_radius=10, bgcolor=theme.PANEL_BG_ALT,
            border_color=theme.CARD_BORDER, focused_border_color=theme.GOLD,
            color=theme.TEXT_MAIN, text_size=14,
            text_style=ft.TextStyle(font_family=theme.FONT_BODY_MEDIUM),
            content_padding=ft.padding.symmetric(horizontal=14, vertical=8),
            on_submit=self.start_launch,
        )

        self.play_icon = ft.Icon(ft.icons.AUTO_FIX_HIGH_ROUNDED, color="#161616", size=20)
        self.play_label = ft.Text("Begin the Journey", size=17, weight=ft.FontWeight.W_600,
                                  color="#161616", font_family=theme.FONT_BODY_SEMIBOLD,
                                  style=ft.TextStyle(letter_spacing=0.5))
        # Two containers, and the split is not cosmetic.
        #
        # Flet's Container has one shape it cannot build: `ink=True` together
        # with `animate`. In that combination it emits an AnimatedContainer
        # carrying the clip but *not* the decoration (see container.dart in
        # flet 0.24), and Flutter requires a decoration wherever it is asked
        # to clip. In a release build that assertion is compiled out and the
        # widget throws instead, which paints a grey ErrorWidget over
        # everything below it - the button, the progress bar and the status
        # line all vanished behind one.
        #
        # So the ripple lives on the inner surface, and everything animated -
        # the breathing shadow, the hover lift, the clip the sheen needs -
        # lives on the outer shell, which has no ink.
        self.play_surface = ft.Container(
            content=ft.Row([self.play_icon, self.play_label], spacing=10,
                           alignment=ft.MainAxisAlignment.CENTER),
            width=340, height=58, border_radius=16,
            alignment=ft.alignment.center, ink=True, on_click=self.start_launch,
            tooltip="Begin the Journey  (P)",
        )
        # The sheen is a sibling of the label, not an overlay on top of it:
        # the shell clips its children, so the band of light enters and leaves
        # through the button's rounded edges.
        self.play_sheen = effects.Sheen(width=340, height=58)
        self.play_btn = ft.Container(
            content=ft.Stack([self.play_surface, self.play_sheen.control],
                             width=340, height=58),
            width=340, height=58, border_radius=16, bgcolor=theme.GOLD,
            alignment=ft.alignment.center,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=ft.BoxShadow(blur_radius=30, spread_radius=1, color="#66f2c14e"),
            scale=ft.Scale(1.0),
            animate_scale=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
            on_hover=self._play_hover,
        )
        self.play_glow = effects.BreathingGlow(self.play_btn, "#66f2c14e")

        # ---- progress ---------------------------------------------------
        self.progress_bar = ft.ProgressBar(
            width=380, height=5, value=0, bgcolor="#1c1c26",
            color=theme.GOLD, border_radius=4,
        )
        self.progress_label = ft.Text("", size=11.5, color=theme.TEXT_SUB,
                                      font_family=theme.FONT_BODY_MEDIUM)
        self.progress_percent = ft.Text("", size=11.5, color=theme.GOLD,
                                        font_family=theme.FONT_BODY_MEDIUM)
        self.progress_detail = ft.Text("", size=10.5, color=theme.TEXT_FAINT,
                                       font_family=theme.FONT_BODY,
                                       text_align=ft.TextAlign.CENTER)
        self.progress_area = ft.Container(
            content=ft.Column(
                [
                    ft.Row([self.progress_label, ft.Container(expand=True),
                            self.progress_percent], width=380),
                    self.progress_bar,
                    self.progress_detail,
                ],
                spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            visible=False,
        )

        self.status_text = ft.Text(self.idle_messages[0], size=13, italic=True,
                                   color=theme.TEXT_FAINT, font_family=theme.FONT_BODY,
                                   text_align=ft.TextAlign.CENTER)
        self.status_dot = ft.Container(width=6, height=6, border_radius=3,
                                       bgcolor=theme.SUCCESS)
        # A still dot beside changing text reads as a stuck indicator; a
        # breathing one reads as a live launcher waiting for you.
        self.status_pulse = effects.Pulse(self.status_dot)
        self.status_text_wrap = ft.Container(
            content=self.status_text, clip_behavior=ft.ClipBehavior.HARD_EDGE,
            offset=ft.Offset(0, 0), animate_offset=None,
        )
        status_row = ft.Row([self.status_dot, self.status_text_wrap], spacing=8,
                            alignment=ft.MainAxisAlignment.CENTER)

        return ft.Container(
            content=ft.Column(
                [
                    title,
                    ft.Container(height=14),
                    title_rule,
                    ft.Container(height=18),
                    subtitle,
                    ft.Container(height=26),
                    self.username_field,
                    ft.Container(height=16),
                    self.play_btn,
                    ft.Container(height=14),
                    self.progress_area,
                    ft.Container(height=10),
                    status_row,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0,
            ),
            alignment=ft.alignment.center, expand=True,
        )

    def _play_hover(self, e):
        """Lift the Play button under the cursor.

        Skipped while the button is disabled: a control that reacts to hover
        but not to a click is worse than one that does neither.
        """
        if self.play_btn.disabled:
            return
        hovering = e.data == "true"
        self.play_btn.scale = ft.Scale(1.03 if hovering else 1.0)
        self._safe_update(self.play_btn)

    def _build_bottom_bar(self):
        self.stop_btn = theme.action_button("Stop", ft.icons.STOP_CIRCLE_ROUNDED,
                                            self.confirm_stop,
                                            tooltip="Stop the game and save the world")
        self.clear_cache_btn = theme.action_button(
            "Clear Cache", ft.icons.DELETE_SWEEP_ROUNDED, self.confirm_clear_cache,
            tooltip="Erase everything, including the downloaded castle and mods")
        # The label is the one players know; the tooltip is where the scope
        # gets stated, because this deletes the world now rather than only the
        # player's progress inside it.
        self.clear_player_data_btn = theme.action_button(
            "Clear Player Data", ft.icons.PERSON_REMOVE_ROUNDED,
            self.confirm_clear_player_data,
            tooltip="Delete the world and reinstall the castle from scratch")
        theme.set_enabled(self.stop_btn, False)

        credit = ft.Text(
            f"Map by {MAP_CREDIT}  •  Launcher by {APP_AUTHOR} {APP_AUTHOR_HANDLE}",
            size=11, color=theme.TEXT_FAINT, font_family=theme.FONT_BODY,
        )

        return ft.Container(
            content=ft.Row(
                [
                    credit,
                    ft.Container(expand=True),
                    self.stop_btn, self.clear_cache_btn, self.clear_player_data_btn,
                ],
                spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(left=32, right=32, bottom=22, top=6),
        )

    # ==================================================================
    # UI plumbing (used by every dialog module)
    # ==================================================================
    def schedule_ui(self, fn, *args, **kwargs):
        """Run `fn` on Flet's event loop. Safe from any thread."""
        async def _runner():
            try:
                fn(*args, **kwargs)
            except Exception:
                self.logger.write_crash(traceback.format_exc())

        try:
            self.page.run_task(_runner)
        except Exception:
            pass

    def _safe_update(self, *controls):
        for control in controls:
            try:
                control.update()
            except Exception:
                pass

    def open_dialog(self, dlg):
        # Safe point to drop previously-closed dialogs: their close animation
        # finished long ago, so removing them now cannot pull a control out
        # from under Flutter mid-transition.
        self._prune_closed_dialogs()
        if dlg not in self.page.overlay:
            self.page.overlay.append(dlg)
        if dlg not in self._open_dialogs:
            self._open_dialogs.append(dlg)
        dlg.open = True
        self.page.update()

    def close_dialog(self, dlg):
        """Close a dialog without detaching it from the overlay.

        The obvious implementation - set ``open = False`` then immediately
        ``page.overlay.remove(dlg)`` - deletes the control while its dismiss
        animation is still running. Flutter is then told to animate a widget
        that no longer exists in the control map, which can take the whole
        view down. The dialog is left in place here and swept up the next
        time one is opened.
        """
        if dlg is None:
            return
        dlg.open = False
        if dlg in self._open_dialogs:
            self._open_dialogs.remove(dlg)
        if dlg is self._auth_waiting_dialog:
            self._auth_waiting_dialog = None
        self.page.update()

    def refresh_dialog(self, dlg):
        """Repaint a dialog whose contents were rebuilt in place."""
        try:
            dlg.update()
        except Exception:
            try:
                self.page.update()
            except Exception:
                pass

    def _prune_closed_dialogs(self, keep=3):
        """Drop closed dialogs from the overlay, newest few retained."""
        closed = [c for c in self.page.overlay
                  if isinstance(c, ft.AlertDialog) and not c.open]
        for stale in closed[:-keep] if len(closed) > keep else []:
            try:
                self.page.overlay.remove(stale)
            except ValueError:
                pass

    def toast(self, message):
        try:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(message, color=theme.TEXT_MAIN),
                bgcolor="#1c1c26", duration=2800,
            )
            self.page.snack_bar.open = True
            self.page.update()
        except Exception:
            pass

    def open_url(self, url):
        """Open an external link. Only https ever leaves the app."""
        if not isinstance(url, str) or not url.startswith("https://"):
            self.toast("That link could not be opened.")
            return
        try:
            self.page.launch_url(url)
        except Exception as e:
            self.logger.write(f"Could not open {url}: {e}", level="WARN")

    # ==================================================================
    # Window controls
    # ==================================================================
    def _window_btn_hover(self, e, hover_bg):
        e.control.bgcolor = hover_bg if e.data == "true" else None
        self._safe_update(e.control)

    def _minimize_window(self, e=None):
        self.page.window.minimized = True
        self.page.update()

    def _toggle_maximize(self, e=None):
        self.page.window.maximized = not self.page.window.maximized
        self.page.update()
        self._sync_maximize_icon()

    def _close_window(self, e=None):
        self.on_close()

    def _sync_maximize_icon(self):
        new_icon = (ft.icons.FILTER_NONE_ROUNDED if self.page.window.maximized
                    else ft.icons.CROP_SQUARE_ROUNDED)
        if self.maximize_icon.name != new_icon:
            self.maximize_icon.name = new_icon
            self._safe_update(self.maximize_icon)

    def _on_window_event(self, e):
        if e.data == "close":
            self.on_close()
        elif e.data in ("maximize", "unmaximize", "resize", "resized", "restore"):
            self._sync_maximize_icon()
            self.embers.resize(self.page.window.width, self.page.window.height)
            self.rune_drift.resize(self.page.window.width, self.page.window.height)
        elif e.data == "blur":
            self.embers.set_focused(False)
        elif e.data == "focus":
            self.embers.set_focused(True)

    # ------------------------------------------------------------------
    def _on_keyboard(self, e: ft.KeyboardEvent):
        """Keyboard shortcuts.

        Only fire when no dialog is open and no modifier is held, so they
        cannot steal a keystroke from the player name field. Escape is the
        exception: closing the top dialog is exactly what it is for.
        """
        if not self.settings.get("keyboard_shortcuts"):
            return
        if e.key == "Escape":
            if self._open_dialogs:
                self.close_dialog(self._open_dialogs[-1])
            return

        if self._open_dialogs or e.ctrl or e.alt or e.meta or e.shift:
            return
        # A typed character while the name field has focus must stay in the
        # field; Flet routes those here too, so anything that is not idle is
        # left alone.
        key = (e.key or "").upper()
        if key == "P" and self.state == STATE_IDLE:
            self.start_launch()
        elif key == "S":
            settings_dialog.show(self)
        elif key == "F1":
            help_dialog.show(self)

    # ==================================================================
    # Idle status
    # ==================================================================
    def _idle_status_loop(self):
        while self._loops_running:
            time.sleep(IDLE_ROTATE_SECONDS)
            if not self._idle_rotating or getattr(self.page.window, "minimized", False):
                continue
            try:
                self._idle_rotation_index = (
                    (self._idle_rotation_index + 1) % len(self.idle_messages)
                )
                self.schedule_ui(self._set_status_slide_up,
                                 self.idle_messages[self._idle_rotation_index],
                                 self.status_dot.bgcolor)
            except Exception:
                pass

    def _idle_timeout_watchdog(self, check_interval=1.0):
        while self._loops_running:
            time.sleep(check_interval)
            try:
                if self._idle_rotating or self.state != STATE_IDLE:
                    continue
                if time.time() - self._last_status_time >= IDLE_TIMEOUT_SECONDS:
                    self._idle_rotation_index = 0
                    self.schedule_ui(self._set_status_slide_up,
                                     self.idle_messages[0], theme.SUCCESS)
                    self._idle_rotating = True
            except Exception:
                pass

    def _set_status_slide_up(self, text, dot_color=None):
        try:
            wrap = self.status_text_wrap
            wrap.animate_offset = None
            wrap.offset = ft.Offset(0, 1)
            wrap.update()

            self.status_text.value = text
            self.status_dot.bgcolor = dot_color or theme.SUCCESS
            self.status_text.update()
            self.status_dot.update()

            wrap.animate_offset = ft.Animation(380, ft.AnimationCurve.EASE_OUT)
            wrap.offset = ft.Offset(0, 0)
            wrap.update()
        except Exception:
            pass

    def set_status(self, text, dot_color=None):
        self._idle_rotating = False
        self._last_status_time = time.time()
        self._set_status_slide_up(text, dot_color or theme.SUCCESS)

    # ==================================================================
    # Logging
    # ==================================================================
    def log(self, message, level="INFO"):
        message = str(message)
        self.logger.write(message, level=level)
        self.schedule_ui(self._append_log_ui, message)

    def _append_log_ui(self, message):
        self.set_status(message[:90], self.status_dot.bgcolor)

    # ==================================================================
    # Progress
    # ==================================================================
    @staticmethod
    def _weigh_steps(steps):
        """Cumulative weight before each step, plus the total.

        Precomputed once so the progress callback - which runs on a worker
        thread hundreds of times a minute - is two lookups and a multiply.
        """
        offsets = []
        running = 0.0
        for _key, _label, weight in steps:
            offsets.append(running)
            running += weight
        return offsets, (running or 1.0)

    def _progress_from_worker(self, fraction, message=None):
        """Progress callback handed to the managers (called off-thread).

        Sub-task progress is folded into the slice of the bar belonging to
        the current step, so the bar only ever moves forward.
        """
        self.schedule_ui(self._apply_sub_progress, fraction, message)

    def _apply_sub_progress(self, fraction, message):
        index = min(self._step_index, len(self.launch_steps) - 1)
        base = self._step_offsets[index] / self._total_weight
        span = self.launch_steps[index][2] / self._total_weight

        if fraction is None:
            self.progress_bar.value = None   # indeterminate
        else:
            value = base + span * max(0.0, min(1.0, fraction))
            self.progress_bar.value = min(1.0, value)
            self.progress_percent.value = f"{int(self.progress_bar.value * 100)}%"
            self._safe_update(self.progress_percent)

        if message:
            # The downloader's messages carry the numbers ("412 MB of 1.1 GB
            # · 5.2 MB/s · 2m 14s left"); those belong on the detail line, not
            # in place of the step name.
            if "·" in message:
                head, _, tail = message.partition("·")
                self.progress_label.value = head.strip()
                self.progress_detail.value = tail.replace("·", "  ·  ").strip()
            else:
                self.progress_label.value = message
                self.progress_detail.value = ""
            self._safe_update(self.progress_label, self.progress_detail)
        self._safe_update(self.progress_bar)

    def _begin_step(self, key):
        """Advance the progress bar to the start of a named launch phase."""
        for index, (step_key, label, _weight) in enumerate(self.launch_steps):
            if step_key == key:
                self._step_index = index
                self.schedule_ui(self._apply_step_ui, index, label)
                return

    def _apply_step_ui(self, index, label):
        total = len(self.launch_steps)
        self.progress_bar.value = self._step_offsets[index] / self._total_weight
        self.progress_label.value = f"Step {index + 1} of {total} - {label}"
        self.progress_percent.value = f"{int(self.progress_bar.value * 100)}%"
        self.progress_detail.value = ""
        self._safe_update(self.progress_bar, self.progress_label,
                          self.progress_percent, self.progress_detail)

    def _show_progress(self, visible):
        self.progress_area.visible = visible
        if visible:
            self.progress_bar.value = 0
            self.progress_label.value = "Preparing..."
            self.progress_percent.value = "0%"
            self.progress_detail.value = ""
        self._safe_update(self.progress_area, self.progress_bar, self.progress_label,
                          self.progress_percent, self.progress_detail)

    # ==================================================================
    # State machine
    # ==================================================================
    def _apply_state(self, state):
        """Single place that decides what every control looks like.

        Previously each handler poked buttons individually, which is how the
        UI ended up claiming "In Game" after a successful Stop.
        """
        self.state = state

        label, color, icon, enabled = {
            STATE_IDLE:      ("Begin the Journey", theme.GOLD,
                              ft.icons.AUTO_FIX_HIGH_ROUNDED, True),
            STATE_LAUNCHING: ("Launching...", theme.GOLD_DIM,
                              ft.icons.HOURGLASS_TOP_ROUNDED, False),
            STATE_PLAYING:   ("In Game", "#1e3a2e", ft.icons.CHECK_CIRCLE_ROUNDED, False),
            STATE_STOPPING:  ("Stopping...", theme.GOLD_DIM,
                              ft.icons.HOURGLASS_TOP_ROUNDED, False),
            STATE_BUSY:      ("Working...", theme.GOLD_DIM,
                              ft.icons.HOURGLASS_TOP_ROUNDED, False),
        }[state]

        self.play_label.value = label
        self.play_icon.name = icon
        self.play_btn.bgcolor = color
        # Set on the shell, not the ink surface: Flet propagates `disabled`
        # down the tree, so this reaches the click target as well.
        self.play_btn.disabled = not enabled
        self.play_btn.opacity = 1.0 if enabled else 0.75
        if not enabled:
            # Otherwise a button disabled mid-hover keeps the lift forever.
            self.play_btn.scale = ft.Scale(1.0)
        self._safe_update(self.play_btn)

        # Stop is available exactly when there is something to stop.
        theme.set_enabled(self.stop_btn, state in (STATE_LAUNCHING, STATE_PLAYING))
        # Destructive maintenance only while fully idle.
        idle = state == STATE_IDLE
        theme.set_enabled(self.clear_cache_btn, idle)
        theme.set_enabled(self.clear_player_data_btn, idle)
        self._safe_update(self.stop_btn, self.clear_cache_btn, self.clear_player_data_btn)

        self.username_field.disabled = (not idle) or (self.account is not None)
        self._safe_update(self.username_field)

        if idle:
            self._show_progress(False)
            self._idle_rotating = True

    def _apply_state_threadsafe(self, state):
        self.state = state
        self.schedule_ui(self._apply_state, state)

    # ==================================================================
    # Simple dialogs
    # ==================================================================
    def show_message(self, title, message, on_confirm=None, danger=True,
                     confirm_label="Confirm"):
        def close(e=None):
            self.close_dialog(dlg)

        def confirm(e=None):
            self.close_dialog(dlg)
            if on_confirm:
                on_confirm()

        if on_confirm:
            actions = [
                theme.ghost_button("Cancel", close),
                ft.ElevatedButton(confirm_label, on_click=confirm,
                                  bgcolor=theme.DANGER if danger else theme.GOLD,
                                  color="#ffffff" if danger else "#161616"),
            ]
        else:
            actions = [theme.ghost_button("OK", close, color=theme.GOLD)]

        dlg = theme.dialog(
            title, ft.Text(message, color=theme.TEXT_SUB, size=13.5, selectable=True),
            actions,
        )
        self.open_dialog(dlg)

    def show_error(self, step_label, error, details=None, fix_id=None):
        """The 'something broke at step Y, do Z' popup.

        LauncherError messages are already written as "what happened" + "how
        to fix"; anything else gets a generic remedy plus a one-click
        diagnostic report, so a bug report is a button rather than a chore.
        """
        message = str(error)
        raw = details if details is not None else traceback.format_exc()
        self._last_failure = (step_label, message, raw)

        def copy_details(e=None):
            self.page.set_clipboard(
                f"{APP_NAME} {VERSION_STRING}\nStep: {step_label}\n\n{message}\n\n{raw}"
            )
            self.toast("Error details copied - paste them into your bug report.")

        def make_report(e=None):
            threading.Thread(target=self._build_failure_report, daemon=True,
                             name="failure-report").start()
            self.toast("Building the report...")

        def open_fix(e=None):
            self.close_dialog(dlg)
            help_dialog.show(self, focus=fix_id)

        body_controls = [
            theme.card(
                ft.Row(
                    [
                        ft.Icon(ft.icons.ERROR_OUTLINE_ROUNDED, color=theme.DANGER, size=18),
                        ft.Text(f"Failed at: {step_label}", size=12.5, color=theme.DANGER,
                                font_family=theme.FONT_BODY_SEMIBOLD, expand=True),
                    ],
                    spacing=8,
                ),
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
                bgcolor=theme.DANGER_WASH, border_color=theme.DANGER_BORDER,
            ),
            ft.Container(height=8),
            ft.Text(message, size=13.5, color=theme.TEXT_SUB, selectable=True),
        ]
        if fix_id:
            body_controls += [
                ft.Container(height=10),
                ft.Row([theme.ghost_button("Show me the step-by-step fix", open_fix,
                                           icon=ft.icons.BUILD_ROUNDED, color=theme.GOLD)]),
            ]

        actions = [
            theme.ghost_button("Copy details", copy_details, icon=ft.icons.COPY_ROUNDED),
            theme.ghost_button("Create report", make_report, icon=ft.icons.BUG_REPORT_ROUNDED),
        ]
        if EXTERNAL_LINKS_ENABLED:
            actions.append(theme.ghost_button(
                "Report an issue", lambda e: self.report_issue(),
                icon=ft.icons.OPEN_IN_NEW_ROUNDED))
        actions.append(ft.ElevatedButton(
            "Close", on_click=lambda e: self.close_dialog(dlg),
            bgcolor=theme.GOLD, color="#161616"))

        dlg = theme.dialog(
            "Something went wrong",
            ft.Column(body_controls, tight=True, spacing=0, scroll=ft.ScrollMode.AUTO),
            actions, width=520,
        )
        self.open_dialog(dlg)

    # ==================================================================
    # Diagnostics & issue reporting
    # ==================================================================
    def _build_failure_report(self):
        step, message, raw = self._last_failure or ("unknown", "", "")
        path = diagnostics.build_report(
            self.logger, self.settings, self.java_manager,
            extra={"failure.txt": f"Step: {step}\n\n{message}\n\n{raw}"},
        )
        self.schedule_ui(self.report_created, path)

    def report_created(self, path):
        if not path:
            self.toast("The report could not be created - check the log folder.")
            return
        self.show_message(
            "Report ready",
            f"A diagnostic report was saved to:\n\n{path}\n\n"
            "It contains your logs, settings and system details, with tokens and "
            "passwords removed. Nothing was uploaded - attach the file to a bug "
            "report yourself.",
        )
        open_path(self.logger.dir)

    def report_issue(self):
        """Open a prefilled GitHub issue in the browser."""
        if not (NEW_ISSUE_URL or ISSUES_URL):
            self.toast("No issue tracker is configured for this build.")
            return
        step = self._last_failure[0] if self._last_failure else None
        error = self._last_failure[1] if self._last_failure else None
        title = f"[{step}] " if step else ""
        url = diagnostics.issue_url(
            NEW_ISSUE_URL or ISSUES_URL,
            f"{title}Wizard Launcher issue",
            diagnostics.issue_body(step, error, self.java_manager),
        ) or ISSUES_URL
        self.open_url(url)
        self.toast("Attach a diagnostic report to the issue if you have one.")

    def open_data_folder(self):
        if not open_path(self.base_dir):
            self.toast(f"Game folder: {self.base_dir}")

    def apply_settings_changes(self):
        """React to settings the running window can honour immediately."""
        if not self.settings.get("animations"):
            self.set_status("Background animations are off.", theme.TEXT_FAINT)

    # ==================================================================
    # Onboarding
    # ==================================================================
    def show_onboarding(self, force=False):
        if not force and not onboarding.should_show(self.settings):
            return
        self._onboarding = onboarding.OnboardingWizard(self, on_finish=self._onboarding_done)
        self._onboarding.open()

    def reopen_onboarding(self, index):
        self._onboarding = onboarding.OnboardingWizard(
            self, on_finish=self._onboarding_done, start_index=index)
        self._onboarding.open()

    def _onboarding_done(self, play_now):
        self._onboarding = None
        if play_now:
            self.start_launch()

    # ==================================================================
    # Account
    # ==================================================================
    @property
    def player_name(self):
        return (self.username_field.value or "").strip()

    def set_player_name(self, name):
        self.username_field.value = (name or "").strip()
        self._safe_update(self.username_field)

    @staticmethod
    def _guest_avatar():
        """Build a FRESH guest icon every call.

        Never share one instance between places in the tree. Flet stamps each
        control with a uid the first time it is rendered; putting that same
        object under a different parent later leaves the Flutter-side control
        map with the id in two places, and the window goes blank with no
        Python exception to show for it.

        That is precisely what used to happen right after a successful
        sign-in: the avatar container's icon was handed to the new remote
        Image as its `error_content`, re-parenting a live control. Because the
        failure was on the Flutter side, the log ended at "Signed in as ..."
        with nothing after it - and every later start blanked too, since the
        cached profile replayed the same swap.
        """
        return ft.Icon(ft.icons.PERSON_ROUNDED, size=14, color=theme.GOLD)

    def _apply_cached_profile(self, cached):
        """Paint the cached identity immediately, before validation."""
        username = cached.get("username")
        self.account_pill_text.value = username or "Guest Wizard"

        uuid = cached.get("uuid")
        if uuid:
            self.account_avatar_box.content = ft.Image(
                src=f"https://crafatar.com/avatars/{uuid}?size=52&overlay",
                width=26, height=26, fit=ft.ImageFit.COVER, border_radius=13,
                # Fresh control - see _guest_avatar().
                error_content=self._guest_avatar(),
            )
            self.account_avatar_box.border = ft.border.all(1, theme.GOLD_DIM)

        if username:
            self.username_field.value = username
        self._safe_update(self.account_pill_text, self.account_avatar_box,
                          self.username_field)

    def _apply_signed_in_account(self, account, silent=False):
        self.account = account
        self._apply_cached_profile({"username": account.username, "uuid": account.uuid})
        self.username_field.disabled = True
        self._safe_update(self.username_field)
        if not silent:
            self.set_status(f"Signed in as {account.username}", theme.SUCCESS)

    def _sign_out_ui_only(self):
        self.account = None
        self.account_pill_text.value = "Guest Wizard"
        # Fresh control rather than putting the original icon back - see
        # _guest_avatar(). Re-parenting the old one blanks the window.
        self.account_avatar_box.content = self._guest_avatar()
        self.account_avatar_box.border = ft.border.all(0.6, "#1f3a5c")
        self.username_field.value = "Player"
        self.username_field.disabled = self.state != STATE_IDLE
        self._safe_update(self.account_pill_text, self.account_avatar_box,
                          self.username_field)

    def sign_out(self):
        self.ms_auth.logout()
        self._sign_out_ui_only()
        self.set_status("Signed out.", theme.SUCCESS)
        self.toast("Signed out. Your session was removed from this computer.")

    def start_microsoft_login(self, on_settled=None):
        """Begin device-code sign-in.

        ``on_settled`` fires on success *and* on failure, which is what the
        first-run guide needs: it stepped aside for the browser and has to
        come back either way rather than leaving the player on the main
        window mid-setup.
        """
        settled = on_settled or (lambda: None)

        def on_success(account):
            if self._auth_waiting_dialog:
                self.close_dialog(self._auth_waiting_dialog)
            self._apply_signed_in_account(account)
            self.toast(f"Welcome, {account.username}!")
            settled()

        def on_error(message):
            if self._auth_waiting_dialog:
                self.close_dialog(self._auth_waiting_dialog)
            self.show_error("Microsoft sign-in", message, details="")
            settled()

        self.ms_auth.login(
            on_url=lambda url, code: self._show_auth_waiting_dialog(url, code),
            on_success=on_success,
            on_error=on_error,
        )

    def _show_auth_waiting_dialog(self, auth_url, user_code=None):
        def cancel(e=None):
            self.ms_auth.cancel()
            self.close_dialog(dlg)

        copy_hint = theme.hint("Tap to copy", size=11)

        def copy_code(e=None):
            if user_code:
                self.page.set_clipboard(user_code)
                copy_hint.value = "Copied!"
                copy_hint.color = theme.SUCCESS
                self._safe_update(copy_hint)

        code_box = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Enter this code on the sign-in page:", size=12,
                            color=theme.TEXT_SUB, text_align=ft.TextAlign.CENTER),
                    ft.Container(height=8),
                    ft.Text(user_code or "------", size=32, weight=ft.FontWeight.W_800,
                            color=theme.GOLD, selectable=True,
                            text_align=ft.TextAlign.CENTER,
                            style=ft.TextStyle(letter_spacing=4)),
                    ft.Container(height=4),
                    ft.Row([ft.Icon(ft.icons.COPY_ROUNDED, size=13, color=theme.TEXT_FAINT),
                            copy_hint],
                           alignment=ft.MainAxisAlignment.CENTER, spacing=6),
                ],
                tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(vertical=16, horizontal=24),
            bgcolor="#1c1c26", border_radius=14, border=ft.border.all(1.2, theme.GOLD_DIM),
            alignment=ft.alignment.center, ink=True, on_click=copy_code,
        )

        body = ft.Column(
            [
                ft.Text("Your browser has opened - sign in to your Microsoft account there.",
                        size=13, color=theme.TEXT_SUB, text_align=ft.TextAlign.CENTER),
                ft.Container(height=10),
                code_box,
                ft.Container(height=14),
                ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2,
                                        color=theme.GOLD)],
                       alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(height=8),
                ft.Text("Waiting for you to finish. You'll come back here automatically.",
                        size=12, color=theme.TEXT_FAINT, text_align=ft.TextAlign.CENTER),
                ft.Container(height=6),
                theme.ghost_button("Browser didn't open? Click here",
                                   lambda e: self.open_url(auth_url), color=theme.GOLD),
            ],
            tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        dlg = theme.dialog("Sign in to Microsoft", body,
                           [theme.ghost_button("Cancel", cancel)], width=420)
        self._auth_waiting_dialog = dlg
        self.open_dialog(dlg)

    # ==================================================================
    # Launch
    # ==================================================================
    def start_launch(self, e=None):
        if self.state != STATE_IDLE:
            return

        username = self.account.username if self.account else self.player_name
        if not username:
            self.show_message("Choose a name",
                              "Please enter a player name before starting.")
            return
        if not re.match(r"^[A-Za-z0-9_]{1,16}$", username):
            self.show_message(
                "That name won't work",
                "A Minecraft name can only contain letters, numbers and underscores, "
                "and must be 16 characters or fewer.",
            )
            return

        with self._session_lock:
            self._session_id += 1
            session = self._session_id

        self._apply_state(STATE_LAUNCHING)
        self._show_progress(True)
        self._play_intro()
        threading.Thread(target=self._launch_thread, args=(username, session),
                         daemon=True, name="launch").start()

    def _play_intro(self):
        """The flourish that turns a button press into the start of something.

        Deliberately non-blocking and skippable: the sparks fly while the
        launch thread is already working, and both the burst and the
        incantation are switched off by their own settings for anybody who
        wants a launcher rather than a show.
        """
        incantation = random.choice(content.incantations())
        if self.settings.get("intro_animation"):
            self.set_status(f"“{incantation}” — casting the launch spell...", theme.WARN)
        else:
            self.set_status("Casting the launch spell...", theme.WARN)

        if not self.settings.get("spell_effects"):
            return
        # The button is centred horizontally and sits a little below the
        # middle of the window; the burst is decorative, so an approximate
        # origin is fine and costs nothing to compute.
        width = self.page.window.width or 1400
        height = self.page.window.height or 880
        self.spell_burst.fire(width / 2, height * 0.62,
                              enabled=bool(self.settings.get("animations")))

    def _session_valid(self, session):
        with self._session_lock:
            return session == self._session_id

    def _fresh_account(self):
        """Return the signed-in account, renewing its token first if needed.

        A Minecraft access token lasts about a day. If the launcher has been
        sitting open longer than that, handing the stale token to the game
        means the player silently loses their skin and name. Renewing is
        cheap and only happens when the cached token has actually expired.
        """
        if self.account is None:
            return None
        if self.ms_auth.tokens.access_token_valid():
            return self.account

        self.log("Renewing your Microsoft session...")
        refresh_token, _access, _expires = self.ms_auth.tokens.load()
        renewed = self.ms_auth.refresh_login(refresh_token)
        if renewed:
            self.account = renewed
            return renewed

        # Renewal failed (revoked, offline, password changed). Play as a
        # guest under the same name rather than failing the whole launch.
        self.log("Could not renew your session; continuing as a guest.", level="WARN")
        return None

    def _launch_thread(self, username, session):
        step_label = "starting up"
        fix_id = None
        started_at = time.time()
        try:
            self.log(f"Launch requested by {username}.")

            # --- Java ---------------------------------------------------
            step_label, fix_id = "finding Java", "no_java"
            self._begin_step("java")
            java_path = self.java_manager.find_java()
            self.java_manager.check_java_version(java_path)

            # The 1.16.5 Paper server refuses to boot on Java 17+, so it gets
            # its own older JVM when one is available.
            try:
                server_java_path = self.java_manager.find_java(
                    min_major=SERVER_JAVA_MIN, max_major=SERVER_JAVA_MAX)
            except LauncherError:
                self.log(f"No Java {SERVER_JAVA_MIN}-{SERVER_JAVA_MAX} found; "
                         "using the newer Java for the server too.", level="WARN")
                server_java_path = java_path

            if not self._session_valid(session):
                return

            # --- Game content ------------------------------------------
            step_label, fix_id = "checking game files", "download_fails"
            self._begin_step("resources")
            self.server_manager.setup_resourcepack()

            step_label = "preparing the world"
            self._begin_step("world")
            self.server_manager.copy_map()
            self.server_manager.setup_server_list()

            if not self._session_valid(session):
                return

            # --- Client prep and server boot, together -----------------
            # These two are independent and both I/O bound. Overlapping them
            # is most of the reason a warm start now lands inside 15 seconds
            # instead of waiting for one and then the other.
            step_label, fix_id = "preparing the client", "download_fails"
            self._begin_step("client_files")

            # A daemon thread rather than a ThreadPoolExecutor: if the server
            # fails to boot we want to raise immediately, and an executor
            # would still join its worker at interpreter exit - which on a
            # first run means the user cannot close the launcher until a
            # multi-minute Fabric download finishes.
            client_prep = _BackgroundResult(self.client_manager.prepare, java_path)

            step_label, fix_id = "starting the world server", "port_in_use"
            self._begin_step("server")
            # The name is handed down so the server can op this player: an
            # offline-mode server derives their UUID from it, and ops.json is
            # keyed by UUID.
            self.server_manager.start_server(
                server_java_path, self.process_manager, username=username)

            step_label, fix_id = "preparing the client", "download_fails"
            fabric_version_id = client_prep.result()

            if not self._session_valid(session):
                return

            # --- Proxy --------------------------------------------------
            step_label, fix_id = "opening the portal", "port_in_use"
            self._begin_step("proxy")
            self.proxy_manager.start_proxy(java_path, self.process_manager)

            if not self._session_valid(session):
                return

            # --- Client -------------------------------------------------
            step_label, fix_id = "launching Minecraft", "not_connected"
            self._begin_step("client")
            client_proc = self.client_manager.launch(
                username, java_path, self.process_manager, fabric_version_id,
                self._fresh_account(),
            )

            # The detached watchdog guarantees the server is shut down when
            # the game exits even if the launcher is closed first. It is handed
            # the process object we just created rather than looking it up by
            # name - watching the wrong (already dead) client is what used to
            # tear the session down seconds into a second launch.
            self.process_manager.spawn_watchdog(client_proc)
            self.server_manager.start_supervisor(
                server_java_path, self.process_manager,
                on_restart=lambda n: self.schedule_ui(
                    self.set_status, f"Server hiccuped - restarting ({n})...", theme.WARN),
                on_give_up=lambda: self.schedule_ui(
                    self.show_error, "world server",
                    "The world server keeps stopping and could not be restarted.\n\n"
                    "How to fix:\n"
                    "1. Close Minecraft and press Play again,\n"
                    "2. Lower the server memory in Settings, or\n"
                    "3. Use \"Clear Cache\" if the world may be damaged.",
                    "", "stutter",
                ),
            )

            threading.Thread(target=self._watch_client_exit,
                             args=(session, client_proc),
                             daemon=True, name="client-watch").start()

            elapsed = time.time() - started_at
            self.log(f"Ready in {elapsed:.1f}s. Have fun!")
            self._apply_state_threadsafe(STATE_PLAYING)
            self.schedule_ui(self.set_status, "Adventure in progress...", theme.SUCCESS)

            if self.settings.get("close_launcher_on_play"):
                self._close_after_launch(session)

        except LauncherError as e:
            # A launch that was deliberately stopped mid-flight raises too.
            # The user knows why it ended; an error popup would be noise.
            if not self._session_valid(session):
                self.logger.write(f"Launch aborted by Stop while {step_label}.")
                return
            self.log(f"ERROR while {step_label}: {e}", level="ERROR")
            self._rollback_partial_launch(session)
            self.schedule_ui(self.show_error, step_label, e, "", fix_id)
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.write_crash(tb)
            if not self._session_valid(session):
                self.logger.write(f"Launch aborted by Stop while {step_label}.")
                return
            self.log(f"UNEXPECTED ERROR while {step_label}: {e}", level="ERROR")
            self._rollback_partial_launch(session)
            self.schedule_ui(
                self.show_error, step_label,
                f"An unexpected problem stopped the launch.\n\nDetails: {e}\n\n"
                "How to fix:\n"
                "1. Press Play again,\n"
                "2. If it keeps happening, use \"Create report\" below and attach the "
                "file to a bug report.",
                tb, "crashed",
            )

    def _close_after_launch(self, session):
        """Shut the launcher down now that the game is up.

        This used to minimise instead of close, which is not what the setting
        says and left a window in the taskbar for the whole session.

        Closing here is safe precisely because of the detached watchdog
        spawned a few lines above: it outlives the launcher and shuts the
        server and proxy down when Minecraft exits.
        :meth:`ProcessManager.cleanup` sees the client still running and hands
        over to it rather than tearing the session down.

        :meth:`_do_close` rather than :meth:`on_close`, because the latter
        asks "Minecraft is still running, close anyway?" - which is the whole
        point of this setting, so asking would be absurd.

        The short pause lets the player read "Ready in 12s" before the window
        goes, and gives Stop a window to cancel: a session that was stopped in
        the meantime must not close the launcher out from under the user.
        """
        time.sleep(1.5)
        if not self._session_valid(session):
            return
        self.log("Closing the launcher - the world will save when you quit Minecraft.")
        self.schedule_ui(self._do_close)

    def _rollback_partial_launch(self, session):
        """A failed launch must not leave a half-started session behind.

        Without this, a failure at the proxy step left the server running and
        port 25565 bound, so the next Play press failed with a completely
        unrelated "port in use" message.
        """
        if not self._session_valid(session):
            return
        try:
            self.server_manager.stop_supervisor()
            # include_client: if the failure happened after Minecraft started,
            # leaving it running would strand the player on a "connection
            # refused" screen with no server to connect to.
            self.process_manager.stop_all(include_client=True)
        except Exception as e:
            self.logger.write(f"Rollback failed: {e}", level="WARN")
        finally:
            self._apply_state_threadsafe(STATE_IDLE)

    def _watch_client_exit(self, session, client_proc=None):
        """When Minecraft exits, shut the session down and return to idle.

        `client_proc` is passed in by the launch thread. Looking it up by name
        here was the original bug: a finished client from the previous session
        was still registered, so this watcher got a process that had already
        exited, returned from ``wait()`` instantly, and shut down the server
        and proxy while the *new* game was still loading.
        """
        if client_proc is None:
            client_proc = self.process_manager.get("client")
        if client_proc is None:
            return

        client_proc.wait()

        # If Stop already ran, or a new launch started, this watcher is stale
        # and must not touch anything.
        if not self._session_valid(session):
            return

        self.log("Minecraft closed. Saving the world and shutting down...")
        self._apply_state_threadsafe(STATE_STOPPING)
        self.server_manager.stop_supervisor()
        self.process_manager.stop_all(include_client=True)

        if not self._session_valid(session):
            return

        self.log("World saved. Ready to play again.")
        self._apply_state_threadsafe(STATE_IDLE)
        self.schedule_ui(self.set_status, self.idle_messages[0], theme.SUCCESS)

    # ==================================================================
    # Stop
    # ==================================================================
    def confirm_stop(self, e=None):
        if self.state not in (STATE_LAUNCHING, STATE_PLAYING):
            return

        game_running = self.process_manager.is_running("client")
        if game_running:
            message = (
                "This will close Minecraft and shut down the world server.\n\n"
                "Your progress is saved before anything is closed - the server is asked "
                "to save the world first, and only forced to close if it stops responding."
            )
        else:
            message = (
                "This will shut down the world server and the portal, freeing "
                f"ports {SERVER_PORT} and {PROXY_PORT}.\n\n"
                "The world is saved first."
            )

        self.show_message(
            "Stop and save?", message,
            on_confirm=lambda: threading.Thread(
                target=self._stop_thread, args=(game_running,), daemon=True, name="stop"
            ).start(),
            confirm_label="Stop & Save",
        )

    def _stop_thread(self, include_client):
        # Invalidate the session FIRST. Every background watcher checks this,
        # so the moment we bump it the client-exit watcher and the crash
        # supervisor both become no-ops and cannot fight this shutdown.
        with self._session_lock:
            self._session_id += 1

        self._apply_state_threadsafe(STATE_STOPPING)
        self.schedule_ui(self._show_progress, True)
        self.schedule_ui(self._apply_sub_progress, None, "Shutting down...")

        try:
            self.server_manager.stop_supervisor()
            self.log("Stopping the world...")

            ok = self.process_manager.stop_all(
                include_client=include_client,
                on_progress=lambda msg: self.schedule_ui(self._apply_sub_progress, None, msg),
            )

            if ok:
                self.log("World saved. Everything is stopped and the ports are free.")
                self.schedule_ui(self.toast, "Stopped. The world was saved.")
            else:
                self.log("Some processes could not be stopped cleanly.", level="WARN")
                self.schedule_ui(
                    self.show_error, "stopping",
                    "Some parts of the game could not be closed automatically.\n\n"
                    "How to fix:\n"
                    "1. Open Task Manager and end any remaining 'java' or 'javaw' processes,\n"
                    "2. Or restart your computer if the ports stay busy.",
                    "", "port_in_use",
                )
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.write_crash(tb)
            self.log(f"ERROR while stopping: {e}", level="ERROR")
            self.schedule_ui(self.show_error, "stopping", e, tb)
        finally:
            self._apply_state_threadsafe(STATE_IDLE)
            self.schedule_ui(self.set_status, self.idle_messages[0], theme.SUCCESS)

    # ==================================================================
    # Maintenance
    # ==================================================================
    def confirm_clear_cache(self, e=None):
        if self.state != STATE_IDLE:
            self.show_message("Not right now",
                              "Please stop the game before clearing the cache.")
            return
        self.show_message(
            "Erase everything?",
            "This deletes:\n"
            "• The whole castle world, including everything you have built\n"
            "• Your inventory, position and progress\n"
            "• The downloaded game files and mods\n\n"
            "The next launch will download and install everything again.\n"
            "This cannot be undone.",
            on_confirm=lambda: threading.Thread(target=self._clear_cache_thread,
                                                daemon=True, name="clear-cache").start(),
            confirm_label="Erase everything",
        )

    def _clear_cache_thread(self):
        self._apply_state_threadsafe(STATE_BUSY)
        self.schedule_ui(self._show_progress, True)
        self.schedule_ui(self._apply_sub_progress, None, "Clearing...")
        try:
            self.log("Clearing cache...")
            if os.path.isdir(self.mc_dir):
                shutil.rmtree(self.mc_dir, ignore_errors=True)
                self.log("Client files removed.")

            if os.path.isdir(self.server_dir):
                for item in os.listdir(self.server_dir):
                    if item in SERVER_KEEP_ON_CLEAR:
                        continue
                    path = os.path.join(self.server_dir, item)
                    try:
                        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
                    except Exception as err:
                        self.log(f"Could not delete {item}: {err}", level="WARN")
                self.log("Server data removed.")

            # Also drop the downloaded archives so the next run re-fetches
            # them; otherwise "clear cache" would reuse a possibly-bad copy.
            copy_dir = os.path.join(self.resources_dir, "copy")
            if os.path.isdir(copy_dir):
                shutil.rmtree(copy_dir, ignore_errors=True)
            if os.path.isdir(self.modpack_cache_dir):
                shutil.rmtree(self.modpack_cache_dir, ignore_errors=True)

            # Last, so a failure above cannot leave the launcher believing
            # work is done that has just been deleted. Forgetting everything
            # is always safe: each step re-verifies from the filesystem.
            self.install_state.clear()

            self.log("Cache cleared.")
            self.schedule_ui(self.show_message, "All clear",
                             "Everything was erased. The next launch will reinstall the castle.")
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.write_crash(tb)
            self.schedule_ui(self.show_error, "clearing the cache", e, tb)
        finally:
            self._apply_state_threadsafe(STATE_IDLE)

    def confirm_clear_player_data(self, e=None):
        if self.state != STATE_IDLE:
            self.show_message("Not right now",
                              "Please stop the game before clearing player data.")
            return
        backing_up = bool(self.settings.get("backup_before_reset"))
        self.show_message(
            "Start the castle over?",
            "This deletes the whole world and installs the castle again from scratch:\n"
            "• Inventory, position, health and advancements\n"
            "• Every block you have placed or broken\n"
            "• Chests, redstone and anything else the world remembers\n\n"
            "The castle comes back exactly as it ships. Your downloaded files are "
            "kept, so this takes seconds rather than another download.\n\n"
            + ("A backup of your progress is saved first, so it can be copied back "
               "from the backups folder."
               if backing_up else
               "Backups are switched off in Settings, so this cannot be undone."),
            on_confirm=lambda: threading.Thread(target=self._clear_player_data_thread,
                                                daemon=True, name="clear-player").start(),
            confirm_label="Reinstall the castle", danger=True,
        )

    def _backup_player_data(self):
        """Zip the player-data folders before they are deleted.

        Small (kilobytes, not gigabytes - the world itself is not touched) and
        the difference between a mis-click costing a session and costing
        nothing. Older backups are pruned so this cannot grow without bound.
        """
        import zipfile

        if not os.path.isdir(self.world_dest_dir):
            return ""

        os.makedirs(self.backup_dir, exist_ok=True)
        path = os.path.join(self.backup_dir,
                            f"playerdata-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in PLAYER_DATA_WORLD_ITEMS:
                    source = os.path.join(self.world_dest_dir, item)
                    if not os.path.isdir(source):
                        continue
                    for dirpath, _dirnames, filenames in os.walk(source):
                        for filename in filenames:
                            full = os.path.join(dirpath, filename)
                            zf.write(full, os.path.join(
                                "world", os.path.relpath(full, self.world_dest_dir)))
                for filename in PLAYER_DATA_SERVER_FILES:
                    source = os.path.join(self.server_dir, filename)
                    if os.path.isfile(source):
                        zf.write(source, filename)
        except Exception as e:
            self.log(f"Could not back up player data: {e}", level="WARN")
            return ""

        try:
            backups = sorted(f for f in os.listdir(self.backup_dir)
                             if f.startswith("playerdata-") and f.endswith(".zip"))
            for stale in backups[:-PLAYER_DATA_BACKUP_KEEP]:
                os.remove(os.path.join(self.backup_dir, stale))
        except OSError:
            pass

        self.log(f"Player data backed up to {os.path.basename(path)}.")
        return path

    def _clear_player_data_thread(self):
        """Delete the world outright and lay the castle back down.

        This used to remove only ``playerdata``/``stats``/``advancements``,
        which reset the player but left every block they had changed. That is
        not what "start over" means to somebody who has been building in the
        castle for a week - they got their inventory wiped and their mess
        kept. Now the world folder goes and the map is reinstalled from the
        downloaded copy, so the castle is genuinely as it shipped.

        The archive is not re-downloaded: it is already on disk, so this is a
        local copy measured in seconds.
        """
        self._apply_state_threadsafe(STATE_BUSY)
        self.schedule_ui(self._show_progress, True)
        self.schedule_ui(self._apply_sub_progress, None, "Starting the castle over...")
        backup_path = ""
        try:
            if self.settings.get("backup_before_reset"):
                self.schedule_ui(self._apply_sub_progress, None, "Backing up your progress...")
                backup_path = self._backup_player_data()

            self.log("Removing the world...")
            self.schedule_ui(self._apply_sub_progress, None, "Removing the old world...")
            if os.path.isdir(self.world_dest_dir):
                shutil.rmtree(self.world_dest_dir, ignore_errors=True)
                if os.path.isdir(self.world_dest_dir):
                    raise LauncherError(
                        "The world folder could not be deleted.\n\n"
                        "How to fix:\n"
                        "1. Make sure Minecraft and the world server are closed,\n"
                        "2. Press Stop if either is still running, then try again."
                    )
                self.log("World removed.")
            else:
                self.log("No world installed yet; installing a fresh one.")

            # ops.json and usercache.json are rewritten on the next launch;
            # dropping them here keeps a renamed player from inheriting the
            # previous one's entries.
            for filename in PLAYER_DATA_SERVER_FILES:
                path = os.path.join(self.server_dir, filename)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except Exception as err:
                        self.log(f"Could not delete {filename}: {err}", level="WARN")

            self.schedule_ui(self._apply_sub_progress, None, "Installing the castle...")
            self.server_manager.copy_map(force=True)
            self.server_manager.setup_server_list()

            self.log("The castle has been reinstalled.")
            message = ("The world was deleted and the castle reinstalled from scratch. "
                       "Your next launch starts at the very beginning.")
            if backup_path:
                message += f"\n\nA backup of your old progress was saved to:\n{backup_path}"
            self.schedule_ui(self.show_message, "Castle reinstalled", message)
        except LauncherError as e:
            self.log(f"ERROR while reinstalling the castle: {e}", level="ERROR")
            self.schedule_ui(self.show_error, "reinstalling the castle", e, "")
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.write_crash(tb)
            self.schedule_ui(self.show_error, "reinstalling the castle", e, tb)
        finally:
            self._apply_state_threadsafe(STATE_IDLE)

    # ==================================================================
    # Shutdown
    # ==================================================================
    def on_close(self, e=None):
        if self._closing:
            return

        # Closing mid-game should not yank the player out of the castle, but
        # they do deserve to be told what will happen.
        if self.process_manager.is_running("client"):
            self.show_message(
                "Minecraft is still running",
                "You can close the launcher and keep playing - the world will shut down "
                "and save automatically when you quit Minecraft.\n\n"
                "Close the launcher now?",
                on_confirm=self._do_close, danger=False, confirm_label="Close launcher",
            )
            return

        self._do_close()

    def _do_close(self):
        if self._closing:
            return
        self._closing = True
        self._loops_running = False
        self.embers.stop()
        self.rune_drift.stop()
        try:
            self.server_manager.stop_supervisor()
            self.process_manager.cleanup()
        except Exception as e:
            self.logger.write(f"Shutdown cleanup failed: {e}", level="WARN")
        self.logger.write("Launcher closed.")
        try:
            self.page.window.prevent_close = False
            self.page.window.close()
        except Exception:
            pass


def main(page: ft.Page):
    LauncherApp(page)


if __name__ == "__main__":
    ft.app(main)
