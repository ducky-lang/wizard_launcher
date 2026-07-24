"""Tests for the download host allow-list.

This guard sits in front of every map / resource-pack download, so both
failure directions are expensive: too strict and players cannot install the
game at all, too loose and a redirect can pull a payload from anywhere.

The strict case is not hypothetical. The allow-list used to name five exact
CDN hostnames; Hugging Face moved storage to `us.aws.cdn.hf.co` and every
download began failing with a message that read, to the player, like an
accusation that their network was compromised.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core.constants import is_allowed_download_host  # noqa: E402
from launcher_core.exceptions import LauncherError  # noqa: E402
from launcher_core.resource_downloader import _check_host  # noqa: E402


class AllowedHostTests(unittest.TestCase):
    def test_official_hosts_are_allowed(self):
        for host in (
            "huggingface.co",
            "hf.co",
            "cdn-lfs.huggingface.co",
            "cdn-lfs-us-1.huggingface.co",
            "cas-bridge.xethub.hf.co",
            "transfer.xethub.hf.co",
            # The host that broke the old exact-match list.
            "us.aws.cdn.hf.co",
        ):
            self.assertTrue(is_allowed_download_host(host), host)

    def test_lookalike_domains_are_refused(self):
        """The leading dot in the suffix comparison is what these test."""
        for host in (
            "evil-huggingface.co",
            "nothuggingface.co",
            "notthf.co",
            "huggingface.co.attacker.net",
            "hf.co.attacker.net",
            "attacker.net",
            "",
        ):
            self.assertFalse(is_allowed_download_host(host), host)

    def test_case_and_trailing_dot_are_normalised(self):
        self.assertTrue(is_allowed_download_host("HF.CO"))
        self.assertTrue(is_allowed_download_host("US.AWS.CDN.HF.CO"))
        self.assertTrue(is_allowed_download_host("us.aws.cdn.hf.co."))

    def test_none_is_refused_rather_than_crashing(self):
        self.assertFalse(is_allowed_download_host(None))


class CheckHostTests(unittest.TestCase):
    def test_https_official_url_passes(self):
        _check_host("https://us.aws.cdn.hf.co/repos/xyz/map.zip")

    def test_plain_http_is_refused_even_on_an_allowed_host(self):
        with self.assertRaises(LauncherError):
            _check_host("http://huggingface.co/map.zip")

    def test_foreign_host_is_refused(self):
        with self.assertRaises(LauncherError) as ctx:
            _check_host("https://cdn.attacker.net/map.zip")
        self.assertIn("attacker.net", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
