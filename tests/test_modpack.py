"""Tests for the Modrinth modpack installer.

Every file this module writes is a jar the player's JVM will execute, so the
interesting tests are the refusals: a path that escapes the game folder, a
download URL on a host we do not trust, an entry with no usable mirror.

The happy path is covered too, but with a locally built .mrpack rather than a
real download - the point is the unpacking and bookkeeping logic, not
Modrinth's uptime.
"""

import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core import modpack  # noqa: E402
from launcher_core.constants import is_allowed_modpack_host  # noqa: E402
from launcher_core.exceptions import LauncherError  # noqa: E402


class SafePathTests(unittest.TestCase):
    def test_ordinary_mod_paths_are_accepted(self):
        for path in ("mods/sodium.jar", "config/iris.properties",
                     "resourcepacks/pack.zip", "shaderpacks/x.zip"):
            self.assertEqual(modpack._safe_relative_path(path), path)

    def test_traversal_is_refused(self):
        for path in ("../evil.jar", "mods/../../evil.jar", "..",
                     "mods/../../../AppData/Roaming/evil.jar"):
            self.assertIsNone(modpack._safe_relative_path(path), path)

    def test_absolute_and_drive_paths_are_refused(self):
        for path in ("/etc/passwd", "C:/Windows/system32/evil.dll",
                     "C:\\Windows\\evil.dll"):
            self.assertIsNone(modpack._safe_relative_path(path), path)

    def test_paths_outside_the_allowed_roots_are_refused(self):
        """A pack has no business writing to versions/ or the launcher's own files."""
        for path in ("versions/1.20.1/1.20.1.jar", "options.txt",
                     "saves/world/level.dat", "launcher_core/evil.py"):
            self.assertIsNone(modpack._safe_relative_path(path), path)

    def test_backslashes_are_normalised_not_trusted(self):
        self.assertEqual(modpack._safe_relative_path("mods\\sodium.jar"),
                         "mods/sodium.jar")

    def test_empty_and_wrong_types_are_refused(self):
        for value in ("", "   ", None, 7, []):
            self.assertIsNone(modpack._safe_relative_path(value))


class HostTests(unittest.TestCase):
    def test_modrinth_is_allowed(self):
        for host in ("modrinth.com", "cdn.modrinth.com", "api.modrinth.com"):
            self.assertTrue(is_allowed_modpack_host(host), host)

    def test_lookalikes_are_refused(self):
        for host in ("evil-modrinth.com", "modrinth.com.attacker.net",
                     "notmodrinth.com", "", None):
            self.assertFalse(is_allowed_modpack_host(host), host)

    def test_the_map_host_is_not_a_modpack_host(self):
        """The two allow-lists are separate on purpose; they must not blur."""
        self.assertFalse(is_allowed_modpack_host("huggingface.co"))

    def test_check_host_refuses_plain_http(self):
        with self.assertRaises(LauncherError):
            modpack._check_host("http://cdn.modrinth.com/a.jar")

    def test_check_host_refuses_a_foreign_host(self):
        with self.assertRaises(LauncherError) as ctx:
            modpack._check_host("https://cdn.attacker.net/a.jar")
        self.assertIn("attacker.net", str(ctx.exception))


def _index(files, dependencies=None):
    return json.dumps({
        "formatVersion": 1, "game": "minecraft", "name": "Test",
        "versionId": "1.0", "files": files,
        "dependencies": dependencies or {"minecraft": "1.20.1"},
    }).encode("utf-8")


def _entry(path, url="https://cdn.modrinth.com/data/x/y/a.jar", **extra):
    entry = {"path": path, "downloads": [url], "hashes": {"sha512": "ab" * 64},
             "fileSize": 10}
    entry.update(extra)
    return entry


class IndexParsingTests(unittest.TestCase):
    def test_a_well_formed_index_parses(self):
        files, deps = modpack._parse_index(_index([_entry("mods/a.jar")]))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["path"], "mods/a.jar")
        self.assertEqual(deps["minecraft"], "1.20.1")

    def test_unsafe_paths_are_dropped_not_installed(self):
        files, _ = modpack._parse_index(_index([
            _entry("mods/good.jar"), _entry("../evil.jar"),
        ]))
        self.assertEqual([f["path"] for f in files], ["mods/good.jar"])

    def test_entries_on_untrusted_hosts_are_dropped(self):
        files, _ = modpack._parse_index(_index([
            _entry("mods/good.jar"),
            _entry("mods/evil.jar", url="https://cdn.attacker.net/evil.jar"),
        ]))
        self.assertEqual([f["path"] for f in files], ["mods/good.jar"])

    def test_a_trusted_mirror_is_preferred_over_an_untrusted_first_choice(self):
        entry = _entry("mods/a.jar")
        entry["downloads"] = ["https://github.com/x/a.jar",
                              "https://cdn.modrinth.com/data/x/y/a.jar"]
        files, _ = modpack._parse_index(_index([entry]))
        self.assertEqual(files[0]["url"], "https://cdn.modrinth.com/data/x/y/a.jar")

    def test_server_only_files_are_skipped(self):
        files, _ = modpack._parse_index(_index([
            _entry("mods/client.jar"),
            _entry("mods/server.jar", env={"client": "unsupported", "server": "required"}),
        ]))
        self.assertEqual([f["path"] for f in files], ["mods/client.jar"])

    def test_an_index_with_nothing_installable_raises(self):
        with self.assertRaises(LauncherError):
            modpack._parse_index(_index([_entry("../evil.jar")]))

    def test_malformed_json_raises_a_readable_error(self):
        with self.assertRaises(LauncherError) as ctx:
            modpack._parse_index(b"{not json")
        self.assertIn("Clear Cache", str(ctx.exception))


