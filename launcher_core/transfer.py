"""Progress accounting for long downloads, and the retry policy around them.

Split out of :mod:`resource_downloader` because it is pure arithmetic with
no network in it, which makes it the only part of the download path that can
be tested without a socket.

Two things live here:

``ProgressReporter``
    Turns a stream of "n more bytes arrived" into the line the player reads:
    how much of how much, how fast, and how long is left. The speed is a
    sliding window rather than an average since the start - an average makes
    the estimate wrong for minutes after the connection changes speed, which
    is precisely when somebody is staring at it wondering whether to give up.

``retry_delays``
    Exponential backoff with a ceiling. A dropped connection on hotel Wi-Fi
    should cost the player a few seconds, not a trip back to the Play button.
"""

import time

# Speed is averaged over this many seconds of history. Long enough that a
# single slow chunk does not make the estimate jump, short enough that it
# tracks a real change in throughput.
SPEED_WINDOW_SECONDS = 8.0

# The UI is on another thread and repainting costs a frame; four updates a
# second is smooth to the eye and cheap.
MIN_UPDATE_INTERVAL = 0.25


def format_bytes(count):
    if count is None:
        return "?"
    if count >= 1024 ** 3:
        return f"{count / 1024 ** 3:.2f} GB"
    if count >= 1024 ** 2:
        return f"{count / 1024 ** 2:.0f} MB"
    if count >= 1024:
        return f"{count / 1024:.0f} KB"
    return f"{count} B"


def format_speed(bytes_per_second):
    if not bytes_per_second:
        return ""
    if bytes_per_second >= 1024 ** 2:
        return f"{bytes_per_second / 1024 ** 2:.1f} MB/s"
    return f"{bytes_per_second / 1024:.0f} KB/s"


def format_duration(seconds):
    """Human time remaining. Rounded generously and capped, because a "4h 12m"
    estimate from a momentarily stalled connection is worse than no estimate."""
    if seconds is None or seconds < 0 or seconds > 6 * 3600:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{max(seconds, 1)}s left"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s left"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m left"


class ProgressReporter:
    """Accumulates transferred bytes and emits a throttled progress line.

    ``progress_fn(fraction, message)`` is the callback the managers already
    use; ``fraction`` is None when the total size is unknown, which the UI
    renders as an indeterminate bar.
    """

    def __init__(self, name, total_bytes=0, already_have=0, progress_fn=None,
                 now=time.monotonic):
        self.name = name
        self.total = int(total_bytes or 0)
        self.done = int(already_have or 0)
        # Bytes present before this attempt started (a resumed .part). They
        # count towards "how far along am I" but not towards "how fast is
        # this going" - otherwise a resume reports an infinite first second.
        self._baseline = self.done
        self.progress_fn = progress_fn or (lambda fraction, message=None: None)
        self._now = now
        self._started = now()
        self._last_emit = 0.0
        self._samples = [(self._started, self.done)]

    # -- accounting ----------------------------------------------------
    def advance(self, byte_count):
        self.done += byte_count
        self._record()
        self._maybe_emit()

    def _record(self):
        now = self._now()
        self._samples.append((now, self.done))
        cutoff = now - SPEED_WINDOW_SECONDS
        # Keep one sample older than the window so a slow trickle still has
        # two points to measure between.
        while len(self._samples) > 2 and self._samples[1][0] < cutoff:
            self._samples.pop(0)

    def speed(self):
        """Bytes per second over the sliding window, or 0 if not yet known."""
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        elapsed = t1 - t0
        if elapsed <= 0:
            return 0.0
        return max(0.0, (b1 - b0) / elapsed)

    def eta_seconds(self):
        if not self.total or self.done >= self.total:
            return None
        speed = self.speed()
        if speed <= 0:
            return None
        return (self.total - self.done) / speed

    def fraction(self):
        if not self.total:
            return None
        return min(1.0, self.done / self.total)

    # -- output --------------------------------------------------------
    def message(self):
        parts = [f"Downloading {self.name}"]
        if self.total:
            parts.append(f"{format_bytes(self.done)} of {format_bytes(self.total)}")
        else:
            parts.append(format_bytes(self.done))

        speed = format_speed(self.speed())
        if speed:
            parts.append(speed)
        eta = format_duration(self.eta_seconds())
        if eta:
            parts.append(eta)
        return "  ·  ".join(parts)

    def _maybe_emit(self, force=False):
        now = self._now()
        if not force and now - self._last_emit < MIN_UPDATE_INTERVAL:
            return
        self._last_emit = now
        self.progress_fn(self.fraction(), self.message())

    def emit(self, message=None):
        """Force an update, optionally with a message of your own."""
        self._last_emit = self._now()
        self.progress_fn(self.fraction(), message or self.message())

    def resumed_bytes(self):
        return self._baseline


def retry_delays(max_retries, base_seconds, max_seconds):
    """``[2, 4, 8, 16...]`` capped at ``max_seconds``.

    Returned as a list rather than computed inline so the caller can say
    "attempt 2 of 5" without duplicating the arithmetic, and so the policy is
    visible in one place when somebody wonders why a download took 30 extra
    seconds to give up.
    """
    delays = []
    delay = max(0.1, float(base_seconds))
    for _ in range(max(0, int(max_retries))):
        delays.append(min(delay, float(max_seconds)))
        delay *= 2
    return delays
