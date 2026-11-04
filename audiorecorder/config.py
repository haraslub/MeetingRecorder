"""Application settings.

The settings file lives in the per-user configuration directory of the operating system,
not next to the executable, so an installed build never writes into its own program
directory and two accounts on one machine keep separate settings. The Soniox API key is
not part of this file, it belongs to the keyring (see audiorecorder.secrets).
"""

import json
import logging
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path

import platformdirs

from audiorecorder.slides.selector import SlideTuning

# "AudioRecorder", not the current product name: this decides where an existing
# installation already keeps its settings. Renaming it would strand what is there.
CONFIG_DIR = Path(platformdirs.user_config_dir("AudioRecorder", appauthor=False))
CONFIG_PATH = CONFIG_DIR / "config.json"
# The default output folder before it was named after the product.
LEGACY_OUTPUT_DIR = Path.home() / "AudioRecordings"

DEFAULT_SUMMARY_PROMPT = (
    'Summarize the call transcript in the file "{transcript_name}" and write the summary '
    'to "{summary_name}", in the language of the transcript.'
)

DEFAULTS = {
    "output_dir": str(Path.home() / "MeetingRecorder"),
    # Primary language of the transcript. When translation is on and the recording turns
    # out to be in another language, it is translated into this one.
    "language": "en",
    "enable_translation": False,
    # Target language of the dictation translate hotkey, independent of the setting above.
    "translation_target": "en",
    # Device names, not indices, which are renumbered whenever hardware comes and goes.
    # None means the platform default, an empty string means "microphone only".
    "system_source_name": None,
    "mic_source_name": None,
    "system_volume": 1.0,
    "mic_volume": 1.0,
    "summary_prompt": DEFAULT_SUMMARY_PROMPT,
    # Live translated subtitles during a recording. Off by default: it bills realtime
    # minutes for the whole call, on top of the transcription that follows it.
    "rts_translate": False,
    # Live speaker labels open two additional realtime streams (system + microphone), so
    # they are explicit opt-in just like translated subtitles.
    "live_speakers": False,
    # Slide snapshots grab the Meet or Teams browser window during a recording. A
    # snapshot can show private content, so they are opt-in.
    "slide_snapshots": False,
    # Transcription is billed by Soniox, so starting it for every saved recording is opt-in.
    "auto_transcribe": False,
    # Thresholds of the slide selector, changed in Settings; see slides.selector.SlideTuning.
    "slide_tuning": SlideTuning().to_config(),
    # A local-only signal offers the Record button when a meeting window is stable. It
    # never records automatically, but it opens the loopback browser bridge, so it is
    # opt-in like every other listener.
    "meeting_detection": False,
    # Calendar access is a separate, sensitive permission and remains opt-in.
    "calendar_detection": False,
    # Automatic start is deliberately separate from detection and always has a visible
    # cancelable countdown. Calendar-only signals never qualify.
    "automatic_recording": False,
    # Registering a login item is a persistent OS change, so it is always explicit opt-in.
    "launch_at_login": False,
    "auto_start_delay_seconds": 5,
    "auto_stop_delay_seconds": 5,
    "auto_stop_recording": False,
    "meeting_end_grace_seconds": 45,
    "notification_style": "native",
    "native_call_detection": False,
    "capture_readiness": None,
    "native_call_qualifications": {},
    "native_call_absence_qualifications": {},
}


_DELAY_RANGES = {
    "auto_start_delay_seconds": (5, 120),
    "auto_stop_delay_seconds": (5, 120),
    "meeting_end_grace_seconds": (15, 180),
}


