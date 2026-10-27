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

## Denoise (Windows and Linux)

Click **Denoise** beside **mp4s**, or right-click a shot card's `.nk` button and
choose **Denoise**. Both actions process original source media using
`template_denoise_v01.nk`; an existing compositing script is unnecessary.
Compositing scripts, original footage, and the supplied template remain unchanged.

Choose **Overwrite** (default) to replace the latest matching denoise
version, or **Version up** to allocate the next unused version. Outputs
are scene-linear half-float EXRs under
`renders/precomp/<shot>_DN_v###`. Multiple original clips use the preview-style
`<shot>_plate_01_DN_v###` naming. If no version exists, either choice starts at
`v001`. Successful shorter overwrites remove only matching out-of-range EXRs.
The generated `.nk`, including its fresh noise profile, is saved only to the
worker cache at `.cache/temp/denoise-*/profiled.nk`. Output folders contain EXRs
only. Cancellation or failure can leave partial EXR frames; rerunning Overwrite
replaces them. Cached scripts are disposable and removed by Clear Cache.

Each worker loads a fresh template copy and builds
**Read1 → TimeOffset → Reformat1 → Neat Video → Write1**. It detects the source
range, dimensions, pixel aspect and movie FPS, falling back to the shot FPS
when media has none. Output numbering starts at **1001** without changing
playback speed or duration; a still produces one frame. Widths above **4096**
resize to 4096 with proportional height, preserving pixel aspect. At or below
4096, Reformat is bypassed. Portrait footage follows the same width-only rule.
There is no upscaling, cropping or padding. Height rounds to the nearest pixel.

Movies, image sequences and stills are accepted through Nuke's installed
readers and codecs, with no extension whitelist. Sequence patterns (`####` or
`%04d`) are supported; Nuke also groups numbered image files. Missing sequence
frames and unreadable media produce per-clip errors. The shot's input-colourspace
selection uses the same mapping as previews; defaults are ACEScg for EXR and
sRGB for other inputs. The template's OCIO configuration and Neat filter settings
are retained. Every clip evaluates its first frame and performs fresh
**Auto Profile → Apply** before rendering. Windows additionally selects
**Profile Check** and waits for its preview before Apply. Only `Write1` renders, without a display
transform. Existing editing locks are neither acquired nor released.

Job-wide denoise includes unique originals per shot across all timelines,
including hidden shots. Missing inputs are counted as skipped. Clips run
sequentially; individual failures do not stop the remaining queue. Both actions
share a busy guard. **Cancel**, closing the progress window, and application
shutdown stop the active worker and discard the remaining queue after cleanup.
Progress includes frame counts, a timestamped activity log and **Open Logs**.

The target is **NukeX 17.1v2**, installed and licensed, with Neat Video
**6.1.2** on Linux or **6.1.2 / 6.1.3** on Windows, and
English Neat controls at normal scaling. Configure the Nuke executable in
Settings. Other Nuke 17 minor versions remain supported by the launcher but do
not count toward 17.1v2 qualification. Unknown dialogs and controls stop the job
before rendering with an unverified profile.

Linux uses a private authenticated Xvfb display and Openbox:

```bash
# Rocky / RHEL / Fedora
sudo dnf install xorg-x11-server-Xvfb openbox
# Debian / Ubuntu
sudo apt install xvfb openbox
```

Normal Shotbox runtime setup installs Python Xlib. Windows opens **visible NukeX
and Neat Video windows** on your current desktop. Keep Neat Video's controls
uncovered and the window unminimized while it profiles. Keyboard focus may be
in another window. Before each click, Shotbox checks that the screen point
belongs to its Neat editor; covered controls pause the sequence until uncovered.
The profiling timeout still applies. Shotbox does not move your pointer.
It only captures/clicks windows
belonging to its own Nuke process, and closes that process after completion or
cancellation. Other Nuke sessions are excluded. Capture reads displayed client
pixels instead of requesting an offscreen repaint. A persistently black capture
now reports a specific error after ten seconds.

Windows starts the full NukeX GUI through `menu.py`; Linux retains its private
Xvfb display and verified `--tg` launch. The activity log identifies the mode.
macOS is not supported. Windows qualification remains pending: the visible
diagnostic opens Neat 6.1.3, but automated visible capture/clicks need a native retry.

Visible Windows jobs now use control references captured from Windows Neat
6.1.3, with scale calibration from Auto Profile. Enabled Apply and Adjust and
Preview are compared against captured disabled states; Profile Check must be
selected before its preview can permit Apply. Calibration is recorded in
`control-scale.json`. The three supplied Windows states pass screenshot replay
at multiple scales; live Windows automation still needs verification.

Before the versioned editor opens, Windows allows up to two minutes for the
`NeatVideo6 OFX 64` loading window, without clicking or dismissing it.
Auto Profile waits for its enabled control; Profile Check waits for a completed
profile; Apply waits for enabled controls and the Profile Check preview.
Profile Check is a preview mode, not an automated quality verdict. Other modal
windows retain the two-second settling interval. Diagnostics read Windows
accessibility labels when native child controls expose no text. This queries
separate toolkit instances, including Neat's `Qt5152QWindow` and Nuke's Qt 6,
without depending on a readable screenshot. The read-only helper uses
Windows PowerShell/.NET UI Automation on the same desktop, with a
four-second timeout and cancellation support. On another failure, use **Open
Logs** and share `unexpected-dialog.json`, including its `accessibility` section.
If accessibility is unavailable, that section records the reason; the helper
log is `dialog-accessibility.log`. A successful accessibility query can still
return only the window title, as observed for the `Information` dialog.
The PNG may still be black. Unknown dialogs
still stop the job without being dismissed.

Run the Windows acceptance command from the frontend directory:

```powershell
.\venv\Scripts\python.exe test\denoise_windows_acceptance.py --nuke "C:\Program Files\Nuke17.1v2\Nuke17.1.exe"
```

If an unreadable Neat warning still blocks profiling, reproduce the most recent
Shotbox job visibly from the frontend directory:

```powershell
.\venv\Scripts\python.exe test\denoise_windows_diagnostic.py
```

This intentionally opens a visible NukeX window using the cached template,
source, colourspace, sizing and first-frame profiling setup. It does not start
a render or dismiss any Neat dialogs. Screenshot the warning before dismissing
it; if the profile editor opens normally, report that instead. Close this Nuke
window when finished. Working scripts, logs and redirected output paths stay
under `.cache/temp/denoise-visible-*`. The command prints the selected job and
clip; pass `--request "C:\path\to\failed-job\request.json"` to select another.

The verifier requires the supplied `test/noise_test.exr`, template, Python test
dependencies and `ffmpeg` on PATH for movie fixtures. It explicitly checks
17.1v2, NukeX licensing and plugin loading, then tests the template workflow.
It prints PASS/FAIL and report/log paths. Both UI entry points must also pass
with disposable shots before declaring Windows qualified.

All test outputs and worker logs, captures and recovery copies live under
`.cache/temp/denoise-*`. Active jobs hold cache leases so **Clear Cache** preserves
them. Completed diagnostics can be cleared. `SHOTBOX_DENOISE_XVFB` and
`SHOTBOX_DENOISE_OPENBOX` override Linux display executables for testing.
See [denoise verification](docs/DENOISE_VERIFICATION.md) for evidence and commands.
