"""The local API: its listener, its commands against the real window, and the mrctl client."""

import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import test_rts_toggle as rts
from PyQt6.QtWidgets import QApplication

from audiorecorder.api import commands as api_commands
from audiorecorder.api import discovery
from audiorecorder.api.commands import RemoteControl
from audiorecorder.api.server import ApiError, ApiServer
from audiorecorder.api.skill import SKILL_NAME, SKILL_SOURCE, install_skill
from audiorecorder.config import DEFAULTS, validated_config

# The real window with fake capture and engines, shared with the subtitle toggle tests.
app = rts.app
window = rts.window
TOKEN = "t" * 43


def until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        QApplication.processEvents()
        time.sleep(0.01)


def in_thread(function):
    """Run a blocking client while this thread keeps the Qt event loop turning."""
    result = {}

    def run():
        try:
            result["value"] = function()
        except BaseException as error:
            result["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    until(lambda: not thread.is_alive(), timeout=20)
    if "error" in result:
        raise result["error"]
    return result["value"]


# --- Settings ---

def test_the_api_is_off_by_default_and_invalid_values_fall_back():
    assert DEFAULTS["api_enabled"] is False
    config = validated_config({"api_port": 80, "api_token": "short"})
    assert config["api_port"] == DEFAULTS["api_port"]
    assert config["api_token"] is None
    token = discovery.new_token()
    assert validated_config({"api_port": 50000, "api_token": token})["api_token"] == token


# --- Listener ---

@pytest.fixture
def server(app, tmp_path):
    calls = []

    def handle(method, path, query, body):
        calls.append((method, path, query, body))
        if path == "/v1/missing":
            raise ApiError(404, "not_found", "nothing here")
        return {"echo": body}

    built = ApiServer(handle, record_path=tmp_path / "api.json")
    built.calls = calls
    built.configure({"api_enabled": True, "api_port": 0, "api_token": TOKEN})
    yield built
    built.close()


def request(server, path, body=None, token=TOKEN, host=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if host:
        headers["Host"] = host
    call = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", data=data,
                                  headers=headers, method="POST" if data else "GET")

    def send():
        try:
            with urllib.request.urlopen(call, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    return in_thread(send)


def test_a_request_with_the_token_reaches_the_handler_on_the_main_thread(server, tmp_path):
    status, payload = request(server, "/v1/status?x=1", {"a": 1})
    assert (status, payload) == (200, {"ok": True, "echo": {"a": 1}})
    assert server.calls == [("POST", "/v1/status", {"x": "1"}, {"a": 1})]
    record = json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))
    assert record["url"] == f"http://127.0.0.1:{server.port}"
    assert record["token"] == TOKEN
    assert record["launch"]["command"]


def test_a_wrong_token_or_a_foreign_host_is_refused_before_the_handler(server):
    assert request(server, "/v1/status", token="wrong")[0] == 401
    assert request(server, "/v1/status", host="evil.example:1234")[1]["code"] == "forbidden_host"
    assert server.calls == []


def test_a_handler_refusal_keeps_its_status_and_code(server):
    status, payload = request(server, "/v1/missing")
    assert status == 404
    assert payload == {"ok": False, "code": "not_found", "message": "nothing here"}


def test_a_body_that_is_not_an_object_is_refused(server):
    assert request(server, "/v1/status", [1, 2])[1]["code"] == "bad_request"


def test_switching_off_closes_the_port_and_removes_the_record(server, tmp_path):
    port = server.port
    server.configure({"api_enabled": False})
    assert server.port is None
    assert not (tmp_path / "api.json").exists()
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=2)


def test_quitting_keeps_the_record_so_a_client_can_start_the_application(server, tmp_path):
    server.close()
    assert (tmp_path / "api.json").exists()


def test_a_disabled_start_leaves_an_existing_record_alone(app, tmp_path):
    (tmp_path / "api.json").write_text("{}")
    ApiServer(lambda *args: {}, record_path=tmp_path / "api.json").configure({})
    assert (tmp_path / "api.json").exists()


