# The window fixtures come from test_rts_toggle; pytest passes them in by parameter name.
# ruff: noqa: F401, F811
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_rts_toggle import FakeCapture, app, window

from audiorecorder.slides.selector import SlideTuning
from audiorecorder.ui import main_window as mw


class FakeSlideCapture:
    instances = []

    def __init__(self, source, folder, started_at, on_status, tuning):
        self.source = source
        self.folder = folder
        self.started_at = started_at
        self.on_status = on_status
        self.tuning = tuning
        self.started = False
        self.stopped = False
        FakeSlideCapture.instances.append(self)

    def start(self):
        self.started = True

    def set_tuning(self, tuning):
        self.tuning = tuning

    def stop(self):
        self.stopped = True


@pytest.fixture
def slides(monkeypatch):
    FakeSlideCapture.instances = []
    source = SimpleNamespace(meeting_title=lambda: "Weekly sync | Microsoft Teams")
    monkeypatch.setattr(mw, "create_slide_source", lambda: (source, ""))
    monkeypatch.setattr(mw, "SlideCapture", FakeSlideCapture)
    return FakeSlideCapture.instances


def snapshot(folder, name="0001 00-00-04.jpg"):
    folder.path.mkdir(exist_ok=True)
    (folder.path / name).write_bytes(b"jpeg")


def test_armed_toggle_starts_capture_with_the_recording(window, slides, tmp_path):
    w = window(rts_translate=False)
    w._btn_slides.setChecked(True)

    w._start_recording()

    assert len(slides) == 1
    capture = slides[0]
    assert capture.started
    assert capture.folder.path == tmp_path / "_rec_slides_fakestamp"
    assert capture.started_at == w._recording_start_time


def test_toggle_during_a_recording_starts_and_stops_capture(window, slides, tmp_path):
    w = window(rts_translate=False)
    w._start_recording()
    assert slides == []

    w._btn_slides.click()
    assert w.config["slide_snapshots"] is True
    assert slides[0].started
    snapshot(slides[0].folder)

    w._btn_slides.click()
    assert w.config["slide_snapshots"] is False
    assert slides[0].stopped
    assert (tmp_path / "_rec_slides_fakestamp" / "0001 00-00-04.jpg").exists()


def test_stop_with_audio_places_the_snapshots_in_the_recording_folder(
        window, slides, tmp_path, monkeypatch):
    w = window(rts_translate=False)
    w._btn_slides.setChecked(True)
    w._start_recording()
    snapshot(slides[0].folder)
    snapshot(slides[0].folder, "0002 00-01-30.jpg")
    monkeypatch.setattr(FakeCapture, "stop", lambda self: (None, str(tmp_path / "mic.wav")))
    worker = SimpleNamespace(result=SimpleNamespace(cleanup_errors=()))
    monkeypatch.setattr(w, "_enqueue_encoder", lambda *args: worker)
    monkeypatch.setattr(w, "_start_next_encoder", lambda: None)

    w._stop_recording()

    assert slides[0].stopped
    (output_path,) = w._encoding_ids
    w._on_encode_finished(output_path)
    folder = Path(output_path).parent
    assert folder.parent == tmp_path
    assert folder.name.endswith(" - Weekly sync")
    assert Path(output_path).name == folder.name + ".m4a"
    assert sorted(p.name for p in folder.iterdir()) == [
        "0001 00-00-04.jpg", "0002 00-01-30.jpg", "slides.md"]
    assert not (tmp_path / "_rec_slides_fakestamp").exists()


def test_stop_without_audio_removes_the_snapshots(window, slides, tmp_path):
    w = window(rts_translate=False)
    w._btn_slides.setChecked(True)
    w._start_recording()
    snapshot(slides[0].folder)

    w._stop_recording()

    assert not (tmp_path / "_rec_slides_fakestamp").exists()


def test_a_stopped_capture_shows_its_reason_and_the_recording_goes_on(window, slides, app):
    w = window(rts_translate=False)
    w._btn_slides.setChecked(True)
    w._start_recording()

    w._slide_status.emit("stopped", 0, "Screen capture failed: denied")
    app.processEvents()

    assert w._state_card._slides_text.text() == "Slides stopped: Screen capture failed: denied"
    assert w._is_recording


def test_waiting_and_capturing_states_show_the_count(window, slides, app):
    w = window(rts_translate=False)
    w._btn_slides.setChecked(True)
    w._start_recording()

    w._slide_status.emit("waiting", 0, "")
    app.processEvents()
    assert w._state_card._slides_text.text() == "Slides: waiting for a Meet or Teams window"
    w._slide_status.emit("capturing", 3, "")
    app.processEvents()
    assert w._state_card._slides_text.text() == "Slides: watching the meeting, 3 saved"


def test_without_a_screen_source_the_toggle_is_disabled(window, monkeypatch):
    monkeypatch.setattr(mw, "create_slide_source", lambda: (None, "Windows only for now"))
    monkeypatch.setattr(mw, "SlideCapture", FakeSlideCapture)
    FakeSlideCapture.instances = []
    w = window(rts_translate=False)

    assert not w._btn_slides.isEnabled()
    assert w._btn_slides.toolTip() == "Windows only for now"
    w._start_recording()
    assert FakeSlideCapture.instances == []


def test_a_narrow_window_hides_the_title_so_every_toggle_keeps_its_label(window, slides, app):
    w = window(rts_translate=False)
    w.resize(600, 650)
    w.show()
    app.processEvents()
    assert not w._app_title.isVisible()
    w.resize(w._header.sizeHint().width() + w._app_title.sizeHint().width() + 40, 650)
    app.processEvents()
    assert w._app_title.isVisible()


def test_settings_apply_new_tuning_to_a_running_capture(window, slides, monkeypatch):
    w = window(rts_translate=False)
    w._btn_slides.setChecked(True)
    w._start_recording()
    assert slides[0].tuning == SlideTuning()

    class Dialog:
        def __init__(self, config, *args, **kwargs):
            self.config = {**config, "slide_tuning": {"new_content_percent": 0.5}}

        def exec(self):
            return True

        def get_config(self):
            return self.config

    monkeypatch.setattr(mw, "SettingsDialog", Dialog)
    w._open_settings()

    assert slides[0].tuning == SlideTuning(new_content_percent=0.5)
