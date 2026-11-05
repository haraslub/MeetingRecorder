---
name: meeting-recorder
description: "Control the Meeting Recorder desktop app on this computer through its local API: start or stop recording a call, check its state, transcribe or summarize a recording, read live subtitles, list, rename, move or delete recordings. Triggers: 'start recording the meeting', 'stop the recording and transcribe it', 'what did they say on the call', 'nahraj tenhle hovor', 'zastav nahrávání a přepiš to'."
---

# Meeting Recorder

Meeting Recorder records system audio and the microphone, transcribes with Soniox and keeps each
recording in its own folder. Control it with `mrctl.py`, the client beside this file. It needs only
Python 3.9+ and prints one JSON document per command.

```sh
python <skill-directory>/mrctl.py <command> [options]
```

## Rules

- **Record only when the user asks for it now.** Never start a recording on your own initiative.
  The user is responsible for telling the other participants that the call is recorded.
- **Billed actions only on request:** `transcribe`, `toggles --subtitles on` and
  `toggles --live-speakers on` use paid Soniox minutes. `summarize` runs the local Claude CLI.
- **Irreversible actions only on an explicit request that names the recording:** `delete`, `move`,
  `rename`. Run `recordings` first and act on the `id` it returns, never on a guess.
- **Every wait is bounded.** `--wait` stops after `--timeout` seconds (default 600) with exit 5.
  Do not loop around it; report the state instead.
- **Never print or quote the token.** The client reads it from `api.json`; you do not need it.
- **Read results from disk.** Recording commands return the absolute paths of the audio,
  `transcript`, `summary`, `live_transcript`, `speakers` and `slides`. Read those files directly.

## Commands

| Command | Effect |
|---|---|
| `status [--launch]` | State of the capture (`idle`, `starting`, `recording`, `stopping`, ...), toggles, jobs |
| `launch` | Start the application when it is not running and wait until its API answers |
| `start [--launch] [--wait]` | Start recording; `--wait` until the capture runs |
| `stop [--wait]` | Stop recording; `--wait` until the M4A is saved, then `saved` lists the new recording |
| `retry-stop` | Finish a stop that ended in `capture-unresolved` |
| `mute`, `unmute` | Microphone of the running recording |
| `toggles [--subtitles on\|off] [--live-speakers on\|off] [--slides on\|off]` | Live functions; subtitles and speakers start with the next recording, slides at once |
| `live` | Live subtitle and speaker lines heard so far in the running recording |
| `recordings [--limit N]` | Library, newest first |
| `recording <ref>` | One recording with the paths of its files, `processing` and `failure` |
| `transcribe <ref> [--wait]` | Soniox transcription into `<name>.md` |
| `summarize <ref> [--overwrite] [--wait]` | Cleaned transcript and summary into `<name>.summary.md` |
| `import <file> [--overwrite]` | Copy an audio file into the library |
| `rename <ref> <name>` | Rename the recording, its side files and its folder |
| `move <ref> <folder>` | Move the recording folder elsewhere; it leaves the library |
| `delete <ref>` | Delete the recording, its side files and slides |
| `dictation on\|off` | Push-to-talk dictation mode |
| `settings [--set key=value]...` | `language` (`cs`, `en`, `auto`), `translation_target` (`en`, `cs`), `enable_translation`, `auto_transcribe` |
| `show`, `quit` | Bring the window forward; quit (refused while recording) |

`<ref>` is `latest`, the `id` from `recordings` (the path inside the output folder), the
recording's name, or its absolute path.

## Typical flows

```sh
python mrctl.py start --launch --wait          # record the call the user is in
python mrctl.py stop --wait                     # saved[0] is the new recording
python mrctl.py transcribe latest --wait        # then read recording.transcript
python mrctl.py summarize latest --wait         # then read recording.summary
```

## Failures

Exit codes: `0` done, `2` usage, `3` refused (`{"ok": false, "code", "message"}`), `4` not
reachable, `5` wait timed out.

| Code | Meaning and what to do |
|---|---|
| `api_off` | The user must open Settings > AI agents and turn on "Allow the local API" |
| `not_running` | Start it with `launch`, or add `--launch` |
| `unauthorized` | The token changed; the running app writes the new one, so retry once, then ask the user |
| `dictation_active` | Turn dictation off before recording |
| `cannot_start`, `not_recording` | The capture is in another state; read `status` |
| `capture_unresolved` | Run `retry-stop`; if it fails again, tell the user to check the app window |
| `no_api_key` | The user must enter a Soniox API key in Settings |
| `busy` | A job runs on that recording; wait with `recording <ref>` until `processing` is null |
| `transcribe_failed`, `cleanup_failed` | Report `message` to the user |

## Without Python

The API is plain HTTP on `127.0.0.1`. `api.json` lies in the application's settings folder
(`%LOCALAPPDATA%\AudioRecorder` on Windows, `~/Library/Application Support/AudioRecorder` on
macOS, `~/.config/AudioRecorder` on Linux) and holds `url` and `token`. Every request sends
`Authorization: Bearer <token>`; POST bodies are JSON objects.

| Method and path | Body or query |
|---|---|
| `GET /v1/status`, `GET /v1/live` | |
| `POST /v1/capture/start`, `stop`, `retry-stop` | |
| `POST /v1/capture/mute` | `{"muted": true}` |
| `POST /v1/capture/toggles` | `{"subtitles": true, "live_speakers": false, "slides": true}` |
| `GET /v1/recordings` | `?limit=20` |
| `GET /v1/recordings/get` | `?recording=latest` |
| `POST /v1/recordings/transcribe` | `{"recording": "latest"}` |
| `POST /v1/recordings/summarize` | `{"recording": "latest", "overwrite": false}` |
| `POST /v1/recordings/import` | `{"path": "<absolute file>", "overwrite": false}` |
| `POST /v1/recordings/rename` | `{"recording": "<id>", "name": "<new name>"}` |
| `POST /v1/recordings/move` | `{"recording": "<id>", "destination": "<absolute folder>"}` |
| `POST /v1/recordings/delete` | `{"recording": "<id>"}` |
| `POST /v1/dictation` | `{"active": true}` |
| `GET /v1/settings`, `POST /v1/settings` | `{"language": "cs"}` |
| `POST /v1/window/show`, `POST /v1/app/quit` | |
