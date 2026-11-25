"""One-line progress bars and timers for the builder (plain ASCII, so they work in any terminal, Windows cmd included)."""
import sys
import threading
import time

WIDTH = 30


def _write(text):
    sys.stdout.write("\r" + text.ljust(79)[:79])
    sys.stdout.flush()


def _clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class Bar:
    """Bar(total, "Decompiling").update(done); .finish() ends the line."""

    def __init__(self, total, label, unit=""):
        self.total, self.label, self.unit = max(1, total), label, unit
        self.start = time.monotonic()
        self.last = -1.0
        self.update(0)

    def update(self, done, force=False):
        now = time.monotonic()
        if not force and now - self.last < 0.2 and done < self.total:
            return
        self.last = now
        fraction = min(1.0, done / self.total)
        filled = int(WIDTH * fraction)
        if self.unit == "MB":
            count = f"{done / 1e6:.0f}/{self.total / 1e6:.0f} MB"
        else:
            count = f"{done}/{self.total}{(' ' + self.unit) if self.unit else ''}"
        _write(f"  [{'#' * filled}{'-' * (WIDTH - filled)}] {fraction * 100:3.0f}%  {count}  {self.label}")

    def finish(self, message=None):
        self.update(self.total, force=True)
        elapsed = _clock(time.monotonic() - self.start)
        sys.stdout.write(f"\n  {message or 'done'} in {elapsed}\n" if message is not False else "\n")
        sys.stdout.flush()


class Timer:
    """with Timer("Linking", status=lambda: "2/3 parts") : shows the elapsed time (and a status) every second."""

    def __init__(self, label, status=None):
        self.label, self.status = label, status
        self.stop = threading.Event()

    def _run(self):
        spinner = "|/-\\"
        tick = 0
        while not self.stop.wait(1.0):
            tick += 1
            extra = f"  {self.status()}" if self.status else ""
            _write(f"  {spinner[tick % 4]} {self.label}  {_clock(time.monotonic() - self.start)}{extra}")

    def __enter__(self):
        self.start = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        _write(f"  {self.label}: done in {_clock(time.monotonic() - self.start)}")
        sys.stdout.write("\n")
        sys.stdout.flush()
        return False