def test_launch_commands_for_each_kind_of_build(tmp_path):
    exe = tmp_path / "MeetingRecorder.app" / "Contents" / "MacOS" / "MeetingRecorder"
    assert discovery.launch_command("darwin", exe, frozen=True) == (
        ["open", "-a", str(tmp_path / "MeetingRecorder.app")], None)
    assert discovery.launch_command("win32", "C:/MR/MeetingRecorder.exe", frozen=True) == (
        [str(Path("C:/MR/MeetingRecorder.exe"))], None)
    python = tmp_path / "python"
    command, cwd = discovery.launch_command("linux", python, frozen=False)
    assert command == [str(python), "-m", "audiorecorder"]
    assert Path(cwd, "audiorecorder").is_dir()


# --- Commands ---

@pytest.fixture
def control(window, tmp_path, monkeypatch):
    # Never the real one: it writes the user's live configuration file.
    monkeypatch.setattr(api_commands, "save_config", lambda config: None)
    # Every real backend has it (audio/backend.py); status reports it.
    monkeypatch.setattr(rts.FakeCapture, "mic_muted", False, raising=False)
    w = window(rts_translate=False)
    remote = RemoteControl(w)
    remote.window = w
    remote.call = lambda method, path, body=None, query=None: remote.handle(
        method, path, query or {}, body or {})
    return remote


def refused(call, *args):
    with pytest.raises(ApiError) as caught:
        call(*args)
    return caught.value.status, caught.value.code


def add_recording(output_dir, name, transcript=False, summary=False):
    folder = Path(output_dir) / name
    folder.mkdir()
    audio = folder / f"{name}.m4a"
    audio.write_bytes(b"not really audio")
    if transcript:
        (folder / f"{name}.md").write_text("# transcript", encoding="utf-8")
    if summary:
        (folder / f"{name}.summary.md").write_text("# summary", encoding="utf-8")
    return audio


def test_status_describes_an_idle_application(control):
    status = control.call("GET", "/v1/status")
    assert status["capture"]["state"] == "idle"
    assert status["capture"]["recording"] is False
    assert status["dictation"] is False
    assert status["toggles"]["subtitles"] is False
    assert status["busy_reason"] is None


def test_start_mute_and_stop_drive_the_recording(control):
    assert control.call("POST", "/v1/capture/start")["state"] == "recording"
    assert refused(control.call, "POST", "/v1/capture/start") == (409, "cannot_start")
    assert control.call("POST", "/v1/capture/mute", {"muted": True}) == {"muted": True}
    status = control.call("GET", "/v1/status")["capture"]
    assert status["recording"] and status["muted"] and status["elapsed_seconds"] >= 0
    assert refused(control.call, "POST", "/v1/app/quit") == (409, "recording")
    assert control.call("POST", "/v1/capture/stop")["state"] == "idle"
    assert control.window.captures[0].stopped


def test_nothing_to_stop_or_mute_while_idle(control):
    assert refused(control.call, "POST", "/v1/capture/stop") == (409, "not_recording")
    assert refused(control.call, "POST", "/v1/capture/mute", {"muted": True}) == (
        409, "not_recording")
    assert refused(control.call, "POST", "/v1/capture/retry-stop") == (409, "nothing_to_retry")


def test_dictation_blocks_a_recording(control):
    control.window._dictation_active = True
    assert refused(control.call, "POST", "/v1/capture/start") == (409, "dictation_active")
    control.window._dictation_active = False


def test_toggles_change_the_buttons_and_the_settings(control):
    result = control.call("POST", "/v1/capture/toggles", {"subtitles": True})
    assert result["toggles"]["subtitles"] is True
    assert control.window.config["rts_translate"] is True
    assert refused(control.call, "POST", "/v1/capture/toggles", {"other": True})[0] == 400
    assert refused(control.call, "POST", "/v1/capture/toggles", {"subtitles": "yes"})[0] == 400


def test_recordings_are_found_by_latest_id_name_or_path(control):
    output = control.window.config["output_dir"]
    audio = add_recording(output, "2026-11-05 10-00-00 - Weekly", transcript=True)
    listed = control.call("GET", "/v1/recordings")["recordings"]
    assert [item["id"] for item in listed] == [
        "2026-11-05 10-00-00 - Weekly/2026-11-05 10-00-00 - Weekly.m4a"]
    assert listed[0]["transcript"] == str(audio.with_suffix(".md"))
    assert listed[0]["summary"] is None
    for reference in ("latest", listed[0]["id"], "2026-11-05 10-00-00 - Weekly", str(audio)):
        found = control.call("GET", "/v1/recordings/get", query={"recording": reference})
        assert found["recording"]["path"] == str(audio)
    assert refused(control.call, "GET", "/v1/recordings/get", None, {"recording": "nope"}) == (
        404, "unknown_recording")


