# ShotBox Nuke plugin implementation brief v02

Date: 23 October 2026  
Status: Audited build brief; plugin implementation and Nuke validation are still pending  
Intended implementer: GPT-6.1 Sol  
Required host: the user's installed **Nuke 17.1v2**, `/opt/Nuke17.1v2/Nuke17.1`  
Source plan: [Nuke_plugin_plan_v01.md](Nuke_plugin_plan_v01.md)  
Repository baseline reviewed: `8e16b35`, plus the untracked v01 plan

## 1. Implementation instruction

Implement this brief in the existing ShotBox repository. Build a Python-created
Nuke Group named **ShotBoxMatte** that captures its connected input, runs the
existing ShotBox worker in a separate process, and copies the completed matte
into the original image's alpha. Deliver working code, installation instructions,
automated tests, Nuke smoke-test scripts and an honest verification report.
This is an implementation request, not a request to produce another plan.

Compatibility with the installed Nuke 17.1v2 is mandatory. Use that executable
for host tests and verify `nuke.NUKE_VERSION_STRING` reports `17.1v2`; tests in
another Nuke version do not satisfy this requirement. Do not require the user
to downgrade or upgrade Nuke to use the plugin.

Read the current repository and applicable instructions before editing; the
baseline may have moved. Preserve desktop/CLI behavior, the native inference
recipe and unrelated work. Do not install ShotBox's dependencies into Nuke,
rewrite the inference engine, download weights, or build a web UI. Implement
the core path before optional refinements. Keep v01 and this brief as records.

The Nuke 17.1v2 installation was found locally during this audit; do not assume
Nuke is unavailable without checking the recorded executable. Where a license,
Windows, a suitable GPU, or that installation in a later environment is
unavailable, complete the independent implementation and automated checks,
supply runnable host tests,
and identify the exact unverified gates. Do not substitute mocks for host or
GPU evidence, and do not describe an untested plugin as production-ready.

## 2. Audit findings and decisions

The v01 architecture is sound: a saved Group, disk caches and an external
ShotBox worker are appropriate. Its main gaps are unspecified contracts,
unproven host assumptions and overly broad geometry/release promises.

| Priority | Finding in v01 | Decision for v02 |
| --- | --- | --- |
| Required | A plan over captured files cannot run before those files exist. | Run an environment probe before capture, then a complete file preflight after capture. |
| Required | `--worker` executes immediately; `--dry-run` does not change that branch. | Keep the worker unchanged; add a small external bridge for probe, preflight and validation. |
| Required | Arbitrary Nuke metadata cannot be added to flat `JobOptions` JSON. | Separate `worker.json` from versioned `integration.json`. |
| Required | An output-destination extension is unnecessary for the first release. | Place captures inside the project cache; existing sibling output naming keeps results there. |
| Required | Background capture, snapshot isolation and cancellation are not established for a target host. | Use foreground Nuke capture first; AI processing remains asynchronous. Defer background capture. |
| Required | Changing tight bounding boxes can violate the backend's fixed-raster constraint. Pixel aspect is preserved as metadata, not corrected for inference. | Capture a fixed format rectangle; require square pixels. Explicitly exclude off-format overscan from generation. |
| Required | Export is recoverable on caught failures, not an atomic whole-sequence transaction. | Give each attempt a new directory; switch the active reference only after full validation. Never overwrite an active cache. |
| Required | Capture checksums do not establish that today's upstream pixels are identical. | Track cache integrity and input freshness separately; document detection limits. |
| Required | Saved native nodes cannot enforce Python validation if the integration is absent. | Require headless integration registration and a checked farm startup contract. |
| Required | A 250-frame full-range GPU success is not guaranteed. | Test the request and clean failure behavior; label the mode experimental and assess quality separately. |
| Important | Nuke Python/Qt and ShotBox Python/PyQt are different runtimes. | Host package uses Python 3.11-compatible syntax and Nuke's PySide6; backend retains its existing Python requirements. |
| Important | A single ready/running flag loses the previous result during regeneration. | Maintain attempt state separately from the active completed cache. |

Backend evidence: [JobOptions and planning](shotbox_matte/core.py),
[worker dispatch and events](shotbox_matte/cli.py),
[output reservation and commit](shotbox_matte/export.py),
[image geometry and colour](shotbox_matte/imaging.py),
[temporal limits](shotbox_matte/temporal.py), and
[actual verification limits](docs/VERIFICATION.md).

## 3. Locked first-release scope

- Commercial Nuke/NukeX **17.1v2**, matching the user's installed version.
  Record executable path, version, edition, OS, Python and PySide versions.
  Other builds require their own smoke tests and cannot replace 17.1v2 acceptance.
- The installed Linux host is the primary implementation and acceptance target.
  Windows is an additional intended target; list it as unverified until tested.
