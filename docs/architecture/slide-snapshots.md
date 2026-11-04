# Slide snapshots

## Decision

During a recorded call, Meeting Recorder keeps still snapshots of what was shown, not a video.
Every 5 seconds it grabs the browser window whose active tab is a Google Meet or Teams web
meeting, also when another window covers it. A selector decides during the call what to keep:
new slides or shared screens always, a view of only the people once when such a phase starts and
again when the people change. The snapshots are saved in the recording's folder
([recording folders](recording-folders.md)). Version 1 runs on Windows only.

- **Meeting window, not full screen.** The first version captured a monitor only while one
  window filled it. A meeting in a normal browser window never does, so the owner replaced that
  trigger on 2026-11-04. Teams desktop and Zoom are not covered; the owner does not use them.
- **Covered windows through `PrintWindow`.** Chosen over visible-only capture (`mss`), which
  misses the meeting while another window is in front, and over `windows-capture` (Windows
  Graphics Capture), which imports OpenCV (113 MB unpacked) at import time.
- **Live, local selection.** Chosen over capturing every sample and filtering after the call,
  and over AI classification after the call: the snapshots never leave the computer, and private
  frames do not wait on disk for an end filter.
- **Folder and index first.** Inserting the snapshots into the transcript and the AI summary at
  their times is follow-up work.

## Behavior and entry points

**Toggle and card.** `ui/main_window.py` adds a checkable **Slides** header button beside
Translate and Speakers. It has `toggle="local"`, so it turns blue (`accent*` tokens), and it is
not part of the cost strip, because it uses no paid service. Its state is the config key
`slide_snapshots`, `False` by default and remembered. At recording start, when a screen source
exists and the audio backend has a `capture_stamp`, `_slide_folder` is created for that stamp,
and `_start_slides` starts capture when the toggle is on. Switching the toggle on during a
recording starts capture at once; switching it off stops capture and keeps the saved snapshots.
The worker reports its state through the queued `_slide_status` signal, and
`StateCard.set_slides` shows it under the recording line:

| Worker state | Card line |
|---|---|
| `waiting` | `Slides: waiting for a Meet or Teams window`, with `, N saved` once a snapshot exists |
| `capturing` | `Slides: watching the meeting, N saved` |
| `stopped` | `Slides stopped: <reason>`, also shown in the status bar |
| switched off by the user | `Slides: off, saved snapshots are kept` |

**Meeting window** (`slides/windows_screen.py`). `_Win32` lists top-level windows front to back
(`GetTopWindow`, `GetWindow`) through ctypes. It reads the title only of windows of the class
`Chrome_WidgetWin_1`, which Chrome, Brave and Edge share; a browser window's title is the title of
its active tab. The pure function `meeting_window` keeps visible, not minimized, not DWM-cloaked
browser windows outside Meeting Recorder's own process whose title starts with `Meet - `,
`Meet: ` or the en-dash form, or contains `| Microsoft Teams`. The `Google Meet` text alone is
not a rule, because a Gmail tab such as "Google Meet invitation" would match. Of several matches
the foreground window wins, else the front-most. Titles stay on this computer.

**Grab.** `_Win32.render` asks the window to draw itself into a memory bitmap with
`PrintWindow(PW_RENDERFULLCONTENT)` and copies it with `GetDIBits` as top-down BGRA; the source
returns BGR at the window's physical size. The window draws itself, so another window in front of
it never appears in the snapshot, and the meeting is captured while it is covered. A minimized
window is not drawn and reports `waiting`. On the owner's PC a 2576 x 1416 Chromium window took
36 to 55 ms. `create_slide_source()` returns this source on Windows, else `None` and the reason
that the disabled toggle shows as its tooltip.

**Sampling** (`slides/capture.py`). `SlideCapture` runs one daemon thread that samples every
`SlideTuning.sample_seconds` (5 s by default). Without a target it reports `waiting`. A failed grab skips the sample;
`MAX_GRAB_FAILURES` (5) failures in a row, a failed write or any other error stop the worker with
a reason. Every error ends in a status, never in an exception, and the audio capture is never
touched. A state is reported only when it changes, so the UI is not woken at every sample.

**Selection** (`slides/selector.py`). `cells()` reduces a frame to a 27 x 48 grid of gray means
and spreads (the pixel deviation inside each cell). It reads 8 x 8 pixels per cell at fixed
positions, so a 4K frame costs about 250 KB of copying and any frame size works.
`SlideSelector.offer` keeps a per-cell activity average of cells that changed by more than 6 gray
levels (weight 0.35, so a camera tile that changes in two samples is volatile). Volatile cells
(camera tiles, video, a cursor) are left out of every comparison. A sample is considered only
when at most 2 % of the stable cells changed since the previous sample and the whole grid is not
blank (gray deviation of at least 2.0). Then:

- **Content.** When stable cells with a spread above 12 cover at least 12 % of the grid, the
  sample is a slide or a shared screen. It is kept when at least 4 % of the stable cells differ
  from every content snapshot already kept, and it ends the current people phase.
- **People.** Any other sample is a view of the people. It is kept when a people phase starts
  (at the start of the recording or after content) and again only when at least 15 % of the
  cells changed their layout against the last kept people view: a tile became or stopped being
  volatile, or a stable cell changed. Somebody joining or leaving changes the tile layout.

A kept snapshot gets the time of the first sample of its settled pair, so content has to stay
for about 5 to 10 seconds. A slide with very little text can count as a people view; it is then
kept when it changes the layout enough.

