# MKVCleaner 3.1.0

MKVCleaner is a batch queue to clean selected MKV metadata safely with
MKVToolNix without re-encoding video, audio, or subtitle streams. It uses
mkvpropedit for fast in-place edits whenever possible and mkvmerge only as a
fallback.

Version 3.0 adds a dark graphical interface styled to match Video Optimizer.
The GUI lists all found files and permanent results, current activity and
overall progress, live worker actions, and the run summary. Its Settings window
edits the shared `config.json`.

The build produces two applications:

- `MKVCleaner.exe` — graphical interface without a console window
- `MKVCleaner-cli.exe` — traditional command-line interface

## Default behavior

Removes:

- Segment title
- Global tags
- Track tags
- Attachments

Preserves:

- Track names
- Chapters
- All tracks and codecs
- Language metadata
- Default, forced, and enabled flags

The default worker count is 4.

## Requirements

- Windows
- Python 3.10 or newer for source builds
- MKVToolNix installed, or both `mkvmerge.exe` and `mkvpropedit.exe` placed beside the application

## License

MKVCleaner is shared under the MIT License. See `LICENSE`.

## Build

Open PowerShell in the project directory and run:

```powershell
.\build.bat
```

The executables will be created at:

```text
dist\MKVCleaner.exe
dist\MKVCleaner-cli.exe
```

## Usage

```powershell
MKVCleaner-cli.exe "D:\Movies"
```

Useful options:

```text
--dry-run
--threads 4
--recursive
--keep-backup
--remove-chapters
--remove-attachments
--show-skipped
--mkvmerge "C:\Program Files\MKVToolNix\mkvmerge.exe"
```

If `config.json` does not exist, MKVCleaner creates it automatically beside the executable.

## Progress display

The progress bar remains visible and now also shows the number of active workers and the most recently started file.

## Worker display

The console now keeps the main progress bar visible and shows one persistent status line for each worker:

```text
Processing: 35%|███████▎             | 7/20
Worker 1: Movie One.mkv
Worker 2: Movie Two.mkv
Worker 3: Movie Three.mkv
Worker 4: Movie Four.mkv
```

Worker lines update in place and show `Idle` when no file is assigned. Long filenames are shortened to reduce wrapping.

## Pause and resume

During processing:

- Press `P` to pause between files.
- Files already being processed are allowed to finish safely.
- No new files start while paused.
- Press `R` to resume.

The status line changes from `Running` to `Pausing` and then `Paused` after active workers finish.

## Version 1.4.1 display fix

Fixed a worker-status race where a completed file could change a worker line to `Idle` after that worker had already started another file. The worker ID is now released only after the completion display and log update finish.

## Version 1.5 input selection

`-i` / `--input` is now the primary input option:

```powershell
.\MKVCleaner.exe -i "D:\Movies"
```

When `-i` is omitted, MKVCleaner uses `default_input_directory` from `config.json`:

```json
"default_input_directory": "D:\\Movies"
```

The older positional form remains available for compatibility:

```powershell
.\MKVCleaner.exe "D:\Movies"
```

Command-line `-i` takes priority over the configured default. Do not supply both `-i` and a positional path in the same command.

## v1.5.1 result display

Every processed file now leaves a persistent result line showing `Cleaned`, `Would clean`, `Skipped`, `Dry-run skipped`, or `Failed`. This makes single-file and very fast scans visible even when the transient worker line updates too quickly to notice. In dry-run mode, the summary reports `Would clean` separately from already-clean files that were skipped.


## Input directory

The recommended CLI syntax is:

```powershell
.\MKVCleaner.exe -i "D:\Movies"
```

When no input is supplied, `default_input_directory` from `config.json` is used.

For paths stored inside JSON, forward slashes are recommended:

```json
"default_input_directory": "D:/Movies"
```

Doubled backslashes also work:

```json
"default_input_directory": "D:\\Movies"
```

Do not use a single backslash in a JSON string because sequences such as `\M`
can produce an `Invalid \escape` error.

## Inspection modes

```json
"inspection_mode": "stream"
```

`stream` is the default. Each file is inspected, its planned removals are
printed, and it is immediately submitted to an available worker. This starts
large directory jobs quickly.

```json
"inspection_mode": "full"
```

`full` inspects and lists every file first, then begins cleaning. Use it when
you want to review the complete plan before any file is changed.

## Console display

Version 1.6 uses one shared, periodically refreshed worker-status panel rather
than several stacked tqdm bars. Permanent messages are still printed for:

- queued files and planned removals
- already-clean/skipped files
- dry-run results
- cleaned files
- failures

The live panel shows the main progress bar, running/paused status, inspection
and completion counts, every worker's active filename, and the latest activity.

Press `P` to pause between files and `R` to resume.


## Command-line inspection mode

Valid values are shown in `--help`:

```powershell
.\MKVCleaner.exe --inspection-mode stream
.\MKVCleaner.exe --inspection-mode full
```

- `stream` inspects each file and starts cleaning it immediately.
- `full` inspects every file and shows the complete plan before cleaning begins.

## Show effective configuration

```powershell
.\MKVCleaner.exe --show-config
```

This displays the final configuration, including command-line overrides, and exits.

## Important Windows JSON path note

A path copied from the Windows File Explorer address bar usually looks like:

```text
D:\Movies\Test
```

Do not paste that directly into `config.json`. Change every backslash to a
forward slash:

```json
"default_input_directory": "D:/Movies/Test"
```

Doubled backslashes also work:

```json
"default_input_directory": "D:\\Movies\\Test"
```


## Skipped-file display

Use:

```powershell
.\MKVCleaner.exe --show-skipped
```

