# ShotBox Nuke plugin implementation plan v01

Date: 23 October 2026  
Status: Proposed implementation plan  
Selected approach: Python gizmo with a Generate button

Build a ShotBoxMatte node that accepts an input in Nuke, processes a selected
frame range through the existing ShotBox engine, and returns the original RGB
with the generated matte in alpha. Artists stay inside Nuke while AI generation
runs in ShotBox's separate virtual environment. Completed mattes are cached on
disk and remain usable after saving and reopening the comp.

## 1. First release scope

- One connected, flat 2D input and one output. Start with a single view.
- An inclusive, contiguous range of up to 250 frames, with one output per Nuke
  frame. Keep frame skipping out of the first release to avoid implicit retiming.
- Generation height with width calculated from the captured input's aspect ratio.
- Segments as the default, retaining the experimental full-range option.
- Generate, Cancel, Reload Result, Validate Cache, Open Output Folder and Open Log.
- Progress, elapsed time, errors and clear ready/running/stale/cancelled states.
- Output choices: original RGB with generated alpha, or matte-only viewing.
- Original RGB passes through without a colour transform or automatic Premult.

Generation runs only when Generate is pressed. Scrubbing and ordinary comp
renders read the completed cache. Persistent inference services, compiled NDK
or OFX plugins, Cattery conversion, automatic render-farm generation, stereo and
deep-image support are later work.

## 2. Artist workflow

1. Prepare the existing ShotBox installation using `launch.bat --setup-only`
   or `./launch.sh --setup-only`.
2. Install the Nuke integration and configure its ShotBox application path.
3. Create ShotBoxMatte from a menu entry and connect footage or an upstream
   processing branch.
4. Choose the frame range, input colour encoding, generation height and mode.
5. Press Generate. The integration validates the job, captures the input,
   runs ShotBox, and displays progress.
6. After successful completion and validation, the node loads the matte cache
   and exposes it as alpha. A failed or cancelled job does not replace the last
   completed result.
7. Regenerate after upstream changes. Save the Nuke script with its cache
   reference and job metadata.

## 3. Node design and controls

Use a Python-created Group named ShotBoxMatte. It provides the gizmo workflow
while storing its internal Nuke nodes inside the saved script. Python callbacks
call functions in a small `shotbox_nuke` package; they do not embed installation
paths or large scripts in knob values.

Conceptual internal graph:

```text
Input ------------------------------------> Copy generated alpha --> Output
                                                ^
Cached EXR Read --> red-to-alpha mapping --------|
                         |
                         +--> matte viewing branch
```

The backend currently exports grayscale RGB EXRs, so the integration must
explicitly map the red channel into alpha. Read these files as data, without
colour conversion. Before the first result, pass through the input and label
the node Not Generated. After generation, output zero generated alpha outside
the processed range; do not hold the first or last matte indefinitely.

| Control | Planned behavior |
| --- | --- |
| Frame In / Out | Inclusive Nuke timeline frames; validate 1–250 selected frames. |
| Frames per second | Use the Nuke project's FPS by default; save the value in the job snapshot. |
| Generation height | Editable; update width from the input raster proportions and existing model size limits. |
| Frame processing | Segments by default, or Full range · experimental. |
| Input colour | Explicit Linear Rec.709, ACEScg or display sRGB; explain the expected encoding. |
| Output | Original RGB plus generated alpha, or matte-only viewing. |
| Advanced | Seed, offload, VAE memory option and EXR precision. |
| Cache location | Writable project cache directory, selected explicitly for unsaved comps. |
| Status | Stage, progress, elapsed time, job ID and actionable errors. |

Model paths and the ShotBox interpreter belong in integration preferences, with
a per-node override if required. Reuse the existing backend parameter meanings.
Full range retains the existing experimental warning and does not silently
fall back to segmentation.

## 4. Input capture, colour and frame alignment

Capture the evaluated pixels entering ShotBoxMatte, including upstream grades,
crops and retimes. Do not identify an upstream Read node and process its source
file while ignoring the intervening graph.

- Freeze the selected settings and upstream graph into a job snapshot.
- Render that snapshot to a unique staging EXR sequence using Nuke's background
  rendering facilities where supported. Keep Nuke API and UI changes on the
  main thread. Confirm background-render availability on the target edition;
  provide cancellable foreground capture if necessary.
- Capture at full resolution with proxy and Viewer ROI restrictions disabled,
  using the explicitly selected view. Use the project timeline FPS after retiming,
  rather than silently substituting the original media's FPS.