- One flat RGB input in a single-view comp; one inclusive contiguous range of
  1–250 integer Nuke frames, including zero and negative timeline frames.
- A stable input format rectangle with pixel aspect 1.0. Capture that rectangle
  at full resolution. Cropped/animated input bounding boxes are normalized to it.
  Off-format overscan is excluded from AI input; generated alpha is zero there.
  Original RGB and its original bounding box remain on the direct output branch.
- Nuke timeline FPS, with no frame skipping, temporal resampling, or inferred
  source FPS. Spatial resizing remains part of the existing model preparation.
- Existing segmented processing by default; existing full-range mode exposed
  under Advanced with a persistent experimental warning.
- A saved cache works without a GPU or ShotBox runtime on render machines;
  Nuke, the small integration package, its registered validator and the cache
  are required there.

Nuke Indie and Non-commercial are outside this first release. Their documented
Python restrictions affect traversal and validation; do not treat them as
commercial Nuke with identical capabilities. See
[Foundry's edition comparison](https://learn.foundry.com/nuke/17.1/content/getting_started/meet_nuke/about_indie.html).

Deferred: background/Frame Server capture, persistent inference services,
concurrent generation queues, farm generation, macOS inference, stereo/deep,
non-square-pixel normalization, arbitrary overscan preservation, NDK/OFX,
Cattery, automatic cache reuse between nodes, and cache garbage collection.
These are deliberate reductions from v01, not completed capabilities.

The installed 17.1v2 headers identify version 17.1, release 2. Its bundled
library documentation lists **Python 3.11.11 and Qt/PySide 6.5.3**; use its
PySide6 binding. ShotBox's project requires Python 3.12–3.13. Keep the runtimes
separate. Sources: [installed version header](/opt/Nuke17.1v2/include/DDImage/ddImageVersionNumbers.h),
[installed library versions](/opt/Nuke17.1v2/Documentation/ThirdPartyLibraries/index.html)
and [pyproject.toml](pyproject.toml). This establishes the installed files and
documented runtime, not a successful Nuke launch or plugin test.

Use the versioned 17.1 API/node references below and the installed documentation
under `/opt/Nuke17.1v2/Documentation/PythonDevGuide/Nuke/` and
`/opt/Nuke17.1v2/Documentation/html/`. Prefer those bundled references when an
online page is unavailable; inspect actual 17.1v2 knobs during milestone 0.

## 4. Architecture and package boundaries

```text
Nuke process                         ShotBox Python process
------------                         ----------------------
shotbox_nuke                        shotbox_matte.nuke_bridge
  native Group + knobs   --JSON-->     probe / preflight / validate
  capture Write
  QProcess controller    --JSON-->   shotbox_matte --worker worker.json
  cache/freshness checks <--events--   existing engine and export
       |
       +-- native raw Read + Copy <-- completed EXRs
```

The Nuke package may import the standard library, `nuke`, `nukescripts` and the
PySide shipped by that Nuke build. It must not import `shotbox_matte`, PyQt6,
PyTorch, LTX, OpenImageIO or the ShotBox virtual environment into Nuke. Do not
copy the backend into the host package. Bridge processes can import the backend.

Use native knobs rather than a separate dockable application. Create a Group,
not a compiled node or a gizmo that depends on an external graph definition.
Store only short callbacks into the integration package. Register GUI menus
in `menu.py`; register headless validation and lifecycle hooks in `init.py`.
Import Qt/controller code lazily when the GUI needs it.

Nuke 17.1v2/PySide6 is the required adapter. Do not install a new Qt binding or add
untested PySide2 compatibility. All Nuke API and UI mutation stays on the main
thread. Use QProcess signals for child-process I/O; filesystem hashing may run
in a plain worker thread without Nuke calls. Never synchronously wait for the
GPU process in the UI thread. See
[Foundry threading guidance](https://learn.foundry.com/nuke/developers/17.1/pythondevguide/threading.html).

## 5. Node behavior and controls

The internal output graph is:

```text
Input -----------------------------> Copy (B: original) ---> output switch
Raw cached Read -> spatial black --> Copy (A: matte red)           |
               \------------------> matte-only branch ------------+--> Output
Input ----------------------------------------------------------/  (no result)
```

Copy only `rgba.red` from the matte to `rgba.alpha` on the original branch.
Leave RGB and other input channels unchanged; use the original branch's format,
bounding box, metadata and range. No colour transform, clamp, Premult or Unpremult
belongs on that output branch. Configure actual input indices from a tested
Nuke node, not assumptions about A/B socket order. See
[Copy node semantics](https://learn.foundry.com/nuke/17.1/content/reference_guide/channel_nodes/copy.html).

Read mattes with `raw=True`, missing-frame behavior `error`, and out-of-range
behavior `black`. Disable implicit localization for these immutable caches.
Explicitly black outside the recorded capture rectangle; do not edge-extend
the matte. Apply the saved frame mapping in section 6. Matte-only output has
R=G=B=matte and alpha=1 for viewing. Raw values below zero or above one remain
unchanged. See [Read controls](https://learn.foundry.com/nuke/17.1/content/reference_guide/image_nodes/read.html).

Before the first result, show **Not generated** and pass the input through in
the interactive viewer. A production Write depending on an enabled, ungenerated
ShotBoxMatte must fail validation. After generation, generated alpha is zero
outside the processed timeline range; original RGB still passes through.

| Control | Contract/default |
| --- | --- |
| Frame In / Out | Integer inclusive range; initialize from project range, but reject more than 250 frames instead of silently truncating. |
| FPS | Project FPS; save its click-time value. A later project FPS change makes the result stale. |
| Generation height / width | Height 544 initially; width calculated from captured raster ratio and shown read-only. Display actual fitted/padded dimensions after preflight. |
| Input colour encoding | Required explicit choice: Linear Rec.709 (`linear-srgb`), ACEScg (`acescg`), or display sRGB (`srgb`). Initial value is Unset, which blocks Generate. |
| Capture area | Read-only format rectangle and notice that off-format overscan is excluded. |
| Frame processing | Segments by default; Full range · experimental under Advanced. |
| Output | Original RGB + generated alpha, or Matte only. This display choice does not invalidate generation. |
| Advanced | Seed 42, offload `cpu`, tiled reference VAE (`low_vram=False` initially), EXR precision `half`. Preserve backend meanings. |
| Cache root | Explicit writable directory; suggest a `.shotbox-cache` folder beside saved comps. Unsaved comps require a selected persistent location. |
| Actions | Generate, Cancel, Reload Result, Validate Cache, Relink Cache, Open Output Folder, Open Log. |
| Status | Attempt stage/progress/elapsed time, active cache validity/freshness, short attempt ID and actionable errors. |

Preferences store the ShotBox application directory, external interpreter,
model root/optional model configuration, and preferred cache root. Resolve
interpreter candidates using the conventions in [launcher.py](launcher.py),
including `.venv-windows` and `.venv-linux`, without importing that application
module in Nuke. Permit an explicit interpreter override. Generation must never
silently install dependencies or open the ShotBox desktop.

Persist hidden, schema-versioned knobs for node UUID, active manifest path,
active attempt ID, timeline range, captured view/format and integration version.
Keep detailed provenance in the manifest rather than huge knob scripts.
Settings changes affect the next attempt; they cannot retime the active result.

## 6. Capture, colour and exact frame mapping

### Capture sequence

1. Acquire the session's single-attempt lock; validate connection, edition,
   single view, range, colour choice, persistent cache path and square pixels.
   Allocate an exclusive attempt directory and immutable settings snapshot.
2. Run the external environment probe. A writable-path check may create and
   remove a tiny owned test file; do not call this operation entirely read-only.
   Report disk capacity and a conservative capture/output size estimate.
3. Record the upstream graph, relevant expressions/dependencies, format, FPS,
   Nuke/build identity and colour configuration. Record a fingerprint before
   capture and again after it. A serialized graph is provenance, not proof
   that external source files are frozen.
4. Create a temporary capture-only branch connected to the Group's actual input.
   Add a Crop with a frozen input-format box, `intersect=False`, `reformat=False`,
   `crop=True` and zero softness. Keep the visible output graph untouched.
   Verify that pixels outside an incoming smaller bbox become black, not edge
   extensions, in the host test. See [Crop controls](https://learn.foundry.com/nuke/17.1/content/reference_guide/transform_nodes/crop.html).
5. Create an owned Write: RGB-only, single-part float32 EXR, lossless ZIP,
   `raw=True`, `autocrop=False`, no proxy file, and no ACES-compliant-write override.
   Set `frame_mode='offset'`, `frame=1-start`, and file `capture.%04d.exr` in
   `capture_v001`. The Write offset changes filenames while the upstream graph
   evaluates at the original timeline frame. Confirm enum strings/knobs in the
   actual target build. See [Write controls](https://learn.foundry.com/nuke/17.1/content/reference_guide/image_nodes/write.html).
6. Save and disable root proxy mode, then call foreground
   `nuke.execute(write_node, start, end, 1, views=[view], continueOnError=False)`
   on the main thread. This stage occupies Nuke; expose cancellation through
   Nuke's render progress UI. Do not promise that the Group's Cancel button can
   interrupt a blocked foreground call. Verify the exact call/cancel behavior
   in milestone 0. Viewer zoom/ROI is not the capture domain. See the
   [execute API](https://learn.foundry.com/nuke/developers/17.1/pythondevguide/_autosummary/nuke.execute.html).
7. In `finally`, remove only owned temporary nodes and restore proxy, frame,
   selection and other temporarily changed host state. Avoid making capture
   cleanup an unexpected user undo operation; test undo/redo behavior.
8. Treat cancellation, render error, changed graph/dependencies during capture,
   or an incomplete sequence as failure of this attempt; do not launch inference.
   After success, the EXRs form the immutable pixel snapshot for generation.
   Preflight every captured frame before running the worker.

The input format dimensions/rectangle must remain constant across the selected
range. Reject changes, deep input, multiple views or non-square pixels with
specific remediation. A varying incoming bbox alone is allowed because the
capture rectangle is fixed. Do not infer EXR coordinates from Nuke's bbox
coordinates: verify actual headers and round-trip corner markers, including
vertical orientation, through Nuke Write → ShotBox → Nuke Read.

### Colour and source alpha

Capture the evaluated connected RGB as-is, with no Viewer LUT and no implicit
Write transform. The colour control describes those captured values; it does
not choose a Nuke output transform. Match it to the backend `source_color`.
Unsupported working spaces require an explicit upstream conversion before
ShotBoxMatte. Do not infer encoding from `.exr` or label every working space
linear Rec.709.

Backend linear-sRGB preparation clips and sRGB-encodes; ACEScg first transforms
AP1/D60 to Rec.709/D65. This is not a show's OCIO display/view transform.
For a show-specific display preparation, artists provide that conversion and
choose `srgb`. Record OCIO configuration/context identities where available.
Source alpha is ignored: no automatic unpremultiplication. Artists needing
straight RGB must prepare it explicitly upstream. Original output RGB remains
exactly the connected input, including its existing premultiplication state.

### Frame map

For Nuke range `[start, end]`, define `N = end - start + 1` and
`cache_index = nuke_frame - start + 1`, for indices `1..N`.

| Nuke range | Capture/output indices | Read expression |
| --- | --- | --- |
| 1001–1003 | 0001–0003 | `frame - 1001 + 1` |
| -2–0 | 0001–0003 | `frame - (-2) + 1` |
| 0–0 | 0001 | `frame - 0 + 1` |

Set Read `first/last` to `1/N` and its frame mode to the equivalent expression.
Persist the active result's start/end separately from editable next-job knobs.
Add an explicit timeline-range switch to a zero-matte branch so boundary
behavior does not depend solely on Read defaults. Preserve upstream Nuke
retimes by evaluating at the original timeline frames; do not shift time on
the capture input to obtain positive filenames.

## 7. Cache layout and process contracts

### Attempt ownership

```text
<cache-root>/<node-uuid>/<attempt-uuid>/
  integration.json                  Versioned integration record
  runtime.json                      Probe request
  worker.json                       Flat JobOptions only
  worker.json.cancel                Created only to request cancellation
  events.jsonl / stderr.log          Separate complete streams
  capture_v001/capture.0001.exr      Immutable positive-numbered inputs
  capture_matte_v001/               Actual name comes from worker result
    capture_matte_v001.0001.exr
  backend_jobs/<backend-job-id>/
    _shotbox_job.json
    _shotbox_source.png
    _shotbox_matte.png
```

Set child `SHOTBOX_JOB_DIR=<attempt>/backend_jobs`. Existing export derives
the matte folder beside `capture_v001`; no new backend destination option is
required. Existing collision/version locks remain in use. Always trust the
validated result's actual paths, not the preview or assumed `_v001` name.
Use absolute resolved paths for processing. Integration-owned paths stored in
`integration.json` should be relative to its attempt root for relocation.
The backend manifest and saved worker result retain original absolute paths as
provenance. After relocation, integration-relative paths resolved under the
current attempt root are authoritative for owned files. Validate them against
the recorded identities and original path mapping; do not reopen old absolute
paths or rewrite immutable `worker.json` to make relinking work. Required live
upstream dependencies remain separate from relocated cache files.

Keep captures, records and completed outputs by default. No automatic recursive
cache cleanup in v1. Relink changes a node's reference only after validating a
complete moved attempt; it must not copy, overwrite or delete source files.
Save As leaves existing cache references valid and does not move the cache.

### New bridge, running only in ShotBox Python

Add `shotbox_matte/nuke_bridge.py` with these explicit entry points:

```text
<python> -u -X utf8 -m shotbox_matte.nuke_bridge probe --request <runtime.json>
<python> -u -X utf8 -m shotbox_matte.nuke_bridge preflight --job <worker.json>
<python> -u -X utf8 -m shotbox_matte.nuke_bridge validate --attempt <integration.json>
```

New integration requests/reports use `schema_version=1` or `protocol_version=1`
as appropriate. The existing flat `worker.json` is explicitly exempt: preflight
consumes unversioned JobOptions and returns a versioned bridge report.
Specify fields in a shared fixture/schema document; validate
types, required fields and supported versions, and fail clearly on incompatible
contracts. Host-side schema utilities must remain standard-library-only.

| Operation | Required behavior and successful terminal event |
| --- | --- |
| `probe` | Request contains model root/optional configuration and explicit model overrides. Resolve all six model paths with existing backend precedence; report Python, dependency versions, model-header validation, CUDA availability/bf16 capability, backend `build_metadata()`, pinned LTX revision and bridge protocol. Emit `{"type":"probe","protocol_version":1,"report":{...}}`. Never load weights or start inference. |
| `preflight` | Load flat JobOptions; call `plan_job()`, inspect every selected header, fully decode/check finite RGB one frame at a time, hash capture files, and check exact files `1..N`, dimensions/windows/PAR, colour, options, padded spatial and temporal bounds. Emit `{"type":"plan","protocol_version":1,"plan":{...},"capture":{...}}`. Do not reserve output or load weights/CUDA. |
| `validate` | Read the recorded worker result and backend manifest; require `status='complete'`, expected count, exact frame map and owned paths. Open/decode every EXR, verify geometry, finite RGB grayscale, precision and hashes; check source snapshot identity. Emit `{"type":"validation","protocol_version":1,"report":{...}}`. Do not load models or reserve output. |

Bridge stdout is newline-delimited JSON; diagnostics go to stderr. Errors use
`{"type":"error","message":"..."}`. Exit 0 means a valid successful terminal
report; 1 means operation failure; 2 means CLI usage error. Unknown protocol
versions block generation. Emit progress for long scans. Parent cancellation
may terminate these non-writing helper processes; capture/inference cancellation
follows their distinct contracts. Exiting zero without a valid terminal report
is an error. Bridge preflight is independent of GPU availability; only the
separate probe checks CUDA. A successful probe is not a guarantee of VRAM fit.

Do not import `NativeAlphaEngine` or call `run_job()` to implement preflight.
Validate all frames explicitly: current `plan_job()` alone does not establish
their dimensions. Use existing discovery, model, image and temporal helpers;
avoid copying their implementation. Supply missing explicit option validation
at this boundary without changing existing worker defaults.

### Existing worker

```text
<python> -u -X utf8 -m shotbox_matte --worker <worker.json>
```

Run with application root as cwd and an argument list, never a shell command.
Use a per-child environment; do not change Nuke's process environment. Remove
inherited `PYTHONHOME`, `PYTHONPATH`, Qt plugin paths and known Nuke-injected
library directories. Set the intended child Python/UTF-8 environment and
`SHOTBOX_JOB_DIR`. Preserve required system, driver and license paths; do not
blindly erase all `PATH`/`LD_LIBRARY_PATH`. Record the sanitization policy and
verify it on each target OS without logging unrelated environment secrets.

Required worker values, using the actual immutable snapshot:

```json
{
  "input_path": "/project/.shotbox-cache/node/attempt/capture_v001",
  "sequence": null,
  "source_color": "linear-srgb",
  "fps": 24.0,
  "frame_skip": 0,
  "frame_cap": 0,
  "source_start": 1,
  "source_end": 3,
  "generation_width": 960,
  "generation_height": 544,
  "seed": 42,
  "suffix": "_matte",
  "first_frame": 1,
  "padding": 4,
  "precision": "half",
  "existing": "version-up",
  "models": {
    "transformer": "/models/transformer.safetensors",
    "text_encoder": "/models/text_encoder.safetensors",
    "video_vae": "/models/video_vae.safetensors",
    "audio_vae": "/models/audio_vae.safetensors",
    "spatial_upsampler": "/models/spatial_upsampler.safetensors",
    "alpha_lora": "/models/alpha_lora.safetensors"
  },
  "offload": "cpu",
  "low_vram": false,
  "temporal_mode": "segmented"
}
```

Paths above are illustrative, not filenames to invent or download. Use the
probe's resolved real model paths. `source_end` is N; dimensions/FPS/colour
come from the current capture. All six paths are necessary: the existing worker
branch does not invoke CLI model discovery. Never add provenance, a schema key,
or a user prompt to this flat payload.

Existing worker events are:

```json
{"type":"progress","stage":"prepare","current":1,"total":3,"message":"..."}
{"type":"result","result":{"status":"complete","output_dir":"...","frame_count":3,"first_source":"...","first_output":"...","source_preview":"...","matte_preview":"...","manifest":"...","job_dir":"..."}}
{"type":"error","message":"..."}
{"type":"cancelled","message":"..."}
```

Use an incremental UTF-8 decoder and newline buffer, including the final
unterminated line. Preserve stderr separately; do not merge it into JSON.
Bound buffers for malformed/oversized output. Unknown progress stages can be
displayed generically; malformed terminal events must fail safely. Progress
may interleave preparation, inference, decode and export; do not assume stages
or percentages are globally monotonic.

Require normal exit code 0, a well-formed successful result, and bridge validation
before activation. Cancellation returns 130; execution errors return 1. Nonzero
exit, crash, or missing result leaves the previous active reference unchanged.

### Integration manifest

Define and test a concrete schema containing at least:

- Schema/integration versions; node/attempt IDs; timestamps and attempt state.
- Relative owned paths, exact worker JSON digest, captured range/view/FPS/format,
  selected colour, source-alpha policy, frame map and per-capture SHA-256/size.
- Graph/settings fingerprint, known source dependencies and identities,
  OCIO context/config identity, host identity and explicit unresolved dependencies.
- Backend build ID/source hash, pinned LTX revision and resolved model identity
  information. Path/size/mtime is not a full model checksum; record which was used.
- Worker exit/result, backend manifest location, expected output filenames,
  per-output SHA-256/size/geometry and validation report.

Write manifests via a temporary sibling and replace; tolerate an interrupted
update on recovery. Backend status must be `complete`, not `ready`, `running`
or `committing`. Capture hashes establish the generated snapshot's identity;
they must not be presented as proof of the current live graph's pixel identity.

## 8. Lifecycle, cancellation and cache validity

Keep two independent records:

```text
attempt: idle -> probing -> capturing -> preflight -> generating -> validating
                                                        \-> complete / failed / cancelled
active cache: none | valid | missing/corrupt
freshness: no detected change | stale | unverifiable
```

Every active attempt stage can fail or be cancelled. Hold the per-session lock
through validation, releasing it in every terminal path. Reject duplicate
Generate clicks and a second node's Generate while busy; no implicit queue.

Controllers are keyed by node UUID plus attempt UUID and script-session token.
Never resolve asynchronous completion through current selection or `thisNode()`.
Only a matching live owner can activate a result. Node rename is harmless.
Copy/paste receives a new node UUID; it may keep a validated read-only cache
reference but never inherits a running controller. Reconcile UUID collisions
on load/paste/undo while preserving legitimate saved cache references.

During generation, Cancel creates `<worker.json>.cancel`. Wait cooperatively;
after 30 seconds without exit expose **Force stop worker**. Terminate/kill only
the owned QProcess, with a short grace interval, and retain the attempt for
diagnosis. Never kill processes by executable name. A killed process may leave
locks or partial exports; do not mark it complete or remove unknown recovery
files automatically.

A cancel arriving after commit may still produce a valid successful result:
the existing worker intentionally reports success in that race. Validate and
accept it if the owner and freshness checks allow; explain that completion
preceded cancellation. On Nuke exit/script close/node deletion, invalidate
callbacks, request cancellation and ensure owned processes are reaped. A
bounded shutdown may force-stop owned children; it must not change prior caches.

Activation is a main-thread update only after validation and a fresh owner/
input check. Update the Group's read path, map and active record as one guarded
operation; restore the previous settings if it fails. A graph/settings change
during processing leaves the completed attempt available for inspection but
does not automatically replace the current active result. Show its stale status.
On reopen, an interrupted attempt becomes interrupted/failed, not running;
restore only the last recorded completed active cache. Recovery of an unactivated
complete attempt requires explicit Reload Result and normal validation.

### Integrity versus freshness

Integrity checks validate schema, complete backend status, exact files/count,
geometry and frame map. The explicit Validate Cache action verifies full
checksums. Headless checks use only standard-library manifest/path/hash checks
and Nuke's own image decoding; they must not launch any ShotBox process.

Recompute relevant graph/settings fingerprints at Generate, completion,
Reload/Validate, script load and render preflight. Include upstream topology,
image-affecting knob animation/expressions, relevant hidden/expression dependencies,
root FPS/format, colour context and known media identities. Exclude selection,
node position, label, viewer frame and this node's own status/output-view knobs
and result-display internals. For an upstream ShotBoxMatte, include its output
selector, active cache identity and frame mapping: those can change the RGB
captured by the downstream node. Define this canonicalization in code and
test both real changes and harmless UI edits.

Use callbacks to invalidate promptly, but not as the only evidence: Foundry
documents limits to `knobChanged` delivery. Unknown/unresolvable dependencies
must be reported; a failure to compute the required fingerprint makes freshness
unverifiable and blocks strict rendering until resolved. A matching fingerprint
means **no detected change**, not mathematical proof that an arbitrary procedural
graph produces identical pixels. External content replaced with unchanged
metadata may evade a lightweight check; explicit dependency hashes or recapture
are needed for stronger verification. See
[Foundry callback behavior](https://learn.foundry.com/nuke/developers/17.1/pythondevguide/callbacks.html).

### Rendering and reopening

Register an `addBeforeRender` validator from `init.py`. Inspect ShotBoxMatte
nodes contributing to the current Write, including nested Groups and relevant
expression/hidden dependencies. Fail with node name and remediation for enabled
nodes with no cache, missing/corrupt files, known stale or unverifiable input.
Exempt only the integration's identified temporary capture Write from checking
its own not-yet-generated node; upstream ShotBox nodes still require valid caches.
Unrelated invalid nodes must not block unrelated Writes. Ordinary disabled-node
bypass remains an explicit artist choice, but exempt a node only when bypass
is established across the requested render range. A disabled value at the
current GUI frame is insufficient for animated/expression-driven disable.
If the range or bypass cannot be established, validate conservatively or enforce
the guard per rendered frame. Test dependency traversal and animated bypass
in Nuke.

The render deployment must load this integration. Supply a farm preflight
entry point that explicitly imports it and validates the script before render;
missing installation must fail that entry point. A saved native Group alone
cannot promise Python guards when the package is absent. Test the supported
launch path, including background render sessions, and document this requirement.
Never launch inference on script load, scrub, Reload, Validate or render.

## 9. Files to implement

```text
integrations/nuke/
  init.py                         Headless-safe registration
  menu.py                         GUI menu registration
  README.md                       Setup, usage, limits and render deployment
  PROTOCOL.md                     Versioned bridge/manifest fields and fixtures
  shotbox_nuke/
    __init__.py                   Small public entry points, no eager GUI imports
    node.py                       Group creation, controls and saved graph
    controller.py                 Attempt state and process ownership
    capture.py                    Temporary capture branch and host restoration
    process.py                    QProcess adapter and child environment
    protocol.py                   Pure Python schema/event/frame-map utilities
    cache.py                      Integrity, relocation and active references
    freshness.py                  Fingerprints and known dependency tracking
    lifecycle.py                  Idempotent callback registration and identities
    render.py                     Write dependency validation and farm preflight
    preferences.py                Paths and settings
    qt_compat.py                  Target-host PySide6 access
  tests_nuke/
    smoke.py                      Host fixture construction and round trips
    render_cached.py              Headless success/failure checks
shotbox_matte/
  nuke_bridge.py                  External-only probe/preflight/validation
tests/
  test_nuke_bridge.py
  test_nuke_protocol.py
  test_nuke_cache.py
  test_nuke_lifecycle.py
docs/
  NUKE_VERIFICATION.md            Exact tested matrix, results and remaining gates
```

Separate the pure controller state machine from Qt/Nuke adapters so failure
tests can run outside Nuke. Host-dependent tests live outside the normal pytest
collection. Add import-boundary checks: pure utilities and headless registration
must not accidentally import the inference stack or create a QApplication.
Do not add the host package to ShotBox's dependency environment as a requirement
for ordinary CLI/desktop use.

Provide a plugin-path install snippet that the artist can add to their existing
`.nuke/init.py`; never replace their files. Make repeated startup/registration
idempotent. Document `launch.bat --setup-only` / `./launch.sh --setup-only`,
preferences, colour/alpha preparation, capture blocking/cancellation, relinking,
cache storage costs, render installation and troubleshooting.

## 10. Build order and acceptance gates

| Milestone | Implement | Evidence required before claiming it passes |
| --- | --- | --- |
| 0. Host contract spike | Launch `/opt/Nuke17.1v2/Nuke17.1`; assert version `17.1v2`, record edition/runtime and construct native Group/Read/Copy/Crop/Write; verify knobs, write offset, foreground cancel and raw read. | A three-frame round trip in the installed Nuke 17.1v2, including a negative timeline range, correct corner positions and restored proxy state. |
| 1. Pure contracts and bridge | Frame maps, versioned schemas, process events, external probe/preflight/validation and immutable attempt layout. | Tests with real small EXRs and controlled workers; preflight performs no reservation/weight load; current CLI/desktop tests still pass. |
| 2. Complete controlled workflow | Node, preferences, capture, process launch, active cache update and viewer output. | Generate from graded/retimed connected pixels using controlled inference; original RGB/auxiliary channels preserved and first/last frames aligned. |
| 3. Failure and persistence | Cancellation, late events, ownership, stale results, missing/corrupt output, reload/relink, copy/paste, close/delete and strict render checks. | Failure matrix below; cached headless render works with the ShotBox interpreter deliberately unavailable. |
| 4. Native GPU integration | Existing real worker with short shot, segmentation and experimental request. | Actual aligned matte in Nuke; native success/error evidence separated from simulated tests and quality review. |
| 5. Packaging and handoff | Plugin install folder, protocol/usage docs, test fixtures and verification matrix. | Clean startup without replacing user config; exact OS/build results; no unsupported compatibility or quality claims. |

Do not wait for unavailable host access before implementing independent bridge,
schema or state-machine work. Do not bypass a failed host gate by marking a
mocked test as host success. Keep blockers and runnable next checks explicit.

### Automated and host test matrix

| Area | Required cases and expected result |
| --- | --- |
| Protocol | Split UTF-8 and JSON lines, multiple events per read, final partial line, stderr noise, unknown stage, malformed/oversized terminal event, crash, nonzero exit, and exit 0 without result. Only complete valid results advance to validation. |
| Planning | Missing models/dependencies, invalid colour/range/options, 1/250/251 selected frames, gaps/duplicates, changing format/PAR, non-finite capture and output, unwritable/full cache. No weights or output reservation during file preflight. |
| Frames | Starts 1001, 0 and -2; single frame; both endpoints and one frame outside each boundary. Correct filenames and zero generated alpha outside range. |
| Capture | Grade, retime, nested Group/expression dependency, changing tight bbox, cropped input, portrait/square/widescreen, proxy enabled and interrupted capture. Capture matches evaluated pixels inside the fixed format; host state is restored. |
| Colour/channels | Linear Rec.709, ACEScg, display sRGB and unsupported space; known corner/colour patches; no alpha, premultiplied input and auxiliary layers. Float RGB output equality with direct input; raw matte values such as -0.1 and 1.25 are preserved. |
| Cache integrity | Missing/truncated/wrong-size/corrupted frame, wrong count/map, manifest with non-complete status, path outside owned attempt, moved complete cache, saved/reopened script and interrupted manifest update. Reject invalid caches, retain last valid active result. |
| Freshness | Upstream image knob/animation, topology, media identity, FPS/format/OCIO and upstream ShotBox output/cache changes; changes during generation; harmless movement/selection and this node's output-view edits. Detect relevant changes without invalidating on cosmetic edits. |
| Ownership | Double-click Generate, two nodes, rename, copy/paste, undo/redo, delete, script close/reopen and delayed callbacks. No result activates on the wrong node/session; no duplicate callbacks or orphaned worker. |
| Cancellation | Capture cancel, preprocessing cancel, inference/export cancel, late cancel after commit, force-stop during commit. Incomplete attempts never replace the previous cache; successful committed results follow the documented late-cancel rule. |
| Render | Valid cached render with no external Python/GPU; no AI startup; missing/stale/unverifiable cache errors; unrelated Write succeeds; nested dependencies; static and animated disabled bypass; farm entry point fails when integration is unavailable. |
| Platforms | Paths with spaces/Unicode, Windows drive and UNC paths where used, Linux paths, Nuke-injected Python/Qt/library variables. Validate on actual hosts before declaring support. |

For generation width, use the existing height-to-aspect-ratio calculation as
the reference, extracting a reusable pure backend helper if necessary. Do not
import the desktop GUI into Nuke. Test the host calculation against backend
fixtures and let the external spatial plan be authoritative.

At 24 FPS the existing 250-frame segmented plan has three overlapping passes
with padded lengths 121, 121 and 49 and exactly 250 output frames. A 250-frame
full-range request pads to 257 model frames. Verify these with controlled tests.
For real GPU testing, require a short successful generation first; validate a
long segmented run at a suitable reduced resolution, then separately attempt
full-range. Full-range may succeed or produce a clean actionable OOM/error;
it must not silently fall back. Inspect segment joins and subject consistency
separately from frame-count correctness.

The official Alpha Gen guidance recommends short segments, specifies spatial
and temporal constraints, and does not support prompt/mask subject selection.
Keep the backend's existing additional duration policy; do not infer that every
local limit appears verbatim in the current upstream guide. See
[Alpha Gen documentation](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen).

## 11. Completion and reporting

Run the existing regression suite and relevant lint/compilation checks after
implementation. Add focused tests for the new failure boundaries, using real
EXRs where practical. Compile/import the host package under Nuke 17.1v2's
bundled Python 3.11.11 and execute GUI/headless acceptance in that exact build; the project's Python 3.12 Ruff target alone does not prove Nuke Python
compatibility. Never claim this planning audit executed Nuke or generated mattes.

The implementation handoff must include:

1. The installable integration and external bridge, with no placeholder critical
   paths, fake inference, unfinished lifecycle handling or hidden auto-generation.
2. A short usage guide covering setup → create/connect → explicit colour/range
   → Generate → inspect alpha → save/reopen → cached render.
3. Exact automated commands/results and an OS/Nuke/GPU matrix in
   `docs/NUKE_VERIFICATION.md`, distinguishing passed, failed and not run.
4. Known limitations, any changed decisions with reasons, and precise remaining
   host/GPU checks when unavailable. Experimental quality remains separate.
5. Pending changelog notes for the implementation actually delivered. This v02
   document is a plan and must not be logged as a completed plugin feature.

Declare the compatibility requirement fulfilled only after the installed
Nuke 17.1v2 acceptance gates pass, backed by recorded host-test results.
If Nuke 17.1v2 host gates remain unverified, the user's compatibility requirement
is still outstanding. Describe the deliverable as implemented but awaiting
those named checks, rather than complete or release-verified. Passing tests in
a different Nuke build cannot close this requirement.
