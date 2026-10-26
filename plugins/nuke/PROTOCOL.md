# ShotBox Nuke protocol 1

The host package runs inside commercial Nuke 17.1v2, with only its standard
library, Nuke API and lazily imported bundled PySide6. The bridge and worker run
in the configured ShotBox interpreter, with the application directory as cwd.
Neither startup nor cached rendering starts a bridge, worker or QApplication.

The bridge commands are argument lists, never shell commands:

```text
python -u -X utf8 -m shotbox_matte.nuke_bridge probe --request runtime.json
python -u -X utf8 -m shotbox_matte.nuke_bridge preflight --job worker.json
python -u -X utf8 -m shotbox_matte.nuke_bridge validate --attempt integration.json
python -u -X utf8 -m shotbox_matte --worker worker.json
```

Paths passed to child processes are absolute. Every integration-owned file
record stores a slash-separated path relative to the attempt directory.
Absolute paths, parent traversal, drive prefixes and escaping symlinks are
rejected at the integration boundary. Each attempt has its own capture,
worker request, log streams, backend job records and versioned matte folder.
New project precomps reserve the first unused version of PrecompSwitcher's
literal `WRITE_FILE_TEMPLATE`, defaulting to
`{script_dir}/../renders/precomp/{precomp_name}{ver}/{precomp_name}{ver}.####.exr`.
The version folder contains only final EXRs. Work is allocated separately under
`<cache>/.jobs/<node UUID>/<attempt UUID>`. After publication, failure or
cancellation, only a small `integration.json` receipt remains in that private
job folder. Its `storage:{mode:"published",output_directory:"/absolute/final"}`
points to the output folder, and `precomp.files` stores safe final basenames.
Legacy saved references to removed public receipts use bounded, single-hop
aliases under the configured cache root. Custom-cache final outputs retain the
node UUID/attempt UUID layout separately from `.jobs`.

## Alpha-only publication (current workflow)

New Nuke workers specify `output_channels:"alpha"`; absent mode defaults to
`"rgb"` for existing desktop/CLI and legacy worker files. The backend manifest
records `output_channels:["A"]` (legacy absent means `["R","G","B"]`).
New nodes default to `source_color:"acescg"`. Output alpha is raw data.

`capture.mode:"alpha"` selects one fixed-format RGB capture, without a separate
`source_v001` snapshot. The worker writes the restored matte directly as one
EXR `A` channel using the requested half/float precision. External validation
checks the exact channel, precision, complete finite raster, geometry and hash.
`storage.promote_alpha` describes those validated worker files directly, then
`storage.commit` maps their sequential 1..N filenames to the original timeline.
Same-filesystem hard links avoid another data copy; cross-filesystem copies
preserve bytes. Neither path decodes or re-encodes the EXRs, invokes a Nuke
Write, combines RGB, or changes pixel values.

The retained descriptor uses `precomp.kind:"alpha"`, `channels:["rgba.alpha"]`,
`precision:"half"|"float"`, the timeline first/last and fixed capture dimensions
and `[0,0,width,height]` data window. It requires no `capture.source_files`.
The standalone Read uses only those alpha EXRs with raw enabled. Group
matte-only preview displays alpha as RGB in memory, without changing the files.
The schema-1 RGB publication below remains readable when `kind` is absent or
`"rgba"`; those legacy descriptors still require float32 and original snapshots
unless already migrated to published storage.

## Probe request and report

`runtime.json` is a JSON object with exactly the supported keys below. Only
`schema_version` is required; optional paths may be omitted. `model_config`
may wrap its six component settings in a `models` object; its relative paths
resolve beside that configuration file. Precedence is backend discovery,
configuration file, explicit `models` overrides. No models are downloaded.

```json
{
  "schema_version": 1,
  "model_root": "/show/ShotBox/models",
  "model_config": "/show/ShotBox/models.json",
  "models": {"alpha_lora": "/show/models/alpha.safetensors"},
  "cache_root": "/show/cache/node-id/attempt-id",
  "capture": {"width": 1920, "height": 1080, "count": 3, "precision": "half", "precomp_channels": 4}
}
```

