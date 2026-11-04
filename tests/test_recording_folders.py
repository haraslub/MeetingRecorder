"""Each recording lives in its own folder; delete, move and the start-up migration keep it whole."""

from datetime import datetime

import pytest

from audiorecorder.recordings.manager import (
    SLIDES,
    RecordingManager,
    new_recording_path,
    remove_empty_folder,
)
from audiorecorder.recordings.naming import folder_name, meeting_title

STARTED = datetime(2026, 11, 4, 10, 0, 0)
NAME = "2026-11-04 10-00-00 - Weekly sync"


def make_folder_recording(output, name=NAME, transcript=True, slides=True):
    folder = output / name
    folder.mkdir()
    audio = folder / f"{name}.m4a"
    audio.write_bytes(b"not really audio")
    if transcript:
        (folder / f"{name}.md").write_text("# Transcript", encoding="utf-8")
        (folder / f"{name}.summary.md").write_text("# Summary", encoding="utf-8")
    if slides:
        (folder / "0001 00-00-04.jpg").write_bytes(b"jpeg one")
        (folder / "slides.md").write_text("# Slides", encoding="utf-8")
    return audio


@pytest.mark.parametrize("raw,title", [
    ("Weekly sync | Microsoft Teams - Microsoft​ Edge", "Weekly sync"),
    ("(2) Budget review | Microsoft Teams", "Budget review"),
    ("Meet - Weekly sync - Google Chrome", "Weekly sync"),
    ("Meet - abc-defg-hij - Google Chrome", ""),
    ("Chat | Microsoft Teams - Google Chrome", ""),
    ("Active Microsoft Teams call", ""),
    ("Scheduled meeting", ""),
    ("Q1: plan/budget? <draft> | Microsoft Teams", "Q1 plan budget draft"),
    ("", ""),
])
def test_meeting_titles_become_folder_safe_names(raw, title):
    assert meeting_title(raw) == title


def test_a_long_title_is_cut_at_a_word_and_never_ends_with_a_dot():
    title = meeting_title("Word " * 20 + "| Microsoft Teams")
    assert len(title) <= 60 and title.endswith("Word")
    assert meeting_title("Sync with Radek... | Microsoft Teams") == "Sync with Radek"


def test_the_folder_name_is_the_start_time_and_the_title_when_known():
    assert folder_name(STARTED, "Weekly sync") == NAME
    assert folder_name(STARTED) == "2026-11-04 10-00-00"


def test_a_new_recording_gets_its_own_folder_and_a_free_name(tmp_path):
    first = new_recording_path(tmp_path, STARTED, "Weekly sync")
    second = new_recording_path(tmp_path, STARTED, "Weekly sync")
    assert first == str(tmp_path / NAME / f"{NAME}.m4a")
    assert second == str(tmp_path / f"{NAME} (2)" / f"{NAME} (2).m4a")
    remove_empty_folder(second)
    assert sorted(p.name for p in tmp_path.iterdir()) == [NAME]


def test_scan_lists_folder_recordings_with_their_folder_and_slides(tmp_path):
    audio = make_folder_recording(tmp_path)
    (tmp_path / "loose.m4a").write_bytes(b"audio")
    (tmp_path / "other folder").mkdir()

    by_name = {r.name: r for r in RecordingManager(str(tmp_path)).scan()}

    assert set(by_name) == {NAME, "loose"}
    recording = by_name[NAME]
    assert recording.path == str(audio)
    assert recording.folder == str(tmp_path / NAME)
    assert recording.is_transcribed
    assert recording.outputs == (".md", ".summary.md", SLIDES)
    assert by_name["loose"].folder == ""


def test_move_takes_the_whole_folder(tmp_path):
    output, destination = tmp_path / "out", tmp_path / "archive"
    output.mkdir()
    destination.mkdir()
    audio = make_folder_recording(output)

    moved = RecordingManager(str(output)).move(str(audio), str(destination))

    assert moved == str(destination / NAME / f"{NAME}.m4a")
    assert not (output / NAME).exists()
    assert sorted(p.name for p in (destination / NAME).iterdir()) == [
        "0001 00-00-04.jpg", f"{NAME}.m4a", f"{NAME}.md", f"{NAME}.summary.md", "slides.md"]


