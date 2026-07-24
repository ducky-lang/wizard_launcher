"""Tests for how the Azure application id is resolved.

The id decides which Microsoft app registration the consent screen names, so
the interesting cases are all about what the launcher *refuses*: an id from
the environment in a packaged build, a manifest served from an unexpected
host, a manifest that is not signed, a value that is not a GUID.

Note what is deliberately not tested, because it is not true: none of this
keeps the id confidential. A public-client application id is public by
design - see the module docstring of :mod:`launcher_core.provisioning`.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core import provisioning, secure_store  # noqa: E402

GOOD_ID = "11111111-2222-3333-4444-555555555555"
OTHER_ID = "99999999-8888-7777-6666-555555555555"


class HostAllowListTests(unittest.TestCase):
    def test_https_on_a_listed_domain_is_allowed(self):
        self.assertTrue(provisioning._host_allowed(
            "https://raw.githubusercontent.com/foxy/x/main/client-id.json"))
        self.assertTrue(provisioning._host_allowed(
            "https://huggingface.co/datasets/x/resolve/main/client-id.json"))

    def test_plain_http_is_refused(self):
        self.assertFalse(provisioning._host_allowed(
            "http://raw.githubusercontent.com/x.json"))

    def test_lookalike_domains_are_refused(self):
        """The leading dot in the suffix comparison is what these test."""
        for url in (
            "https://evil-githubusercontent.com/x.json",
            "https://githubusercontent.com.evil.tld/x.json",
            "https://nothuggingface.co/x.json",
        ):
            with self.subTest(url=url):
                self.assertFalse(provisioning._host_allowed(url))

    def test_garbage_is_refused_rather_than_crashing(self):
        for url in ("", None, "not a url", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.assertFalse(provisioning._host_allowed(url))


class ManifestValidationTests(unittest.TestCase):
    """fetch_manifest() with the network stubbed out."""

    def setUp(self):
        self.messages = []
        self._real_config = provisioning._config
        self._real_fetch = provisioning._fetch

    def tearDown(self):
        provisioning._config = self._real_config
        provisioning._fetch = self._real_fetch

    def configure(self, **overrides):
        config = {
            "manifest_url": "https://raw.githubusercontent.com/x/y/main/client-id.json",
            "signature_url": "",
            "public_key": "",
            "allowed_domains": ["githubusercontent.com"],
            "refresh_hours": 24,
        }
        config.update(overrides)
        provisioning._config = lambda: config

    def serve(self, body, signature=None):
        def fake_fetch(url, log):
            if url.endswith(".sig"):
                return signature
            return body

        provisioning._fetch = fake_fetch

    def test_a_valid_unsigned_manifest_is_accepted_with_a_warning(self):
        self.configure()
        self.serve(json.dumps({"client_id": GOOD_ID}).encode())
        manifest = provisioning.fetch_manifest(self.messages.append)
        self.assertEqual(manifest["client_id"], GOOD_ID)
        self.assertTrue(any("no public key" in m for m in self.messages))

    def test_a_non_guid_is_refused(self):
        self.configure()
        self.serve(json.dumps({"client_id": "; DROP TABLE users"}).encode())
        self.assertIsNone(provisioning.fetch_manifest(self.messages.append))

    def test_malformed_json_is_refused(self):
        self.configure()
        self.serve(b"{ not json")
        self.assertIsNone(provisioning.fetch_manifest(self.messages.append))

    def test_revoked_entries_are_normalised_and_filtered(self):
        self.configure()
        self.serve(json.dumps({
            "client_id": GOOD_ID,
            "revoked": [OTHER_ID.upper(), "not-a-guid", 42],
        }).encode())
        manifest = provisioning.fetch_manifest(self.messages.append)
        self.assertEqual(manifest["revoked"], {OTHER_ID})

    def test_a_configured_key_with_no_signature_fails_closed(self):
        self.configure(public_key="AAAA")
        self.serve(json.dumps({"client_id": GOOD_ID}).encode(), signature=None)
        self.assertIsNone(provisioning.fetch_manifest(self.messages.append))

    def test_a_configured_key_with_a_bad_signature_fails_closed(self):
        self.configure(public_key="AAAA")
        self.serve(json.dumps({"client_id": GOOD_ID}).encode(), signature=b"bogus")
        self.assertIsNone(provisioning.fetch_manifest(self.messages.append))

    def test_an_unreachable_server_is_not_an_error(self):
        self.configure()
        self.serve(None)
        self.assertIsNone(provisioning.fetch_manifest(self.messages.append))

    def test_the_feature_is_off_when_no_url_is_configured(self):
        self.configure(manifest_url="")
        self.assertFalse(provisioning.is_enabled())
        self.assertIsNone(provisioning.fetch_manifest(self.messages.append))

    def test_shipped_catalog_has_provisioning_switched_off(self):
        """The repository must not ship pointing at somebody's manifest."""
        self.assertFalse(provisioning.is_enabled())


class ClientIdResolutionTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.store = secure_store.ClientIdStore(self.messages.append)
        self.store.set("")
        self.store._cached = None
        self._real_is_frozen = secure_store.is_frozen
        self._saved_env = os.environ.get("MC_LAUNCHER_CLIENT_ID")

    def tearDown(self):
        secure_store.is_frozen = self._real_is_frozen
        if self._saved_env is None:
            os.environ.pop("MC_LAUNCHER_CLIENT_ID", None)
        else:
            os.environ["MC_LAUNCHER_CLIENT_ID"] = self._saved_env

    def test_a_source_checkout_honours_the_environment(self):
        secure_store.is_frozen = lambda: False
        os.environ["MC_LAUNCHER_CLIENT_ID"] = GOOD_ID
        self.assertEqual(secure_store.ClientIdStore(self.messages.append).get(), GOOD_ID)

    def test_a_packaged_build_ignores_the_environment(self):
        """Anything able to set an environment variable in the player's
        session could otherwise redirect sign-in to its own Azure app while
        the window still said Wizard Launcher."""
        secure_store.is_frozen = lambda: True
        os.environ["MC_LAUNCHER_CLIENT_ID"] = GOOD_ID
        store = secure_store.ClientIdStore(self.messages.append)
        self.assertEqual(store.get(), "")
        self.assertTrue(any("Ignoring MC_LAUNCHER_CLIENT_ID" in m for m in self.messages))

    def test_a_malformed_id_is_never_returned(self):
        secure_store.is_frozen = lambda: False
        os.environ["MC_LAUNCHER_CLIENT_ID"] = "totally-not-a-guid"
        self.assertEqual(secure_store.ClientIdStore(self.messages.append).get(), "")

    def test_a_stored_id_survives_a_restart(self):
        secure_store.is_frozen = lambda: True
        os.environ.pop("MC_LAUNCHER_CLIENT_ID", None)
        self.store.set(GOOD_ID)
        self.assertEqual(secure_store.ClientIdStore(self.messages.append).get(), GOOD_ID)

    def test_the_manifest_never_overrides_a_developer_override(self):
        secure_store.is_frozen = lambda: False
        os.environ["MC_LAUNCHER_CLIENT_ID"] = GOOD_ID
        store = secure_store.ClientIdStore(self.messages.append)
        self.assertFalse(store.refresh_from_manifest(force=True))

    def test_a_rotated_id_replaces_the_stored_one(self):
        secure_store.is_frozen = lambda: True
        os.environ.pop("MC_LAUNCHER_CLIENT_ID", None)
        store = secure_store.ClientIdStore(self.messages.append)
        store.set(OTHER_ID)

        real_enabled, real_fetch = provisioning.is_enabled, provisioning.fetch_manifest
        provisioning.is_enabled = lambda: True
        provisioning.fetch_manifest = lambda log=None: {
            "client_id": GOOD_ID, "revoked": {OTHER_ID}, "message": "",
            "fetched_at": 1,
        }
        try:
            self.assertTrue(store.refresh_from_manifest(force=True))
        finally:
            provisioning.is_enabled, provisioning.fetch_manifest = real_enabled, real_fetch

        self.assertEqual(secure_store.ClientIdStore(self.messages.append).get(), GOOD_ID)
        self.assertTrue(any("retired" in m for m in self.messages))

    def test_an_unchanged_manifest_reports_no_change(self):
        secure_store.is_frozen = lambda: True
        os.environ.pop("MC_LAUNCHER_CLIENT_ID", None)
        store = secure_store.ClientIdStore(self.messages.append)
        store.set(GOOD_ID)

        real_enabled, real_fetch = provisioning.is_enabled, provisioning.fetch_manifest
        provisioning.is_enabled = lambda: True
        provisioning.fetch_manifest = lambda log=None: {
            "client_id": GOOD_ID, "revoked": set(), "message": "", "fetched_at": 1,
        }
        try:
            self.assertFalse(store.refresh_from_manifest(force=True))
        finally:
            provisioning.is_enabled, provisioning.fetch_manifest = real_enabled, real_fetch


if __name__ == "__main__":
    unittest.main()
