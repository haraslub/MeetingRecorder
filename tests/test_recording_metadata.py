"""Display names do not move, overwrite or invalidate recording resources."""

import json

import pytest

from audiorecorder.recordings import metadata
from audiorecorder.recordings.manager import RecordingManager
from audiorecorder.transcription.jobs import TranscriptionJob


@pytest.mark.parametrize("name", ["slides", "Slides", 'Q4: budget / plan? <draft>',
                                  "CON", "../outside", ".hidden", "🎙" * 60])
def test_display_titles_never_become_paths(tmp_path, name):
    folder = tmp_path / "stable"
    folder.mkdir()
    audio = folder / "stable.m4a"
    audio.write_bytes(b"audio")
    audio.with_suffix(".md").write_text("Transcript")
    slides = folder / "slides.md"
    slides.write_text("Slide index")
    manager = RecordingManager(str(tmp_path))
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert manager.rename(str(audio), name) == str(audio)
    assert manager.scan()[0].name == name
    assert {p.name: p.read_bytes() for p in folder.iterdir()
            if p.name in before} == before
    assert json.loads(metadata.metadata_path(audio).read_text())["display_name"] == name


def test_unknown_fields_survive_and_failed_replace_preserves_previous_metadata(
        tmp_path, monkeypatch):
    audio = tmp_path / "stable.m4a"
    audio.write_bytes(b"audio")
    path = metadata.metadata_path(audio)
    path.write_text(json.dumps({"display_name": "Before", "notes": {"keep": True}}))
    metadata.set_display_name(audio, "After")
    assert json.loads(path.read_text()) == {"display_name": "After", "notes": {"keep": True}}
    original = path.read_bytes()

    def denied(*args):
        raise PermissionError("No replacement allowed")

    monkeypatch.setattr(metadata.os, "replace", denied)
    with pytest.raises(PermissionError):
        metadata.set_display_name(audio, "Failed")
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".recording-*.json"))


@pytest.mark.parametrize("contents", ["{", "[]", '"text"', "\ud800"])
def test_corrupt_metadata_falls_back_without_being_overwritten(tmp_path, contents):
    audio = tmp_path / "stable.m4a"
    audio.write_bytes(b"audio")
    path = metadata.metadata_path(audio)
    path.write_bytes(contents.encode("utf-8", errors="surrogatepass"))
    before = path.read_bytes()
    assert metadata.get_display_name(audio) == "stable"
    with pytest.raises(ValueError):
        metadata.set_display_name(audio, "New")
    assert path.read_bytes() == before


def test_rename_works_during_transcription_and_does_not_change_checkpoint(tmp_path):
    audio = tmp_path / "stable.m4a"
    audio.write_bytes(b"audio")
    checkpoint = audio.with_suffix(".transcription.json")
    checkpoint.write_text("saved job")
    with TranscriptionJob(audio).locked():
        RecordingManager(str(tmp_path)).rename(str(audio), "Rename while busy")
    assert checkpoint.read_text() == "saved job"
    assert metadata.get_display_name(audio) == "Rename while busy"


def test_metadata_follows_move_and_delete(tmp_path):
    output, archive = tmp_path / "out", tmp_path / "archive"
    output.mkdir()
    archive.mkdir()
    folder = output / "stable"
    folder.mkdir()
    audio = folder / "stable.m4a"
    audio.write_bytes(b"audio")
    manager = RecordingManager(str(output))
    manager.rename(str(audio), "My display name")
    moved = manager.move(str(audio), str(archive))
    assert metadata.get_display_name(moved) == "My display name"
    assert not folder.exists()
    RecordingManager(str(archive)).delete(moved)
    assert list(archive.iterdir()) == []


def test_legacy_migration_rejects_duplicate_destinations_without_data_loss(tmp_path):
    audio = tmp_path / "slides.m4a"
    audio.write_bytes(b"audio")
    audio.with_suffix(".md").write_text("Actual transcript")
    old_slides = tmp_path / "slides.slides"
    old_slides.mkdir()
    (old_slides / "index.md").write_text("Slide index")
    failed = RecordingManager(str(tmp_path)).migrate()
    assert failed and "conflicting names" in failed[0][1]
    assert audio.read_bytes() == b"audio"
    assert audio.with_suffix(".md").read_text() == "Actual transcript"
    assert (old_slides / "index.md").read_text() == "Slide index"
    assert not (tmp_path / "slides").exists()


@pytest.mark.parametrize("name", ["", "  ", "a\nb", "a\x00b", "x" * 201, "bad\ud800name"])
def test_bad_display_names_never_create_metadata(tmp_path, name):
    audio = tmp_path / "stable.m4a"
    audio.write_bytes(b"audio")
    with pytest.raises(ValueError):
        metadata.set_display_name(audio, name)
    assert not metadata.metadata_path(audio).exists()
