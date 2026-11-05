# Local API for agents

## Decision

The running application exposes its controls as JSON over HTTP on `127.0.0.1`, with a bearer
token, and ships an agent skill with a small stdlib-only client. No MCP server: an agent needs
only a shell command, and the application needs no new dependency.

## Behavior

- **Off by default.** `api_enabled` (config) turns the listener on. Settings > AI agents creates
  the token (`secrets.token_urlsafe(32)`) when the API is allowed without one; **New token**
  replaces it. `api_port` defaults to 47321.
- **Discovery.** While the listener runs, `api.json` beside `config.json` holds `url`, `token`,
  `pid`, `version` and `launch` (the command and working directory that start this copy). It
  stays after quit, so the client can start the application, and is removed only when the API
  is switched off in the running application. A start with the API off leaves an existing file
  alone.
- **Access.** Every request needs `Authorization: Bearer <token>` (constant-time comparison) and a
  `Host` of `127.0.0.1` or `localhost`, which stops DNS rebinding from a web page. No CORS
  headers are sent, so a browser preflight fails. Bodies are JSON objects up to 64 KiB.
- **Threading.** `ThreadingHTTPServer` runs on a daemon thread. Each request is handed to the Qt
  main thread through a queued signal; the worker waits up to 15 s for the result and answers
  504 after that.
- **No dialogs.** Each command calls a dialog-free method of `MainWindow` (`start_recording`,
  `queue_transcription`, `queue_summary`, `start_dictation`, `import_audio`,
  `rename_recording`, `move_recording`, `delete_recording`). The UI handlers wrap the same
  methods and show the dialogs. A refusal is `{"ok": false, "code", "message"}` with an HTTP
  status: 400 bad input, 401 token, 403 host, 404 unknown, 409 state, 500 file error.
- **Recording references.** `latest`, the id (path inside the output folder with `/`), the name,
  or the absolute path. Only recordings the library lists can be named, so delete, rename and
  move cannot reach other files. The library lists M4A files only: an imported MP3 is copied
  but answers `recording: null`.
- **Long jobs.** Start, stop, transcription and summary return at once. The client's `--wait`
  polls status once a second up to `--timeout` (default 600 s); `stop --wait` reports the
  recordings that are new after the save.

## Entry points

| File | Role |
|---|---|
| `audiorecorder/api/server.py` | Listener, token and host checks, hand-off to the main thread, `api.json` lifetime |
| `audiorecorder/api/commands.py` | Route table and every command |
| `audiorecorder/api/discovery.py` | `api.json`, launch command, token generation |
| `audiorecorder/api/skill.py` | Copies the skill into `~/.claude/skills` and Codex's `skills` |
| `audiorecorder/ui/settings_dialog.py` | AI agents tab |
| `skills/meeting-recorder/SKILL.md`, `mrctl.py` | The agent skill and its client; bundled by `MeetingRecorder.spec` |
| `tests/test_local_api.py` | Listener, commands against the real window, client, installer |

## Constraints and trade-offs

- **Same-user trust.** Any process of the same user can read `api.json`, as it can read
  `config.json`. The token protects against other users and web pages, not against the user's
  own processes; the "Allow the local API" switch is the consent.
- **Copy, not link.** A frozen build unpacks into a temporary folder, so the installer copies
  the skill and must run again after an update. A linked skill folder is left alone.
- **Settings through the API** are limited to `language`, `translation_target`,
  `enable_translation` and `auto_transcribe`. The API cannot change its own access, the output
  folder, devices or the Soniox key, and never returns the key or the token.
- **Quit** is refused while a recording is not idle; a quit with saves or jobs pending finishes
  them first, as the window's close does.
