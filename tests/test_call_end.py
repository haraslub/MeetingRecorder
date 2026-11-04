# The window fixtures come from test_rts_toggle; pytest passes them in by parameter name.
# ruff: noqa: F401, F811
from types import SimpleNamespace

import pytest
from test_rts_toggle import app, window

from audiorecorder.meetings.call_end import CallEndWatch
from audiorecorder.ui import main_window as mw


def test_a_recording_without_a_meeting_never_asks():
    watch = CallEndWatch(grace=30)
    assert [watch.observe(False, t) for t in (0, 40, 400)] == ["", "", ""]


def test_a_meeting_gone_for_the_grace_time_asks_once():
    watch = CallEndWatch(grace=30)
    assert watch.observe(True, 0) == ""
    assert watch.observe(False, 20) == ""
    assert watch.observe(False, 30) == "ask"
    assert watch.observe(False, 300) == ""


def test_a_meeting_that_comes_back_closes_the_prompt_and_rearms_it():
    watch = CallEndWatch(grace=30)
    watch.observe(True, 0)
    assert watch.observe(False, 35) == "ask"
    assert watch.observe(True, 40) == "back"
    assert watch.observe(True, 45) == ""
    assert watch.observe(False, 70) == ""
    assert watch.observe(False, 75) == "ask"


@pytest.fixture
def meeting(monkeypatch):
    source = SimpleNamespace(title="Weekly sync | Microsoft Teams - Microsoft Edge")
    source.meeting_title = lambda: source.title
    monkeypatch.setattr(mw, "create_slide_source", lambda: (source, ""))
    return source


def test_the_window_asks_when_the_meeting_window_closes_and_stops_on_yes(
        window, meeting, monkeypatch):
    w = window(rts_translate=False)
    w._start_recording()
    w._call_end = CallEndWatch(grace=0)
    stops = []
    monkeypatch.setattr(w, "_stop_recording", lambda: stops.append(True))

    w._watch_call()
    assert w._call_end_prompt is None
    meeting.title = ""
    w._watch_call()

    assert w._call_end_prompt is not None and w._call_end_prompt.isVisible()
    w._call_end_prompt.accept()
    assert stops == [True]
    assert w._call_end_prompt is None


def test_the_prompt_closes_when_the_meeting_window_comes_back(window, meeting):
    w = window(rts_translate=False)
    w._start_recording()
    w._call_end = CallEndWatch(grace=0)
    w._watch_call()
    meeting.title = ""
    w._watch_call()
    assert w._call_end_prompt is not None

    meeting.title = "Weekly sync | Microsoft Teams - Microsoft Edge"
    w._watch_call()

    assert w._call_end_prompt is None
    assert w._is_recording


def test_stopping_the_recording_closes_the_prompt(window, meeting):
    w = window(rts_translate=False)
    w._start_recording()
    w._call_end = CallEndWatch(grace=0)
    w._watch_call()
    meeting.title = ""
    w._watch_call()

    w._stop_recording()

    assert w._call_end_prompt is None
    assert w._call_end is None


def test_a_saved_recording_is_transcribed_only_when_the_setting_is_on(window, monkeypatch):
    w = window(rts_translate=False)
    queued = []
    monkeypatch.setattr(w, "_transcribe_recording",
                        lambda path, automatic=False: queued.append((path, automatic)))

    w._transcribe_when_saved("call.m4a")
    w.config["auto_transcribe"] = True
    w._transcribe_when_saved("call.m4a")

    assert queued == [("call.m4a", True)]


def test_an_automatic_transcription_without_a_key_only_tells_the_status_bar(
        window, monkeypatch):
    w = window(rts_translate=False)
    monkeypatch.setattr(mw.secrets, "get_api_key", lambda: None)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *args: pytest.fail("no dialog"))

    w._transcribe_recording("call.m4a", automatic=True)

    assert "no Soniox API key" in w._status_bar.currentMessage()
    assert "call.m4a" not in w._busy_paths
