import numpy as np
import pytest

from audiorecorder.slides.store import INDEX, TEMP_PREFIX, SlideFolder, finalize_folder


def frame(value=200):
    image = np.full((90, 160, 3), value, dtype=np.uint8)
    image[20:40, 30:120] = 30
    return image


def test_add_writes_a_numbered_jpeg_with_its_time(tmp_path):
    folder = SlideFolder(tmp_path, "abc")
    first = folder.add(frame(), 4.6)
    second = folder.add(frame(150), 3725)
    assert folder.path == tmp_path / f"{TEMP_PREFIX}abc"
    assert first.name == "0001 00-00-04.jpg"
    assert second.name == "0002 01-02-05.jpg"
    assert first.read_bytes()[:2] == b"\xff\xd8"
    assert folder.count == 2


def test_add_accepts_a_bgr_view_of_a_bgra_frame(tmp_path):
    bgra = np.zeros((60, 80, 4), dtype=np.uint8)
    path = SlideFolder(tmp_path, "view").add(bgra[..., :3], 0)
    assert path.stat().st_size > 0


def test_finalize_moves_the_snapshots_into_the_recording_folder_with_an_index(tmp_path):
    folder = SlideFolder(tmp_path, "abc")
    folder.add(frame(), 12)
    folder.add(frame(120), 75)
    recording_folder = tmp_path / "2026-11-04 10-00-00 - Weekly sync"
    recording_folder.mkdir()
    recording = recording_folder / "2026-11-04 10-00-00 - Weekly sync.m4a"

    result = folder.finalize(recording)

    assert result == recording_folder / INDEX
    assert not folder.path.exists()
    index = result.read_text(encoding="utf-8")
    assert "## 1 · 00:00:12" in index
    assert "![Slide 2 at 00:01:15](<0002 00-01-15.jpg>)" in index
    assert sorted(p.name for p in recording_folder.iterdir()) == [
        "0001 00-00-12.jpg", "0002 00-01-15.jpg", INDEX]


def test_finalize_without_snapshots_leaves_nothing(tmp_path):
    folder = SlideFolder(tmp_path, "empty")
    folder.path.mkdir()
    assert folder.finalize(tmp_path / "call.m4a") is None
    assert list(tmp_path.iterdir()) == []
    assert SlideFolder(tmp_path, "never").finalize(tmp_path / "call.m4a") is None


def test_finalize_never_replaces_existing_slides(tmp_path):
    folder = SlideFolder(tmp_path, "abc")
    folder.add(frame(), 1)
    (tmp_path / INDEX).write_text("kept", encoding="utf-8")
    with pytest.raises(FileExistsError):
        finalize_folder(folder.path, tmp_path / "call.m4a")
    assert (tmp_path / INDEX).read_text(encoding="utf-8") == "kept"
    assert (folder.path / "0001 00-00-01.jpg").exists()


def test_discard_removes_the_temporary_folder(tmp_path):
    folder = SlideFolder(tmp_path, "abc")
    folder.add(frame(), 1)
    folder.discard()
    assert list(tmp_path.iterdir()) == []