def validated_config(stored):
    """Migrate known values without discarding unrelated user settings."""
    cfg = {**DEFAULTS, **stored}
    for key, default in DEFAULTS.items():
        value = cfg[key]
        if isinstance(default, bool) and type(value) is not bool:
            cfg[key] = default
    for key, (low, high) in _DELAY_RANGES.items():
        value = cfg[key]
        if type(value) is not int or not low <= value <= high:
            cfg[key] = DEFAULTS[key]
    if cfg["notification_style"] not in ("native", "in-app"):
        cfg["notification_style"] = "native"
    for key in ("system_source_name", "mic_source_name"):
        if cfg[key] is not None and not isinstance(cfg[key], str):
            cfg[key] = None
    if not isinstance(cfg["output_dir"], str) or not cfg["output_dir"].strip():
        cfg["output_dir"] = DEFAULTS["output_dir"]
    cfg["slide_tuning"] = SlideTuning.from_config(cfg["slide_tuning"]).to_config()
    from audiorecorder.meetings.native_profiles import PROFILES

    for name in ("native_call_qualifications", "native_call_absence_qualifications"):
        qualifications = cfg.get(name)
        cfg[name] = (
            {
                key: value
                for key, value in qualifications.items()
                if key in PROFILES
                and isinstance(value, str)
                and len(value) == 64
                and all(c in "0123456789abcdef" for c in value)
            }
            if isinstance(qualifications, dict)
            else {}
        )
    test = cfg.get("capture_readiness")
    if (
        not isinstance(test, dict)
        or not all(isinstance(test.get(key), dict) for key in ("config", "frames"))
        or type(test.get("time")) not in (int, float)
        or not math.isfinite(test["time"])
        or not isinstance(test.get("runtime"), str)
        or len(test["runtime"]) != 64
        or not isinstance(test.get("status"), str)
        or not isinstance(test.get("mic"), str)
        or (test.get("system") is not None and not isinstance(test["system"], str))
    ):
        cfg["capture_readiness"] = None
    else:
        cfg["capture_readiness"] = {
            key: test.get(key) for key in ("time", "runtime", "status", "system", "mic")
        }
        cfg["capture_readiness"]["config"] = {
            key: test["config"].get(key)
            for key in ("output_dir", "system_source_name", "mic_source_name")
        }
        cfg["capture_readiness"]["frames"] = {
            key: test["frames"].get(key, 0) if type(test["frames"].get(key, 0)) is int else 0
            for key in ("system", "mic")
        }
    return cfg


def load_config():
    if not CONFIG_PATH.exists():
        return validated_config({})
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("settings root must be an object")
        return validated_config(stored)
    except (ValueError, UnicodeError):
        # Keep the original bytes before a later save can replace the corrupt file.
        backup = CONFIG_PATH.with_name(
            f"config.invalid-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.json"
        )
        backup.write_bytes(CONFIG_PATH.read_bytes())
        logging.getLogger(__name__).warning("Invalid settings preserved at %s", backup)
        return validated_config({})


def _same_path(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def move_legacy_output_dir(cfg, legacy=LEGACY_OUTPUT_DIR, current=None):
    """Move recordings from the old default folder to the new one, once.

    Only an output folder that is still the old default moves; a folder the user chose stays.
    When the new folder already exists, or the old one cannot be renamed (a window has it
    open), nothing moves and the settings keep the old folder. Returns True when the
    settings changed.
    """
    current = Path(current or DEFAULTS["output_dir"])
    if not _same_path(cfg["output_dir"], legacy) or os.path.lexists(current):
        return False
    if os.path.lexists(legacy):
        try:
            os.rename(legacy, current)
        except OSError:
            logging.getLogger(__name__).warning(
                "The output folder %s could not be renamed to %s", legacy, current)
            return False
    cfg["output_dir"] = str(current)
    # The audio test was made in the same folder under its old name; it stays valid.
    readiness = cfg.get("capture_readiness")
    if isinstance(readiness, dict) and isinstance(readiness.get("config"), dict) and _same_path(
            readiness["config"].get("output_dir") or "", legacy):
        readiness["config"]["output_dir"] = str(current)
    return True


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(validated_config(cfg), stream, indent=2, ensure_ascii=False)
            stream.flush()
        os.replace(name, CONFIG_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)
