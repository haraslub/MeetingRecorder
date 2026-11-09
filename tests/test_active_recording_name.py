"""Naming a live capture affects only its eventual output, never its open audio files."""

# ruff: noqa: F401, F811
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from test_inline_rename import edit, show
from test_rts_toggle import FakeCapture, app, window

from audiorecorder.meetings.detection import MeetingCandidate
from audiorecorder.recordings.naming import folder_name


def test_live_name_is_shown_and_updates_when_a_title_is_detected(window):
    w = window(rts_translate=False)
    show(w)
    assert not w._state_card.recording_name.isVisible()
    w._start_recording()
    field = w._state_card.recording_name
    stamp = datetime.fromtimestamp(w._recording_start_time)
    assert field.isVisible()
    assert field.label.text() == folder_name(stamp)
    w._lifecycle.candidates["test"] = MeetingCandidate(
        "test", "Teams", "Weekly sync | Microsoft Teams")
    w._watch_call()
    assert field.label.text() == folder_name(stamp, "Weekly sync")


def test_chosen_name_is_immediately_journaled_and_detection_cannot_replace_it(window, tmp_path):
    w = window(rts_translate=False)
    show(w)
    w._start_recording()
    field = w._state_card.recording_name
    edit(field, "My live recording")
    assert field.label.text() == "My live recording"
    assert field.label.isVisible()
    assert not field._submitting
    stored = json.loads(w._journal_path.read_text())
    assert stored["display_name"] == "My live recording"
    w._lifecycle.candidates["test"] = MeetingCandidate(
        "test", "Teams", "Later meeting title | Microsoft Teams")
    w._watch_call()
    assert field.label.text() == "My live recording"
    assert w._meeting_title == "Later meeting title"
    # Repeated edits are accepted, rather than leaving the field in submitting state.
    edit(field, "Final name")
    assert w._recording_display_name == "Final name"
    assert not list(tmp_path.glob("*.m4a"))
    assert not (tmp_path / "Final name").exists()


def test_detection_during_an_edit_keeps_the_draft_and_escape_restores_detected_name(window):
    w = window(rts_translate=False)
    show(w)
    w._start_recording()
    field = w._state_card.recording_name
    field.begin()
    field.editor.setText("Uncommitted draft")
    w._lifecycle.candidates["test"] = MeetingCandidate(
        "test", "Teams", "Detected title | Microsoft Teams")
    w._watch_call()
    assert field.editor.text() == "Uncommitted draft"
    QTest.keyClick(field.editor, Qt.Key.Key_Escape)
    assert field.label.text().endswith(" - Detected title")
    assert w._recording_display_name is None


def test_cancel_invalid_names_and_mute_do_not_change_committed_name(window):
    w = window(rts_translate=False)
    show(w)
    w._start_recording()
    field = w._state_card.recording_name
    original = field.label.text()
    edit(field, "bad\nname")
    assert field.error.isVisible()
    assert w._recording_display_name is None
    field.editor.setText("🎙" * 201)
    QTest.keyClick(field.editor, Qt.Key.Key_Return)
    assert "too long" in field.error.text()
    assert w._recording_display_name is None
    QTest.keyClick(field.editor, Qt.Key.Key_Escape)
    assert field.label.text() == original
    w._state_card.set_state("recording", muted=True)
    assert field.isVisible() and field.label.text() == original


def test_live_custom_name_is_used_at_stop_and_resets_for_next_capture(
        window, tmp_path, monkeypatch):
    w = window(rts_translate=False)
    show(w)
    w._start_recording()
    edit(w._state_card.recording_name, "Chosen name")
    # An existing recording is preserved; the new one gets a free suffix.
    (tmp_path / "Chosen name").mkdir()
    monkeypatch.setattr(FakeCapture, "stop", lambda self: (
        None, str(tmp_path / "_rec_mic_fakestamp.wav")))
    worker = SimpleNamespace(result=SimpleNamespace(cleanup_errors=()))
    monkeypatch.setattr(w, "_enqueue_encoder", lambda *args: worker)
    monkeypatch.setattr(w, "_start_next_encoder", lambda: None)
    w._stop_recording()
    (output_path,) = w._encoding_ids
    assert "Chosen name" not in output_path
    stored = json.loads((tmp_path / "_rec_live_fakestamp.json").read_text())
    assert stored["display_name"] == "Chosen name"
    assert not w._state_card.recording_name.isVisible()
    Path(output_path).write_bytes(b"encoded audio")
    w._start_recording()
    assert w._recording_display_name is None
    assert w._state_card.recording_name.label.text() != "Chosen name"
    edit(w._state_card.recording_name, "Second recording")
    w._on_encode_finished(output_path)
    from audiorecorder.recordings.metadata import get_display_name
    assert get_display_name(output_path) == "Chosen name"
    assert w._state_card.recording_name.label.text() == "Second recording"
    # The second fake capture has no audio and can close without another encoding job.
    monkeypatch.setattr(FakeCapture, "stop", lambda self: (None, None))


def test_stopping_or_failed_capture_disables_editing_but_keeps_name(window):
    w = window(rts_translate=False)
    show(w)
    w._start_recording()
    field = w._state_card.recording_name
    edit(field, "Keep on retry")
    for state in ("stopping", "failed"):
        w._state_card.set_state(state)
        assert field.isVisible()
        field.begin()
        assert not field._editing
        assert field.label.text() == "Keep on retry"
    w._state_card.set_state("recording")
    assert field.isVisible()
    field.begin()
    assert field._editing
    QApplication.processEvents()


def test_failed_capture_metadata_write_keeps_the_name_journal(window, tmp_path, monkeypatch):
    w = window(rts_translate=False)
    audio = tmp_path / "saved.m4a"
    audio.write_bytes(b"safe audio")
    journal = tmp_path / "_rec_live_failed.json"
    journal.write_text('{"display_name":"Keep this title"}')
    w._pending_sidecars[str(audio)] = {"display_name": "Keep this title", "journal": journal}
    from audiorecorder.recordings import metadata

    def denied(*args):
        raise PermissionError("Metadata denied")

    reported = []
    monkeypatch.setattr(metadata, "set_display_name", denied)
    monkeypatch.setattr(w, "_show_recording_error", lambda *args: reported.append(args))
    w._write_pending_sidecars(str(audio))
    assert audio.read_bytes() == b"safe audio"
    assert journal.exists()
    assert reported and "name could not be saved" in reported[0][0]
