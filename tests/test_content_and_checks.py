"""Tests for the data layer and the system check.

The data layer is now load-bearing: the FAQ, the onboarding steps, the map
URL, the port numbers and the palette all come out of ``launcher_core/data``.
Two things therefore have to be true and stay true:

* the shipped files parse and have the shape the accessors promise, and
* a missing or mangled file degrades to a working launcher rather than a
  stack trace, because the one time this breaks will be in a packaged build
  on somebody else's computer.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core import content, system_check  # noqa: E402


class ShippedDataTests(unittest.TestCase):
    """The files that actually ship must be valid and complete."""

    DATA_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "launcher_core", "data",
    )

    def test_every_data_file_is_valid_json(self):
        names = [f for f in os.listdir(self.DATA_DIR) if f.endswith(".json")]
        self.assertTrue(names, "no data files found")
        for name in names:
            with self.subTest(file=name):
                with open(os.path.join(self.DATA_DIR, name), encoding="utf-8") as f:
                    self.assertIsInstance(json.load(f), dict)

    def test_content_accessors_return_populated_lists(self):
        self.assertGreaterEqual(len(content.faq()), 5)
        self.assertGreaterEqual(len(content.troubleshooting()), 5)
        self.assertGreaterEqual(len(content.onboarding_steps()), 3)
        self.assertGreaterEqual(len(content.getting_started()), 3)
        self.assertGreaterEqual(len(content.idle_messages()), 3)
        self.assertEqual(len(content.account_modes()), 2)

    def test_story_has_prose_and_facts(self):
        story = content.story()
        self.assertTrue(story["title"])
        self.assertGreaterEqual(len(story["paragraphs"]), 2)
        self.assertTrue(all(f["label"] and f["value"] for f in story["facts"]))

    def test_troubleshooting_entries_all_offer_a_fix(self):
        for entry in content.troubleshooting():
            with self.subTest(entry=entry["id"]):
                self.assertTrue(entry["steps"], "an entry with no steps is not a fix")

    def test_launch_steps_are_ordered_and_weighted(self):
        steps = content.launch_steps()
        keys = [key for key, _label, _weight in steps]
        self.assertEqual(keys[0], "java")
        self.assertEqual(keys[-1], "client")
        self.assertTrue(all(weight > 0 for _k, _l, weight in steps))

    def test_changelog_covers_the_running_version(self):
        from launcher_core.version import VERSION
        self.assertIsNotNone(
            content.release_for(VERSION),
            f"data/changelog.json has no entry for {VERSION}; the About dialog "
            "would show an empty 'What's new'.",
        )

    def test_release_notes_since_only_returns_newer_releases(self):
        versions = [r["version"] for r in content.release_notes_since("1.2.0")]
        self.assertNotIn("1.2.0", versions)
        self.assertTrue(versions)
        self.assertEqual(content.release_notes_since("99.0.0"), [])


class FallbackTests(unittest.TestCase):
    """A missing or broken data file must not stop anybody playing."""

    def setUp(self):
        content.reload()

    def tearDown(self):
        content.reload()

    def test_unknown_file_yields_empty_data_not_an_error(self):
        self.assertEqual(content.load("no-such-file"), {})

    def test_accessors_survive_a_wiped_cache_entry(self):
        content._CACHE["content"] = {}
        self.assertEqual(content.faq(), [])
        self.assertEqual(content.troubleshooting(), [])
        # These two have hard fallbacks because the UI cannot render without
        # them: the status line needs a message and the bar needs steps.
        self.assertTrue(content.idle_messages())
        self.assertEqual(len(content.launch_steps()), 7)

    def test_malformed_entries_are_dropped_not_rendered(self):
        content._CACHE["content"] = {
            "faq": [
                {"question": "Real", "answer": "Yes"},
                {"question": "Missing an answer"},
                "not a dict",
                {},
            ],
        }
        self.assertEqual(len(content.faq()), 1)

    def test_a_corrupt_file_on_disk_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "content.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{ this is not json")
            original = content._candidate_dirs
            content._candidate_dirs = lambda: [tmp]
            try:
                content.reload()
                self.assertTrue(content.idle_messages())
            finally:
                content._candidate_dirs = original
                content.reload()


class ConstantsFromCatalogTests(unittest.TestCase):
    def test_catalog_drives_the_constants(self):
        from launcher_core import constants
        self.assertEqual(constants.MC_VERSION, "1.20.1")
        self.assertEqual(constants.SERVER_VERSION_DIR, "1.16.5")
        self.assertEqual(constants.SERVER_ENTRY_IP,
                         f"127.0.0.1:{constants.PROXY_PORT}")
        self.assertTrue(constants.MAP_DOWNLOAD_URL.startswith("https://"))

    def test_disk_requirement_is_more_than_the_download(self):
        from launcher_core import constants
        self.assertGreater(constants.REQUIRED_DISK_MB, constants.DOWNLOAD_MB)

    def test_only_https_links_survive_the_catalog(self):
        from launcher_core import constants
        for url in (constants.ISSUES_URL, constants.RELEASES_URL,
                    constants.JAVA_DOWNLOAD_URL):
            if url:
                self.assertTrue(url.startswith("https://"), url)


class SystemCheckTests(unittest.TestCase):
    def test_disk_check_reports_free_space(self):
        check = system_check.check_disk()
        self.assertEqual(check.key, "disk")
        self.assertIn(check.status, (system_check.OK, system_check.WARN, system_check.FAIL))
        self.assertTrue(check.detail)

    def test_disk_check_fails_loudly_on_a_full_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            original = shutil.disk_usage
            # A tiny free-space figure must produce a blocking failure with a
            # number the player can act on.
            shutil.disk_usage = lambda _p: type(
                "Usage", (), {"total": 0, "used": 0, "free": 50 * 1024 * 1024})()
            try:
                check = system_check.check_disk(tmp)
            finally:
                shutil.disk_usage = original
        self.assertEqual(check.status, system_check.FAIL)
        self.assertIn("Free up", check.remedy)

    def test_unmeasurable_disk_warns_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            original = shutil.disk_usage

            def boom(_path):
                raise OSError("no such device")

            shutil.disk_usage = boom
            try:
                check = system_check.check_disk(tmp)
            finally:
                shutil.disk_usage = original
        self.assertEqual(check.status, system_check.WARN)

    def test_memory_check_always_answers(self):
        check = system_check.check_memory()
        self.assertTrue(check.detail)
        self.assertIsInstance(check.value, int)

    def test_java_check_without_a_manager_is_pending(self):
        self.assertEqual(system_check.check_java().status, system_check.PENDING)

    def test_network_check_warns_when_dns_fails(self):
        def boom(_host):
            raise OSError("dns down")

        check = system_check.check_network(probe=boom)
        self.assertEqual(check.status, system_check.WARN)
        self.assertIn("first launch", check.remedy)

    def test_network_check_passes_when_dns_resolves(self):
        check = system_check.check_network(probe=lambda host: [host])
        self.assertEqual(check.status, system_check.OK)

    def test_run_all_produces_a_readable_summary(self):
        checks = system_check.run_all(include_network=False)
        self.assertTrue(checks)
        self.assertTrue(system_check.summary_line(checks))
        text = system_check.as_text(checks)
        self.assertIn("Disk space", text)
        self.assertIn("Memory", text)

    def test_worst_status_wins(self):
        Check = system_check.Check
        checks = [
            Check("a", "A", system_check.OK, "", "", None),
            Check("b", "B", system_check.WARN, "", "", None),
            Check("c", "C", system_check.FAIL, "", "", None),
        ]
        self.assertEqual(system_check.worst_status(checks), system_check.FAIL)
        self.assertEqual(system_check.worst_status(checks[:2]), system_check.WARN)
        self.assertEqual(system_check.worst_status(checks[:1]), system_check.OK)


class DiagnosticsTests(unittest.TestCase):
    def test_issue_url_stays_within_a_usable_length(self):
        from launcher_core import diagnostics
        body = "x" * 50000
        url = diagnostics.issue_url("https://example.com/new", "A title", body,
                                    max_length=2000)
        self.assertLessEqual(len(url), 2000)
        self.assertTrue(url.startswith("https://example.com/new?title="))
        # Truncation must never cut a percent-escape in half.
        self.assertNotRegex(url, r"%[0-9A-Fa-f]?$")

    def test_issue_url_is_empty_without_a_tracker(self):
        from launcher_core import diagnostics
        self.assertEqual(diagnostics.issue_url("", "t", "b"), "")

    def test_issue_body_is_redacted(self):
        from launcher_core import diagnostics
        body = diagnostics.issue_body("finding Java", 'access_token: "abcdef123456"')
        self.assertNotIn("abcdef123456", body)
        self.assertIn("<redacted>", body)


if __name__ == "__main__":
    unittest.main()