- Preserve the full raster, data/display windows and pixel aspect. Reject
  changing raster dimensions within a job, deep inputs and unsupported views
  with a clear message. Respect the backend's existing metadata constraints.
- Write captured pixels without an implicit output colour transform. Match
  ShotBox's `source_color` to the encoding actually captured. Do not treat an
  arbitrary Nuke working space as linear Rec.709. For other working spaces,
  require an explicit conversion to a supported encoding before capture.
- Restrict colour preparation to the AI input. The node's output RGB continues
  to come directly from its original input branch.
- Store an explicit map between Nuke timeline frames, staging filenames and
  generated frame indices. Positive staging/output numbering allows negative
  Nuke timeline frames without changing the backend's numbering rules.
- For the contiguous first-release range, use the saved offset to map cached
  frame indices back onto the Nuke timeline. Test first, last and boundary frames.

Input capture and AI generation are separate progress stages. Cancel during
capture must stop the capture task and prevent inference from starting.

## 5. Reuse the existing ShotBox worker

Nuke loads only the integration package and its own Qt bindings. It must not
import ShotBox's PyQt6 application, PyTorch, CUDA libraries or LTX packages.
Nuke's embedded Python and ShotBox's Python can therefore remain different.

Reuse the current worker command as the execution interface:

```text
<ShotBox venv Python> -u -X utf8 -m shotbox_matte --worker <job.json>
```

The existing worker already accepts JobOptions JSON, reports progress/result/
error/cancelled events, and watches a cancellation file. Reuse those contracts
and the existing transactional export behavior.

Required backend additions:

1. Add a read-only preflight operation that accepts the same job JSON and
   returns a plan without loading weights or reserving output. The current
   `--worker` branch executes immediately, so combining it with `--dry-run`
   must not be assumed to provide this operation.
2. Add an optional output/cache destination so results can live in the Nuke
   project's cache rather than being derived from a temporary input folder.
   Preserve current desktop and CLI defaults and output collision protection.
3. Record the Nuke frame map, capture identity and integration version in a
   companion manifest or backward-compatible provenance field.

Preflight checks the interpreter and dependencies, readable model headers,
writable cache, frame range, image dimensions and GPU compatibility without
loading model weights. Recheck the completed capture against the saved plan
before starting inference.

Use asynchronous process management with the Qt bindings shipped by the target
Nuke version. Maintain a controller keyed by node UUID rather than relying on
the current selection or `thisNode()` inside asynchronous callbacks. Parse
partial stdout lines incrementally and save stderr to the job log. Isolate child
Python/Qt library paths from Nuke's environment while retaining required OS and
GPU driver paths.

Start with one GPU generation job at a time per Nuke session; reject another
Generate request with a clear busy status. Persistent workers can be considered
after the first release works reliably.

## 6. Cancellation, cache and lifecycle

Use a state machine:

```text
Idle -> Preflight -> Capturing -> Generating -> Validating -> Ready
                         |             |            |
                         +-------------+------------+-> Failed / Cancelled
```

- Give every generation attempt a unique job directory and immutable settings.
- Cancel cooperatively through the existing cancellation file. If a worker
  remains unresponsive, offer termination of that worker process only. Do not
  terminate unrelated Python or Nuke processes.
- Activate a cache only after the worker reports success and its manifest and
  expected EXR files pass validation. Verify count, geometry and frame mapping.
- Preserve the previous completed cache on failure or cancellation. Clean up
  owned staging files without deleting source media or unrelated cache folders.
- Identify the captured input using checksums; also record graph/settings,
  source dependency information, colour configuration and backend version.
  Treat known upstream/settings changes as stale. Parameter equality alone
  does not prove that an old matte matches new pixels.
- Validate Cache checks the saved manifest, file availability, frame map and
  known dependencies. Where input identity cannot be established, require a
  fresh capture/generation instead of silently adopting a previous job.
- If the graph changes during a job, retain the finished cache as a result of
  its frozen snapshot and mark it stale for the current input.
- Save cache references and frame mapping in the node. Reopening a comp must
  restore completed results without starting inference.
- Copying a node creates a new controller identity. Deleting a node or closing
  Nuke must detach callbacks and handle owned running jobs predictably.
- Do not auto-delete completed caches. Provide an explicit cleanup operation
  limited to integration-owned data.

Cached headless renders must use the saved internal nodes without starting AI.
Missing, incomplete or known-stale results should cause a clear render error
rather than silently render an unrelated matte. The integration package and
cache must be available to render machines; the GPU worker is only needed for
generation.