def test_rename_move_and_delete_change_the_files(control, tmp_path_factory):
    output = control.window.config["output_dir"]
    audio = add_recording(output, "2026-11-05 10-00-00", transcript=True)
    renamed = control.call("POST", "/v1/recordings/rename",
                           {"recording": "latest", "name": "Client call"})
    assert renamed["recording"]["id"] == "Client call/Client call.m4a"
    assert not audio.exists()
    assert refused(control.call, "POST", "/v1/recordings/rename",
                   {"recording": "latest", "name": "a/b"}) == (400, "bad_request")

    archive = tmp_path_factory.mktemp("archive")
    moved = control.call("POST", "/v1/recordings/move",
                         {"recording": "latest", "destination": str(archive)})
    assert Path(moved["path"]) == archive / "Client call" / "Client call.m4a"
    assert (archive / "Client call" / "Client call.md").exists()

    add_recording(output, "Old")
    control.call("POST", "/v1/recordings/delete", {"recording": "Old"})
    assert not (Path(output) / "Old").exists()


def test_a_busy_recording_is_neither_processed_nor_changed(control):
    audio = add_recording(control.window.config["output_dir"], "Busy", transcript=True)
    control.window._busy_paths.add(str(audio))
    control.window._busy_kinds[str(audio)] = "transcribe"
    assert control.call("GET", "/v1/recordings/get",
                        query={"recording": "Busy"})["recording"]["processing"] == "transcribe"
    for path in ("/v1/recordings/transcribe", "/v1/recordings/delete",
                 "/v1/recordings/summarize"):
        assert refused(control.call, "POST", path, {"recording": "Busy"}) == (409, "busy")
    control.window._busy_paths.clear()
    control.window._busy_kinds.clear()


@pytest.mark.parametrize("name", ["", "../escape", ".hidden", "CON", "NUL.txt", "LPT1"])
def test_api_rename_uses_the_shared_filename_rules(control, name):
    audio = add_recording(control.window.config["output_dir"], "Original")
    assert refused(control.call, "POST", "/v1/recordings/rename",
                   {"recording": "Original", "name": name}) == (400, "bad_request")
    assert audio.exists()


def test_transcription_and_summary_explain_what_is_missing(control, monkeypatch):
    add_recording(control.window.config["output_dir"], "Plain")
    monkeypatch.setattr(rts.mw.secrets, "get_api_key", lambda: None)
    assert refused(control.call, "POST", "/v1/recordings/transcribe", {"recording": "Plain"}) == (
        409, "no_api_key")
    assert refused(control.call, "POST", "/v1/recordings/summarize", {"recording": "Plain"}) == (
        409, "no_transcript")
    add_recording(control.window.config["output_dir"], "Done", transcript=True, summary=True)
    assert refused(control.call, "POST", "/v1/recordings/summarize", {"recording": "Done"}) == (
        409, "summary_exists")


def test_import_copies_a_file_into_the_library_once(control, tmp_path_factory):
    source = tmp_path_factory.mktemp("downloads") / "interview.m4a"
    source.write_bytes(b"audio")
    imported = control.call("POST", "/v1/recordings/import", {"path": str(source)})
    assert imported["recording"]["id"] == "interview/interview.m4a"
    assert refused(control.call, "POST", "/v1/recordings/import", {"path": str(source)}) == (
        409, "exists")
    relative = {"path": "relative.m4a"}
    assert refused(control.call, "POST", "/v1/recordings/import", relative)[0] == 400


def test_settings_change_only_the_offered_values(control):
    result = control.call("POST", "/v1/settings", {"language": "cs", "auto_transcribe": True})
    assert result["settings"]["language"] == "cs"
    assert control.window.config["auto_transcribe"] is True
    assert refused(control.call, "POST", "/v1/settings", {"language": "de"})[0] == 400
    assert refused(control.call, "POST", "/v1/settings", {"api_enabled": False})[0] == 400


def test_unknown_paths_and_methods_are_told_apart(control):
    assert refused(control.call, "GET", "/v1/nothing") == (404, "not_found")
    assert refused(control.call, "GET", "/v1/capture/start") == (405, "method_not_allowed")


# --- Client ---