Successful terminal:
`{"type":"probe","protocol_version":1,"report":{...}}`.
The report has `ready`, `errors`, `python`, `dependencies`, `models` (all six
resolved paths), `model_identities`, `model_validation`, `cuda`, `backend`,
`ltx_revision` and `disk`. Model identity uses path, size, mtime_ns and validated
safetensors headers, not full weight checksums. Backend identity is the current
`build_metadata()` source hash. The revision is the backend's declared pinned
LTX revision. A probe queries CUDA/bf16 capability but never loads weights.
It creates/removes a small owned writable-path probe file and estimates space
for capture, output and staged output without compression savings. Probe
failure emits an `error` event with the report and exits 1. VRAM fit is not
established by a successful probe.
Legacy RGB jobs may set the optional integer `capture.precomp_channels`, enabling the additional disk
estimate for a full-channel float source snapshot, final float publication and
staging. It does not change inference precision or worker settings.

## Immutable worker request and preflight

`worker.json` is unversioned, flat `JobOptions`, with **all** fields, no extras:

```json
{
  "input_path": "/show/cache/node-id/attempt-id/capture_v001",
  "sequence": null,
  "source_color": "acescg",
  "output_channels": "alpha",
  "fps": 24.0,
  "frame_skip": 0,
  "frame_cap": 0,
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
  "source_start": 1,
  "source_end": 3,
  "temporal_mode": "segmented"
}
```

The model paths above are illustrative. Use probe-resolved existing paths.
Allowed colours are `linear-srgb`, `acescg`, `srgb`; `auto` is forbidden.
Nuke fixes the sequence/null, skip/0, cap/0, start/1, first/1, padding/4,
suffix/_matte and existing/version-up values. Inclusive `source_end` is 1–250.
Precision is half/float, offload none/cpu/disk, low_vram boolean, temporal mode
segmented/full-range. Seeds are integer 0 through 2^63−1 and FPS is finite and
positive. Boolean values do not satisfy integer fields.

Preflight requires exactly `capture.0001.exr` through `capture.NNNN.exr`, with
no extra files or duplicate indices. Every frame is fully decoded and hashed.
Inputs must be single-part flat float32 RGB EXRs, finite, square pixel, with
fixed equal data/display windows. Spatial and temporal plans come from the
existing backend helpers. Preflight never loads weights, imports torch/native
inference, reserves output or writes files. The report is:

```text
{"type":"plan","protocol_version":1,
 "plan":<JobPlan.to_dict()>,
 "capture":{"count":N,"width":W,"height":H,"geometry":<window>,
            "worker_sha256":<exact JSON bytes SHA-256>,"files":[<file record>, ...]}}
```

The plan's output preview remains provisional. Actual output paths always come
from the completed worker result and subsequent validation.

## Integration manifest and file records

`integration.json` is schema 1, integration version `0.1.0`. The complete
example below is structural: angle-bracket values stand for actual records,
hashes and provenance, and are not literal fixture data to run.

```text
{
  "schema_version":1, "integration_version":"0.1.0",
  "node_id":"<UUID>", "attempt_id":"<UUID>",
  "created_at":"<UTC ISO8601>", "updated_at":"<UTC ISO8601>",
  "state":"complete",
  "original_attempt_root":"/show/cache/node-id/attempt-id",
  "worker_path":"worker.json", "worker_sha256":"<64 lowercase hex>",
  "capture":{
    "start":-2,"end":0,"count":3,"view":"main","fps":24.0,
    "width":1920,"height":1080,"pixel_aspect":1.0,
    "geometry":<window>,"worker_sha256":"<same SHA-256>",
    "files":[<capture file record>,<capture file record>,<capture file record>]
  },
  "frame_map":{"start":-2,"end":0,"count":3,"first":1,"last":3,
               "expression":"frame - (-2) + 1"},
  "source_alpha_policy":"ignored; no unpremultiplication",
  "settings":{ "source_color":"linear-srgb", "<other immutable UI settings>":"..." },
  "fingerprint":{"digest":"<SHA-256>","unresolved":[],"<graph/dependency provenance>":"..."},
  "host":{ "<executable/version/edition/OS/Python/PySide identity>":"..." },
  "probe":<complete successful probe report>,
  "plan":<complete preflight plan>,
  "result":<original complete worker result>, "worker_exit":0,
  "backend_manifest":"backend_jobs/<backend-job-id>/_shotbox_job.json",
  "outputs":[<output file record>,<output file record>,<output file record>],
  "validation":<successful validation report>
}
```

