"""Real mouse/keyboard edits, filesystem renames, and refresh/error recovery."""

# Fixtures use fake capture/providers and a temporary recording library.
# ruff: noqa: F401, F811
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget
from test_recording_folders import make_folder_recording
from test_rts_toggle import app, window

from audiorecorder.recordings.manager import RecordingManager
from audiorecorder.ui.recording_list import RecordingListWidget
from audiorecorder.ui.recording_name import RecordingName


def show(widget):
    widget.show()
    widget.activateWindow()
    QApplication.processEvents()


def edit(field, text):
    QTest.mouseClick(field.label, Qt.MouseButton.LeftButton)
    field.editor.setText(text)
    QTest.keyClick(field.editor, Qt.Key.Key_Return)
    QApplication.processEvents()


def test_click_selects_full_name_and_enter_emits_once(app):
    field = RecordingName("Weekly sync")
    received = []
    field.rename_requested.connect(received.append)
    show(field)
    QTest.mouseClick(field.label, Qt.MouseButton.LeftButton)
    assert field.editor.selectedText() == "Weekly sync"
    field.editor.setText("  Budget review  ")
    QTest.keyClick(field.editor, Qt.Key.Key_Return)
    QTest.keyClick(field.editor, Qt.Key.Key_Return)
    assert received == ["Budget review"]
    field.close()


def test_keyboard_can_start_and_cancel_without_a_request(app):
    field = RecordingName("Weekly")
    received = []
    field.rename_requested.connect(received.append)
    show(field)
    field.label.setFocus()
    QTest.keyClick(field.label, Qt.Key.Key_F2)
    assert field.editor.isVisible()
    field.editor.setText("Draft")
    QTest.keyClick(field.editor, Qt.Key.Key_Escape)
    assert field.label.isVisible()
    assert not field._editing
    assert received == []
    field.close()


def test_clicking_elsewhere_cancels_and_unchanged_enter_is_a_noop(app):
    host = QWidget()
    layout = QVBoxLayout(host)
    field, button = RecordingName("Weekly"), QPushButton("Elsewhere")
    layout.addWidget(field)
    layout.addWidget(button)
    received = []
    field.rename_requested.connect(received.append)
    show(host)
    field.begin()
    field.editor.setText("Draft")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert field.label.isVisible()
    edit(field, " Weekly ")
    assert field.label.isVisible()
    assert received == []
    host.close()


