"""Sample the meeting window during a recording and keep the slides, on a worker thread."""

import contextlib
import threading
import time

from audiorecorder.slides.selector import SlideSelector, SlideTuning, cells

MAX_GRAB_FAILURES = 5


class SlideCapture:
    """Runs until stop(); reports ("waiting" | "capturing" | "stopped", count, reason).

    The status callback runs on the worker thread. Every error ends in a status, never in
    an exception, so the audio recording beside it is never affected.
    """

    def __init__(self, source, folder, started_at, on_status, tuning=None):
        self.source = source
        self.folder = folder
        self.started_at = started_at
        self.on_status = on_status
        self.selector = SlideSelector(tuning or SlideTuning())
        self.stop_event = threading.Event()
        self.last_status = None
        self.thread = threading.Thread(target=self._run, name="slides", daemon=True)

    def start(self):
        self.thread.start()

    def set_tuning(self, tuning):
        """Use new values from the next sample on, while the capture runs."""
        self.selector.tuning = tuning

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=self.selector.tuning.sample_seconds * 2 + 5)

    def _report(self, state, reason=""):
        status = (state, self.folder.count, reason)
        if status != self.last_status:
            self.last_status = status
            self.on_status(*status)

    def _run(self):
        try:
            self._report("waiting")
            self._loop()
        except Exception as error:
            self._report("stopped", f"Slide capture failed: {error}")
        finally:
            with contextlib.suppress(Exception):
                self.source.close()

    def _loop(self):
        failures = 0
        while not self.stop_event.wait(self.selector.tuning.sample_seconds):
            target = self.source.find_target()
            if target is None:
                self._report("waiting")
                continue
            try:
                frame = self.source.grab(target)
            except Exception as error:
                failures += 1
                if failures >= MAX_GRAB_FAILURES:
                    self._report("stopped", f"Screen capture failed: {error}")
                    return
                continue
            failures = 0
            kept_at = self.selector.offer(cells(frame), time.time())
            if kept_at is not None and not self.stop_event.is_set():
                try:
                    self.folder.add(frame, kept_at - self.started_at)
                except OSError as error:
                    self._report("stopped", f"Cannot save snapshot: {error}")
                    return
            self._report("capturing")
