# ShotBoxMatte for Nuke 17.1v2

This integration generates **alpha-only EXRs** with the external ShotBox worker
and creates a standalone Read in the main node graph. **Input colour defaults
to ACEScg**. Nuke captures RGB once for inference; the worker restores the matte
to source resolution and writes a single `A` channel. Those exact EXRs are
published under your chosen precomp name, without a second Nuke render or an
RGB/alpha bake. Connected downstream nodes and Viewers switch to the Read.
Nuke never imports ShotBox's inference dependencies. See
[alpha-only verification](../../toolbox/shotbox_matte/docs/NUKE_ALPHA_VERIFICATION.md) for current
workflow, channel and pixel checks, and
[recovery/control verification](../../toolbox/shotbox_matte/docs/NUKE_RECOVERY_VERIFICATION.md) for
relinking, cancellation, graph navigation and narrow layouts. Historical
[named Read verification](../../toolbox/shotbox_matte/docs/NUKE_READBACK_VERIFICATION.md) and
[RGB precomp verification](../../toolbox/shotbox_matte/docs/NUKE_PRECOMP_VERIFICATION.md) describe the
earlier behavior; saved RGB precomps remain readable.

The required host is **commercial Nuke/NukeX 17.1v2**. Linux is the primary
target. Windows requires its own acceptance run; Indie, Non-commercial,
macOS inference, stereo and deep inputs are outside this release's scope.

## Installation and configuration through Shotbox

The authoritative integration source lives in `shotbox_frontend/plugins/nuke/`.
Matte's backend source lives in `toolbox/shotbox_matte/` and uses the frontend's
main Python 3.12 environment.

1. Open **Shotbox → Settings → Plugins → Shotbox Matte / Nuke**.
2. Run the normal Shotbox installer/updater to prepare frontend `venv` with
   Python 3.12 and the tested CUDA dependencies. No model weights download.
3. Select your external model folder and optional model configuration. Save.
4. Choose **Install / Update Nuke Integration**, then restart Nuke.

Shotbox preserves unrelated `~/.nuke/init.py` code, backs it up before the first
change, and manages one marked registration block pointing to this directory.
Recognized standalone registrations for the old Shotbox Matte integration are
migrated. Custom registrations requiring interpretation are reported for manual
correction. Reinstall after relocating the frontend to refresh its registration
and backend application path.

Shared settings remain in `~/.nuke/shotbox_preferences.json`. Shotbox configures
the relocated backend root, external model paths and optional persistent Nuke
cache override. By default, Nuke runs workers with frontend `venv`. Recognized
old managed Matte interpreter paths migrate to that default; explicit custom
Nuke interpreter overrides remain supported. Nuke's advanced Preferences edit the
same file. Nuke reads settings for the next generation job and works without
Shotbox running. It never installs dependencies or imports the frontend's Qt.

Default disposable Nuke job files use `shotbox_frontend/.cache/temp/nuke-matte`.
Active Nuke jobs create a process lease so Shotbox's Clear Cache preserves their
working files. Final EXRs stay at their chosen output location. Explicit
persistent cache overrides and legacy output locations remain supported.

## Generate and inspect

The **ShotBox** tab keeps frame range, input colour, resolution, matte name,
Render/Cancel and progress together. **Find Read** selects and frames the output
Read; **View Matte** connects it to a Viewer and displays alpha while preserving
other Viewer connections. A new Read is placed in nearby clear graph space;
subsequent renders preserve its artist-chosen position.
**Advanced** contains output locations, recovery, processing, latest-job
logging and legacy Group preview controls. Unavailable actions are disabled.
Saved nodes receive this layout without replacing their values or animations.

1. Save your Nuke script. Select a flat RGB source and create ShotBoxMatte;
   it uses normal Nuke placement and inserts beneath the selected source, with
   its properties open. With nothing selected, it uses Nuke's normal free-node
   placement. Creation also works inside Groups and supports one-step Undo/Redo.
   You can connect a source afterwards. Grade, retime or prepare the
   source upstream; capture evaluates the actual connected pixels at each
   original timeline frame.
