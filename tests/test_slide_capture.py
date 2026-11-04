import threading
import time

import numpy as np

from audiorecorder.slides.capture import MAX_GRAB_FAILURES, SlideCapture
from audiorecorder.slides.selector import SlideTuning
from audiorecorder.slides.store import SlideFolder

WINDOW = 0x1234


def slide():
    image = np.full((270, 480, 3), 230, dtype=np.uint8)
    image[40:80, 60:400] = 30
    image[150:180, 60:300] = 30
    return image


class FakeSource:
    def __init__(self, target=WINDOW, frame=None, error=None):
        self.target = target
        self.frame = slide() if frame is None else frame
        self.error = error
        self.grabs = 0
        self.grabbed = []
        self.closed = False

    def find_target(self):
        return self.target

    def grab(self, handle):
        self.grabs += 1
        self.grabbed.append(handle)
        if self.error is not None:
            raise self.error
        return self.frame

    def close(self):
        self.closed = True


class Statuses:
    def __init__(self):
        self.items = []
        self.changed = threading.Condition()

    def __call__(self, state, count, reason):
        with self.changed:
            self.items.append((state, count, reason))
            self.changed.notify_all()

    def wait_for(self, predicate, timeout=5.0):
        with self.changed:
            assert self.changed.wait_for(lambda: any(predicate(i) for i in self.items), timeout)


def run(tmp_path, source):
    statuses = Statuses()
    capture = SlideCapture(source, SlideFolder(tmp_path, "s"), time.time(), statuses,
                           SlideTuning(sample_seconds=0.01))
    capture.start()
    return capture, statuses


def test_no_full_screen_window_waits_and_writes_nothing(tmp_path):
    capture, statuses = run(tmp_path, FakeSource(target=None))
    statuses.wait_for(lambda s: s[0] == "waiting")
    time.sleep(0.1)
    capture.stop()
    assert list(tmp_path.iterdir()) == []
    assert {s[0] for s in statuses.items} == {"waiting"}


def test_a_steady_slide_is_saved_once(tmp_path):
    source = FakeSource()
    capture, statuses = run(tmp_path, source)
    statuses.wait_for(lambda s: s == ("capturing", 1, ""))
    while source.grabs < 6:
        time.sleep(0.01)
    capture.stop()
    files = [p.name for p in (tmp_path / "_rec_slides_s").iterdir()]
    assert files == ["0001 00-00-00.jpg"]
    assert source.closed
    assert set(source.grabbed) == {WINDOW}


def test_repeated_grab_errors_stop_with_the_reason(tmp_path):
    capture, statuses = run(tmp_path, FakeSource(error=OSError("BitBlt failed")))
    statuses.wait_for(lambda s: s[0] == "stopped")
    capture.thread.join(2)
    assert not capture.thread.is_alive()
    assert ("stopped", 0, "Screen capture failed: BitBlt failed") in statuses.items
    assert MAX_GRAB_FAILURES == 5


def test_a_write_error_stops_with_the_reason(tmp_path):
    (tmp_path / "_rec_slides_s").write_text("a file where the folder should be")
    capture, statuses = run(tmp_path, FakeSource())
    statuses.wait_for(lambda s: s[0] == "stopped")
    reason = next(s[2] for s in statuses.items if s[0] == "stopped")
    assert reason.startswith("Cannot save snapshot")


def test_stop_returns_promptly_and_nothing_is_written_after_it(tmp_path):
    capture, statuses = run(tmp_path, FakeSource(target=None))
    started = time.monotonic()
    capture.stop()
    assert time.monotonic() - started < 1.0
    assert not capture.thread.is_alive()
    assert not (tmp_path / "_rec_slides_s").exists()


def test_new_tuning_applies_while_the_capture_runs(tmp_path):
    source = FakeSource()
    capture, statuses = run(tmp_path, source)
    statuses.wait_for(lambda s: s[0] == "capturing")
    capture.set_tuning(SlideTuning(sample_seconds=30.0))
    time.sleep(0.1)
    calls = source.grabs
    time.sleep(0.2)
    assert source.grabs == calls
    started = time.monotonic()
    capture.stop()
    assert time.monotonic() - started < 1.0
