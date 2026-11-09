"""Live subtitles, speaker lines and the meeting title survive a crash with the audio.

They live in memory until the M4A is saved. The window keeps a journal beside the capture
files while it records, and start-up recovery puts them into the recovered folder.
"""

# The window fixtures come from test_rts_toggle; pytest passes them in by parameter name.
# ruff: noqa: F401, F811
import json

import pytest
from test_rts_toggle import app, window
from test_session_recovery import run_window, tone

from audiorecorder.recordings import journal
from audiorecorder.recordings.manager import RecordingManager

SUBTITLES = [["10:00:01", "Hello", "Ahoj"]]
SPEAKERS = [["10:00:02", "Me", "Good morning"]]


def test_a_journal_round_trips_its_payloads(tmp_path):
    path = journal.journal_path(tmp_path, "a")
    journal.save(path, journal.encode("Weekly sync", (SUBTITLES, "cs"), (SPEAKERS, {})))

    assert journal.journal_for_capture(tmp_path / "_rec_mic_a.wav") == path
    assert journal.load(path) == ("Weekly sync", (SUBTITLES, "cs"), (SPEAKERS, {}), None)
    assert not path.with_name(path.name + ".tmp").exists()


def test_a_missing_or_damaged_journal_gives_nothing(tmp_path):
    assert journal.load(tmp_path / "_rec_live_none.json") == ("", None, None, None)
    damaged = tmp_path / "_rec_live_bad.json"
    damaged.write_text('{"title": "Weekly', encoding="utf-8")
    assert journal.load(damaged) == ("", None, None, None)
    damaged.write_text('{"title": 3, "subtitles": [[], "cs"], "speakers": "x"}',
                       encoding="utf-8")
    assert journal.load(damaged) == ("", None, None, None)


@pytest.mark.parametrize("name,expected", [("Chosen name", "Chosen name"),
                                         ("../escape", "../escape"), (".hidden", ".hidden"),
                                         (123, None), ("a\nb", None)])
def test_custom_names_are_validated_when_recovered(tmp_path, name, expected):
    path = journal.journal_path(tmp_path, "name")
    journal.save(path, journal.encode("Weekly sync", None, None, name=name))
    assert journal.load(path) == ("Weekly sync", None, None, expected)


def test_an_old_journal_without_a_custom_name_still_loads(tmp_path):
    path = journal.journal_path(tmp_path, "old")
    path.write_text(json.dumps({"title": "Weekly", "subtitles": [SUBTITLES, "cs"]}))
    assert journal.load(path) == ("Weekly", (SUBTITLES, "cs"), None, None)
    path.write_text('{"title":"Weekly","name":"Legacy custom name"}')
    assert journal.load(path) == ("Weekly", None, None, "Legacy custom name")


def test_a_capture_set_aside_keeps_its_journal(tmp_path):
    (tmp_path / "_rec_mic_a.wav").write_bytes(b"mic")
    journal.journal_path(tmp_path, "a").write_text("{}", encoding="utf-8")

    kept = RecordingManager.set_aside_capture(None, str(tmp_path / "_rec_mic_a.wav"))

    assert sorted(path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for path in kept) == [
        "Unrecovered_live_a.json", "Unrecovered_mic_a.wav"]


def test_the_window_keeps_the_journal_while_recording_and_drops_it_without_audio(
        window, tmp_path):
    w = window(rts_translate=False)
    w._start_recording()
    path = journal.journal_path(tmp_path, "fakestamp")
    w._meeting_title = "Weekly sync"
    w._watch_call()

    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Weekly sync"

    w._stop_recording()  # The fake capture returns no audio files.

    assert not path.exists()


@pytest.mark.parametrize("custom_name", [None, "My chosen name"])
def test_start_up_recovery_restores_title_and_live_subtitles(tmp_path, custom_name):
    tone(tmp_path / "_rec_mic_a.wav")
    tone(tmp_path / "_rec_system_a.wav")
    journal.save(journal.journal_path(tmp_path, "a"),
                 journal.encode("Weekly sync", (SUBTITLES, "cs"), (SPEAKERS, {"Me": ""}),
                                name=custom_name))
    run_window(f"""
        import sys
        from pathlib import Path
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from audiorecorder.ui import main_window as mw

        app = QApplication([])
        directory = Path(sys.argv[1])
        window = mw.MainWindow({{'output_dir': str(directory), 'meeting_detection': False}})

        def check():
            if not window._encoder_queue and not window._active_encoders:
                app.quit()
            else:
                QTimer.singleShot(50, check)

        QTimer.singleShot(0, check)
        QTimer.singleShot(15000, lambda: app.exit(9))
        assert app.exec() == 0
        folders = [path for path in directory.iterdir() if path.is_dir()]
        assert len(folders) == 1, folders
        folder = folders[0]
        expected_name = {custom_name!r}
        assert folder.name.endswith(' - Weekly sync'), folder.name
        if expected_name:
            from audiorecorder.recordings.metadata import get_display_name
            assert get_display_name(folder / (folder.name + '.m4a')) == expected_name
        files = sorted(path.name for path in folder.iterdir())
        suffixes = ['.m4a', '.live.md', '.speakers.md', '.speakers.json']
        if expected_name:
            suffixes.append('.recording.json')
        assert files == sorted(folder.name + suffix for suffix in suffixes), files
        assert 'Ahoj' in (folder / (folder.name + '.live.md')).read_text(encoding='utf-8')
        assert not [path for path in directory.iterdir() if path.name.startswith('_rec_')]
    """, tmp_path)