2. Set inclusive Frame In/Out, covering 1–250 frames. Negative frames and zero
   are supported. FPS is captured from the project; no frames are skipped.
3. **Input colour defaults to `acescg`**. Change it for other inputs: `linear-srgb` means linear
   Rec.709; `acescg` means AP1/D60; `srgb` means display-encoded sRGB. This labels
   captured values and does not apply a Nuke Write transform. For other working
   spaces, convert upstream yourself. Show-specific OCIO display preparation
   belongs upstream, followed by `srgb`.
4. On the **Advanced** tab, choose the save location and inspect the output path.
   **Project precomp** is the default and follows PrecompSwitcher's configured
   `WRITE_FILE_TEMPLATE`. Set generation
   height (default 544); width follows the input raster. The bridge's fitted,
   padded spatial plan is authoritative and recorded in the attempt manifest.
5. Click **Render**. A **Name precomp** dialog asks for your name, such as
   `bam030_subject_alpha`. New nodes start blank; regenerating remembers your
   previous choice. Cancel creates no job or output version. Accepted names are
   converted live to letters, numbers and underscores: Space immediately inserts
   `_`, including while editing the middle of a name. Pasted punctuation is
   converted too. The dialog previews the EXR filename, next free version and
   full output location before you confirm. Empty names and invalid destinations
   stay in the dialog with an inline explanation. Environment probing then runs, and Nuke captures an
   fixed-format RGB capture for inference, without a separate original-channel snapshot.
   Every inference frame is preflighted. The external worker runs in the
   background; progress and errors appear on the node. The worker writes
   full-resolution alpha-only EXRs. After validation these files are renamed
   onto the original timeline and published without re-encoding or another Nuke render.
6. A normal Read named after your precomp appears beside ShotBoxMatte and
   receives its downstream connections. It reads only the generated alpha
   on the original timeline, with **raw** enabled because alpha is data. The generator shows **Precomped** and stays
   connected to its source for regeneration. Save the comp; reopening never
   starts inference or creates duplicate Reads.

For `/project/shot/nuke/comp.nk` and name `bam030_subject_alpha`, the shared default is:

```text
/project/shot/renders/precomp/bam030_subject_alpha_v001/bam030_subject_alpha_v001.1001.exr
```

Each render reserves the first unused `_v001`, `_v002`, etc. Existing versions,
including PrecompSwitcher renders, are never overwritten. Empty failed version
reservations are released after cleanup.
The path preview uses `####`; the saved Read uses equivalent `%04d`.
The version folder contains only the finished alpha EXRs. Temporary RGB captures,
worker output and backend files live under the preferred cache
root's `.jobs/<node UUID>/<attempt UUID>` folder. After completion, failure or
cancellation, those working files are removed once the owned worker has exited.
Only a small `integration.json` job receipt stays in that private cache folder;
it records validation, checksums and any failure message. No receipt, log, lock
or temporary folder is left beside the final EXRs. While rendering, allow disk
space for the RGB capture and alpha output. Publication uses hard links on
the same filesystem, or a byte-preserving copy across filesystems.
The integration reads a literal template from the available PrecompSwitcher
configuration without importing its UI. It also works without PrecompSwitcher,
using the same default. A computed/disabled template needs an explicit ShotBox
template override. Templates must contain a versioned immediate parent folder.
**Custom cache** uses the preferred absolute cache root instead, allowing unsaved
scripts. A preferred cache root does not override **Project precomp**.

Before the first valid result the Group previews the input in **Bypass**.
Processing shows **Rendering**. Completed alpha is shown in the Group's
matte-only preview. The standalone Read has only `rgba.alpha`; use the Viewer's
alpha display to inspect it, or connect it wherever the comp needs a matte.
There is no source RGB or auxiliary data in the output EXRs. **Matte precision**
controls the final half/float output directly. Negative and greater-than-one
matte values are preserved within that precision. Outside the saved range,
alpha is black. The standalone Read is independent of the generator.