**Tuning.** The sample interval and the five thresholds above are `SlideTuning` fields with the
defaults named here: `sample_seconds` 5, `changed_level` 6, `settle_percent` 2,
`new_content_percent` 4, `content_area_percent` 12, `new_people_percent` 15. They are saved as
`slide_tuning` in the settings; `SlideTuning.from_config` replaces a missing or out-of-range
value (`RANGES`) with its default. The owner asked on 2026-11-04 to change them in Settings,
also during a recording, because typing into a shared chart changes fewer cells than 4 % of the
window. The **Slides** tab of the Settings dialog shows one spin box per field, with an (i)
button whose wrapped tooltip explains the value on hover or click. On OK,
`MainWindow._open_settings` passes a new `SlideTuning` to `SlideCapture.set_tuning`, which
replaces the selector's tuning object; the worker reads it once per sample, so a change applies
from the next sample without a lock. Cells that changed in two samples in a row stay out of the
comparison until they are still again for about two samples; that weight is not a setting.

**Storage** (`slides/store.py`). `SlideFolder` writes into `_rec_slides_<stamp>/` in the output
folder, created with the first kept snapshot. Each snapshot is written at once as JPEG quality 90
at native resolution, named `NNNN HH-MM-SS.jpg` (order, time from recording start). JPEG because
PNG of photographic slide content is several times larger, and quality 90 keeps text legible.
After the M4A is published, `MainWindow._write_pending_sidecars` calls `finalize_folder`: it
moves the snapshots into the recording's folder, writes `slides.md` there (one heading per
snapshot with its time, and the image) through a temporary file and `os.replace`, and removes
the temporary folder. A folder without snapshots is removed. A recording cancelled for lack of
audio discards the folder.

**Library** (`recordings/manager.py`, `ui/recording_list.py`). `snapshot_files` names the files
slides own in a folder: `NNNN HH-MM-SS.jpg` and `slides.md`. The row shows a Slides badge
(`SLIDES`) when `slides.md` exists, and it opens the folder; Move and rename carry the snapshots
with the recording, Delete removes them. The first version's `<recording>.slides/` with
`index.md` is migrated into the folder at start-up.

**Recovery.** The WASAPI and PortAudio backends expose the uuid stamp of their temporary WAVs as
`capture_stamp`, and the slides folder uses the same stamp. `RecordingManager.orphaned_captures`
returns `(system, mic, slides, started)` and pairs `_rec_slides_<stamp>/` with
`_rec_mic_<stamp>.wav`. `_recover_orphaned_captures` puts the folder into `_pending_sidecars`, and
`_on_recovery_finished` finalizes it after the recovered M4A is published. After a failed live
encode the WAVs keep their `_rec_` names, so the next start recovers audio and snapshots
together. A failed recovery encode renames the WAVs to `Unrecovered_*` and leaves the folder as
`_rec_slides_<stamp>/` beside them; start-up does not retry it. A slides folder without WAVs is
left as it is, without a log line.

## Constraints and trade-offs

- **Meet and Teams web in a browser only.** The meeting must be the active tab of a Chrome,
  Brave or Edge window that is not minimized. A user who reads Teams chat in the meeting tab
  during the call gets chat snapshots as content. The title rules may need an update when Meet
  or Teams change their tab titles.
- **Snapshots show the whole browser window,** including its tabs and address bar, but never
  another window. Hardware video overlays or protected content can draw black; such samples are
  dropped as blank.
- **Local only.** Snapshots are never uploaded; transcription sends only the audio.
- **Windows only.** On macOS the ScreenCaptureKit stream is audio-only and owned by the audio
  backend, and the native macOS path has no `capture_stamp`; Linux X11 needs Xlib window state.
  On both the toggle is disabled with the tooltip "Slides are available on Windows only for now".
  macOS capture needs its own stream, a changed `NSScreenCaptureUsageDescription` and a tester.
- **One screen source, no platform interface.** `SlideCapture` takes any object with
  `find_target()`, `grab()` and `close()`; the main window builds the Windows source or none, and
  tests pass a fake.
- **Off and on again in one recording** starts a new selector on the same folder: numbering
  continues, but a slide kept before the pause can be kept a second time.
- **No limit per recording.** Every kept frame is distinct stable content, and 5-second sampling
  bounds the rate. There is no "snap now" button and no preview on the card.
- **Same process as the audio.** The selector reads a few thousand cells per sample; a separate
  process is not built unless real calls show audio dropouts.
- An existing `slides.md` or snapshot of the same name in the recording's folder is never
  replaced: nothing moves, the error is logged and the snapshots stay in `_rec_slides_<stamp>/`.
- Recovery scans only the current output directory, as for the audio
  ([session end and recovery](session-end-recovery.md)).

## Verification

`tests/test_slide_selector.py` covers a camera grid, an avatar grid, a participant joining,
slides beside a camera strip, a people phase after slides, a black window, a settling slide, and
the cell means and spreads. `tests/test_windows_screen.py` covers the title and window rules
with fixtures on every OS and one real render of the taskbar window on Windows.
`tests/test_slide_capture.py` runs the worker against a fake source, including
grab and write failures and a prompt stop; `tests/test_slide_store.py` covers naming, the index,
the move into the recording folder, the empty folder, an existing `slides.md` and discard. `tests/test_slides_toggle.py`
exercises the toggle before and during a recording, the card line, finalizing after encode,
discarding without audio, the disabled toggle and the narrow header. `tests/test_manager.py` and `tests/test_live_transcript.py`
and `tests/test_recording_folders.py` cover the snapshots in the folder and the migration of
`.slides` folders; `tests/test_session_recovery.py` covers pairing by stamp and
recovery. A real Meet call in a normal browser window, a covered meeting window and a killed process are
checks in `docs/manual-test-checklist.md`.
