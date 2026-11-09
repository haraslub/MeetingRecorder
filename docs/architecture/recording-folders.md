# Recording folders

## Decision

Each recording has its own folder in the output folder, named by its start time and, when the
recorder saw it, the meeting subject: `2026-11-04 10-00-00 - Weekly sync`, or
`2026-11-04 10-00-00` without a subject. The folder holds the M4A and everything made from it,
with the same name as the folder: transcript `.md`, AI summary `.summary.md`, live subtitles
`.live.md`, speaker files `.speakers.md` and `.speakers.json`, and the slide snapshots with
their `slides.md` index. Move to folder moves the whole folder. The owner asked for this on
2026-11-04, so that a call is one unit to find, move and archive.

- **The name comes from the meeting title only.** Meeting Recorder cannot read the participant
  list of Meet or Teams; that would need reading the call page. Teams web titles carry the
  meeting subject. A Meet tab shows only the meeting code (`abc-defg-hij`), which is not used,
  so a Meet call gets a time-only name unless the meeting prompt (Browser Agent, calendar) had
  a title.
- **Old recordings are migrated.** Chosen over listing both layouts forever: one layout keeps
  move and delete simple. A recording the migration cannot move stays where it is and keeps
  working, so the list, move and delete still read a recording directly in the output folder.
- **Snapshots sit directly in the folder,** not in a `slides\` subfolder, as the owner chose.

- **The default output folder is `~/MeetingRecorder`,** named after the product; it was
  `~/AudioRecordings`. At start, `config.move_legacy_output_dir` renames `~/AudioRecordings` to
  `~/MeetingRecorder` when the settings still point to the old default and the new folder does
  not exist, and updates the settings and the saved audio test. A folder the user chose stays.
  When the rename fails (a window has the folder open), the old folder stays in use and the
  next start tries again.

## Behavior and entry points

**Naming** (`recordings/naming.py`). `meeting_title(raw)` takes a window or tab title and
returns the subject or `""`: it strips the browser suffix (` - Google Chrome`, Edge, Brave),
`| Microsoft Teams` and an unread count `(2)`, refuses Teams sections (`Chat`, `Activity`, ...),
Meet codes and generic texts (`Active Microsoft Teams call`, `Scheduled meeting`), replaces
characters Windows refuses in names, cuts at 60 characters on a word and drops trailing dots
and spaces. `folder_name(started, title)` joins the start time and the title.

**Title while recording** (`ui/main_window.py`). `_watch_call` runs when capture starts,
every 5 seconds while it runs (`_call_timer`) and once at stop, until a title is found. It also
notices the end of the call ([call end prompt](call-end-prompt.md)). It reads the titles of the
meeting candidates the lifecycle adapter last polled (native detection, Browser Agent,
calendar) and, on Windows, the title of a Meet or Teams browser window, also a minimized one
(`WindowsScreenSource.meeting_title`). The first usable subject
wins. The title stays on this computer.

**New recording** (`recordings/manager.py`). At stop, `new_recording_path(output_dir, started,
title)` creates the folder and returns `<folder>/<folder name>.m4a`; a taken name gets ` (2)`,
` (3)`. The encoder publishes the M4A there, then `_write_pending_sidecars` writes the live
files beside it and `finalize_folder` moves the snapshots into the folder. When encoding fails,
`remove_empty_folder` removes the empty folder; the WAVs stay for recovery. A recovered capture
is named by the creation time of its microphone WAV, the start of the recording.
**Add** copies an audio file into a new folder named after the file.

**Library.** `RecordingManager.scan` lists `<output>/<name>/<name>.m4a` and any M4A directly in
the output folder. `Recording.folder` is the recording's folder, or `""` for a recording
directly in the output folder. The Slides badge (`SLIDES`) shows when `slides.md` exists and
opens the folder. The row menu **⋯** has **Open folder** and **Copy folder name**; Copy is
disabled for a recording without its own folder, and Open then opens the output folder.

**Move, rename, delete.** `_transfer` creates the target folder and refuses one that exists,
so two recordings never mix. It moves the M4A, the sidecars and the snapshot files with the
transcription locks of both paths held, restores what moved when a file fails, then removes the
old folder if it is empty. Files a person added to the folder stay in the old folder. Delete
removes the M4A, the sidecars, the snapshots and `slides.md`, then the folder if it is empty.

**Inline rename.** Click the name in a saved recording's row to edit the full name in place.
Keyboard users can focus the name and press Enter, Space or F2. Enter saves; Escape or moving
focus to another control cancels. Switching applications keeps the draft. An unchanged name
does nothing. The extension is not shown in the
editor. A successful rename updates the folder, audio and side files together and highlights
the updated row. Playback of that recording stops before moving its files.

`ui/recording_name.py` owns editing and inline errors. `RecordingListWidget` forwards the
path and proposed name to `MainWindow.rename_recording`, which checks the processing state
and uses the existing manager transfer. Unchanged rows are reused during library refreshes,
so completion of another recording does not discard the draft, cursor or error message.
Processing disables editing; if processing begins during an edit, the draft is cancelled.
Filesystem failures and conflicts preserve the original files and leave the attempted name
in the editor for correction. A removed or changed row is rebuilt from disk.

Both the UI and API use `recordings.naming.recording_name`. The manager validates again before
any filesystem change. It trims outer spaces and rejects empty names, leading/trailing dots,
path separators, control characters, Windows-invalid characters and reserved device names.
This prevents a renamed recording from disappearing from the library as a hidden file or
escaping its folder. Existing files and folders are never overwritten. A case-only rename
can be refused as an existing folder on a filesystem that ignores case.

**Migration.** `MainWindow.__init__` calls `RecordingManager.migrate` before the first list.
Each M4A directly in the output folder (hidden staging files excluded) moves into its own
folder: `Recording 2026-11-04 10-00-00 1a2b3c4d` becomes `2026-11-04 10-00-00`, other names
keep their name. A `<recording>.slides/` folder of the first slides version moves into the new
folder, with `index.md` renamed to `slides.md`. A recording in use, for example locked by a CLI
transcription, stays and is tried again at the next start; the status bar names the count.

## Constraints and trade-offs

- Recordings were named by their stop time before; now by their start time. A migrated
  folder keeps the time of its old name, the stop time.
- The title is the first one seen. A recording started before the meeting window opened takes
  the title from a later sample.
- A Meet call has a time-only name unless Browser Agent or the calendar named it.
- Migration runs only for the current output folder, at start-up. Changing the output folder
  in Settings lists old recordings there as they are until the next start.
- The transcript inside a migrated folder still has the old name in its heading.

## Verification

`tests/test_recording_folders.py` covers the title rules, folder names, a taken name, scan,
move of a whole folder, a taken target folder, added files in the old folder, rename, delete
with and without added files, and the migration with old slides and a locked recording.
`tests/test_recording_menu.py` covers **Open folder**, **Copy folder name** and the Slides badge.
`tests/test_inline_rename.py` exercises mouse and keyboard editing, cancellation, errors and
correction, busy guards, Unicode names, all side files and drafts across library refreshes.
The folder and API tests also enforce the shared filename rules.
`tests/test_slides_toggle.py` covers a recording named after the Teams window title with its
snapshots in the folder; `tests/test_session_recovery.py` covers the recovered folder.
