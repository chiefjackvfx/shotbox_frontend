# Shotbox Toolbox integration

This folder contains the complete Matte application/backend and standalone
launchers. The Nuke host integration lives in `../../plugins/nuke/` and is
installed and configured through Shotbox's **Settings → Plugins** panel.

Open **Toolbox → Open Shotbox Matte** for one owned pop-out window. Repeated
clicks restore it. Shotbox stays open while Matte has active work; finish or
cancel that work before quitting. Selecting a Shotbox shot does not
implicitly change Matte's source.

The frontend uses Python 3.11+ with PyQt6, NumPy, Pillow, PyAV and OpenImageIO.
Inference stays in its own pinned Python 3.12/3.13 environment. Run **Prepare
Matte Runtime** in Settings, or use `launch.sh --setup-only` / `launch.bat
--setup-only` from this folder. Virtual environments must be created at their
final location, rather than copied between installations.

Model weights are external and excluded from this source payload. Configure
an existing model library in Settings; no extra 68 GB model copy is needed.
The `models/manifest.json` records expected components. `models.local.json` is
an optional example using relative paths; a live external configuration should
resolve its component paths against its own location.

Shared runtime/model configuration lives in `~/.nuke/shotbox_preferences.json`.
Window preferences and session logs use the per-user `ShotBox/Matte` application
data directory. Disposable worker files and setup download caches live under
frontend `.cache/temp/`, protected while active. The small `.cache/.lease.lock`
coordinates cache clearing with independently running Nuke sessions.

The original standalone workflow notes below describe bundled model copies.
For the integrated frontend, the external configuration above takes precedence.
Existing verification reports are historical; they do not certify this move.

---

# ShotBox Matte

A standalone Python application that generates RGB grayscale matte EXRs from
image sequences or MOV/MP4/MXF using Lightricks' native **LTX-2.5 distilled + Alpha Gen IC-LoRA**.
The PyQt6 desktop and headless CLI share the same processing backend.

The six required model files are stored in this project's `models/` directory.
The app loads these independent local copies by default. There are no automatic
model downloads, video intermediates, or copies of source EXRs.

## Start the app

**Windows 10/11 x64:** copy this project folder, including `models/`, onto the
Windows PC and double-click **`launch.bat`**. It obtains local Python 3.12 when
needed, creates `.venv-windows`, installs the desktop and CUDA inference
dependencies, and opens the app. Python and Git do not need to be installed
separately. A compatible existing Windows `.venv` is also supported.

**Linux:** run **`./launch.sh`** (or `sh launch.sh`). It reuses the existing
`.venv` when ready, or creates and prepares one using local Python 3.12.
Automatic CUDA setup targets Linux and Windows; macOS is not supported.

The first setup needs internet access and enough disk space for Python and the
CUDA libraries. Later launches reuse the environment without reinstalling or
downloading anything when it passes the dependency check. Generation requires
a compatible NVIDIA GPU and driver supporting the CUDA 13.0 runtime.

