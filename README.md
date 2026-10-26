# ShotBox Frontend

PyQt6 desktop frontend for ShotBox.

## Entry Point

- Run the app from `main.py` using frontend `venv` (Python 3.13 on Linux, Windows and macOS).
- Use the normal Windows installer/update batch files or Unix installer/update
  scripts to prepare that environment. They provision local Python without
  changing system Python or PATH and install frontend and Matte dependencies
  together. Setup tools and managed Python live under ignored `.runtime/`.

Python 3.13 qualification is available through
`sh setup_runtime.sh --qualify-python313` on Linux/macOS or
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_runtime.ps1 --qualify-python313`
on Windows. It builds an isolated `.runtime/qualification/python313/venv` and
records installation evidence in the neighbouring `report.json`. The desktop
and Matte worker share that interpreter when launched explicitly from it.
All installers select Python 3.13 through `runtime_python.txt`. Normal
installers/updaters migrate older environments with a rollback backup. Existing
launchers keep using frontend `venv`; no special launch command is needed.
Windows/macOS dependency resolution passed; native acceptance remains pending. See
[Python 3.13 qualification](toolbox/shotbox_matte/docs/PYTHON313_QUALIFICATION.md)
for launch commands, results, pending checks and the promotion procedure.

An older frontend `venv` is moved to `.runtime/backups` before migration. The
new environment is created directly at its final path, verified, and restored
from backup if migration fails. Successful backups remain available for manual
rollback: close Shotbox and restore the backup to its original `venv` path;
virtual environments must not be run from renamed backup directories. A legacy
parent `../.venv` is preserved, because another application may share it.
Interrupted setup is recovered on the next installer/update run.

On Windows, `windows_run_shotbox.bat` enables unbuffered output and Python fault
diagnostics. Failed launches show the exit code and pause, including native
failures with negative Windows exit codes. Copy the error output above the
prompt when reporting a crash. If using an older launcher, run
`venv\Scripts\python.exe -u -X faulthandler main.py` from an open Command Prompt
in the frontend folder, then run `echo Exit code: %ERRORLEVEL%`.

DaVinci Resolve's native scripting library loads only when a Resolve action is
requested. Normal Shotbox startup does not load or require that library.

At startup, the terminal shows an ASCII version of the Shotbox logo alongside
Shotbox, Python, OS, Qt, media and Matte package versions, followed by the active
Python executable path. Versions come from the running environment's installed
metadata without loading inference libraries or probing the GPU. Missing packages
show `not installed`; unreadable metadata shows `unknown`.
Supported terminals use orange and light grey, with an eight-colour terminal
palette strip at the bottom; set `NO_COLOR` to disable colour and the strip.
Redirected output is plain text, terminals below 80 columns omit the logo, and
launches without a console skip the banner.

Video previews suppress FFmpeg's repeated input, metadata and stream summaries
by default, while warnings and playback errors remain enabled. To inspect those
summaries, launch with
`QT_LOGGING_RULES="qt.multimedia.ffmpeg.mediadataholder.info=true"`.

## Make All Previews

**Make All Previews** reuses one headless Nuke process for the batch, loading a
fresh preview template for each input. Rendering uses up to 16 threads and
top-down processing. The existing output naming, quality, slate, progress,
overwrite setting, and per-preview database updates still apply. Cancel stops
the worker; a failed input is reported before the next input is attempted.
Single-preview generation keeps its existing launch path.

Worker temporary files and Nuke disk caches live in `.cache/temp/preview-worker-*`,
protected from Clear Cache until the process stops, then removed automatically.
The mixed MOV/EXR verification batch took 15.7 seconds versus 30.8 seconds before
the change, with identical decoded frames. See
[batch preview verification](docs/PREVIEW_BATCH_VERIFICATION.md) for measurements,
test commands, and the limits of this comparison.

## Cache and temporary files

Runtime caches and disposable working files live in `.cache` beside `main.py`,
including on Windows and macOS. EXR preview proxies use `.cache/exr-previews-v1`;
thumbnail uploads, exports, and Nuke/3DE requests use `.cache/temp`. Runtime
setup downloads and disposable installation files also use `.cache/temp`,
protected while setup runs. Persistent Python, setup tools and environment
backups live under `.runtime` and are not deleted by Clear Cache.
Proxy JPEGs sit directly inside each job folder, named
`original_frame_name_<unique_id>.jpg`. The stable suffix identifies the source
path, keeping matching names from different shots and sequences separate.
Source-change checks are stored inside the JPEG metadata. Existing hashed or
nested proxies migrate when their sequences reopen.
Precomp EXR previews sample at 10 fps to reduce preparation work while retaining
the shot's normal timing. Hovering a folder in the right-click Precomp menu
shows its preview without waiting for the folder submenu to open. The panel
moves beside the submenu when it opens without restarting preparation.
Hovering first shows a still from the EXR while the
playback proxy is prepared, then switches to playback automatically. The still
loads independently of queued proxy jobs; its loading indicator sits in the
corner once an image is visible.
Moving away cancels queued preview work for that folder. Temporary read failures
retry on the next hover, and an open folder preview resumes after cache clearing.
See [preview verification](docs/EXR_PREVIEW_VERIFICATION.md) for lifecycle tests
and the Python 3.11/3.13 comparison.

Use **Settings → Cache → Clear Cache** to remove cached previews and unused
temporary files. The button clears in the background, stops preview preparation,
and keeps temporary files needed by running exports or external applications.
Previews rebuild when opened again. The `.cache` directory is ignored by Git.
**Open Cache Folder** opens that directory in your system's file browser.

**Development rule for Codex, other coding agents, and human developers:** all
new runtime temporary files and disk caches must use the shared `app_cache`
helpers and the frontend `.cache` folder. Follow the cleanup requirements in
[AGENTS.md](AGENTS.md#runtime-cache-and-temporary-files).

## Main Live Modules

- `main.py` wires the application tabs and startup behavior.
- `page_nukedash.py` is the primary task / Nuke dashboard.
- `page_assignment_board.py` is the active assignment board implementation.
- `review_page.py`, `activity_page.py`, and `import_xml_v2.py` are active feature pages.

## Review renders and colour management

Use **Play Render** to play the latest MOV or EXR sequence in a shot's
`renders/comp` folder. While viewing a render, the button changes to **Play Preview**
to return to the chosen preview at the current playback time. If a shot has only
previews or only renders, the button plays the selected media. The **Media**
selector lists versions for the active mode: previews in preview mode and
renders in render mode. Shots without previews remain available for render review.
Switch versions with the selector or Up/Down to keep the playback time and
paused/playing state, as in Quick View. Shorter versions clamp to their last
frame; moving to another shot starts at the beginning.

For a supervisor and team watching together, choose shots from **Review** or use
**Previous/Next** (Page Up/Page Down). Render mode stays active between shots;
a missing render stops playback and offers an explicit switch to the preview.
**Play Timeline** plays from the current shot through the remaining shots, stops
at missing media, and restores your loop setting when finished. Manual shot or
version selection ends timeline playback. **Loop** repeats a shot or its In/Out
range. Use `[` and `]` to set the range at the current frame and `\` to reset it.
Version comparisons keep that range at the same playback times; changing shots
resets the range to the whole shot.

**Present** (F11) opens the same player full-screen, retaining playback position
and the visible shot, version, and transport. Escape returns to the normal tab.
**Drawing** reveals the annotation tools; selecting a drawing tool pauses
playback. EXR frame counters use the sequence's source numbers; movie counters
start at 1001. Review automatically loads the active range into its frame cache,
including while paused. Pressing Play waits for those frames to load, then plays
the cached images at the selected FPS. EXR reads, movie decoding, and OCIO
processing run during loading; cached loops and frame stepping reuse the images.
The cache row shows loaded frames and RAM usage; **Load Frames** loads the current
range and retries unavailable frames. **Cache RAM** defaults to 1 GB, can be raised
up to 8 GB, and is remembered in workstation settings. Ranges larger than the
budget load in sections, with playback holding its frame while the next section
loads. Increase RAM or shorten In/Out to keep a whole range cached. Changing
sources, resolution, or OCIO settings rebuilds the cache. Pausing during loading
cancels automatic playback while frame loading continues.

Up to three background reads run together within the display-cache budget.
Ordinary RGB(A) EXRs use a decoder that lets the interface stay responsive;
layered EXRs read their RGB channels together without loading unrelated passes.
Loading time depends on storage and CPU speed. Review currently plays
images only; movie audio is not played.

Use **Resolution** beside the playback status to choose **Full**, **Half**,
**Quarter**, or **Eighth**. Full is the default for detailed QC. Lower settings
reduce the displayed pixel dimensions before OCIO processing, which helps with
expensive transforms such as ACES 2. Changing resolution retains the current
frame, playing/paused state, and In/Out range. Return to Full to inspect
fine detail. EXR decompression still reads the source frame at its original size.

Click **OCIO** to expand the colour controls. Choose an `.ocio` file, the `OCIO`
environment configuration, or the built-in ACES studio configuration, then select
Input, Display, View, and Look. EXRs default to the shot colourspace or ACEScg;
movies default to sRGB when the configuration provides that space. Input choices
are remembered separately for EXRs and movies. The OCIO configuration and
selections are saved with the existing application settings.

OCIO starts off unless saved settings or an environment configuration enable it.
With OCIO off, floating point EXRs are clipped for display without a colour
transform. With it enabled, the display transform runs on floating point pixels
before screen conversion. It does not modify render files. EXR reads run in the
background with a bounded display-frame cache. The **FPS** control sets playback speed without
changing the source's frame count. EXR sequences use the current rate, initially
24 fps; movies start at their encoded frame rate.

Review selectors use available row space and open wider menus for long names.
Long selected values use ellipses, retaining render version suffixes; hover for
the full label and source path. Popup menus stay within the current screen and
support arrow-key selection without triggering Review playback shortcuts.

Install the updated `requirements.txt` for the OpenColorIO Python bindings.
The pipeline uses the official [OCIO viewing API](https://opencolorio.readthedocs.io/en/latest/api/apphelpers.html)
and [OpenEXR Python reader](https://openexr.com/en/latest/python.html).


## Shotbox Matte Toolbox and Nuke integration

Open **Toolbox → Open Shotbox Matte** for the owned Matte pop-out. Its source
and backend live in `toolbox/shotbox_matte/`; the Nuke host integration lives in
`plugins/nuke/`. Both ship with the frontend, without model weights or a copied
inference environment.

For a compact single-sequence workflow, right-click a shot's **Precomp** button,
choose its sequence folder, then **Roto**. The popup uses the sequence displayed
in the menu preview. It defaults to 25 FPS, ACEScg, Version up and `_roto`, with
optional inclusive source In/Out, editable generation bounds and a Seed field
defaulting to 42. **Frame processing** switches between **Segments · recommended**
and **Full range · experimental**. It defaults to Segments for each new source;
switching modes updates the plan and summary. The live summary
shows model padding and the provisional output folder; extra model frames are
trimmed before export. **Generate Now** keeps progress and cancellation in the
popup while Shotbox remains usable. **Add to Queue** is reserved for future work.

Matte shares Shotbox's main Python 3.13 environment, including its background
workers; there is no separate Matte launcher or runtime preparation. In
**Settings → Plugins → Shotbox Matte / Nuke**, choose an external model library,
save configuration and install the Nuke integration. Restart Nuke after
integration updates. Nuke defaults to frontend `venv`; advanced custom Nuke
interpreter overrides remain supported. Shotbox stays open while Matte is busy.
CUDA generation requires a supported Windows/Linux NVIDIA GPU (Linux glibc
2.34 or newer for the pinned Qt build); macOS retains
the frontend without CUDA inference dependencies.

Matte's worker files, staged EXRs, overwrite backups and export locks use
`.cache/temp/shotbox-matte/`, including retained partial frames after cancellation
or failure. Exporting to another drive or network mount copies files from this
cache to the final output. Setup downloads and default Nuke jobs also use
`.cache/temp/`; active desktop and Nuke sessions protect their files during
Settings → Cache → Clear Cache. `.cache/.lease.lock` is retained as coordination
metadata. Final mattes are written to the chosen production output locations.
See [Matte setup](toolbox/shotbox_matte/README.md) and
[Nuke installation](plugins/nuke/README.md).

[Linux integration verification](toolbox/shotbox_matte/docs/SHOTBOX_INTEGRATION_VERIFICATION.md) records the relocated runtime checks and platform limits.

## Background denoise (Windows and Linux)

Click **Denoise** beside **mp4s** to denoise the current job. The confirmation
shows the clip count. Shotbox queues the latest saved Nuke script for each shot
across all timelines, including hidden shots, skips shots without saved scripts,
and avoids duplicate scripts. Clips run one at a time using the same workflow
as the shot-card action. Progress shows the current clip and batch counts;
**Cancel** stops the current clip and the remaining queue. Failed clips are
reported and the next clip continues. A final summary lists completed, failed,
skipped, and remaining clips. The button is disabled while denoise is running.

Right-click the `.nk` button on a shot card and choose **Denoise**. Shotbox opens
the saved script on a private display, evaluates its middle frame, runs Neat
Video **Auto Profile → Apply**, saves the profile into the same `.nk`, and renders
`WriteDN1` over the full script range. Matching output frames are overwritten.
Existing filter settings and output paths are preserved. Denoise runs even when
someone has the script open or the shot is locked, using the last saved script
on disk. It does not acquire or release shot locks. If the disk script changes
before the profile is saved, the job stops to preserve that edit. Only one
denoise job runs at a time.
The lock monitor identifies supervised denoise workers from their job records
and excludes them from editing-session detection. Normal Nuke sessions keep
their existing lock checks.

The target combination is **NukeX 17.1v2 + Neat Video 6.1.2**, with the English
Neat Video controls at normal scaling. Linux is qualified with native tests;
the Windows backend is implemented and awaiting native verification. Configure
the Nuke executable in Settings.
NukeX and Neat Video must already be installed and licensed. Background Nuke
starts with `--nukex` so existing ModelBuilder and other NukeX nodes load normally,
including nodes outside the denoise branch. Other plugin versions,
unrecognized controls, unexpected dialogs, and failed profiles stop the job.

Denoise uses Nuke's Qt terminal mode with its interactive NukeX license to
reduce startup time while retaining Neat Video's profiling editor. It uses
full-frame rendering and up to 16 Nuke rendering threads, limited to half the
machine's logical CPUs. On the verified 59-frame 4096 × 2850 shot, the complete
controller workflow took about 21 seconds versus the earlier 43–44 seconds.
The frame range, resolution, filter settings, and output format were preserved.

On Linux, install the optional display prerequisites before using this action:

```bash
# Rocky / RHEL / Fedora
sudo dnf install xorg-x11-server-Xvfb openbox
# Debian / Ubuntu
sudo apt install xvfb openbox
```

Run normal Shotbox runtime setup to install the Linux-only `python-xlib`
dependency. Each Linux job starts its own authenticated Xvfb server.

On Windows 10/11, denoise uses a separate Windows desktop and a process job to
contain Nuke and its children. It sends control messages only to that desktop's
Neat Video window. No desktop switching or global mouse/keyboard input is used.
Xvfb, Openbox, Xlib, and administrator access are not required. Both the toolbar
button and shot-card action are enabled on Windows; macOS remains disabled.

To verify the real Windows workflow, open PowerShell in the updated frontend
folder and run:

```powershell
.\venv\Scripts\python.exe test\denoise_windows_acceptance.py
```

The command uses the Nuke path from Settings or automatic discovery; pass
`--nuke "C:\path\to\Nuke17.1.exe"` to select it explicitly. It generates test clips
under `.cache/temp`, checks profiling, rendering, cancellation, crash cleanup,
and two queued clips, and prints PASS/FAIL plus a report path. It does not use
production shot scripts or contact the database. Windows qualification remains
pending until this command passes on Windows with the installed Neat Video UI.

The nonmodal progress window shows the current action, eight workflow steps,
elapsed time, and a timestamped activity history. Profiling shows the frame and
image size; rendering shows the frame count, percentage, and latest output path.
It includes **Cancel** and **Open Logs**. Closing it while running cancels the job. Closing
Shotbox waits for background cleanup. Cancellation after saving
keeps the profile and any completed frames; rerunning replaces those frames.
The popup closes automatically after successful rendering and cleanup.
The Shotbox console also prints timestamped `[Denoise …]` messages for startup,
Auto Profile and Apply clicks, saving, each rendered frame, Nuke output,
failures, cancellation, and cleanup, including the job's log directory.

Disposable job files, the original script recovery copy, screenshots, and logs
live under `.cache/temp/denoise-*`. Active jobs hold a cache lease so **Clear
Cache** preserves their files. Completed logs and recovery copies can be removed
by clearing the cache. The supervisor supports `SHOTBOX_DENOISE_XVFB` and
`SHOTBOX_DENOISE_OPENBOX` executable overrides for isolated testing.
See [denoise verification](docs/DENOISE_VERIFICATION.md) for the real Nuke
acceptance command, checked behavior, and platform limits.
