"""Display-only recording metadata. Changing a title never changes an audio path."""

import json
import logging
import os
import tempfile
from pathlib import Path

from audiorecorder.recordings.naming import display_name

SUFFIX = ".recording.json"
log = logging.getLogger(__name__)


def metadata_path(audio):
    return Path(audio).with_suffix(SUFFIX)


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Recording metadata must be a JSON object: {path}")
    return data


def get_display_name(audio):
    """A valid saved title, or the existing filename for older/damaged metadata."""
    path = metadata_path(audio)
    try:
        value = _read(path).get("display_name")
        return display_name(value) if value is not None else Path(audio).stem
    except (OSError, ValueError):
        log.warning("Recording title could not be read from %s", path)
        return Path(audio).stem


def set_display_name(audio, name):
    """Atomically update only display_name, preserving other metadata or failed writes."""
    name = display_name(name)
    audio = Path(audio)
    if not audio.is_file():
        raise FileNotFoundError(audio)
    path = metadata_path(audio)
    data = _read(path)  # Corrupt metadata must be reviewed, never silently replaced.
    data["display_name"] = name
    fd, temporary = tempfile.mkstemp(prefix=".recording-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(audio)
