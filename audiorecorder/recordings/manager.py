import logging
import os
import re
import shutil
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import av

from audiorecorder.recordings.journal import journal_for_capture
from audiorecorder.recordings.naming import folder_name
from audiorecorder.slides.store import INDEX as SLIDES_INDEX
from audiorecorder.slides.store import LEGACY_INDEX, LEGACY_SUFFIX, snapshot_files
from audiorecorder.slides.store import TEMP_PREFIX as SLIDES_PREFIX
from audiorecorder.transcription.jobs import TranscriptionJob, checkpoint_status

log = logging.getLogger(__name__)

# Everything written beside a recording and named after it. One list, because delete,
# rename and move each used to carry their own idea of what belongs to a recording, and
# delete's idea left the summary behind.
SIDECAR_SUFFIXES = (
    ".md", ".summary.md", ".live.md", ".speakers.md", ".speakers.json",
    ".transcription.json",
)
# The side files a person reads, shown as badges on the recording.
OUTPUT_SUFFIXES = (".md", ".summary.md", ".live.md", ".speakers.md")
# The badge key of slide snapshots, which are files in the folder, not one sidecar.
SLIDES = ".slides"
# Names recordings had before they got folders.
LEGACY_NAME = re.compile(r"^Recording (\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}) [0-9a-f]{8}$")


def sidecars(recording_path):
    """The sidecar files that exist for this recording."""
    recording = Path(recording_path)
    candidates = (recording.with_name(recording.stem + suffix) for suffix in SIDECAR_SUFFIXES)
    return [path for path in candidates if path.exists()]


def _remove(sidecar):
    if sidecar.is_dir() and not sidecar.is_symlink():
        shutil.rmtree(sidecar)
    else:
        sidecar.unlink()


def _suffix_of(recording, sidecar):
    return sidecar.name[len(recording.stem):]


def _free_name(directory, base):
    """base, or base with a number, so that nothing of that name exists in directory yet."""
    name, number = base, 1
    while os.path.lexists(Path(directory) / name):
        number += 1
        name = f"{base} ({number})"
    return name


