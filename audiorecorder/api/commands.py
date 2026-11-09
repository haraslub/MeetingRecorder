"""Commands of the local API. They run on the Qt main thread, against the main window.

A command never opens a dialog: the person at the desk did not ask for it, and a modal
dialog would hold the request until somebody clicks. Every refusal is an ApiError instead.
"""

import os
import time
from pathlib import Path

from PyQt6.QtCore import QTimer

from audiorecorder import secrets
from audiorecorder.api.server import ApiError
from audiorecorder.config import save_config
from audiorecorder.recordings.manager import SLIDES, RecordingManager
from audiorecorder.recordings.naming import display_name
from audiorecorder.slides.store import INDEX as SLIDES_INDEX
from audiorecorder.slides.store import LEGACY_SUFFIX
from audiorecorder.subtitles.speaker_document import resolve_speaker
from audiorecorder.version import __version__

# The settings the API may change, with the values Settings offers for each.
SETTINGS = {
    "language": ("cs", "en", "auto"),
    "translation_target": ("en", "cs"),
    "enable_translation": bool,
    "auto_transcribe": bool,
}
QUIT_DELAY_MS = 200


class RemoteControl:
    def __init__(self, window):
        self._window = window
        self._routes = {
            ("GET", "/v1/status"): self.status,
            ("GET", "/v1/live"): self.live,
            ("POST", "/v1/capture/start"): self.start_capture,
            ("POST", "/v1/capture/stop"): self.stop_capture,
            ("POST", "/v1/capture/retry-stop"): self.retry_stop,
            ("POST", "/v1/capture/mute"): self.mute,
            ("POST", "/v1/capture/toggles"): self.toggles,
            ("GET", "/v1/recordings"): self.recordings,
            ("GET", "/v1/recordings/get"): self.recording,
            ("POST", "/v1/recordings/transcribe"): self.transcribe,
            ("POST", "/v1/recordings/summarize"): self.summarize,
            ("POST", "/v1/recordings/import"): self.import_audio,
            ("POST", "/v1/recordings/rename"): self.rename,
            ("POST", "/v1/recordings/move"): self.move,
            ("POST", "/v1/recordings/delete"): self.delete,
            ("POST", "/v1/dictation"): self.dictation,
            ("GET", "/v1/settings"): self.settings,
            ("POST", "/v1/settings"): self.change_settings,
            ("POST", "/v1/window/show"): self.show_window,
            ("POST", "/v1/app/quit"): self.quit,
        }

    def handle(self, method, path, query, body):
        route = self._routes.get((method, path))
        if route is not None:
            return route(query, body)
        if any(known == path for _, known in self._routes):
            raise ApiError(405, "method_not_allowed", f"{method} is not allowed on {path}.")
        raise ApiError(404, "not_found", f"There is no endpoint {path}.")

    # --- State ---

    def status(self, query, body):
        window = self._window
        controller = window._lifecycle.controller
        capture = window._capture
        elapsed = (time.time() - window._recording_start_time
                   if window._is_recording and window._recording_start_time else None)
        return {
            "version": __version__,
            "capture": {
                "state": controller.state,
                "recording": window._is_recording,
                "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
                "muted": capture.mic_muted if capture is not None else None,
                "meeting_title": window._meeting_title or None,
                "system_source": capture.active_system_source if capture is not None else None,
                "microphone": capture.active_mic_source if capture is not None else None,
                "saving": len(window._active_encoders) + len(window._encoder_queue),
            },
            "toggles": self._toggle_states(),
            "dictation": window._dictation_active,
            "processing": [
                {"recording": self._id(path), "kind": kind}
                for path, kind in window._busy_kinds.items()
            ],
            "busy_reason": window.busy_reason(),
            "output_dir": window.config["output_dir"],
        }

    def live(self, query, body):
        """What live subtitles and speaker labels heard so far in the running recording."""
        window = self._window
        engine = window._subtitle_engine
        subtitles = [
            {"time": stamp, "text": original, "translation": translation or None}
            for stamp, original, translation in (engine.transcript() if engine else ())
        ]
        timed = list(window._speaker_segments)
        for speaker_engine in (window._mic_speaker_engine, window._remote_speaker_engine):
            if speaker_engine is not None:
                timed.extend(speaker_engine.timed_speaker_transcript())
        timed.sort(key=lambda line: line[0])
        speakers = [
            {"time": stamp, "speaker": resolve_speaker(speaker, window._speaker_name_map),
             "text": text}
            for _, stamp, speaker, text in timed
        ]
        return {"recording": window._is_recording, "subtitles": subtitles, "speakers": speakers}

    # --- Capture ---

    def start_capture(self, query, body):
        problem = self._window.start_recording()
        if problem == "dictation":
            raise ApiError(409, "dictation_active", "Turn dictation off before recording.")
        if problem == "busy":
            raise ApiError(409, "cannot_start", self._cannot_start_reason())
        if problem is not None:
            raise ValueError(f"unknown start problem: {problem}")
        return {"state": self._window._lifecycle.controller.state}

    def _cannot_start_reason(self):
        window = self._window
        controller = window._lifecycle.controller
        if controller.closing:
            return "The application is closing."
        if controller.state not in ("idle", "start-pending"):
            return f"A recording is already {controller.state}."
        return "Three recordings are still being saved; wait until one finishes."

    def stop_capture(self, query, body):
        controller = self._window._lifecycle.controller
        if controller.state == "capture-unresolved":
            raise ApiError(409, "capture_unresolved",
                           "The last stop did not finish; call /v1/capture/retry-stop.")
        if controller.state not in ("starting", "recording", "end-grace", "stop-pending"):
            raise ApiError(409, "not_recording",
                           f"Nothing to stop, the state is {controller.state}.")
        self._window._stop_recording()
        return {"state": controller.state}

    def retry_stop(self, query, body):
        controller = self._window._lifecycle.controller
        if controller.state != "capture-unresolved":
            raise ApiError(409, "nothing_to_retry", f"The state is {controller.state}.")
        self._window._retry_capture_stop()
        return {"state": controller.state}

    def mute(self, query, body):
        muted = _bool(body, "muted")
        window = self._window
        if window._capture is None:
            raise ApiError(409, "not_recording", "The microphone mutes only during a recording.")
        if window._capture.mic_muted != muted:
            window._toggle_mute()
        return {"muted": window._capture.mic_muted}

    def toggles(self, query, body):
        window = self._window
        buttons = {
            "subtitles": (window._btn_rts, window._toggle_subtitles),
            "live_speakers": (window._btn_live_speakers, window._toggle_live_speakers),
            "slides": (window._btn_slides, window._toggle_slides),
        }
        unknown = set(body) - set(buttons)
        if unknown or not body:
            raise ApiError(400, "bad_request",
                           f"Give one or more of {', '.join(buttons)} as true or false.")
        wanted = {name: _bool(body, name) for name in body}
        for name, value in wanted.items():
            button, toggle = buttons[name]
            if value and not button.isEnabled():
                raise ApiError(409, "unavailable", f"{name}: {button.toolTip()}")
        for name, value in wanted.items():
            button, toggle = buttons[name]
            if button.isChecked() != value:
                button.setChecked(value)
                toggle()
        return {"toggles": self._toggle_states()}

    def _toggle_states(self):
        window = self._window
        return {
            "subtitles": window._btn_rts.isChecked(),
            "live_speakers": window._btn_live_speakers.isChecked(),
            "slides": window._btn_slides.isChecked(),
            "slides_available": window._btn_slides.isEnabled(),
        }

    # --- Recordings ---

    def recordings(self, query, body):
        limit = query.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except ValueError:
            raise ApiError(400, "bad_request", "limit must be a whole number.") from None
        found = self._scan()
        return {"recordings": [self._describe(recording) for recording in found[:limit]]}

    def recording(self, query, body):
        return {"recording": self._describe(self._find(query.get("recording")))}

    def transcribe(self, query, body):
        recording = self._find(body.get("recording"))
        problem = self._window.queue_transcription(recording.path)
        if problem == "no-api-key":
            raise ApiError(409, "no_api_key", "No Soniox API key is configured in Settings.")
        if problem is not None:
            self._refuse_processing(problem)
        return {"recording": self._describe(recording)}

    def summarize(self, query, body):
        recording = self._find(body.get("recording"))
        overwrite = _bool(body, "overwrite", default=False)
        problem = self._window.queue_summary(recording.path, overwrite=overwrite)
        if problem == "no-transcript":
            raise ApiError(409, "no_transcript", "Transcribe the recording first.")
        if problem == "summary-exists":
            raise ApiError(409, "summary_exists",
                           "A summary exists already; send overwrite: true to replace it.")
        if problem is not None:
            self._refuse_processing(problem)
        return {"recording": self._describe(recording)}

    @staticmethod
    def _refuse_processing(problem):
        if problem == "busy":
            raise ApiError(409, "busy", "This recording is already being processed.")
        if problem == "closing":
            raise ApiError(409, "closing", "The application is closing.")
        raise ValueError(f"unknown processing problem: {problem}")

    def import_audio(self, query, body):
        source = _text(body, "path")
        if not os.path.isabs(source) or not os.path.isfile(source):
            raise ApiError(400, "bad_request", f"No file at the absolute path {source}.")
        try:
            path = self._window.import_audio(
                source, overwrite=_bool(body, "overwrite", default=False))
        except FileExistsError:
            raise ApiError(409, "exists", "The file was added before; send overwrite: true "
                                          "to replace it.") from None
        # The library lists M4A files only, so another format is copied but not listed.
        listed = [recording for recording in self._scan() if recording.path == path]
        return {"path": path, "recording": self._describe(listed[0]) if listed else None}

    def rename(self, query, body):
        recording = self._find(body.get("recording"))
        try:
            name = display_name(body.get("name"))
        except ValueError as error:
            raise ApiError(400, "bad_request", str(error)) from None
        path = self._transfer(lambda: self._window.rename_recording(recording.path, name))
        return {"recording": self._describe(self._find(path))}

    def move(self, query, body):
        recording = self._find(body.get("recording"))
        destination = _text(body, "destination")
        if not os.path.isabs(destination) or not os.path.isdir(destination):
            raise ApiError(400, "bad_request",
                           f"The destination must be an existing absolute folder: {destination}")
        self._refuse_busy(recording)
        path = self._transfer(lambda: self._window.move_recording(recording.path, destination))
        # Outside the output folder the library no longer lists it, so only its path is known.
        return {"path": path}

    def delete(self, query, body):
        recording = self._find(body.get("recording"))
        self._refuse_busy(recording)
        self._transfer(lambda: self._window.delete_recording(recording.path))
        return {"deleted": recording.path}

    def _refuse_busy(self, recording):
        if self._window.recording_busy(recording.path) is not None:
            raise ApiError(409, "busy", "This recording is being processed; wait until it ends.")

    @staticmethod
    def _transfer(operation):
        try:
            return operation()
        except FileExistsError as error:
            raise ApiError(409, "exists", str(error)) from None
        except (OSError, ValueError, RuntimeError) as error:
            raise ApiError(500, "file_error", str(error)) from None

    def _scan(self):
        return RecordingManager(self._window.config["output_dir"]).scan()

    def _find(self, reference):
        """The recording a client names: "latest", its id, its name or its absolute path."""
        if not isinstance(reference, str) or not reference.strip():
            raise ApiError(400, "bad_request", "Name the recording: latest, its id or its path.")
        found = self._scan()
        if reference == "latest":
            if not found:
                raise ApiError(404, "no_recordings", "There are no recordings yet.")
            return found[0]
        wanted = os.path.normcase(os.path.abspath(reference)) if os.path.isabs(reference) else None
        matches = [
            recording for recording in found
            if self._id(recording.path) == reference
            or (wanted is not None and os.path.normcase(os.path.abspath(recording.path)) == wanted)
        ] or [recording for recording in found if recording.name == reference]
        if not matches:
            raise ApiError(404, "unknown_recording", f"No recording {reference!r}.")
        if len(matches) > 1:
            raise ApiError(409, "ambiguous",
                           f"Several recordings are named {reference!r}; use the id.")
        return matches[0]

    def _id(self, path):
        """The path relative to the output folder, with forward slashes."""
        try:
            return Path(path).relative_to(self._window.config["output_dir"]).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def _describe(self, recording):
        stem = os.path.splitext(recording.path)[0]
        outputs = set(recording.outputs)

        def output(suffix):
            return stem + suffix if suffix in outputs else None

        slides = None
        if SLIDES in outputs:
            slides = (os.path.join(recording.folder, SLIDES_INDEX) if recording.folder
                      else stem + LEGACY_SUFFIX)
        failure = self._window.recording_failure(recording.path)
        return {
            "id": self._id(recording.path),
            "name": recording.name,
            "path": recording.path,
            "folder": recording.folder or None,
            "date": recording.date.isoformat(timespec="seconds"),
            "duration_seconds": round(recording.duration_seconds, 1),
            "transcript": output(".md"),
            "summary": output(".summary.md"),
            "live_transcript": output(".live.md"),
            "speakers": output(".speakers.md"),
            "slides": slides,
            "transcription_status": recording.transcription_status or None,
            "processing": self._window.recording_busy(recording.path),
            "failure": {"kind": failure[0], "message": str(failure[1])} if failure else None,
        }

    # --- Dictation, settings, application ---

    def dictation(self, query, body):
        window = self._window
        if _bool(body, "active"):
            problem = window.start_dictation()
            if problem is not None:
                raise ApiError(409, "dictation_unavailable", f"{problem[0]}: {problem[1]}")
        elif window._dictation_active:
            window._stop_dictation()
        return {"dictation": window._dictation_active}

    def settings(self, query, body):
        config = self._window.config
        return {
            "settings": {key: config.get(key) for key in SETTINGS},
            "allowed": {key: "boolean" if allowed is bool else list(allowed)
                        for key, allowed in SETTINGS.items()},
            "soniox_key_configured": bool(secrets.get_api_key()),
        }

    def change_settings(self, query, body):
        unknown = set(body) - set(SETTINGS)
        if unknown or not body:
            raise ApiError(400, "bad_request",
                           f"Only these settings can be changed: {', '.join(SETTINGS)}.")
        for key, value in body.items():
            allowed = SETTINGS[key]
            if allowed is bool:
                _bool(body, key)
            elif value not in allowed:
                raise ApiError(400, "bad_request", f"{key} must be one of {', '.join(allowed)}.")
        window = self._window
        window.config.update(body)
        save_config(window.config)
        window._update_cost_strip()
        return self.settings(query, {})

    def show_window(self, query, body):
        window = self._window
        window.showNormal()
        window.raise_()
        window.activateWindow()
        return {}

    def quit(self, query, body):
        state = self._window._lifecycle.controller.state
        if state != "idle":
            raise ApiError(409, "recording", f"Stop the recording first; the state is {state}.")
        # After the answer is on its way: quitting stops this server.
        QTimer.singleShot(QUIT_DELAY_MS, self._window._quit_from_tray)
        return {}


def _bool(body, key, default=None):
    value = body.get(key, default)
    if type(value) is not bool:
        raise ApiError(400, "bad_request", f"{key} must be true or false.")
    return value


def _text(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "bad_request", f"{key} must be a non-empty string.")
    return value
