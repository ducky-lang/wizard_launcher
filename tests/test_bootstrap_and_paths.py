"""Tests for the data folder and the bundled-resource install step.

These matter most on macOS, where copying the jars out of the read-only .app
bundle is not a fallback but the only way they ever reach disk.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core import paths, platform_utils  # noqa: E402
from launcher_core.bootstrap import ensure_data_resources  # noqa: E402
from launcher_core.migration import migrate_data_root  # noqa: E402


class DataRootTests(unittest.TestCase):
    """Path resolution only.

    ``get_data_root()`` is called with an override or not at all here: without
    one it would create - and migrate - the real install belonging to whoever
    is running the suite. The platform-shape assertion therefore goes through
    ``_default_data_root()``, which computes the path and touches nothing.
    See :mod:`tests` for why this matters.
    """

    def setUp(self):
        self._saved = os.environ.get("WIZARD_LAUNCHER_DATA")
        paths.reset_data_root_cache()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("WIZARD_LAUNCHER_DATA", None)
        else:
            os.environ["WIZARD_LAUNCHER_DATA"] = self._saved
        paths.reset_data_root_cache()

    def test_override_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "custom root")
            os.environ["WIZARD_LAUNCHER_DATA"] = target
            self.assertEqual(paths.get_data_root(), os.path.abspath(target))
            self.assertTrue(os.path.isdir(target))

    def test_default_root_is_application_data_not_documents(self):
        root = paths._default_data_root()
        self.assertTrue(root.endswith(paths.APP_DIR_NAME))
        if platform_utils.IS_MACOS:
            self.assertIn("Application Support", root)
        elif platform_utils.IS_WINDOWS:
            # A OneDrive-synced Documents folder and a live Minecraft world
            # do not mix; application state belongs in LocalAppData.
            self.assertIn("AppData", root)
            self.assertNotIn("Documents", root)

    def test_legacy_root_is_still_known(self):
        """The old location has to stay resolvable - the migration and the
        diagnostics report both need to be able to look at it."""
        legacy = paths.get_legacy_data_root()
        if platform_utils.IS_WINDOWS:
            self.assertIn("Documents", legacy)
            self.assertNotEqual(legacy, paths._default_data_root())
        else:
            self.assertEqual(legacy, "")

    def test_repeated_calls_are_memoised(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["WIZARD_LAUNCHER_DATA"] = tmp
            self.assertIs(paths.get_data_root(), paths.get_data_root())


class MigrationTests(unittest.TestCase):
    """The move out of Documents must never cost anybody their castle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = os.path.join(self.tmp, "Documents", "WizardLauncher")
        self.new = os.path.join(self.tmp, "AppData", "Local", "WizardLauncher")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self, root, relative="resources/servers/1.16.5/world/level.dat", content="world"):
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_existing_install_is_moved(self):
        self.seed(self.legacy)
        self.assertEqual(migrate_data_root(self.legacy, self.new), self.new)
        self.assertTrue(os.path.isfile(os.path.join(
            self.new, "resources", "servers", "1.16.5", "world", "level.dat")))
        self.assertFalse(os.path.isdir(self.legacy))

    def test_player_is_told_where_it_went(self):
        self.seed(self.legacy)
        migrate_data_root(self.legacy, self.new)
        note = os.path.join(self.tmp, "Documents", "WizardLauncher - moved.txt")
        self.assertTrue(os.path.isfile(note))

    def test_fresh_install_is_left_alone(self):
        self.assertEqual(migrate_data_root(self.legacy, self.new), self.new)
        self.assertFalse(os.path.isdir(self.legacy))

    def test_empty_new_folder_does_not_block_the_move(self):
        """get_data_root() may already have created the target."""
        self.seed(self.legacy)
        os.makedirs(self.new)
        self.assertEqual(migrate_data_root(self.legacy, self.new), self.new)
        self.assertTrue(os.path.isfile(os.path.join(
            self.new, "resources", "servers", "1.16.5", "world", "level.dat")))

    def test_already_migrated_install_is_not_touched_again(self):
        self.seed(self.new, content="current")
        self.seed(self.legacy, content="stale")
        self.assertEqual(migrate_data_root(self.legacy, self.new), self.new)
        with open(os.path.join(self.new, "resources", "servers", "1.16.5",
                               "world", "level.dat"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "current")
        # The old folder is left exactly as it was rather than deleted.
        self.assertTrue(os.path.isdir(self.legacy))

    def test_failed_move_keeps_using_the_old_folder(self):
        """A locked file must not look like "you have no world"."""
        self.seed(self.legacy)
        messages = []

        def exploding_rename(*_args, **_kwargs):
            raise OSError(13, "Permission denied")

        original = os.rename
        os.rename = exploding_rename
        try:
            result = migrate_data_root(self.legacy, self.new, log=messages.append)
        finally:
            os.rename = original

        self.assertEqual(result, self.legacy)
        self.assertTrue(os.path.isfile(os.path.join(
            self.legacy, "resources", "servers", "1.16.5", "world", "level.dat")))
        self.assertTrue(any("Documents" in m for m in messages))

    def test_same_source_and_destination_is_a_no_op(self):
        self.seed(self.legacy)
        self.assertEqual(migrate_data_root(self.legacy, self.legacy), self.legacy)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bundled = os.path.join(self.tmp, "bundle", "resources")
        self.data = os.path.join(self.tmp, "data", "resources")
        os.makedirs(os.path.join(self.bundled, "servers", "1.16.5", "plugins"))
        os.makedirs(os.path.join(self.bundled, "proxy"))
        self.write(os.path.join(self.bundled, "proxy", "ViaProxy.jar"), "v1")
        self.write(os.path.join(self.bundled, "servers", "1.16.5", "server.properties"),
                   "shipped-defaults")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def write(path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def read(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def run_bootstrap(self):
        return ensure_data_resources(self.bundled, self.data)

    def test_first_run_installs_everything(self):
        self.assertEqual(self.run_bootstrap(), 2)
        self.assertTrue(os.path.isfile(os.path.join(self.data, "proxy", "ViaProxy.jar")))

    def test_second_run_copies_nothing(self):
        self.run_bootstrap()
        self.assertEqual(self.run_bootstrap(), 0)

    def test_missing_jar_is_repaired(self):
        """Antivirus quarantine, a tidy-up, a failed uninstall - the launcher
        puts the file back rather than failing at step four."""
        self.run_bootstrap()
        os.remove(os.path.join(self.data, "proxy", "ViaProxy.jar"))
        self.assertEqual(self.run_bootstrap(), 1)

    def test_newer_jar_replaces_older(self):
        self.run_bootstrap()
        time.sleep(0.05)
        self.write(os.path.join(self.bundled, "proxy", "ViaProxy.jar"), "v2")
        os.utime(os.path.join(self.bundled, "proxy", "ViaProxy.jar"),
                 (time.time() + 60, time.time() + 60))
        self.assertEqual(self.run_bootstrap(), 1)
        self.assertEqual(self.read(os.path.join(self.data, "proxy", "ViaProxy.jar")), "v2")

    def test_user_edited_config_is_never_overwritten(self):
        self.run_bootstrap()
        edited = os.path.join(self.data, "servers", "1.16.5", "server.properties")
        self.write(edited, "my-own-settings")
        os.utime(os.path.join(self.bundled, "servers", "1.16.5", "server.properties"),
                 (time.time() + 60, time.time() + 60))
        self.run_bootstrap()
        self.assertEqual(self.read(edited), "my-own-settings")

    def test_world_is_never_shipped_or_touched(self):
        world = os.path.join(self.bundled, "servers", "1.16.5", "world")
        self.write(os.path.join(world, "level.dat"), "x")
        self.run_bootstrap()
        self.assertFalse(os.path.exists(
            os.path.join(self.data, "servers", "1.16.5", "world")))

    def test_same_source_and_destination_is_a_no_op(self):
        """Running from a source checkout where both point at resources/."""
        self.assertEqual(ensure_data_resources(self.bundled, self.bundled), 0)

    def test_missing_bundle_is_not_an_error(self):
        self.assertEqual(ensure_data_resources("", self.data), 0)
        self.assertEqual(ensure_data_resources("/no/such/folder", self.data), 0)


if __name__ == "__main__":
    unittest.main()