def load_client():
    spec = importlib.util.spec_from_file_location("mrctl", SKILL_SOURCE / "mrctl.py")
    module = importlib.util.module_from_spec(spec)
    # No __pycache__ in the skill folder: the build bundles and the installer copies it.
    written, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = written
    return module


def run_client(argv, capsys):
    code = in_thread(lambda: load_client().main(argv))
    return code, json.loads(capsys.readouterr().out)


def test_the_client_explains_that_the_api_is_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEETING_RECORDER_API_FILE", str(tmp_path / "missing.json"))
    code, output = run_client(["status"], capsys)
    assert code == 4
    assert output["code"] == "api_off"


def test_the_client_reads_the_record_and_reports_refusals(server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEETING_RECORDER_API_FILE", str(tmp_path / "api.json"))
    monkeypatch.delenv("MEETING_RECORDER_TOKEN", raising=False)
    code, output = run_client(["rename", "latest", "New name"], capsys)
    assert code == 0
    assert output["echo"] == {"recording": "latest", "name": "New name"}

    monkeypatch.setenv("MEETING_RECORDER_TOKEN", "wrong")
    code, output = run_client(["status"], capsys)
    assert (code, output["code"]) == (3, "unauthorized")


def test_the_client_reports_an_application_that_is_not_running(tmp_path, monkeypatch, capsys):
    record = tmp_path / "api.json"
    record.write_text(json.dumps({"url": "http://127.0.0.1:9", "token": TOKEN}))
    monkeypatch.setenv("MEETING_RECORDER_API_FILE", str(record))
    code, output = run_client(["status"], capsys)
    assert (code, output["code"]) == (4, "not_running")


# --- Skill ---

def test_the_bundled_skill_names_itself_and_its_client():
    text = (SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith(f"---\nname: {SKILL_NAME}\n")
    assert "mrctl.py" in text
    commands = load_client().parser()._subparsers._group_actions[0].choices
    for command in commands:
        assert f"`{command}" in text, f"SKILL.md does not document {command}"


def test_install_copies_updates_and_leaves_foreign_folders_alone(tmp_path):
    claude = tmp_path / "claude" / SKILL_NAME
    foreign = tmp_path / "codex" / SKILL_NAME
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: other\n---\n", encoding="utf-8")
    targets = {"Claude Code": claude, "Codex": foreign}
    assert [status for _, _, status in install_skill(targets=targets)] == [
        "installed", "refused"]
    assert (claude / "mrctl.py").is_file()
    assert install_skill(targets={"Claude Code": claude})[0][2] == "updated"


def test_install_leaves_a_linked_development_skill_alone(tmp_path):
    link = tmp_path / SKILL_NAME
    try:
        os.symlink(SKILL_SOURCE, link, target_is_directory=True)
    except OSError:
        pytest.skip("creating a link needs a privilege here")
    assert install_skill(targets={"Claude Code": link})[0][2] == "linked"


def test_live_lines_are_empty_without_a_live_function(control):
    assert control.call("GET", "/v1/live") == {
        "recording": False, "subtitles": [], "speakers": []}


def test_dictation_turns_on_and_off_and_blocks_while_recording(control):
    assert control.call("POST", "/v1/dictation", {"active": True}) == {"dictation": True}
    assert control.call("POST", "/v1/dictation", {"active": False}) == {"dictation": False}
    control.call("POST", "/v1/capture/start")
    assert refused(control.call, "POST", "/v1/dictation", {"active": True}) == (
        409, "dictation_unavailable")


def test_the_client_drives_the_real_window_through_the_listener(
        control, tmp_path_factory, monkeypatch, capsys):
    record = tmp_path_factory.mktemp("config") / "api.json"
    listener = ApiServer(control.handle, record_path=record)
    listener.configure({"api_enabled": True, "api_port": 0, "api_token": TOKEN})
    monkeypatch.setenv("MEETING_RECORDER_API_FILE", str(record))
    monkeypatch.delenv("MEETING_RECORDER_TOKEN", raising=False)
    try:
        code, output = run_client(["start", "--wait", "--timeout", "10"], capsys)
        assert (code, output["capture"]["state"]) == (0, "recording")
        code, output = run_client(["stop", "--wait", "--timeout", "10"], capsys)
        # The fake capture records no audio, so nothing new is saved.
        assert (code, output["saved"]) == (0, [])
        assert output["status"]["capture"]["state"] == "idle"
    finally:
        listener.close()
