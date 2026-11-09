"""What a running recording knows beyond its audio, kept on disk beside the capture files.

The live subtitles, the live speaker lines and the meeting title exist only in memory until
the M4A is saved. The window writes them to `_rec_live_<id>.json` while it records, so
start-up recovery after a crash or a sign-out restores them with the audio. The file goes
away when the recording's sidecars are written.
"""

import json
import logging
import os
from pathlib import Path

from audiorecorder.recordings.naming import recording_name

log = logging.getLogger(__name__)

PREFIX = "_rec_live_"
MIC_PREFIX = "_rec_mic_"


def journal_path(directory, stamp):
    return Path(directory) / f"{PREFIX}{stamp}.json"


def journal_for_capture(mic_path):
    """The journal of the capture whose microphone file is mic_path."""
    mic = Path(mic_path)
    return journal_path(mic.parent, mic.stem.removeprefix(MIC_PREFIX))


def encode(title, subtitles, speakers, *, name=None):
    """The journal text; subtitles and speakers are the payloads the sidecar writers take."""
    return json.dumps(
        {"title": title, "subtitles": subtitles, "speakers": speakers, "name": name},
        ensure_ascii=False)


def save(path, text):
    """Replace the journal at once, so a crash leaves the old or the new one, never half."""
    path = Path(path)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def load(path):
    """(title, subtitles, speakers, custom name); old journals have no custom name."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "", None, None, None
    except (OSError, ValueError):
        log.exception("the live journal %s could not be read", path)
        return "", None, None, None
    if not isinstance(data, dict):
        return "", None, None, None
    title = data.get("title")
    try:
        name = recording_name(data.get("name"))
    except ValueError:
        name = None
    return (
        title if isinstance(title, str) else "",
        _payload(data.get("subtitles")),
        _payload(data.get("speakers")),
        name,
    )


def _payload(value):
    # Both payloads are [lines, extra]: subtitles carry the target language, speakers the
    # name map. Lines come back as lists, which the sidecar writers unpack like tuples.
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], list) and value[0]:
        return value[0], value[1]
    return None


def remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("the live journal %s could not be removed", path)