class OverrideTests(unittest.TestCase):
    """Unpacking overrides/ must not be able to write outside the game folder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wizard-modpack-")
        self.mc_dir = os.path.join(self.dir, "minecraft")
        os.makedirs(self.mc_dir)
        self.messages = []
        self.installer = modpack.ModpackInstaller(
            self.messages.append, self.mc_dir, os.path.join(self.dir, "cache"))

    def _pack(self, members):
        path = os.path.join(self.dir, "test.mrpack")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return zipfile.ZipFile(path)

    def test_overrides_land_in_the_game_folder(self):
        with self._pack({"overrides/config/a.json": "{}"}) as pack:
            written = self.installer._apply_overrides(pack)
        self.assertEqual(written, ["config/a.json"])
        self.assertTrue(os.path.isfile(os.path.join(self.mc_dir, "config", "a.json")))

    def test_client_overrides_are_applied_too(self):
        with self._pack({"client-overrides/config/b.json": "{}"}) as pack:
            written = self.installer._apply_overrides(pack)
        self.assertEqual(written, ["config/b.json"])

    def test_a_traversing_override_is_refused(self):
        with self._pack({"overrides/../../escaped.txt": "x"}) as pack:
            written = self.installer._apply_overrides(pack)
        self.assertEqual(written, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "escaped.txt")))

    def test_an_override_outside_the_allowed_roots_is_refused(self):
        with self._pack({"overrides/options.txt": "x"}) as pack:
            written = self.installer._apply_overrides(pack)
        self.assertEqual(written, [])


class SweepTests(unittest.TestCase):
    """The sweep is what migrates a player off the old bundled mod set."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wizard-sweep-")
        self.mods = os.path.join(self.dir, "mods")
        os.makedirs(self.mods)
        self.messages = []
        self.installer = modpack.ModpackInstaller(
            self.messages.append, self.dir, os.path.join(self.dir, "cache"))

    def _touch(self, name):
        with open(os.path.join(self.mods, name), "w", encoding="utf-8") as f:
            f.write("x")

    def test_unmanaged_jars_are_removed(self):
        self._touch("ViaFabricPlus.jar")
        self._touch("sodium.jar")
        self.installer._sweep_foreign_mods(["mods/sodium.jar"])
        self.assertFalse(os.path.exists(os.path.join(self.mods, "ViaFabricPlus.jar")))
        self.assertTrue(os.path.exists(os.path.join(self.mods, "sodium.jar")))

    def test_non_jar_files_are_left_alone(self):
        self._touch("README.txt")
        self.installer._sweep_foreign_mods([])
        self.assertTrue(os.path.exists(os.path.join(self.mods, "README.txt")))

    def test_a_missing_mods_folder_is_not_an_error(self):
        installer = modpack.ModpackInstaller(
            self.messages.append, os.path.join(self.dir, "nope"), self.dir)
        installer._sweep_foreign_mods([])

    def test_player_mods_survive_a_pack_upgrade(self):
        # Once the launcher owns the folder (a previous manifest exists), a jar
        # the player added is not one of ours and must not be swept - even when
        # a modpack upgrade drops the old bundled jar that replaced it.
        self._touch("my-cool-mod.jar")     # the player's own
        self._touch("sodium-0.5.3.jar")    # ours, from the previous pack
        self._touch("sodium-0.5.8.jar")    # ours, from the new pack
        previous = {"files": ["mods/sodium-0.5.3.jar"]}
        self.installer._sweep_foreign_mods(["mods/sodium-0.5.8.jar"], previous)
        self.assertTrue(os.path.exists(os.path.join(self.mods, "my-cool-mod.jar")))
        self.assertTrue(os.path.exists(os.path.join(self.mods, "sodium-0.5.8.jar")))
        self.assertFalse(os.path.exists(os.path.join(self.mods, "sodium-0.5.3.jar")))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wizard-manifest-")
        self.installer = modpack.ModpackInstaller(
            lambda m: None, self.dir, os.path.join(self.dir, "cache"))

    def test_is_installed_is_false_without_a_manifest(self):
        self.assertFalse(self.installer.is_installed())

    def test_is_installed_is_true_once_the_files_are_there(self):
        wanted = self.installer._desired_fingerprint()
        os.makedirs(os.path.join(self.dir, "mods"))
        with open(os.path.join(self.dir, "mods", "a.jar"), "w") as f:
            f.write("x")
        self.installer._write_manifest(["mods/a.jar"], wanted)
        self.assertTrue(self.installer.is_installed())

    def test_a_deleted_file_makes_the_install_incomplete_again(self):
        """State is an optimisation; the filesystem is the truth."""
        wanted = self.installer._desired_fingerprint()
        self.installer._write_manifest(["mods/gone.jar"], wanted)
        self.assertFalse(self.installer.is_installed())

    def test_a_different_pack_version_invalidates_the_manifest(self):
        os.makedirs(os.path.join(self.dir, "mods"))
        with open(os.path.join(self.dir, "mods", "a.jar"), "w") as f:
            f.write("x")
        self.installer._write_manifest(["mods/a.jar"], "a-stale-fingerprint")
        self.assertFalse(self.installer.is_installed())


if __name__ == "__main__":
    unittest.main()