Setup uses [uv's local installer](https://docs.astral.sh/uv/reference/installer/)
and [managed Python](https://docs.astral.sh/uv/concepts/python-versions/), with
tools, Python and download caches inside `.launcher/`. It does not change the
system Python, PATH, shell profiles or Windows Python registry entries. Library
versions and the two official LTX source archives are pinned in
`launcher-requirements.txt`; model weights are read from `models/` and are never
downloaded by the launcher.

Windows ignores Linux environments and installs Windows packages in its own
venv. You can omit `.venv`, `.venv-linux`, `.venv-windows`, `.launcher` and `build`
when copying to another PC; the appropriate environment is recreated locally.
Keep `models/`, all application/launcher files, and `settings.ini` if present.
Settings and logs created by these launchers stay inside this project folder.
The old `dist` EXE distribution has been removed; no EXE build is needed.

Both scripts accept an optional source path and `--setup-only` to prepare/check
dependencies without opening the UI:

```bat
launch.bat "D:\shots\plate_v001"
launch.bat --setup-only
```

```bash
./launch.sh /shots/plate_v001
./launch.sh --setup-only
```

Windows launcher execution and CUDA inference still need verification on a
Windows PC; this development host runs Linux.

For an already prepared environment, the direct Python entry point remains:

```bash
python launch_ui.py
```

`launch_ui.py` automatically uses this project's native venv. You can also pass an
image-sequence folder/frame or movie: `python launch_ui.py /shots/bam190_DN_v001`.

Validate the existing local weights:

```bash
.venv/bin/python -m shotbox_matte --validate-models \
  --model-root models
```

The desktop accepts EXR/image-sequence folders or frames and MOV/MP4/MXF files.
One source dropped onto **Single shot** loads it for setup and review; dropping
several sources adds them to **Batch queue**. Choose a source if several are
found in a single-shot input. Its preview and plan load in background threads;
generation runs in a separate process. The processing and export controls,
model paths, and window geometry persist between sessions. A source/matte
preview and **Open output folder** are available after completion.

Alpha Gen selects its subject automatically from the source footage. Inference
uses the supported empty-prompt recipe; text prompt controls are not exposed.

## Nuke integration

The optional [ShotBoxMatte integration](../../plugins/nuke/README.md) creates a
native Group for commercial Nuke/NukeX 17.1v2. Generate opens a naming panel,
captures RGB once, then runs the existing worker externally to save alpha-only
EXRs. Input colour defaults to ACEScg; no RGB bake or second Nuke render is needed. The finished precomp appears as a
standalone Read in the main node graph, connected to the downstream comp.
New versions use PrecompSwitcher's project output layout and contain only final
EXRs. Working images are removed after each job; small receipts stay in the
private cache. The Read renders
using its EXRs, without ShotBox's Python or GPU. Follow the installation and input
colour instructions in that guide. See the current
[alpha-only verification](docs/NUKE_ALPHA_VERIFICATION.md). See [Nuke verification](docs/NUKE_VERIFICATION.md)
and [precomp verification](docs/NUKE_PRECOMP_VERIFICATION.md) for tested behavior
and platform limits. The latest [named Read verification](docs/NUKE_READBACK_VERIFICATION.md)
covers the dialog and main-graph output.

## Desktop workflow

- **Source ranges:** enable **Use In / Out** in **Source & Generation** to
  restrict a shot before applying frame skip/cap. Numbered images use their
  filename frame numbers; MOV/MP4/MXF and unnumbered stills use positions starting
  at 1. The UI constrains numbering padding to 1–8 and generation bounds to
  the backend's supported limits.
- **Generation size:** changing height automatically updates width to match the
  selected source's aspect ratio. Loading or switching sources and applying
  presets also updates the width. Height is reduced when needed to stay within
  1920 pixels wide; model padding is added afterward. Batch imports calculate
  the matching dimensions separately for each shot using the captured height.
- **Frame processing:** choose **Segments · recommended** for automatic overlap
  blending, or **Full range · experimental** to force the entire selected range
  through one model pass. The mode is saved with preferences, presets and each
  queued job. Both modes accept up to 250 selected frames.
- **Batch queue:** switch between the dedicated **Single shot** and **Batch
  queue** tabs. Set up a shot and use **Add current**, or use **Add movies / files…**
  to select several movies or sequence frames. **Add sequence folders…** opens
  a folder picker that supports Ctrl/Shift to select multiple shot folders.
  **Scan parent folder…** finds nested image sequences and MOV/MP4/MXF files
  recursively. Both drop areas accept several mixed sources together, and the
  queue list also accepts drops. Bulk additions create one job per movie or
  image sequence even when several frames or overlapping folders are selected;
  sources already pending or running are skipped. Each addition captures the
  source range, model paths and processing/export settings at
  the time you add it. Adding sources does not start processing: click
  **Start batch** in the footer or **Start queue** to run one worker at a time.
  **Pause after current** finishes that shot before pausing; cancelling the
  current job pauses the queue. Failed entries retain their status and the queue
  continues to subsequent pending jobs. Pending entries can be removed and
  finished entries cleared.
- **Frame review:** expand **Frame Review** to scrub or step through the selected
  source frames, or play them with optional looping. After generation, switch
  between **Source**, **Matte** and **Overlay**; the opacity slider controls the
  foreground tint. Completed jobs use their recorded source-to-output mapping,
  including skip/cap/range selections. Playback is a display preview and is
  limited by source decoding speed.
- **Workflow presets:** enter a name in **Workflow Presets** and choose **Save**.
  **Apply** restores colour, range, FPS, generation, memory and export
  settings without changing the selected source or configured model paths.
  Presets persist with preferences; **Delete** removes the named preset.
- **Timing:** the footer shows total elapsed time, current stage duration and a
  stage ETA once enough progress samples are available, based on observed
  current-phase progress. Estimated job finish uses previous successful runs with matching
  workflow settings and scales their timings to the selected/model workload;
  it stays unknown until a matching run has been measured. This is an
  approximate forecast, and disk/GPU load can change the actual finish time.
  Hover over the timing text for measured durations of completed stages.
- **Diagnostics:** the header and session log display the app version and
  source-derived build ID. **Process Log** labels optional-library notices,
  warnings and errors while keeping full worker output in `logs/ui/`.
  **Copy log** copies the saved session log; **Open logs** opens the app's log
  directory. Matte result folders continue to contain results only.

## Installation

Target: Linux or Windows, Python **3.12**, NVIDIA CUDA with BF16 support.
The native source is pinned to LTX-2 revision
`e98f6d927d83c92ef2be6aec76c8440b19306e39` (packages 1.5.0).
See [native implementation and environment details](docs/INFERENCE.md) for the
verified interfaces, memory options, and upstream dependency differences.

Create an isolated environment and install the desktop and test dependencies:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[desktop,dev]'
```

Install the CUDA build of PyTorch before the native extra. This project's Linux
environment uses PyTorch 2.11.0 / CUDA 13.0, torchvision 0.26.0,
torchaudio 2.11.0, and transformers 5.14.1:

```bash
uv pip install --python .venv/bin/python \
  'torch==2.11.0' 'torchvision==0.26.0' 'torchaudio==2.11.0' \
  --index-url https://download.pytorch.org/whl/cu130
uv pip install --python .venv/bin/python -e '.[desktop,dev,inference]' \
  'torch==2.11.0' 'torchvision==0.26.0' 'torchaudio==2.11.0' 'transformers==5.14.1'
```

These commands install Python libraries, not model weights. Use a compatible
NVIDIA driver. The pinned upstream repository also declares a newer CUDA 13.2
environment; consult the native notes before choosing a different stack.
No optional NATTEN or compiled custom CUDA kernels are required by this app.

On Windows PowerShell, substitute `.venv\Scripts\python.exe` for
`.venv/bin/python` in the commands, for example:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e '.[desktop,dev]'
.venv\Scripts\python.exe -m shotbox_matte --gui
```

Install the native extra after installing a matching CUDA PyTorch stack as
above. Windows support is implemented but has not been exercised on a Windows
host. For a CLI-only installation, omit `desktop`. Qt and a display server are
unnecessary for processing and planning.

## CLI

Inspect a sequence without loading models or creating output folders:

```bash
.venv/bin/python -m shotbox_matte /shots/bam190_DN_v001 \
  --dry-run --source-color linear-srgb --resolution 960x544
```

Generate a matte with local weights:

```bash
.venv/bin/python -m shotbox_matte /shots/bam190_DN_v001 \
  --model-root models \
  --source-color linear-srgb --fps 24 --frame-cap 20 \
  --resolution 960x544 --seed 42 --suffix _alpha \
  --precision half --existing version-up --offload cpu --low-vram
```

Other useful options:

```bash
shotbox-matte /shots/frames --list-sequences
shotbox-matte /shots/frames --sequence 'beauty.#.exr' --dry-run
shotbox-matte /shots/frames --first-frame 1001 --padding 4 --existing overwrite
shotbox-matte /shots/frames --models-config models.local.json --json-progress
shotbox-matte /shots/plate_v001.mov --frame-cap 20 --source-color auto
shotbox-matte /shots/camera_v001.mxf --source-in 1 --source-out 20 --fps 25
shotbox-matte /shots/frames --source-in 1040 --source-out 1099 --frame-skip 1
shotbox-matte /shots/plate_v001.mov --source-in 41 --source-out 100 --dry-run
shotbox-matte --help
```

`--source-in` and `--source-out` select an inclusive range before skip/cap.
For numbered images, these are the filename frame numbers; for MOV/MP4/MXF and
unnumbered stills, they are 1-based source positions. Either endpoint may be
omitted to use that source bound. Bounds outside the source, reversed ranges
and intervals containing no frames fail clearly. Numbering gaps inside a source
are allowed. `--frame-skip N` selects every `N+1`th frame within that range,
then `--frame-cap` limits the selection. Zero cap means all.
The configured FPS is passed to the
model unchanged; output files are numbered consecutively. Source numbering and
the exact selected files and zero-based source indices are recorded in the job
manifest. An image file identifies its whole sequence. Discovery groups by
filename prefix and image extension, naturally sorts numbers, and rejects
duplicate numbers. Each movie in a folder is a separate selectable source.

Dry-run plans and job manifests record `"prompt": ""` as the fixed Alpha Gen
recipe. Earlier saved prompt preferences and worker payloads cannot override it.

Input is restricted to `.exr`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.dpx`,
`.bmp`, `.mov`, `.mp4`, and `.mxf` (case insensitive). Other files in a source folder
are ignored, and unsupported files cannot be selected as sources. GIF, AVI,
MKV, and WebM are outside this input list. Output remains raw RGB matte EXRs.
Images use OpenImageIO. Movies decode directly in memory through PyAV/FFmpeg
to RGB48 float values, preserving more than 8-bit precision where present;
there is no intermediate movie, extracted source sequence, or audio processing.
MXF uses the first video stream; audio is ignored. Codec support follows the
bundled PyAV/FFmpeg build. MPEG-2 4:2:2 and 10-bit DNxHD MXF are verified;
other camera-specific codecs, sidecar-dependent media and every MXF operational
pattern are not individually verified. MXF timecode does not change source In/Out: these remain 1-based
positions in the decoded video.
Video frame counts come from container metadata, with a decode-count fallback
when unspecified. Truncated selections fail clearly. Video FPS appears in the
UI and remains editable; the CLI uses `--fps` (24 by default) and warns when it
differs from the container rate. Variable-rate timestamps are not retimed.

`--json-progress` writes one JSON object per line to stdout (`progress`, `plan`,
`result`, `error`, or `cancelled`). Third-party model diagnostics go to stderr.
Successful execution returns 0, failures return 1, usage errors return 2, and
cancellation returns 130. Ctrl+C requests cooperative cancellation.

`--models-config` accepts a JSON object with `transformer`, `text_encoder`,
`video_vae`, `audio_vae`, `spatial_upsampler`, and `alpha_lora` paths. Relative
paths are relative to the JSON file. Individual component flags override the
configuration. Discovery accepts a split-pack models directory, its parent,
or a flat folder containing the six released filenames. The runtime needs only
these files; they can live anywhere. Header validation explains missing or
incompatible files and rejects ComfyUI quantized variants.

## Frame and colour handling

1. Read selected images with OpenImageIO or MOV/MP4/MXF frames with PyAV, recording
   original dimensions, source numbers and frame indices.
2. Convert the chosen source colour encoding into bounded display RGB.
3. Fit the selected generation box while preserving aspect ratio, then edge-pad
   the spatial dimensions to multiples of 32.
4. In **Segments** mode, split long selections into overlapping segments;
   **Full range** keeps one pass. Repeat each pass's last frame to reach `8n+1`.
5. Run each segment through native Alpha Gen with an empty prompt, adapter
   strength 1.0, and stage two skipped. The adapter accounts for the upstream
   half-size output.
6. Trim added frames, blend shared matte frames across segment joins, crop
   spatial padding, resize to the source dimensions, and export raw matte EXRs.

For example: **20 selected frames → 25 model frames → 20 exported frames**.
ShotBox accepts **up to 250 selected frames per shot**. By default, selections
exceeding a single pass use overlapping segments of at most 121 model frames, each within
the six-second limit. At 24 FPS, 250 selected frames use three segments with
17-frame overlaps: 121 + 121 + 49 model frames produce exactly 250 EXRs.
Shared matte frames receive a linear blend, retaining raw float values. Only
one segment's RGB buffer and shared tails are held at a time.

The model's supported limits remain **1920×1088** and **145 frames / 6 seconds**
per pass. Existing supported short selections keep their single-pass behavior;
counts above 121 produce a warning. Segmentation follows the
[official recommendation to split longer shots](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen).
The model selects its subject independently in each segment; review joins for
subject changes, edge differences and seams. Blending cannot guarantee subject
consistency. Inputs exceeding 250 selected frames require a range, frame cap
or frame skip; no source frames are silently discarded.

To attempt a longer single pass, choose **Full range · experimental**, or pass
`--temporal-mode full-range` in the CLI. This explicitly bypasses the supported
temporal frame/duration limits while keeping spatial, colour and frame-grid
validation. For example, **250 selected → 257 model frames → 250 exported EXRs**,
with no overlap or join blending. This can exhaust GPU memory or leak source
RGB into the matte beyond the model's supported range. Full-range failures are
reported without automatically switching modes. Native GPU quality for this
experimental length has not been verified. Use `--temporal-mode segmented` to
choose the default behavior explicitly.

`--source-color auto` is the default: EXRs use linear-sRGB preparation; other
images and MOV/MP4/MXF use display RGB. This is a format-based default, not colour
space detection. Override it for ACEScg EXRs or log/linear TIFF, DPX and movies.

The supplied reference workflow loads display RGB video. For scene-linear
EXRs, `linear-srgb` applies the sRGB transfer curve; `acescg` first transforms
AP1/D60 to Rec.709/D65 using Bradford adaptation. Both clip to the display range.
`srgb` accepts already display-encoded RGB. This explicit preparation is **not a
show-specific OCIO/ACES view or filmic tone map**. For an established show look,
prepare display RGB EXRs with that view and select `srgb`. NaNs, infinities,
deep EXRs, and ambiguous multilayer RGB inputs are rejected clearly.
Dimensions refer to the EXR data raster. Each output preserves its source
EXR data-window origin, display window and pixel aspect ratio, including
cropped windows, negative offsets and overscan. These metadata may vary between
frames. Selected images must share the stored raster width/height; changing
raster dimensions fail before model loading so those frames can be split into
separate jobs. Deep, 3D and volume images are rejected.

The generated matte is data. Export adds no display gamma, ACES transform,
HDR expansion, or value clamp. The decoded red channel is replicated into R/G/B,
matching the reference workflow. **These EXRs have no dedicated alpha channel.**
Half float and full float are supported with ZIP compression. Half float obeys
its normal precision/range; choose full float when needed. Preview PNGs alone
are clipped to the display range.

## Output versions and cancellation

For input folder `bam190_DN_v001` and suffix `_alpha`, output goes beside it:

```text
bam190_DN_alpha_v001/
    bam190_DN_alpha_v001.1001.exr
    bam190_DN_alpha_v001.1002.exr
    ...
```

Generated output folders contain the matte EXR sequence. Job manifests and PNG
previews are saved separately in the app's `logs/jobs/<job-id>/` folder; in the
Windows portable build this is beside the EXE. Set `SHOTBOX_JOB_DIR` to override
the job-storage root if needed. The desktop still loads source/matte previews
from that separate storage. CLI results include their paths and the manifest path.

The first number comes from the selected source sequence when unambiguous,
otherwise 1001. Set `--first-frame` and `--padding` to override it. Folders
without a `_vNNN` ending start at `_v001`.
For a movie, the output name derives from the movie stem: `plate_v001.mov`
produces `plate_roto_v001` beside the movie. Movie frame ordinals start at zero
in the manifest; exported numbering defaults to 1001.

**Version up** is the default each time the desktop opens. Overwrite is not
restored from the previous session; choose it explicitly or load a saved preset.
If the derived target exists, Version up selects the next
higher matching version and reserves the folder using filesystem locks and
exclusive creation. The displayed path remains provisional until reservation.

**Overwrite** reuses the source-derived target version. Images are staged first;
replacement and exact-sequence stale-frame cleanup happen only after all new
frames have exported. Unrelated files and other versions remain intact. Caught
commit failures roll replaced files back. Multi-file replacement cannot be
atomic across sudden power loss; the manifest records its committing state and
staging contains backups for recovery. In standalone mode, staging is a hidden
sibling of the output folder, on the same volume for atomic frame replacement.
When opened from Shotbox Toolbox, staging, overwrite backups and export locks
instead live under the frontend's `.cache/temp/shotbox-matte/`. The host sets
`SHOTBOX_TEMP_DIR` for this worker only. Cross-volume exports copy files between
the cache and output, preserving backups for rollback on caught commit failures.
On a successful overwrite, older ShotBox manifests and preview PNGs in the
results folder are archived into the new job's `previous-export/` directory.

Every reserved job has a manifest in app storage. Only a successful commit marks
it `complete`. Failed or cancelled jobs retain a sibling `.shotbox-partial-*`
directory outside the output folder; the manifest records its path and status.
Toolbox jobs retain these partial frames in the cache instead of a sibling folder.
Unfinished frames and recovery backups remain outside the results. Existing
successful overwrite output stays intact until commit. Cancellation is checked
between preparation, native denoising, decoding and export operations. After
the commit point, a successful export is reported as complete even if a late
cancel request arrives.

The desktop asks its worker to cancel, then forcibly stops it after a timeout
if a native operation cannot return. A forced stop can leave a running job manifest
and stale sibling `.shotbox-lock` directory. Inspect the indicated partial output before
removing that lock and retrying. GPU memory is released when the worker exits.
For Toolbox jobs the stale lock is in the cache; its manifest records the path.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check shotbox_matte tests
.venv/bin/python -m compileall -q shotbox_matte
```

Tests exercise real OpenImageIO files and Qt offscreen where installed, with
controlled inference doubles for deterministic backend checks. See
[verification results and remaining limits](docs/VERIFICATION.md) for the
actual native smoke test and platform coverage. Mocked output does not establish
matte quality. Review generated edges and temporal consistency before production.

The implementation follows the
[official Alpha Gen guide](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen),
[native PyTorch documentation](https://docs.ltx.io/open-source-model/integration-tools/pytorch-api),
and the supplied `ltx25_ag_alpha_00004.json` / `shotbox_nodes` references. Sequence
preparation, naming, version allocation, and EXR export are independent Python
implementations.

[Linux integration verification](docs/SHOTBOX_INTEGRATION_VERIFICATION.md) records the relocated runtime checks and platform limits.