A window uses exact backend coordinates, not inferred Nuke bbox coordinates:

```json
{"x":0,"y":0,"width":1920,"height":1080,"full_x":0,"full_y":0,
 "full_width":1920,"full_height":1080,"pixel_aspect_ratio":1.0}
```

Every file record has `path` (relative owned path), `size` (positive bytes),
`sha256` (64 lowercase hexadecimal characters), `geometry` (the window object)
and `precision` (`float` capture, `half` or `float` matte). For example, the
capture path is `capture_v001/capture.0001.exr`; the output path may be
`capture_matte_v002/capture_matte_v002.0001.exr` after version collision.

The original result retains the worker's absolute `output_dir`, `first_source`,
`first_output`, `source_preview`, `matte_preview`, `manifest`, `job_dir`, plus
`status:"complete"` and integer `frame_count`. Backend records retain their
original absolute paths too. These are provenance, not destinations to reopen.

During validation, state is `validating`, `worker_exit` is 0, `result` is complete,
`outputs` is initially empty and `validation` is initially `{}`. A successful
report supplies file records before state becomes `complete`. Manifests use
a temporary sibling and replace, so an interrupted write cannot install a
partially written JSON document. Unknown versions block use.

### Legacy full-precomp extension

Schema 1 also accepts the optional host-owned fields below; older alpha-only
manifests remain supported. Worker/backend manifests keep their existing format.

```text
"precomp_request": {
  "pattern":"ShotBoxMatte_v01.%04d.exr", "location":"Project precomp",
  "script":"/project/shot/nuke/comp.nk", "name":"ShotBoxMatte"
},
"capture": {
  "<existing capture fields>":"...",
  "source_channels":["rgba.red","rgba.green","rgba.blue","rgba.alpha"],
  "source_files":[<file record for source_v001/source.0001.exr>, ...]
},
"precomp": {
  "state":"complete", "precision":"float",
  "pattern":"ShotBoxMatte_v01.%04d.exr", "first":1001, "last":1003,
  "channels":["rgba.red","rgba.green","rgba.blue","rgba.alpha"],
  "files":[{
    "path":"ShotBoxMatte_v01.1001.exr", "size":<positive bytes>,
    "sha256":"<64 lowercase hex>", "geometry":{"width":1920,"height":1080},
    "box":[0,0,1920,1080], "pixel_aspect":1.0,
    "channels":["rgba.red","rgba.green","rgba.blue","rgba.alpha"]
  }, ...]
}
```

The all-channel raw float source sequence is captured first, numbered 1 through
N. Fixed-format RGB inference input is then derived from that immutable snapshot.
After backend validation, Nuke combines the snapshot with matte red copied to
alpha, renders all channels to float32 EXRs and validates every final frame.
Final filenames use the original timeline, including zero or negative frames.
No current upstream pixels are read during publication. `outputs` continues to
describe raw worker mattes; `precomp.files` describes final RGB/alpha images.
The two geometry representations are deliberate: backend windows include
display/data coordinates, whereas precomp format dimensions and native Nuke
bboxes are stored separately. Auxiliary channels may contain infinity; RGBA
must decode to finite values.

Only a completed publication receives `precomp`. Capture, backend validation,
publication or activation failure preserves the prior active version. If the
source changes during generation, the complete result is retained for explicit
Reload Result rather than activated automatically. Automatic activation sets
the compatible internal raw Read to the original timeline, then creates or
updates a standalone raw Read in the generator's parent graph. Existing direct
downstream input slots are rewired from the Group to that Read. The normal Read
has no dependency on the Group or backend runtime; its hidden ownership tags
support regeneration and do not register callbacks or inference.

GUI Generate opens a bundled-Qt naming dialog before allocating a session or
version directory. Live edits match PrecompSwitcher: characters outside
`A–Z`, `a–z`, `0–9` and `_` become underscores; outer underscores remain while typing
and are trimmed on acceptance. Space is intercepted before Nuke's dialog shortcuts.
Filename and first-free-version previews do not reserve output. Cancel leaves
the current job/cache and graph unchanged. Programmatic
callers may use `controller.generate(node, prompt_name=False)` with a configured
name. Headless callers do not open the dialog. Neither script load nor cached
render creates a standalone Read; completion and explicit Reload/relink do.

## Validation, relocation and freshness

