#!/usr/bin/env python3
"""Command-line client of the Meeting Recorder local API.

Standard library only, so any Python 3.9+ runs it without the application's environment.
It reads the API address and token from the api.json the application writes beside its
settings, and prints one JSON document per command.

Exit codes: 0 done, 2 usage, 3 refused by the application, 4 not reachable, 5 wait timed out.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LAUNCH_SECONDS = 60
POLL_SECONDS = 1.0
CAPTURE_ACTIVE = ("starting", "recording", "end-grace", "stop-pending", "stopping")


class Failure(Exception):
    def __init__(self, exit_code, payload):
        super().__init__(payload.get("message"))
        self.exit_code = exit_code
        self.payload = payload


def record_path():
    if os.environ.get("MEETING_RECORDER_API_FILE"):
        return Path(os.environ["MEETING_RECORDER_API_FILE"])
    # The settings folder of the application ("AudioRecorder" is its original name).
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "AudioRecorder" / "api.json"


def load_record():
    path = record_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Failure(4, {
            "ok": False, "code": "api_off",
            "message": f"{path} does not exist. In Meeting Recorder, open Settings > AI agents "
                       "and turn on 'Allow the local API'."}) from None
    except (OSError, ValueError) as error:
        raise Failure(4, {"ok": False, "code": "api_off",
                          "message": f"{path} cannot be read: {error}"}) from None
    if os.environ.get("MEETING_RECORDER_TOKEN"):
        record["token"] = os.environ["MEETING_RECORDER_TOKEN"]
    return record


class Client:
    def __init__(self, record):
        self.record = record

    def call(self, method, path, body=None, query=None):
        url = self.record["url"] + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.record['token']}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except ValueError:
                payload = {"ok": False, "code": f"http_{error.code}", "message": error.reason}
            raise Failure(3, payload) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
            raise Failure(4, {
                "ok": False, "code": "not_running",
                "message": f"Meeting Recorder does not answer at {self.record['url']}: "
                           f"{getattr(error, 'reason', error)}. Start it, or pass --launch."
            }) from None

    def running(self):
        try:
            self.call("GET", "/v1/status")
            return True
        except Failure as failure:
            if failure.payload.get("code") == "not_running":
                return False
            raise

    def launch(self):
        """Start the application when it is not running, and wait until its API answers."""
        if self.running():
            return False
        launch = self.record.get("launch") or {}
        command = launch.get("command")
        if not command:
            raise Failure(4, {"ok": False, "code": "cannot_launch",
                              "message": "api.json does not say how to start the application."})
        options = {"cwd": launch.get("cwd"), "stdin": subprocess.DEVNULL,
                   "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            options["creationflags"] = (subprocess.DETACHED_PROCESS
                                        | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            options["start_new_session"] = True
        subprocess.Popen(command, **options)
        deadline = time.monotonic() + LAUNCH_SECONDS
        while time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
            # A new start may have created a new token, so read the record again.
            self.record = load_record()
            if self.running():
                return True
        raise Failure(4, {"ok": False, "code": "launch_timeout",
                          "message": f"The application did not answer within {LAUNCH_SECONDS} s."})


def wait_until(check, timeout, what):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            raise Failure(5, {"ok": False, "code": "wait_timeout",
                              "message": f"Still waiting for {what} after {timeout} s."})
        time.sleep(POLL_SECONDS)


def on_off(value):
    if value in ("on", "true", "1", "yes"):
        return True
    if value in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("use on or off")


def setting_value(text):
    if "=" not in text:
        raise argparse.ArgumentTypeError("use key=value")
    key, value = text.split("=", 1)
    if value in ("true", "false"):
        return key, value == "true"
    return key, value


def wait_for_job(client, recording, kind, output, timeout):
    """Wait until the recording's job ends; the recording, or Failure when the job failed."""
    def done():
        current = client.call("GET", "/v1/recordings/get", query={"recording": recording})
        described = current["recording"]
        return None if described["processing"] else described
    described = wait_until(done, timeout, f"the {kind} of {recording}")
    failure = described.get("failure")
    if failure and failure["kind"] == kind:
        raise Failure(3, {"ok": False, "code": f"{kind}_failed", "message": failure["message"],
                          "recording": described})
    if not described.get(output):
        raise Failure(3, {"ok": False, "code": f"{kind}_failed",
                          "message": "The job ended without its output.", "recording": described})
    return {"ok": True, "recording": described}