Skipped files are shown during processing when possible and are also printed
in a permanent `Skipped files` section after the live worker panel closes.
This prevents panel refreshes from hiding the filename or skip reason.

Example:

```text
Skipped files
-------------
Skipped: D:\Movies\Already Clean.mkv
  - No configured cleanup needed
```


## Console display in 1.7

The console now uses Rich's live rendering system instead of manually moving
the cursor with ANSI escape sequences. This is designed to work correctly in
Windows PowerShell.

The progress and worker table remain together at the bottom while permanent
file records scroll above them.

Every file receives a permanent, numbered inspection block:

```text
FILE 1/3 — WILL CHANGE
File: D:\Movies\Example.mkv
Planned changes:
  • Segment title
  • Global tags
  • Attachments
```

After processing, a second permanent block confirms what happened:

```text
FILE 1/3 — CHANGED SUCCESSFULLY
File: D:\Movies\Example.mkv
Changes made:
  • Segment title
  • Global tags
  • Attachments
```

Already-clean files receive a permanent `SKIPPED` block. Dry-run files receive
both a `WOULD CHANGE` inspection block and a `DRY RUN COMPLETE` result block.

`--show-skipped` additionally prints a skipped-files table at the end of the run.


## Console display fixes in 1.7.1

- Removed reverse-video headings, which could become invisible in PowerShell
  when using a light console background.
- Headings now use plain bold colored text and a separator line, making them
  readable on both light and dark terminal themes.
- Inspection and result entries are now labeled separately:
  `INSPECTION: WILL CHANGE` and `RESULT: CHANGED SUCCESSFULLY`.
- Full headings span the console width and are no longer truncated to `WOULD…`,
  `SKIPP…`, or `DRY R…`.
- `.video-optimizer-*.mkv` temporary files are ignored by the scanner.


## Console redesign in 1.8.0

The console is now divided into two live sections:

1. **File History** — one numbered row per file. A file's row is updated as it
   moves from inspection to cleaning, dry-run completion, skipping, or failure.
   Duplicate plan/result blocks are no longer printed.
2. **Workers** — a dashboard kept beneath the history and progress bar. Each
   worker shows its current action and filename.

The full lifecycle remains visible in one place:

- Waiting
- Will clean / Would clean
- Cleaning
- Done / Dry run complete
- Skipped
- Failed

Long paths no longer dominate the display. The filename is shown by default.
When duplicate filenames exist, the parent folder is included automatically.

The display uses ordinary foreground colors only and does not use reverse-video
styling, making it readable on dark and light PowerShell themes.

`.video-optimizer-*.mkv` temporary files are ignored.


## Progress display update in 1.8.1

- Restored a more traditional compact progress-meter appearance.
- Active progress is cyan.
- A fully completed run is green.
- The unfilled portion is neutral gray.
- Red remains reserved for failures and error states.
- The File History and Workers dashboard from 1.8.0 are unchanged.


## Long-run display stabilization in 1.8.2

- Completed files are printed permanently above the live dashboard.
- The live **Current Files** table is capped at eight rows.
- Additional queued files are summarized in one row instead of extending past
  the terminal height.
- The progress and worker sections remain pinned in a small live region.
- Live refresh frequency was reduced to minimize console redraw and flicker.
- The live region now crops safely rather than forcing PowerShell to scroll.


## Version 1.8.3 title optimization

When the segment title is the only metadata that needs cleaning, MKVCleaner now
uses `mkvpropedit` to clear it directly instead of remuxing the entire MKV. This
is normally much faster and does not re-encode or rewrite the video and audio.

If other configured changes are also required, MKVCleaner continues to use
`mkvmerge` for a full remux. Both programs are included with MKVToolNix.

When `keep_backup` is enabled, MKVCleaner must still copy the complete original
file to create the `.bak`, so title-only cleanup will take longer than it does
with backups disabled.


## Version 2.0.0 metadata engine and worker display

- Uses one combined `mkvpropedit` command for supported metadata changes.
- Supports in-place removal of segment titles, track names, tags, chapters, and attachments.
- Uses `mkvmerge` only when the selected tag configuration cannot be represented safely by one `mkvpropedit` command.
- Worker rows now distinguish `Waiting (paused)`, `Creating backup`, `Editing metadata`, `Remuxing`, `Verifying`, and `Replacing original`.
- Workers waiting at the pause gate are no longer counted as active cleaning processes.
- The pause heading reports active operations and reserved paused files separately.

## Version 2.0.1

- Already-clean files now remain labeled **Skipped** in Current Files.
- Already-clean files are no longer submitted to cleaning workers.
- Removed the stale **Would clean** / **Cleaning** status overwrite for skipped files.

## Version 3.0.0

- Added a dark graphical interface styled to match Video Optimizer.
- Added a tabbed Settings window that reads and writes `config.json`.
- Added found-file, current-activity, overall-progress, worker-status, and run-summary areas.
- Added safe GUI pause/resume and cancellation between files.
- Added separate `MKVCleaner.exe` and `MKVCleaner-cli.exe` builds backed by the same engine.

## Version 3.0.1

- Prevented MKVToolNix console windows from flashing during GUI inspection,
  cleaning, and verification.

## Version 3.1.0

- Replaced separate global-tag and track-tag checkboxes with one Tag cleanup
  selection and automatic migration of older configurations.
- Added a warning when track-tags-only cleanup may require full remuxing.
- Added fast in-place versus full-remux indicators to inspected file results.
- Fixed invisible selected text in all closed pull-down controls in dark mode.
- Changed the Tools setting to select the MKVToolNix folder.
- Expanded tool detection to verify both mkvmerge and mkvpropedit and show versions.
- Added a link to the official MKVToolNix download page.
- Added Help → About with version, repository, releases, issues, and MKVToolNix links.
