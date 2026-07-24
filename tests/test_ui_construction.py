"""Build every dialog against a stub app and check the tree is sane.

Flet controls can be constructed without a running page, which makes this
the cheapest way to catch the failures that used to need a human to click
through the launcher: a renamed helper on the app, a missing settings key, a
data file whose shape changed, an icon name that does not exist.

It also enforces the rule the whole UI depends on - **a control may only
ever occupy one slot in the tree**. Flet stamps a control with a uid the
first time it is rendered; putting the same object under a second parent
leaves the Flutter-side control map with one id in two places and the window
goes blank, with no Python traceback to explain it. That bug cost a lot of
debugging once; :func:`walk` makes it a test failure instead.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The tests package pins WIZARD_LAUNCHER_DATA at a scratch folder; this is
# the fallback for running this module on its own. Either way it must be set
# before anything asks where the data lives - a StubApp builds a real logger
# and a real Settings, and both write files.
if not (os.environ.get("WIZARD_LAUNCHER_DATA") or "").strip():
    os.environ["WIZARD_LAUNCHER_DATA"] = tempfile.mkdtemp(prefix="wizard-ui-test-")
_TMP_ROOT = os.environ["WIZARD_LAUNCHER_DATA"]

import flet as ft  # noqa: E402

from launcher_core import content  # noqa: E402
from launcher_core.app_log import get_logger  # noqa: E402
from launcher_core.config import Settings  # noqa: E402
from ui import (  # noqa: E402
    about_dialog, account_dialog, help_dialog, onboarding, settings_dialog, theme,
)


def walk(control, path="root", seen=None, problems=None):
    """Depth-first walk collecting controls that appear more than once."""
    seen = {} if seen is None else seen
    problems = [] if problems is None else problems
    if control is None or not isinstance(control, ft.Control):
        return problems

    key = id(control)
    if key in seen:
        problems.append(f"{type(control).__name__} appears at {seen[key]} and {path}")
        return problems
    seen[key] = path

    for attr in ("content", "controls", "actions", "title", "tabs", "segments",
                 "leading", "trailing", "error_content", "prefix", "suffix"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for index, item in enumerate(child):
                walk(item, f"{path}.{attr}[{index}]", seen, problems)
        else:
            walk(child, f"{path}.{attr}", seen, problems)
    return problems


def count_controls(control, seen=None):
    seen = set() if seen is None else seen
    if control is None or not isinstance(control, ft.Control) or id(control) in seen:
        return 0
    seen.add(id(control))
    total = 1
    for attr in ("content", "controls", "actions", "title", "tabs", "segments",
                 "leading", "trailing", "error_content"):
        child = getattr(control, attr, None)
        children = child if isinstance(child, list) else [child]
        for item in children:
            total += count_controls(item, seen)
    return total


class StubApp:
    """The surface every dialog module is allowed to use.

    Kept deliberately explicit: if a dialog reaches for something that is not
    here, that is the test telling you the UI grew a new dependency on the
    main window, which is exactly the coupling the split was meant to stop.
    """

    def __init__(self):
        self.settings = Settings()
        self.logger = get_logger()
        self.java_manager = None
        self.account = None
        self.player_name = "Player"
        self.base_dir = _TMP_ROOT
        self.opened = []
        self.closed = []
        self.toasts = []
        self.urls = []

    # dialog plumbing
    def open_dialog(self, dlg):
        self.opened.append(dlg)

    def close_dialog(self, dlg):
        self.closed.append(dlg)

    def refresh_dialog(self, dlg):
        pass

    def schedule_ui(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def toast(self, message):
        self.toasts.append(message)

    def open_url(self, url):
        self.urls.append(url)

    # app behaviour the dialogs call into
    def log(self, message, level="INFO"):
        pass

    def open_data_folder(self):
        pass

    def report_issue(self):
        pass

    def report_created(self, path):
        pass

    def apply_settings_changes(self):
        pass

    def show_onboarding(self, force=False):
        pass

    def reopen_onboarding(self, index):
        pass

    def start_microsoft_login(self, on_settled=None):
        pass

    def sign_out(self):
        pass

    def set_player_name(self, name):
        self.player_name = name


class DialogConstructionTests(unittest.TestCase):
    def setUp(self):
        self.app = StubApp()

    def assert_tree_ok(self, dlg, minimum=10):
        self.assertIsInstance(dlg, ft.AlertDialog)
        problems = walk(dlg)
        self.assertEqual(problems, [], "a control was placed in two slots: " + "; ".join(problems))
        self.assertGreater(count_controls(dlg), minimum)

    def test_settings_dialog_builds(self):
        settings_dialog.show(self.app)
        self.assertEqual(len(self.app.opened), 1)
        self.assert_tree_ok(self.app.opened[0], minimum=60)

    def test_settings_dialog_saves_every_switch(self):
        dialog = settings_dialog.SettingsDialog(self.app)
        for switch in dialog.switches.values():
            switch.value = True
        dialog.server_slider.value = 2048
        dialog.client_slider.value = 3072
        dialog._save()
        self.assertEqual(self.app.settings.get("server_ram_mb"), 2048)
        self.assertEqual(self.app.settings.get("client_ram_mb"), 3072)
        self.assertTrue(self.app.settings.get("allow_lan"))

    def test_help_dialog_builds(self):
        help_dialog.show(self.app)
        self.assert_tree_ok(self.app.opened[0], minimum=40)

    def test_help_search_narrows_the_lists(self):
        dialog = help_dialog.HelpDialog(self.app)
        everything = len(dialog.faq_column.controls)
        dialog.query = "zzzznothing"
        dialog._render_lists()
        # A no-match state still renders one line - the "nothing matches" hint.
        self.assertEqual(len(dialog.faq_column.controls), 1)
        self.assertLess(1, everything)

    def test_help_can_focus_a_troubleshooting_entry(self):
        entry_id = content.troubleshooting()[0]["id"]
        dialog = help_dialog.HelpDialog(self.app, focus=entry_id)
        expanded = [c for c in dialog.fix_column.controls
                    if isinstance(c, ft.ExpansionTile) and c.initially_expanded]
        self.assertEqual(len(expanded), 1)

    def test_about_dialog_builds(self):
        about_dialog.show(self.app)
        self.assert_tree_ok(self.app.opened[0], minimum=40)

    def test_update_dialog_previews_the_changelog(self):
        about_dialog.show_update(self.app, {"version": "9.9.9", "url": "https://example.com",
                                            "notes": "Everything is new."})
        self.assert_tree_ok(self.app.opened[0], minimum=15)

    def test_account_dialog_signed_out_builds(self):
        account_dialog.show(self.app)
        self.assert_tree_ok(self.app.opened[0], minimum=25)

    def test_account_dialog_signed_in_builds(self):
        class FakeAccount:
            username = "Foxy"
            uuid = "0" * 32

        self.app.account = FakeAccount()
        account_dialog.show(self.app)
        dlg = self.app.opened[0]
        self.assert_tree_ok(dlg, minimum=15)

    def test_onboarding_renders_every_step(self):
        wizard = onboarding.OnboardingWizard(self.app)
        wizard.open()
        for index in range(len(wizard.steps)):
            wizard.index = index
            wizard._render()
            self.assertEqual(walk(wizard.dialog), [])
        self.assertEqual(len(wizard.dots_row.controls), len(wizard.steps))

    def test_onboarding_completion_is_recorded(self):
        self.assertTrue(onboarding.should_show(self.app.settings))
        onboarding.mark_complete(self.app.settings)
        self.assertFalse(onboarding.should_show(self.app.settings))

    def test_onboarding_memory_preset_writes_settings(self):
        wizard = onboarding.OnboardingWizard(self.app)
        wizard._memory_preset = "performance"
        wizard._apply_memory_preset()
        self.assertGreater(self.app.settings.get("server_ram_mb"), 0)
        wizard._memory_preset = "balanced"
        wizard._apply_memory_preset()
        # Balanced stores 0, meaning "follow the machine", so adding RAM later
        # is picked up without the player having to revisit Settings.
        self.assertEqual(self.app.settings.get("server_ram_mb"), 0)


class ThemeTests(unittest.TestCase):
    def test_palette_is_loaded_from_data(self):
        self.assertTrue(theme.GOLD.startswith("#"))
        self.assertTrue(theme.FONTS[theme.FONT_DISPLAY].startswith("https://"))

    def test_unknown_icon_name_falls_back(self):
        self.assertEqual(theme.icon_name("definitely_not_an_icon", ft.icons.HELP_ROUNDED),
                         ft.icons.HELP_ROUNDED)
        self.assertEqual(theme.icon_name("memory_rounded"), ft.icons.MEMORY_ROUNDED)

    def test_action_button_can_be_disabled_and_re_enabled(self):
        btn = theme.action_button("Stop", ft.icons.STOP_CIRCLE_ROUNDED, None)
        theme.set_enabled(btn, False)
        self.assertTrue(btn.disabled)
        self.assertEqual(btn.data["text"].color, theme.TEXT_FAINT)
        theme.set_enabled(btn, True)
        self.assertFalse(btn.disabled)
        self.assertEqual(btn.data["text"].color, theme.DANGER)


if __name__ == "__main__":
    unittest.main()
