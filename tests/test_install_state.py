"""Tests for the "already done" record.

This cache decides whether a launch redoes several hundred megabytes of
copying, so both failure directions matter: too eager and a player waits
through work that was already finished, too trusting and a deleted folder
never comes back.

The rule the rest of the code relies on is that a stale entry can only ever
cost time, never correctness - every caller pairs :meth:`matches` with an
existence check on what the step produced.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core.install_state import InstallState, fingerprint  # noqa: E402


class FingerprintTests(unittest.TestCase):
    def test_same_inputs_give_the_same_value(self):
        self.assertEqual(fingerprint("a", 1, None), fingerprint("a", 1, None))

    def test_any_change_gives_a_different_value(self):
        base = fingerprint("pack", "5.4.1")
        self.assertNotEqual(base, fingerprint("pack", "5.4.2"))
        self.assertNotEqual(base, fingerprint("other", "5.4.1"))

    def test_field_boundaries_cannot_be_smuggled(self):
        """('a/b', 'c') and ('a', 'b/c') are different work, not the same."""
        self.assertNotEqual(fingerprint("a/b", "c"), fingerprint("a", "b/c"))


class InstallStateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wizard-state-")
        self.path = os.path.join(self.dir, "install-state.json")

    def state(self):
        return InstallState(path=self.path)

    def test_nothing_is_done_before_anything_is_marked(self):
        self.assertFalse(self.state().matches("modpack", "abc"))

    def test_a_marked_step_is_remembered_across_instances(self):
        self.state().mark("modpack", "abc")
        self.assertTrue(self.state().matches("modpack", "abc"))

    def test_a_changed_fingerprint_no_longer_matches(self):
        state = self.state()
        state.mark("modpack", "abc")
        self.assertFalse(state.matches("modpack", "def"))

    def test_an_empty_fingerprint_never_matches(self):
        """Otherwise a step that failed to compute one would look complete."""
        state = self.state()
        state.mark("modpack", "")
        self.assertFalse(state.matches("modpack", ""))

    def test_get_returns_the_recorded_value(self):
        state = self.state()
        state.mark("fabric_version", "fabric-loader-0.15.0-1.20.1")
        self.assertEqual(self.state().get("fabric_version"),
                         "fabric-loader-0.15.0-1.20.1")
        self.assertEqual(state.get("missing", "fallback"), "fallback")

    def test_clear_forgets_one_key(self):
        state = self.state()
        state.mark("a", "1")
        state.mark("b", "2")
        state.clear("a")
        self.assertFalse(state.matches("a", "1"))
        self.assertTrue(state.matches("b", "2"))

    def test_clear_with_no_arguments_forgets_everything(self):
        state = self.state()
        state.mark("a", "1")
        state.mark("b", "2")
        state.clear()
        self.assertFalse(self.state().matches("a", "1"))
        self.assertFalse(self.state().matches("b", "2"))

    def test_a_corrupt_file_is_discarded_rather_than_trusted(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{this is not json")
        messages = []
        state = InstallState(log=messages.append, path=self.path)
        self.assertFalse(state.matches("modpack", "abc"))
        self.assertTrue(any("re-run" in m for m in messages), messages)

    def test_non_string_entries_are_ignored(self):
        """A hand-edited file must not put a dict where a fingerprint goes."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"good": "abc", "bad": {"nested": True}, "worse": 7}, f)
        state = self.state()
        self.assertTrue(state.matches("good", "abc"))
        self.assertFalse(state.matches("bad", "abc"))
        self.assertFalse(state.matches("worse", "7"))


if __name__ == "__main__":
    unittest.main()
