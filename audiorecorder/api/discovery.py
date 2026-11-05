"""The record that tells a local client where the API listens and how to start the application.

`api.json` sits beside the settings. The client in skills/meeting-recorder/mrctl.py reads it,
so an agent on this computer needs no token handed to it; the "Allow the local API" switch
in Settings is what grants access. The record stays after the application quits, so the
client can start it again, and goes away when the API is switched off.
"""

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

from audiorecorder.config import CONFIG_DIR
from audiorecorder.version import __version__

RECORD_PATH = CONFIG_DIR / "api.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def new_token():
    return secrets.token_urlsafe(32)


def launch_command(platform_name=None, executable=None, frozen=None):
    """(argv, cwd) that start this copy of the application from any directory."""
    platform_name = sys.platform if platform_name is None else platform_name
    executable = Path(sys.executable if executable is None else executable)
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen and platform_name == "darwin" and executable.parents[2].suffix == ".app":
        # Through Launch Services, so the bundle keeps its identity for notifications.
        return ["open", "-a", str(executable.parents[2])], None
    if frozen:
        return [str(executable)], None
    if platform_name == "win32":
        # pythonw.exe, or a console window would stay open beside the application.
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            executable = windowless
    return [str(executable), "-m", "audiorecorder"], str(PROJECT_ROOT)


def write_record(port, token, path=RECORD_PATH):
    command, cwd = launch_command()
    record = {
        "url": f"http://127.0.0.1:{port}",
        "token": token,
        "pid": os.getpid(),
        "version": __version__,
        "launch": {"command": command, "cwd": cwd},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates the file readable by its owner only, which the token needs on Unix.
    fd, name = tempfile.mkstemp(prefix=".api-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def remove_record(path=RECORD_PATH):
    path.unlink(missing_ok=True)
