"""A short local log of what the call watch saw change, to explain a call that was missed.

One line per change: whether a call is present, which programs hold the microphone, whether
a meeting window is on screen, and what the recorder did. No window titles. The file stays
on this computer and is cut back to its newer half when it grows past MAX_BYTES.
"""

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024


class CallLog:
    def __init__(self, path):
        self.path = Path(path)
        self._last = None

    def record(self, present, microphone, meeting_shown, action):
        programs = "unknown" if microphone is None else ",".join(microphone.programs) or "none"
        state = (present, programs, meeting_shown, action)
        if state == self._last:
            return
        self._last = state
        line = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}  call={'yes' if present else 'no'}"
            f"  microphone={programs}  meeting-window={'yes' if meeting_shown else 'no'}"
            + (f"  action={action}" if action else "")
        )
        try:
            self._append(line)
        except OSError:
            log.exception("the call detection log could not be written")

    def _append(self, line):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
            text = self.path.read_text(encoding="utf-8")
            kept = text[len(text) // 2:]
            self.path.write_text(kept[kept.find("\n") + 1:], encoding="utf-8")
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(line + "\n")