**Advanced → Group preview** retains compatibility controls for older RGB
precomps and a live-input toggle. These affect the Group output; the main-graph
Read continues reading its saved EXRs. A Write connected directly to an enabled,
ungenerated Group still fails validation unless **Preview rendered precomp** is off.

Capture covers the fixed full-resolution format rectangle, with square pixels
only. Cropped and changing input bounding boxes are filled with black inside
that rectangle; off-format overscan is excluded from inference. Convert
animated formats, non-square pixels, multiview or deep data upstream first.
Nuke may normalize a
conservative zero-valued one-pixel halo from an exact-format Crop when writing
EXR; captured RGB values remain unchanged. Source alpha is ignored. No automatic unpremultiplication occurs; artists who
need straight RGB must prepare it upstream. Viewer LUT/zoom/ROI do not control
capture. Proxy mode is temporarily disabled and restored.

Advanced settings retain backend meanings: seed 42, CPU offload, reference VAE
tiling off, half EXRs, segmented processing. **Full range is experimental** and
may exceed VRAM; a failure is reported without silent fallback. Frame-count
validation does not establish matte quality or segment-join consistency.

## Cancellation, recovery and caches

There is one generation attempt per Nuke script session, with no queue. Each
attempt owns a new version directory; regeneration preserves the previous active cache and standalone Read
until the new result passes validation and its input/settings remain current.
Renaming a node preserves ownership; pasted copies receive new UUIDs and never
inherit a running controller. Changing settings affects the next job and cannot
retime the active Read's saved frame map. Changing the live input after completion
does not change a frozen precomp. Changes during generation prevent automatic
activation; **Reload Result** can explicitly select the completed snapshot.
Successful regeneration updates the owned Read while retaining its position
and downstream connections. Renaming the Read is supported. A deleted Read is
recreated on the next successful generation or explicit Reload result. A Read
repurposed to a different file is left alone. Reads copied by an artist are not
all updated together. Loading an old script retains its existing wiring;
Generate or Reload result creates the standalone Read explicitly.

Foreground RGB capture occupies Nuke. Cancel through Nuke's render progress UI;
this is recorded as cancellation and preserves the previous result.
the node's Cancel button cannot interrupt a blocked foreground execute call.
During AI processing **Cancel** creates the worker's cancellation file. After
30 seconds **Force stop worker** becomes available and terminates only that
owned child process. Closing a script or deleting its owner reaps owned children.
A result committed before a late cancellation can still be accepted. Partial
working images, logs and locks are removed; a private job receipt retains the
terminal status and error message. A host crash can leave private working files
until its saved attempt is reopened and the previous owner has exited.

**Reload result** validates the most recent attempt explicitly. Failed or
interrupted attempts never become active automatically. Reload also creates or
updates the standalone Read. **Validate files**
checks full checksums, decodes cached images with Nuke and recomputes freshness.
**Relink EXRs** accepts a moved or renamed EXR sequence, including an individual
frame, `####` or `%04d` pattern. It verifies every frame's size and checksum,
then decodes with Nuke before changing the Group and Read. A wrong, incomplete
or corrupt sequence leaves the current reference unchanged. A new private
receipt records the relocated path; original receipts and EXR bytes are never
modified. One Undo/Redo reverts/restores both Group and Read references. Unrelated
sequences may share the destination folder; extra frames matching the selected
sequence remain an error. Legacy `integration.json` relinking is supported too.
**Open Output Folder** opens the final precomp location and
**Latest job log** always targets the most recent attempt, including failed or
cancelled regeneration, while Open output folder still targets the active result.
After temporary logs are removed, the receipt retains the terminal error/status.

