"""Tests for download progress arithmetic and the retry policy.

These are the parts of the download path that decide what the player reads
while they wait, and how many times the launcher tries before giving up -
both of which are easy to get subtly wrong and impossible to notice by
looking at a fast connection.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core.transfer import (  # noqa: E402
    ProgressReporter, format_bytes, format_duration, format_speed, retry_delays,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FormattingTests(unittest.TestCase):
    def test_bytes_scale_to_a_readable_unit(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(2048), "2 KB")
        self.assertEqual(format_bytes(5 * 1024 ** 2), "5 MB")
        self.assertEqual(format_bytes(3 * 1024 ** 3), "3.00 GB")

    def test_unknown_size_does_not_crash(self):
        self.assertEqual(format_bytes(None), "?")

    def test_speed_is_blank_when_unknown(self):
        self.assertEqual(format_speed(0), "")
        self.assertEqual(format_speed(None), "")

    def test_duration_reads_as_time_remaining(self):
        self.assertEqual(format_duration(30), "30s left")
        self.assertEqual(format_duration(125), "2m 05s left")
        self.assertEqual(format_duration(3700), "1h 01m left")

    def test_implausible_estimates_are_suppressed(self):
        """A stalled connection produces a "9h left" figure that is worse
        than showing nothing at all."""
        self.assertEqual(format_duration(60 * 60 * 24), "")
        self.assertEqual(format_duration(None), "")
        self.assertEqual(format_duration(-5), "")


class ProgressReporterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.updates = []

    def make(self, total=1000, already=0):
        return ProgressReporter(
            "Castle", total, already,
            progress_fn=lambda fraction, message=None: self.updates.append((fraction, message)),
            now=self.clock,
        )

    def test_fraction_tracks_bytes(self):
        reporter = self.make(total=1000)
        self.clock.advance(1)
        reporter.advance(250)
        self.assertAlmostEqual(reporter.fraction(), 0.25)

    def test_unknown_total_reports_indeterminate(self):
        reporter = self.make(total=0)
        self.clock.advance(1)
        reporter.advance(500)
        self.assertIsNone(reporter.fraction())
        self.assertIn("500 B", reporter.message())

    def test_speed_and_eta_come_from_observed_throughput(self):
        reporter = self.make(total=1000)
        for _ in range(4):
            self.clock.advance(1)
            reporter.advance(100)
        # 400 bytes over 4 seconds, 600 to go.
        self.assertAlmostEqual(reporter.speed(), 100.0, places=3)
        self.assertAlmostEqual(reporter.eta_seconds(), 6.0, places=3)

    def test_resumed_bytes_do_not_inflate_the_speed(self):
        """A resume starts 900 bytes in; the first second must not report
        900 B/s just because the file was already that long."""
        reporter = self.make(total=1000, already=900)
        self.clock.advance(1)
        reporter.advance(50)
        self.assertAlmostEqual(reporter.speed(), 50.0, places=3)
        self.assertEqual(reporter.resumed_bytes(), 900)
        self.assertAlmostEqual(reporter.fraction(), 0.95)

    def test_updates_are_throttled(self):
        reporter = self.make(total=100000)
        for _ in range(50):
            self.clock.advance(0.001)
            reporter.advance(10)
        self.assertLessEqual(len(self.updates), 2)

    def test_forced_emit_always_reports(self):
        reporter = self.make(total=1000)
        reporter.emit("Unpacking...")
        self.assertEqual(self.updates[-1][1], "Unpacking...")

    def test_message_names_the_resource_and_the_sizes(self):
        reporter = self.make(total=10 * 1024 ** 2)
        self.clock.advance(2)
        reporter.advance(5 * 1024 ** 2)
        message = reporter.message()
        self.assertIn("Castle", message)
        self.assertIn("5 MB of 10 MB", message)
        self.assertIn("MB/s", message)


class RetryPolicyTests(unittest.TestCase):
    def test_delays_double_and_then_stop_growing(self):
        self.assertEqual(retry_delays(5, 2, 16), [2, 4, 8, 16, 16])

    def test_no_retries_means_no_delays(self):
        self.assertEqual(retry_delays(0, 2, 16), [])

    def test_ceiling_is_respected_from_the_first_delay(self):
        self.assertEqual(retry_delays(3, 30, 10), [10, 10, 10])


if __name__ == "__main__":
    unittest.main()