Published EXR relinking accepts individual frames or ####/%04d patterns,
including renamed basenames and Nuke range suffixes. Every selected file must
match its recorded size, hash, timeline, geometry and alpha/RGBA channel contract
before changing the graph. A new private receipt is written under
`<cache>/relinks/<node UUID>/<relink UUID>/integration.json`; source receipts and
EXR bytes are never changed. The Group and owned Read update within one native
Undo transaction. Errors restore the previous graph and remove only the new
receipt. Unrelated sequences may share the new folder; unexpected numeric
frames matching the selected pattern are rejected.

Diagnostics choose the pending/latest attempt before the active result, so a
failed regeneration remains inspectable. Reload selects a completed pending
attempt or the active result; for the same attempt it retains the explicitly
relinked active receipt. Native `nuke.CancelledError` from foreground capture
terminates as cancelled and preserves the previous result.


The validation terminal has `type:"validation"`, `protocol_version:1`, and
`report:{valid:true,count,outputs,geometry,output_dir,backend_manifest,
source_snapshot_verified:true,freshness:"not assessed by backend validation"}`.
Output directory and backend-manifest paths in this report are relative.
Validation checks the immutable worker JSON digest, timeline/index mapping,
every source hash, backend `complete` status and exact exported filenames, all
output hashes/geometry/precision and finite values. Alpha jobs require exactly
one `A` channel; RGB jobs additionally require equal R/G/B channels.
Raw values outside 0–1 are valid. Subsequent validation checks saved output
identities too. No weight loading, inference or output reservation occurs.

Relocation changes the manifest's current parent directory. Owned relative
paths resolve beneath that new directory. Original absolute backend/result
paths are mapped relative to `original_attempt_root`; old locations are never
opened and immutable `worker.json` is never rewritten. Live upstream media
dependencies retain their separate freshness requirements for legacy alpha-only
caches. Relink activates
only after normal validation, without moving/copying/deleting files.

Capture hashes identify the generated snapshot. They do not prove unchanged
live procedural pixels. The host separately compares graph/settings/media,
FPS/format/colour-context fingerprints and reports unresolved dependencies.
Completed full precomps may report stale/unverifiable live inputs while remaining
renderable: their RGB and alpha are frozen. Host render validation still checks
all snapshot, matte and final precomp file identities and decodes the saved
precomp. Active full-precomp Groups stop traversal into their live inputs;
switching **Use precomp** off instead follows the live branch.

## Streams, exit status and cancellation

Stdout is UTF-8 JSON Lines, stderr a separate complete diagnostic stream.
During execution, events accumulate in `events.jsonl` and stderr in `stderr.log`.
These private working files are removed after the job; the receipt retains its
terminal message and validation information.
Progress is `{type:"progress",stage,current,total,message}`. Stage names may
be new and percentages need not be globally monotonic. Terminal worker events
are result, error or cancelled; bridge success terminals are probe, plan or
validation. Errors have `type:"error",message`. The parser decodes split UTF-8,
multiple lines and the final unterminated line. It bounds line/buffer sizes,
rejects malformed/duplicate terminals and requires normal exit 0 plus the
expected valid terminal. Missing result, crashes and nonzero exits cannot
activate an attempt. Bridge usage errors exit 2; operation failures exit 1.
Worker cancellation exits 130; execution failures exit 1.

Each child gets a copied environment. It removes PYTHONHOME/PYTHONPATH,
Qt/QML plugin variables and recognizable Nuke entries from executable/library
paths and LD_PRELOAD, preserving other system/driver/license entries. It sets
Python UTF-8/unbuffered mode and SHOTBOX_JOB_DIR to the attempt's backend_jobs.
Sanitization needs actual target-OS validation; Windows remains unverified.

Cancellation creates `worker.json.cancel`. The controller offers force stop
after 30 seconds and only terminates/kills its own QProcess. A committed late
success can still validate. Helper probes/scans may be terminated without
writing output. Attempt failure never replaces an existing active cache.

Executable real-EXR protocol fixtures are built by `tests/test_nuke_bridge.py`;
pure parser/cache fixtures are in `tests/test_nuke_protocol.py` and
`tests/test_nuke_cache.py`. Host import boundaries are enforced by
`tests/test_nuke_imports.py`. These automated fixtures are separate from actual
Nuke and native GPU acceptance evidence.
