"""Slide snapshots on disk: a temporary folder during the call, the recording's folder after it."""

import os
import re
import shutil
from pathlib import Path

import numpy as np
from PyQt6.QtGui import QImage

TEMP_PREFIX = "_rec_slides_"
INDEX = "slides.md"
# Before recordings had folders, the snapshots sat in `<recording>.slides/` with `index.md`.
LEGACY_SUFFIX = ".slides"
LEGACY_INDEX = "index.md"
JPEG_QUALITY = 90
NAME = re.compile(r"^(\d{4}) (\d{2})-(\d{2})-(\d{2})\.jpg$")


def snapshot_files(folder, index=INDEX):
    """The snapshots in a folder and its index, the files the slides feature owns there."""
    folder = Path(folder)
    owned = sorted(path for path in folder.glob("*.jpg") if NAME.match(path.name))
    if (folder / index).is_file():
        owned.append(folder / index)
    return owned


def _clock(seconds):
    seconds = max(0, int(seconds))
    return seconds // 3600, seconds // 60 % 60, seconds % 60


class SlideFolder:
    """The snapshots of one recording while it runs, named by order and time in the call."""

    def __init__(self, output_dir, stamp):
        self.path = Path(output_dir) / f"{TEMP_PREFIX}{stamp}"
        self.count = 0

    def add(self, frame, offset_seconds):
        """Write one BGR frame as JPEG; raises OSError when it cannot be saved."""
        self.path.mkdir(exist_ok=True)
        hours, minutes, seconds = _clock(offset_seconds)
        target = self.path / f"{self.count + 1:04d} {hours:02d}-{minutes:02d}-{seconds:02d}.jpg"
        pixels = np.ascontiguousarray(frame)
        height, width = pixels.shape[:2]
        image = QImage(pixels.data, width, height, pixels.strides[0], QImage.Format.Format_BGR888)
        if not image.save(str(target), "JPEG", JPEG_QUALITY):
            raise OSError(f"Cannot write {target}")
        self.count += 1
        return target

    def finalize(self, recording_path):
        return finalize_folder(self.path, recording_path)

    def discard(self):
        shutil.rmtree(self.path, ignore_errors=True)


def _index(recording, images):
    lines = [f"# Slides of {recording.stem}", "",
             "Snapshots of the meeting window, with their time in the recording.",
             ""]
    for image in images:
        match = NAME.match(image.name)
        if match is None:
            continue
        number, hours, minutes, seconds = match.groups()
        clock = f"{hours}:{minutes}:{seconds}"
        lines += [f"## {int(number)} · {clock}", "",
                  f"![Slide {int(number)} at {clock}](<{image.name}>)", ""]
    return "\n".join(lines)


def finalize_folder(folder, recording_path):
    """Move the snapshots into the recording's folder and write their index beside them.

    Returns the index, or None when there was nothing to keep. The temporary folder is
    removed once its snapshots moved. Files already in the recording's folder are never
    replaced: then nothing moves and FileExistsError is raised.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    images = [path for path in snapshot_files(folder) if path.suffix == ".jpg"]
    if not images:
        shutil.rmtree(folder, ignore_errors=True)
        return None
    recording = Path(recording_path)
    target = recording.parent
    for path in [target / INDEX] + [target / image.name for image in images]:
        if os.path.lexists(path):
            raise FileExistsError(path)
    staging = target / f".{INDEX}.tmp"
    staging.write_text(_index(recording, images), encoding="utf-8")
    for image in images:
        image.rename(target / image.name)
    os.replace(staging, target / INDEX)
    shutil.rmtree(folder, ignore_errors=True)
    return target / INDEX