def run(args):
    client = Client(load_record())
    command = args.command
    if command == "launch":
        started = client.launch()
        return {"ok": True, "started": started, **client.call("GET", "/v1/status")}
    if getattr(args, "launch", False):
        client.launch()
    if command == "status":
        return client.call("GET", "/v1/status")
    if command == "start":
        result = client.call("POST", "/v1/capture/start")
        if args.wait:
            def recording():
                status = client.call("GET", "/v1/status")
                state = status["capture"]["state"]
                if state == "recording":
                    return status
                if state not in CAPTURE_ACTIVE:
                    raise Failure(3, {"ok": False, "code": "start_failed",
                                      "message": f"The recording did not start; state {state}."})
                return None
            return wait_until(recording, args.timeout, "the recording to start")
        return result
    if command == "stop":
        before = {item["id"] for item in client.call("GET", "/v1/recordings")["recordings"]}
        result = client.call("POST", "/v1/capture/stop")
        if not args.wait:
            return result

        def saved():
            status = client.call("GET", "/v1/status")
            capture = status["capture"]
            if capture["state"] == "capture-unresolved":
                raise Failure(3, {"ok": False, "code": "capture_unresolved",
                                  "message": "Stopping did not finish; run retry-stop."})
            if capture["state"] != "idle" or capture["saving"]:
                return None
            return status
        status = wait_until(saved, args.timeout, "the recording to be saved")
        new = [item for item in client.call("GET", "/v1/recordings")["recordings"]
               if item["id"] not in before]
        return {"ok": True, "saved": new, "status": status}
    if command == "retry-stop":
        return client.call("POST", "/v1/capture/retry-stop")
    if command in ("mute", "unmute"):
        return client.call("POST", "/v1/capture/mute", {"muted": command == "mute"})
    if command == "toggles":
        body = {key: value for key, value in (
            ("subtitles", args.subtitles), ("live_speakers", args.live_speakers),
            ("slides", args.slides)) if value is not None}
        if not body:
            return client.call("GET", "/v1/status")["toggles"] | {"ok": True}
        return client.call("POST", "/v1/capture/toggles", body)
    if command == "live":
        return client.call("GET", "/v1/live")
    if command == "recordings":
        return client.call("GET", "/v1/recordings", query={"limit": args.limit})
    if command == "recording":
        return client.call("GET", "/v1/recordings/get", query={"recording": args.recording})
    if command == "transcribe":
        result = client.call("POST", "/v1/recordings/transcribe", {"recording": args.recording})
        if args.wait:
            return wait_for_job(client, result["recording"]["id"], "transcribe", "transcript",
                                args.timeout)
        return result
    if command == "summarize":
        result = client.call("POST", "/v1/recordings/summarize",
                             {"recording": args.recording, "overwrite": args.overwrite})
        if args.wait:
            return wait_for_job(client, result["recording"]["id"], "cleanup", "summary",
                                args.timeout)
        return result
    if command == "import":
        return client.call("POST", "/v1/recordings/import",
                           {"path": os.path.abspath(args.file), "overwrite": args.overwrite})
    if command == "rename":
        return client.call("POST", "/v1/recordings/rename",
                           {"recording": args.recording, "name": args.name})
    if command == "move":
        return client.call("POST", "/v1/recordings/move", {
            "recording": args.recording, "destination": os.path.abspath(args.destination)})
    if command == "delete":
        return client.call("POST", "/v1/recordings/delete", {"recording": args.recording})
    if command == "dictation":
        return client.call("POST", "/v1/dictation", {"active": args.state})
    if command == "settings":
        if args.set:
            return client.call("POST", "/v1/settings", dict(args.set))
        return client.call("GET", "/v1/settings")
    if command == "show":
        return client.call("POST", "/v1/window/show")
    if command == "quit":
        return client.call("POST", "/v1/app/quit")
    raise ValueError(f"unknown command: {command}")


def parser():
    root = argparse.ArgumentParser(prog="mrctl", description="Control Meeting Recorder.")
    commands = root.add_subparsers(dest="command", required=True)

    def waitable(command, until):
        command.add_argument("--wait", action="store_true", help=until)
        command.add_argument("--timeout", type=float, default=600,
                             help="seconds the wait may take (default 600)")

    def add(name, help_text, launch=False):
        command = commands.add_parser(name, help=help_text)
        if launch:
            command.add_argument("--launch", action="store_true",
                                 help="start the application first when it is not running")
        return command

    add("launch", "start the application when it is not running")
    add("status", "state of the application", launch=True)
    waitable(add("start", "start recording", launch=True), "until the capture runs")
    waitable(add("stop", "stop recording"), "until the recording is saved; prints it")
    add("retry-stop", "retry a stop that did not finish")
    add("mute", "mute the microphone of the running recording")
    add("unmute", "unmute the microphone")
    toggles = add("toggles", "live functions of recordings")
    toggles.add_argument("--subtitles", type=on_off, help="on or off (billed realtime minutes)")
    toggles.add_argument("--live-speakers", type=on_off, help="on or off (billed)")
    toggles.add_argument("--slides", type=on_off, help="on or off (local snapshots)")
    add("live", "live subtitles and speaker lines of the running recording")
    add("recordings", "the library, newest first").add_argument("--limit", type=int, default=20)
    for name, help_text in (("recording", "one recording with its files"),
                            ("transcribe", "transcribe with Soniox (billed)"),
                            ("summarize", "clean up the transcript and summarize it"),
                            ("delete", "delete the recording and all its files"),
                            ("rename", "rename the recording, its files and folder"),
                            ("move", "move the recording into another folder")):
        command = add(name, help_text, launch=name in ("recording", "transcribe", "summarize"))
        command.add_argument("recording", help="latest, the id, the name or the path")
        if name in ("transcribe", "summarize"):
            waitable(command, "until the output exists")
        if name == "summarize":
            command.add_argument("--overwrite", action="store_true")
        if name == "rename":
            command.add_argument("name")
        if name == "move":
            command.add_argument("destination")
    imported = add("import", "copy an audio file into the library")
    imported.add_argument("file")
    imported.add_argument("--overwrite", action="store_true")
    add("dictation", "push-to-talk dictation").add_argument("state", type=on_off)
    add("settings", "transcription settings").add_argument(
        "--set", type=setting_value, action="append", metavar="KEY=VALUE")
    add("show", "bring the window forward")
    add("quit", "quit the application")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result, code = run(args), 0
    except Failure as failure:
        result, code = failure.payload, failure.exit_code
    # Recording names carry any language; a Windows console code page cannot print them all.
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