## 7. Proposed files

```text
integrations/nuke/
  init.py                       Plugin path and headless registration
  menu.py                       ShotBox menu entry
  README.md                     Installation and usage
  shotbox_nuke/
    __init__.py
    node.py                     Group construction and controls
    controller.py               Job state and node lifecycle
    capture.py                  Snapshot, EXR capture and frame mapping
    worker.py                   Preflight, process events and cancellation
    cache.py                    Result validation and cache identity
    preferences.py              Application, interpreter and cache locations
    qt_compat.py                Target Nuke Qt binding adapter
tests/
  test_nuke_worker_protocol.py
  test_nuke_cache_mapping.py
  test_nuke_job_lifecycle.py
```

Keep the inference engine in `shotbox_matte`. Make any backend extensions
optional and backward compatible. Installing the integration should add its
path to Nuke without replacing an artist's existing `init.py` or `menu.py`.

## 8. Delivery milestones

| Milestone | Deliverable | Completion condition |
| --- | --- | --- |
| 1. Host and contract checks | Select the exact target Nuke build; inspect Python/Qt, background rendering and worker setup on Windows first, then Linux. | A controlled external worker reports events inside Nuke without blocking the UI. |
| 2. Node and capture | Group, controls, frozen input capture, colour handling and frame map. | Captured EXRs match the connected graph at the chosen frames and encoding. |
| 3. Generation and loading | JSON preflight, worker launch, output destination and result activation. | A short real GPU run produces an aligned alpha in the node; original RGB is unchanged. |
| 4. Cancellation and persistence | Cache validation, stale states, failure handling, cancellation and script reload. | Failed jobs preserve the last completed result, and reopened scripts restore their cache correctly. |
| 5. Long shots and packaging | Both temporal modes, installation guide and platform verification. | Supported segmented jobs and experimental full-range requests work through the node; limitations are documented per tested platform. |

## 9. Validation and release criteria

Automated tests should cover JSON serialization, fragmented progress events,
worker errors, cancellation, frame maps, cache validation, stale job completion,
duplicate clicks and node deletion. Use controlled workers for deterministic
failure and lifecycle tests. Keep these tests runnable without Nuke or weights
where possible; test actual node construction and host behavior in Nuke.

Manual Nuke and GPU validation must include:

- A short sequence starting at frame 1001, plus a negative timeline range.
- A graded and retimed branch, proving capture uses the connected pixels.
- Portrait, square and widescreen footage, plus supported EXR window metadata.
- Linear Rec.709, ACEScg and display sRGB input; raw matte loading and untouched RGB.
- A 250-frame segmented job and an experimental 250-frame full-range request.
  Inspect segment joins and full-range quality separately from frame counts.
- Cancellation during capture, inference and export; worker crashes and GPU
  memory failures; unavailable or unwritable cache locations.
- Changed inputs/settings while generating, two node instances, copy/paste,
  node deletion, script save/reopen and missing cache files.
- Windows and Linux paths with spaces and Unicode; Nuke-specific environment
  variables must not break the external worker.
- A headless render of completed caches with no inference startup.

Release only for the Nuke builds and platforms actually checked. Automated
worker simulations do not establish host compatibility or native GPU matte
quality. Include installation, colour setup, cache relocation, cancellation and
troubleshooting instructions, and record completed implementation changes in
the project changelog as each milestone is delivered.

## 10. References

- Existing backend: `shotbox_matte/core.py`, `shotbox_matte/cli.py`,
  `shotbox_matte/export.py`, `shotbox_matte/temporal.py` and `launcher.py`.
- [Foundry Python UI integration](https://learn.foundry.com/nuke/developers/latest/pythondevguide/custom_ui.html).
- [Foundry threading guidance](https://learn.foundry.com/nuke/developers/latest/pythondevguide/threading.html).
- [Foundry background frame callbacks](https://learn.foundry.com/nuke/developers/17.1/pythondevguide/_autosummary/nuke.addAfterBackgroundFrameRender.html).
- [Foundry frame server rendering](https://modosdk.foundry.com/nuke/15.2/content/comp_environment/rendering/frame_server_renders.html).
- [Alpha Gen supported workflow and model limits](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen).
- [CorridorKey external runtime architecture](https://github.com/alexandremendoncaalvaro/CorridorKey-Runtime/blob/main/ARCHITECTURE.md), an architectural reference for a possible later native plugin.