def test_move_refuses_an_existing_folder_and_changes_nothing(tmp_path):
    output, destination = tmp_path / "out", tmp_path / "archive"
    output.mkdir()
    (destination / NAME).mkdir(parents=True)
    audio = make_folder_recording(output)

    with pytest.raises(FileExistsError):
        RecordingManager(str(output)).move(str(audio), str(destination))

    assert audio.exists() and (output / NAME / "slides.md").exists()
    assert list((destination / NAME).iterdir()) == []


def test_move_keeps_files_a_person_added_in_the_old_folder(tmp_path):
    output, destination = tmp_path / "out", tmp_path / "archive"
    output.mkdir()
    destination.mkdir()
    audio = make_folder_recording(output)
    (output / NAME / "notes.txt").write_text("mine", encoding="utf-8")

    RecordingManager(str(output)).move(str(audio), str(destination))

    assert sorted(p.name for p in (output / NAME).iterdir()) == ["notes.txt"]


def test_rename_renames_the_folder_with_the_files(tmp_path):
    audio = make_folder_recording(tmp_path)

    renamed = RecordingManager(str(tmp_path)).rename(str(audio), "Budget")

    assert renamed == str(tmp_path / "Budget" / "Budget.m4a")
    assert sorted(p.name for p in (tmp_path / "Budget").iterdir()) == [
        "0001 00-00-04.jpg", "Budget.m4a", "Budget.md", "Budget.summary.md", "slides.md"]
    assert not (tmp_path / NAME).exists()


def test_delete_removes_the_folder_with_everything_the_app_wrote(tmp_path):
    audio = make_folder_recording(tmp_path)
    RecordingManager(str(tmp_path)).delete(str(audio))
    assert list(tmp_path.iterdir()) == []


def test_delete_keeps_the_folder_when_a_person_added_a_file(tmp_path):
    audio = make_folder_recording(tmp_path)
    (tmp_path / NAME / "notes.txt").write_text("mine", encoding="utf-8")

    RecordingManager(str(tmp_path)).delete(str(audio))

    assert sorted(p.name for p in (tmp_path / NAME).iterdir()) == ["notes.txt"]


def test_migrate_moves_old_recordings_with_their_slides_into_folders(tmp_path):
    old = "Recording 2026-11-03 09-15-00 1a2b3c4d"
    (tmp_path / f"{old}.m4a").write_bytes(b"audio")
    (tmp_path / f"{old}.md").write_text("# Transcript", encoding="utf-8")
    slides = tmp_path / f"{old}.slides"
    slides.mkdir()
    (slides / "0001 00-00-04.jpg").write_bytes(b"jpeg")
    (slides / "index.md").write_text("# Slides", encoding="utf-8")
    (tmp_path / "Imported call.m4a").write_bytes(b"audio")
    (tmp_path / ".recording-staging.m4a").write_bytes(b"partial")

    assert RecordingManager(str(tmp_path)).migrate() == []

    new = "2026-11-03 09-15-00"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        ".recording-staging.m4a", "2026-11-03 09-15-00", "Imported call"]
    assert sorted(p.name for p in (tmp_path / new).iterdir()) == [
        "0001 00-00-04.jpg", f"{new}.m4a", f"{new}.md", "slides.md"]
    assert (tmp_path / "Imported call" / "Imported call.m4a").exists()


def test_migrate_leaves_a_recording_in_use_where_it_is(tmp_path):
    from audiorecorder.transcription.jobs import TranscriptionJob

    audio = tmp_path / "Recording 2026-11-03 09-15-00 1a2b3c4d.m4a"
    audio.write_bytes(b"audio")
    with TranscriptionJob(audio).locked():
        failed = RecordingManager(str(tmp_path)).migrate()

    assert [path for path, _ in failed] == [str(audio)]
    assert audio.exists()
    assert not (tmp_path / "2026-11-03 09-15-00").exists()