def new_recording_path(output_dir, started, title=""):
    """Create the folder of a new recording and return the path its M4A gets."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    name = _free_name(output_dir, folder_name(started, title))
    folder = Path(output_dir) / name
    folder.mkdir()
    return str(folder / f"{name}.m4a")


def remove_empty_folder(recording_path):
    """Remove the folder made for a recording that was never written."""
    recording = Path(recording_path)
    if recording.parent.name == recording.stem:
        with suppress(OSError):
            recording.parent.rmdir()


@dataclass
class Recording:
    path: str
    name: str
    date: datetime
    duration_seconds: float
    is_transcribed: bool
    transcript_path: str
    transcription_status: str = ""
    # Side files that exist, by suffix: ".md", ".summary.md", ".live.md", ".speakers.md",
    # and SLIDES when slide snapshots exist.
    outputs: tuple = ()
    # The folder that holds the recording and everything made from it; "" for a recording
    # directly in the output folder.
    folder: str = ""


class RecordingManager:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def scan(self):
        recordings = []
        for f in sorted(self._audio_files(), key=lambda p: p.stat().st_mtime, reverse=True):
            transcript = f.with_suffix(".md")
            folder = self._folder(f)
            slides = (folder is not None and (folder / SLIDES_INDEX).is_file()) or (
                f.with_name(f.stem + LEGACY_SUFFIX).is_dir())
            recordings.append(Recording(
                path=str(f),
                name=f.stem,
                date=datetime.fromtimestamp(f.stat().st_mtime),
                duration_seconds=self._get_duration(str(f)),
                is_transcribed=transcript.exists(),
                transcript_path=str(transcript) if transcript.exists() else "",
                transcription_status="" if transcript.exists() else checkpoint_status(f),
                outputs=tuple(
                    suffix for suffix in OUTPUT_SUFFIXES
                    if f.with_name(f.stem + suffix).exists()
                ) + ((SLIDES,) if slides else ()),
                folder=str(folder) if folder is not None else "",
            ))
        return recordings

    def _audio_files(self):
        """Recordings in their own folder, and any M4A still directly in the output folder."""
        root = Path(self.output_dir)
        if not root.is_dir():
            return []
        files = [path for path in root.glob("*.m4a") if not path.name.startswith(".")]
        for folder in root.iterdir():
            audio = folder / f"{folder.name}.m4a"
            if folder.is_dir() and audio.is_file():
                files.append(audio)
        return files

    def _folder(self, recording):
        """The recording's own folder, or None for a file directly in the output folder."""
        recording = Path(recording)
        parent = recording.parent
        if parent.name != recording.stem:
            return None
        if parent.resolve() == Path(self.output_dir).resolve():
            return None
        return parent

    def _get_duration(self, path):
        try:
            with av.open(path) as container:
                if container.duration is not None:
                    return float(container.duration) / 1_000_000
                stream = container.streams.audio[0]
                return float(stream.duration * stream.time_base)
        except Exception:
            return 0.0

    def orphaned_captures(self):
        """Capture files a killed process left behind, as (system, mic, slides, started).

        A recording writes _rec_mic_<id>.wav and, unless it is microphone-only,
        _rec_system_<id>.wav into the output directory, and the encoder deletes both once
        the M4A exists. Slide snapshots of the same recording sit in _rec_slides_<id>/;
        system and slides are None when absent. Files still there at start-up belong to a
        session that was logged off or crashed mid-recording; their data is readable even
        without a final header.
        """
        directory = Path(self.output_dir)
        if not directory.exists():
            return []
        captures = []
        for mic in sorted(directory.glob("_rec_mic_*.wav")):
            stamp = mic.stem.removeprefix("_rec_mic_")
            system = mic.with_name(f"_rec_system_{stamp}.wav")
            slides = mic.with_name(f"{SLIDES_PREFIX}{stamp}")
            # Recordings are named by the time they started, when the capture file was made.
            stat = mic.stat()
            started = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime))
            captures.append((
                str(system) if system.exists() else None,
                str(mic),
                str(slides) if slides.is_dir() else None,
                started,
            ))
        return captures

    @staticmethod
    def set_aside_capture(system_path, mic_path):
        """Rename capture files that could not be encoded, so start-up stops retrying them.

        Returns the new paths; the audio stays for the user to inspect, and so does the
        live journal with its subtitles and title.
        """
        kept = []
        journal = journal_for_capture(mic_path)
        for path in (mic_path, system_path, str(journal) if journal.exists() else None):
            if path is None:
                continue
            source = Path(path)
            target = source.with_name("Unrecovered" + source.name.removeprefix("_rec"))
            try:
                source.rename(target)
                kept.append(str(target))
            except OSError:
                kept.append(str(source))
        return kept

    def delete(self, recording_path):
        """Delete the recording, its side files and slides, and its folder once it is empty.

        Files a person put into the folder stay, and so does the folder then.
        """
        recording = Path(recording_path)
        if not recording.parent.exists():
            return
        folder = self._folder(recording)
        job = TranscriptionJob(recording)
        with job.locked():
            recording.unlink(missing_ok=True)
            for sidecar in sidecars(recording):
                _remove(sidecar)
            legacy = recording.with_name(recording.stem + LEGACY_SUFFIX)
            if legacy.is_dir():
                shutil.rmtree(legacy)
            for path in snapshot_files(folder) if folder is not None else ():
                path.unlink()
        job.discard_lock()
        if folder is not None:
            with suppress(OSError):
                folder.rmdir()

    def rename(self, recording_path, new_name):
        recording = Path(recording_path)
        if self._folder(recording) is None:
            return self._transfer(recording, recording.with_name(f"{new_name}.m4a"))
        return self._transfer(recording, recording.parent.parent / new_name / f"{new_name}.m4a")

    def move(self, recording_path, destination):
        """Move the recording's folder, or a recording without a folder, into destination."""
        recording = Path(recording_path)
        if self._folder(recording) is None:
            return self._transfer(recording, Path(destination) / recording.name)
        return self._transfer(
            recording, Path(destination) / recording.parent.name / recording.name)

    def migrate(self):
        """Move each recording saved directly in the output folder into its own folder.

        "Recording 2026-11-04 10-00-00 1a2b3c4d" becomes "2026-11-04 10-00-00"; other names
        stay. Returns the recordings that could not be moved, as (path, error); they stay
        where they are, keep working, and are tried again at the next start.
        """
        root = Path(self.output_dir)
        if not root.is_dir():
            return []
        failed = []
        for audio in sorted(root.glob("*.m4a")):
            if audio.name.startswith("."):
                continue
            match = LEGACY_NAME.match(audio.stem)
            name = _free_name(root, match.group(1) if match else audio.stem)
            try:
                self._transfer(audio, root / name / f"{name}.m4a")
            except Exception as error:
                log.warning("could not move %s into its folder: %s", audio, error)
                failed.append((str(audio), str(error)))
        return failed

    def _parts(self, recording, target):
        """(source, destination) of every file that moves with the recording."""
        into_folder = target.parent.name == target.stem
        parts = [(recording, target)] + [
            (sidecar, target.with_name(target.stem + _suffix_of(recording, sidecar)))
            for sidecar in sidecars(recording)
        ]
        legacy = recording.with_name(recording.stem + LEGACY_SUFFIX)
        if legacy.is_dir() and into_folder:
            parts += [
                (path, target.parent / (SLIDES_INDEX if path.name == LEGACY_INDEX else path.name))
                for path in snapshot_files(legacy, LEGACY_INDEX)
            ]
        elif legacy.is_dir():
            parts.append((legacy, target.with_name(target.stem + LEGACY_SUFFIX)))
        folder = self._folder(recording)
        if folder is not None and into_folder:
            parts += [(path, target.parent / path.name) for path in snapshot_files(folder)]
        elif folder is not None:
            raise ValueError(f"A recording in its own folder moves only into a folder: {target}")
        return parts

    def _transfer(self, recording, target):
        if recording.absolute() == target.absolute():
            return str(recording)
        # The target folder is new, so a move never mixes two recordings. It must exist
        # before the target's lock file can be opened inside it.
        created = None
        if target.parent.name == target.stem:
            if os.path.lexists(target.parent):
                raise FileExistsError(f"Folder already exists: {target.parent}")
            target.parent.mkdir(parents=True)
            created = target.parent
        try:
            self._move_parts(recording, target)
        except BaseException:
            if created is not None:
                TranscriptionJob(target).discard_lock()
                with suppress(OSError):
                    created.rmdir()
            raise
        for path in (recording, target):
            TranscriptionJob(path).discard_lock()
        # The old folder still holds a lock file until here; files a person added keep it.
        for emptied in (recording.with_name(recording.stem + LEGACY_SUFFIX),
                        self._folder(recording)):
            if emptied is not None and emptied.is_dir():
                with suppress(OSError):
                    emptied.rmdir()
        return str(target)

    def _move_parts(self, recording, target):
        # A CLI transcription can outlive the GUI's in-memory busy-path set. Lock
        # both names until audio and all recovery state have moved together.
        with ExitStack() as locks:
            locks.enter_context(TranscriptionJob(recording).locked())
            locks.enter_context(TranscriptionJob(target).locked())
            pairs = self._parts(recording, target)
            for _, destination in pairs:
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(f"File already exists: {destination}")
            moved = []
            try:
                for source, destination in pairs:
                    shutil.move(str(source), str(destination))
                    moved.append((source, destination))
            except Exception as error:
                # Restore completed moves before allowing a retry on either name.
                failures = []
                for source, destination in reversed(moved):
                    try:
                        if source.exists() or source.is_symlink():
                            raise FileExistsError(source)
                        shutil.move(str(destination), str(source))
                    except OSError:
                        failures.append(str(destination))
                if failures:
                    raise RuntimeError(
                        f"Move failed; files still need recovery: {', '.join(failures)}") from error
                raise