def test_switching_apps_keeps_the_draft(app):
    field = RecordingName("Weekly")
    show(field)
    field.begin()
    field.editor.setText("Keep this draft")
    QApplication.sendEvent(field.editor, QFocusEvent(
        QFocusEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
    assert field._editing
    assert field.editor.text() == "Keep this draft"
    field.close()


@pytest.mark.parametrize("name", ["", "  ", "../outside", ".hidden", "Bad/Name", "Bad\\Name",
                                  "Bad:Name", "Trailing.", "CON", "nul.txt", "LPT1", "COM¹"])
def test_invalid_names_stay_editable_and_never_touch_files(window, name):
    w = window(rts_translate=False)
    audio = make_folder_recording(Path(w.config["output_dir"]))
    w._refresh_list()
    show(w)
    field = w._recording_list._item_widgets[0]._name
    edit(field, name)
    assert field.editor.isVisible()
    assert field.error.isVisible()
    assert field.editor.text() == name
    assert audio.exists()


def test_inline_rename_updates_audio_sidecars_folder_and_row(window):
    w = window(rts_translate=False)
    output = Path(w.config["output_dir"])
    old = make_folder_recording(output)
    # Include private recovery metadata and live speaker outputs too.
    for suffix in (".live.md", ".speakers.json", ".transcription.json"):
        old.with_suffix(suffix).write_text("payload")
    w._refresh_list()
    show(w)
    edit(w._recording_list._item_widgets[0]._name, "Rozpočet – 日本語")
    new = output / "Rozpočet – 日本語" / "Rozpočet – 日本語.m4a"
    assert new.exists() and not old.exists()
    for suffix in (".md", ".summary.md", ".live.md", ".speakers.json", ".transcription.json"):
        assert new.with_suffix(suffix).exists()
    assert (new.parent / "slides.md").exists()
    assert (new.parent / "0001 00-00-04.jpg").exists()
    row = w._recording_list._item_widgets[0]
    assert row.recording.path == str(new)
    assert row._name.label.text() == "Rozpočet – 日本語"


def test_conflict_keeps_draft_for_correction_without_overwriting(window):
    w = window(rts_translate=False)
    output = Path(w.config["output_dir"])
    old = make_folder_recording(output)
    other = make_folder_recording(output, "Existing")
    w._refresh_list()
    show(w)
    row = next(r for r in w._recording_list._item_widgets if r.recording.path == str(old))
    edit(row._name, "Existing")
    assert row._name.error.isVisible()
    assert row._name.editor.text() == "Existing"
    assert old.exists() and other.exists()
    row._name.editor.setText("Corrected")
    QTest.keyClick(row._name.editor, Qt.Key.Key_Return)
    assert (output / "Corrected" / "Corrected.m4a").exists()


def test_busy_recordings_cannot_begin_or_commit_rename(window):
    w = window(rts_translate=False)
    old = make_folder_recording(Path(w.config["output_dir"]))
    w._refresh_list()
    show(w)
    row = w._recording_list._item_widgets[0]
    row.set_processing("Uploading", True, "transcribe")
    QTest.mouseClick(row._name.label, Qt.MouseButton.LeftButton)
    assert not row._name._editing
    row.set_processing("", False)
    row._name.begin()
    row._name.editor.setText("Draft")
    w._busy_paths.add(str(old))
    w._busy_kinds[str(old)] = "transcribe"
    QTest.keyClick(row._name.editor, Qt.Key.Key_Return)
    assert old.exists()
    assert "processing" in row._name.error.text()
    row.set_processing("Queued", True, "transcribe")
    assert not row._name._editing
    w._busy_paths.clear()


def test_library_refresh_preserves_draft_and_error(app, tmp_path):
    audio = make_folder_recording(tmp_path)
    library = RecordingListWidget()
    library.set_recordings(RecordingManager(str(tmp_path)).scan())
    show(library)
    field = library._item_widgets[0]._name
    field.begin()
    field.editor.setText("Unfinished draft")
    field.editor.setCursorPosition(4)
    field.show_error("Choose another name")
    make_folder_recording(tmp_path, "Another recording")
    library.set_recordings(RecordingManager(str(tmp_path)).scan())
    QApplication.processEvents()
    restored = next(r._name for r in library._item_widgets if r.recording.path == str(audio))
    assert restored is field
    assert restored.editor.isVisible()
    assert restored.editor.text() == "Unfinished draft"
    assert restored.editor.cursorPosition() == 4
    assert restored.error.text() == "Choose another name"
    assert audio.exists()
    library.close()


def test_refresh_does_not_duplicate_rename_requests(app, tmp_path):
    make_folder_recording(tmp_path)
    library = RecordingListWidget()
    manager = RecordingManager(str(tmp_path))
    library.set_recordings(manager.scan())
    received = []
    library.rename_requested.connect(lambda path, name: received.append((path, name)))
    show(library)
    library.set_recordings(manager.scan())
    library.set_recordings(manager.scan())
    edit(library._item_widgets[0]._name, "Renamed once")
    assert len(received) == 1
    library.close()


def test_failed_rename_keeps_name_and_files(window, monkeypatch):
    w = window(rts_translate=False)
    audio = make_folder_recording(Path(w.config["output_dir"]))
    w._refresh_list()
    show(w)

    def denied(*args):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(w._manager, "rename", denied)
    field = w._recording_list._item_widgets[0]._name
    edit(field, "New name")
    assert field.error.text() == "Permission denied"
    assert field.editor.isVisible()
    assert audio.exists()


def test_rename_stops_playback_before_moving_the_audio(window, monkeypatch):
    w = window(rts_translate=False)
    audio = make_folder_recording(Path(w.config["output_dir"]))
    w._refresh_list()
    show(w)
    w._player._current_path = str(audio)
    stopped = []
    monkeypatch.setattr(w._player, "stop_playback", lambda: stopped.append(True))
    original = w._manager.rename

    def rename(path, name):
        assert stopped == [True]
        return original(path, name)

    monkeypatch.setattr(w._manager, "rename", rename)
    edit(w._recording_list._item_widgets[0]._name, "Playback stopped")
    assert not audio.exists()


def test_rename_keeps_failed_job_details_and_retry_target(window):
    w = window(rts_translate=False)
    audio = make_folder_recording(Path(w.config["output_dir"]))
    error = RuntimeError("Summary failed")
    w._recording_list.set_failure(str(audio), "cleanup", error)
    w._refresh_list()
    show(w)
    edit(w._recording_list._item_widgets[0]._name, "Retry here")
    row = w._recording_list._item_widgets[0]
    assert w.recording_failure(row.recording.path) == ("cleanup", error)
    assert w.recording_failure(str(audio)) is None
    assert row._primary.text().strip() == "Retry"
    requested = []
    row.cleanup_clicked.disconnect()  # Observe routing without launching a real cleanup job.
    row.cleanup_clicked.connect(requested.append)
    row._on_primary()
    assert requested == [row.recording.path]
