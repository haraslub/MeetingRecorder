# Call end prompt and automatic transcription

## Decision

A recording that is not stopped after the call goes on until somebody notices. When the meeting
of a running recording disappears, Meeting Recorder asks once whether to stop and save it. It
never stops by itself on this signal. The owner asked for this on 2026-11-04 and chose the
meeting window as the only signal, not silence in the system audio: a long pause in a call
would ask too, and the window signal is exact for the Meet and Teams web calls the owner uses.

A separate setting, off by default, starts **Transcribe** for every recording when it is saved,
so a call is transcribed without a visit to the app. It does not start **Clean up with AI**;
the owner chose transcription only.

- **Changed on 2026-11-06 for Windows.** The window title missed the end of Teams web calls:
  a Teams tab keeps `| Microsoft Teams` in its title after the call. On Windows the call is
  now present while a meeting app or a meeting browser holds the microphone, and with
  `auto_stop_recording` on the recording stops and saves without asking. Without microphone
  records (macOS) this prompt works as described here. See
  [automatic call recording](automatic-call-recording.md).
- **Not the native lifecycle.** The automatic stop of `meetings/lifecycle.py` acts only on
  recordings it started itself, after qualified native evidence on macOS. The prompt here works
  for any recording, manual ones included, and only asks.

## Behavior and entry points

**Watch** (`meetings/call_end.py`). `CallEndWatch.observe(present, now)` returns `"ask"` when a
meeting seen during the recording has been absent for `GRACE_SECONDS` (30 s), `"back"` when it
returns while the question is open, and `""` otherwise. A recording that never saw a meeting
never asks. After an answer of Keep recording it asks again only when the meeting returns and
disappears again.

**Evidence** (`ui/main_window.py`, `_watch_call`). The same 5-second timer that samples the
meeting title for the folder name (`_call_timer`) calls `_watch_call`. The meeting counts as
present when a browser window whose active tab is Meet or Teams exists, also minimized,
hidden or on another virtual desktop (`windows_screen.meeting_title`, Windows), or, with
meeting detection on, when a detection candidate other than a calendar event exists (Browser
Agent, native macOS windows). A calendar
event names the folder but is no evidence that the call still runs. The watch starts with the
capture and ends in `_begin_stop`, which also closes an open prompt.

**Prompt** (`ui/call_end_prompt.py`). `CallEndPrompt` is a non-modal window that stays on top:
"The call seems to have ended", **Keep recording** and **Stop and save**. Stop and save calls
`_stop_recording`, the same path as the Stop button. The status bar shows the same text for
10 s. A meeting window that comes back closes the prompt.

**Automatic transcription.** `config["auto_transcribe"]` (Settings → General, "Transcribe each
recording with Soniox when it is saved (paid)"). `_on_encode_finished` and
`_on_recovery_finished` call `_transcribe_when_saved`, which queues
`_transcribe_recording(path, automatic=True)`. An automatic request reports a missing Soniox
key or a busy recording in the status bar only, never in a dialog.

## Constraints and trade-offs

- **Meet's left-meeting page.** The prompt relies on the window title. When the Meet tab still
  shows `Meet - <code>` on the "You left the meeting" page, the end is seen only when the tab
  is closed or navigates away. Not verified on a real call yet.
- Teams desktop, Zoom and other apps without detection give no evidence on Windows, so their
  recordings never ask.
- A meeting tab that is not the active tab of its window counts as gone, because a window's
  title is the title of its active tab; the prompt then asks after 30 s and closes again when
  the tab is shown. A minimized meeting window still counts as present.

## Verification

`tests/test_call_end.py` covers the watch (no meeting, grace, one question per loss, return),
the prompt in the main window (asks when the window closes, Stop and save, closes when the
window returns or the recording stops) and automatic transcription (only with the setting, a
missing key only in the status bar). `tests/test_settings_meeting_detection.py` covers the
setting. A real Meet and Teams web call is a check in `docs/manual-test-checklist.md`.