Cache integrity and input freshness are distinct. A matching graph/animation/
expression/media-metadata fingerprint means **no detected change**, not proof
that arbitrary procedural pixels or external media are unchanged. Replacing
source files while preserving size and modification time can evade this check;
use external dependency hashes or recapture for stronger guarantees. Unresolved
dependencies block strict rendering of legacy caches that combine live RGB
with a cached matte. Published alpha and saved RGB precomps are independent of live input freshness; strict file-integrity checks
still apply. Final EXRs and small receipts persist; bulky working images are
automatically removed. RGB capture and alpha output require additional space
while a job runs.
Probe reports available disk and a conservative estimate before capture.

Older saved Groups retain their alpha-only Read behavior and **Custom cache**
location until regenerated. Their live RGB, zero-alpha out-of-range behavior
and strict upstream freshness checks remain supported. Generating a new result
publishes the alpha-only EXRs in the current workflow.

The child environment removes inherited Python/Qt search overrides and only
identifiable Nuke entries from library/executable paths. System, CUDA driver
and license paths are retained. Per-child `SHOTBOX_JOB_DIR` points inside the
attempt. This policy still needs actual Windows host verification.

## Render machines and farm deployment

The new standalone Read needs the final EXR sequence. It renders as a normal
Nuke Read with no ShotBox package, model weights, external Python, source
snapshot or GPU. Generator validation and reload also need the small private
job receipt; an ordinary Read can be relinked directly with Nuke's file browser.

For a Write that still renders through a ShotBoxMatte Group, install this
integration package in every Nuke render process and
make the final EXRs and private job receipt accessible (the whole cache for
legacy alpha-only results). A GPU, model weights and external ShotBox
Python are unnecessary for a cached render. Validation uses standard-library
paths/hashes and Nuke's own image decoder, never an external helper or inference.

The native Group cannot register guards when this Python package is absent.
Use the explicit farm entry point to make a missing integration fail the job:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t \
  /deployment/integrations/nuke/tests_nuke/render_cached.py \
  --script /project/comp.nk --write Write1 --first=1001 --last=1003
```

Or import and call the same startup contract in your farm wrapper:

```python
import shotbox_nuke
from shotbox_nuke.render import farm_preflight
shotbox_nuke.register()
farm_preflight('/project/comp.nk', 'Write1', 1001, 1003)
nuke.execute(nuke.toNode('Write1'), 1001, 1003, 1)
```

Callbacks validate dependencies of the actual Write, including nested Groups,
expressions and hidden connections. Unrelated invalid nodes do not block it.
Enabled ungenerated, missing or corrupt results fail with the node name and
remediation. Legacy alpha-only results additionally reject stale or unverifiable
upstream inputs. Frozen precomps do not traverse the live source during render.
Switching off **Preview rendered precomp**, or an explicitly disabled node, bypasses validation
only when bypass is established for the requested range; frame callbacks also
guard animated disables and command-line ranges differing from project limits.

## Troubleshooting and tests

- **Environment/model/CUDA error:** run the Shotbox installer/updater outside Nuke, check the six
  local model paths and NVIDIA driver, then confirm Preferences. Probe never
  loads weights; a successful probe cannot guarantee VRAM fit.
- **Capture failed/cancelled:** confirm a stable square-pixel flat RGB format
  and range. Inspect the retained attempt; Generate allocates another directory.
- **Stale/unverifiable legacy cache:** restore the recorded upstream input, FPS,
  colour context and settings, or Generate again. Do not bypass the manifest.
- **Missing output after relocation:** choose **Advanced → Relink EXRs** and
  select the moved sequence. For legacy caches, keep the whole attempt folder
  and select its `integration.json`.
- **Worker crash/OOM:** use Open log for the retained error message; reduce generation
  dimensions or use segmented processing. Full-range errors do not trigger fallback.

Run ordinary automated tests from the frontend with `venv/bin/python -m pytest -q toolbox/shotbox_matte/tests`. Native host
fixtures are outside pytest collection:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/smoke.py
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/precomp_smoke.py
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/readback_smoke.py
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/render_cached.py --self-test
```

These native smoke fixtures deliberately generate synthetic matte values to
verify host behavior. They do not claim real AI/GPU quality. The process and
manifest contracts are documented in [PROTOCOL.md](PROTOCOL.md).
