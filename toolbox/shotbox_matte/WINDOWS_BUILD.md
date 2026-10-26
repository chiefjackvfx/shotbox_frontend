# Legacy Windows EXE build

Normal startup now uses `launch.bat` or `launch.sh`; see [startup instructions](README.md#start-the-app).
The previous `dist/` distribution has been deleted. These optional historical
build instructions recreate an EXE distribution if needed.

The build creates `dist/ShotBoxMatte-Windows/ShotBoxMatte.exe`.
Transfer the **entire** `ShotBoxMatte-Windows` folder to the Windows PC.
The folder includes approximately 67.45 GiB of model weights plus the Windows
runtime and libraries. No ComfyUI installation or external model folder is used.
The EXE is a native Windows x64 GUI launcher, rather than a self-extracting archive.

The launcher locates its own directory through the Unicode Windows API and starts
`windows_runtime/python.exe -u -X utf8 windows_bootstrap.py`, preserving arguments.
It hides the console, captures stdout/stderr in `logs/windows-startup.log`, and
reports startup failures in a Windows dialog. GUI worker processes use that same
private interpreter. Both processes register the bundled native DLL directories.
Preferences are saved in the folder's `settings.ini`; bundled model references
are relocated when the folder moves.
The app header and diagnostics show `0.1.0 (BUILD_ID)`. This ID is derived from
the exact app Python files and `windows_bootstrap.py`, so copying an older app
folder produces a different ID even when the launcher EXE is unchanged.
Generation uses the supported empty-prompt Alpha Gen recipe. Prompt controls
have been removed from the desktop, saved workflows and CLI; legacy saved text
is ignored when loading older settings or worker jobs.
Result folders contain the matte EXRs. Per-job manifests and preview PNGs are
kept separately under `logs/jobs/<job-id>/` beside the EXE. Temporary export
frames and rollback backups use a hidden sibling of the output folder, so
atomic replacement remains on the output drive.

The desktop has dedicated **Single shot** and **Batch queue** tabs. Batch mode
supports selecting several MOV/MP4/MXF files or image frames, selecting multiple sequence
folders with Ctrl/Shift, recursively scanning a parent folder and dropping mixed
sources onto either drop area or the queue list. Imports create one job per
sequence/movie, skip pending/running duplicates and capture the common controls
at add time. Choose **Start batch** or **Start queue** to process them sequentially;
adding sources alone does not start generation.

MXF uses the bundled PyAV/FFmpeg decoder, without an extra installation or
transcoding step. The first video stream is used and audio is ignored. Source
In/Out values are 1-based decoded frame positions, independent of MXF timecode.
MPEG-2 4:2:2 and 10-bit DNxHD MXF are tested on Linux; other camera codecs
and Windows execution of this update still need target-media verification.

Other controls include inclusive source **In / Out** ranges,
scrubbing/playback in **Frame Review** with Source/Matte/Overlay modes,
named **Workflow Presets**, elapsed time, stage ETAs and a history-based estimated
job finish (unknown until a matching successful workflow has been measured).
Presets and the last
workflow settings live in `settings.ini`; full UI session logs live in `logs/ui/`.
**Copy log** and **Open logs** are in **Process Log**. EXR exports preserve each
source frame's data/display windows and pixel aspect ratio, and the UI limits
match the backend. CLI `--gui SOURCE` opens the supplied source in the desktop.
See [the desktop workflow guide](README.md#desktop-workflow) for controls and
range-numbering details.

## Included runtime

- Official [CPython 3.12.10 x64 embeddable distribution](https://www.python.org/downloads/release/python-31210/).
  The archive SHA-256 is pinned in `packaging/windows/python-embed.sha256`.
- PyTorch 2.11.0+cu130, torchvision 0.26.0+cu130 and torchaudio 2.11.0+cu130.
- PyQt6 6.11.0, Qt 6.11.2, OpenImageIO 3.2.1.3, PyAV 19.0.2,
  transformers 5.14.1 and NumPy 2.5.4.
- LTX core and pipelines 1.5.0 built from revision
  `e98f6d927d83c92ef2be6aec76c8440b19306e39`.
- Independent copies of all six files in `models/manifest.json`, including
  `ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors`.

All 51 runtime packages are resolved for Windows x64, with hashes in
`packaging/windows/requirements.txt`. The embedded runtime's `python312._pth`
restricts imports to its standard library, local site-packages and the app folder.
The Python [embedded distribution documentation](https://docs.python.org/3.12/using/windows.html#the-embeddable-package)
describes this application-private runtime approach. Linux native extensions are
not copied into the Windows environment. CUDA and MSVC runtime DLLs supplied by
the Windows wheels are included. Optional Triton/NATTEN kernels are not required;
the app uses the upstream PyTorch fallback paths.

Third-party licenses remain within each installed package. LTX source licenses,
which its wheels omit, are copied separately into `third_party_licenses/LTX-2/`.

## Rebuild on Linux or Windows

Use Python 3.12, Git, uv 0.12.21 and Zig 0.17.0. The compiler is also available
as the `ziglang==0.17.0` Python package. The build downloads Windows dependency
wheels and the small official Python archive; it copies the existing local
`models/` files and never downloads weights.

Create an unmodified checkout of the pinned LTX source, then run:

```bash
git clone https://github.com/Lightricks/LTX-2.git build/LTX-2
git -C build/LTX-2 checkout e98f6d927d83c92ef2be6aec76c8440b19306e39
python build_windows.py --ltx-source build/LTX-2
```

Use this project's `.venv/bin/python` on the current Linux installation.
If Zig is outside PATH, pass `--zig /path/to/zig`. `--uv /path/to/uv` and
`--python-archive /path/to/python-3.12.10-embed-amd64.zip` are also supported.
The build needs enough space for the complete runtime and the local model copies.

The same build is split into these reviewable steps:

```bash
python packaging/windows/build_launcher.py
python packaging/windows/assemble_bundle.py
python packaging/windows/prepare_dependencies.py \
  --ltx-source build/LTX-2 \
  --wheelhouse build/windows/wheels \
  --cache-dir build/windows/uv-cache \
  --install-target dist/ShotBoxMatte-Windows/windows_runtime/Lib/site-packages
python packaging/windows/validate_bundle.py \
  dist/ShotBoxMatte-Windows --full-model-hashes
```

The dependency helper rebuilds the two pure Python LTX wheels and updates the
Windows hash lock before installing. The assembler resumes already copied model
files by size; the final check verifies their full content against the manifest.

## Refresh an existing portable build

When only the app code has changed, reuse the installed Windows runtime and
already copied models. This command rebuilds the launcher, refreshes the app
sources, validates the package and creates a small code-update ZIP:

```bash
.venv/bin/python build_windows.py --code-only --zig /path/to/zig
```

It does not download dependencies or weights. Add `--full-model-hashes` to
recheck all 67.45 GiB of weights; otherwise existing model names, ordinary-file
status and sizes are checked. Source SHA-256 checks always run.
The full build also creates `dist/ShotBoxMatte-Windows-update.zip`.

To apply that ZIP on Windows, close the app and extract the ZIP into an existing
complete `ShotBoxMatte-Windows` folder, replacing matching files. The archive
includes `ShotBoxMatte.exe`, `windows_bootstrap.py`, the complete Python app,
and build metadata. Keep the existing `windows_runtime/`, `models/`,
`settings.ini` and logs. The ZIP cannot run independently.
Check that the header/log build ID matches the ID in `UPDATE_README.txt`.

The update ZIP can also be created independently after a successful assembly:

```bash
.venv/bin/python packaging/windows/export_update.py
```

## Verification on Windows

The Linux build checks real PE headers, x64 architecture and GUI subsystem,
Windows wheel tags, required runtime files and all six complete model SHA-256
hashes. `portable-package.json` records per-file app hashes and their aggregate
build ID; validation rejects modified, missing or extra app Python files.
`bundle-validation.json` records the result. Launcher argument handling
and app packaging behavior also have automated checks on Linux.

Actual Windows execution and Windows GPU inference remain unverified. On the
target Windows 10/11 x64 NVIDIA machine:

1. Run `CHECK_WINDOWS.bat`. It imports the actual bundled native libraries and
   validates model paths and BF16 CUDA support without loading the large weights.
   Its report is `logs/windows-self-test.json`.
2. Double-click `ShotBoxMatte.exe` and load a supported source.
3. Run a short generation and verify its EXR outputs before longer work.

The included CUDA 13 runtime still requires a compatible NVIDIA driver and a
BF16-capable NVIDIA GPU. The self-test does not establish inference output quality
or prove a particular workload will fit in memory.
