# Nuke integration verification

This records the original alpha-cache implementation. The subsequent
[precomp verification](NUKE_PRECOMP_VERIFICATION.md) covers the current default:
versioned RGB/alpha publication, automatic Read activation and frozen-source
rendering. Its behavior supersedes live-RGB freshness requirements below for
new full precomps; legacy alpha-only caches retain those requirements.

Verified on 23 October 2026. The implemented target is **commercial Nuke
17.1v2 on Linux**, executable `/opt/Nuke17.1v2/Nuke17.1`. Actual host launches
reported Python **3.11.11**, PySide **6.5.3**, x86_64 Linux kernel
`5.14.0-687.33.1.el9_8`, glibc 2.34. The tested edition was Nuke, not NukeX,
Indie or Non-commercial. Windows and other Nuke builds remain unverified.

The target build has passed native graph/capture/cache/headless tests, actual
GUI workflows with both controlled CPU and unmodified native GPU inference,
and Nuke inspection of real native GPU outputs. These are distinct forms of
evidence: only the controlled failure-workflow run substitutes its inference
engine. Native runs use the existing actual ShotBox worker and actual models.
These technical checks do not establish production footage quality.

## Evidence and repeatable commands

Run from the repository root with the existing ShotBox environment:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check shotbox_matte integrations/nuke tests
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/smoke.py
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/render_cached.py --self-test
```

The final implementation regression run recorded **466 passed, 13 subtests
passed** (24.38 seconds). The targeted freshness/import tests also passed
independently (9 passed). The final repository-wide Ruff check passed.

Host scripts assert the exact version and print `SHOTBOX_SMOKE_PASS` /
`SHOTBOX_RENDER_PASS` only after assertions complete. Their mattes are explicitly
synthetic Nuke fixtures, not GPU-inference evidence. Test artifacts are retained
in the printed temporary cache directories. Durable reports are:

| Report | Actual evidence |
| --- | --- |
| [nuke-host-smoke.json](nuke-host-smoke.json) | Native capture, image/channel/range tests and cancellation |
| [nuke-headless-render.json](nuke-headless-render.json) | Cached render failure matrix, ownership and separate `-x` background launches |
| [nuke-host-compile.json](nuke-host-compile.json) | All 24 host/startup/test sources compiled by Nuke's actual Python 3.11; all host modules imported without inference dependencies |
| [nuke-gui-workflow.json](nuke-gui-workflow.json) | Actual GUI controller, QProcess and controlled CPU success/failure/cancellation pipeline |
| [nuke-gui-native.json](nuke-gui-native.json) | Actual GUI controller and unmodified native GPU probe/worker through validation and activation |
| [nuke-bam030-gui.json](nuke-bam030-gui.json) | Full 25-frame user ACEScg footage through the actual GUI/native GPU workflow |
| [nuke-bam030-reopen.json](nuke-bam030-reopen.json) | Fresh headless reopen and cached render without the external interpreter |
| [nuke-bridge-host-report.json](nuke-bridge-host-report.json) | External probe/preflight/validation and real EXR checks |
| [nuke-probe.json](nuke-probe.json) | Actual external environment/model/GPU probe |
| [nuke-native-gpu.json](nuke-native-gpu.json) | Real existing native worker, three-frame and two 250-frame requests |
| [nuke-native-host.json](nuke-native-host.json) | Actual native GPU output decoding, mapping and channels in Nuke 17.1v2 |

Initial sandbox launches could not write Foundry's licensing lock directory,
and sandbox GPU visibility was restricted. Authorized host launches with access
to the installed licensing directory and GPU succeeded. This was an execution
sandbox restriction, not an unavailable Nuke installation or GPU. No weights,
packages, Qt binding or Nuke dependency environment were installed for these tests.

## Target host contracts established by execution

- Copy input **0 is B/original**, input **1 is A/matte**. `from0=rgba.red`,
  `to0=rgba.alpha`, and `bbox`, `metainput`, `rangeinput` set to `B` preserve
  original RGB, other channels, metadata/range source and bbox. Tests compared
  RGB samples directly and preserved an auxiliary depth channel with value 42.
- Read uses `raw=True`, `on_error='error'`, `before/after='black'`,
  `localizationPolicy='off'`, `frame_mode='expression'`. A separate native
  range switch produces zero matte outside the active timeline range.
- Write EXR knobs appear after `file_type='exr'`. Confirmed values are
  `datatype='32 bit float'`, `compression='Zip (1 scanline)'`,
  `interleave='channels, layers and views'`, `autocrop=False`, `raw=True`.
  This target has no `aces_compliant_exr` knob; capture disables it if present.
- Write `frame_mode='offset'`, `frame=1-start` evaluates upstream at the original
  timeline frame while writing positive indices. Starts 1001, -2 and 0, both
  endpoints, one-frame ranges and outside-range output were tested.
- A format-sized Crop with `intersect=False`, `crop=True`, `reformat=False`,
  zero softness blackens areas outside a smaller input bbox. Graded and retimed
  corner samples retained their positions through actual EXR capture and Read.
- Nuke vector constructor arguments such as `Constant(color=[...])` do not
  reliably set knob values; the implementation uses explicit `setValue`.
- A foreground `nuke.cancel()` callback at the second frame can make
  `nuke.execute` return with an incomplete sequence **without raising**.
  Capture therefore checks exact filenames/count before inference. Native
  cancellation restored proxy/frame/selection, removed owned temporary nodes,
  and retained the previous active cache.
- Nested `nuke.execute` is forbidden during `beforeRender` ("already executing").
  Host validation instead uses native Read full-area RGB sampling to decode
  every output frame and checks dimensions, data window, PAR and finite sample
  aggregates. SHA-256 validation ties these files to the externally validated
  finite-pixel result. Full checksum/decode runs at render preflight; per-frame
  guards repeat path/size/freshness checks and fully validate an enabled cache
  that the initial conservative range did not encounter.
- Fresh headless Nuke can serialize empty root LUT enums as `0`; loading that
  script resolves the same entries to `linear`. This tested equivalence is
  canonicalized to avoid falsely stale caches after process restart.
- GUI Read colours can serialize the default choice as `default (linear)`,
  while headless Nuke serializes the same choice as `default`. The tested
  default-label equivalence is canonicalized for Read `colorspace`; the raw
  setting, root colour configuration and source-file identities remain tracked.

## Passed matrix and boundaries

| Area | Passed scope |
| --- | --- |
| Capture | Actual connected Grade and TimeOffset, negative timeline, fixed raster, crop black fill, corner orientation, proxy/frame/selection restoration, native cancellation, square-pixel rejection |
| Output | Original float RGB and auxiliary channel preservation, original bbox, raw matte values -0.1 and 1.25, matte-only RGB/alpha, zero alpha before/after range |
| Freshness | Relevant Grade changes, known Read sequence identities using real `File_Knob.getEvaluatedValue(OutputContext)`, media mtime changes, cosmetic movement/label ignored, own output view ignored; normal expression/link/hidden traversal; arbitrary Python image expressions conservatively unresolved |
| Persistence | Save/reopen in-process and in a separate Nuke process; no inference on load/render |
| Ownership | Duplicate Generate and second-node rejection, rename, native copy/paste new UUID with retained cache reference, native delete/undo identity retirement; GUI cancellation and failure retain prior cache |
| Render | Real production Write succeeds; ungenerated and missing/stale cache reject; unrelated Write succeeds; static/animated disable range checks; nested Output traversal ignores disconnected invalid tools and unused external sockets; expression-only dependencies validate |
| Runtime boundary | Headless startup idempotent, no worker/GUI adapter imported by registration, inference stack absent; cached rendering with ShotBox interpreter deliberately unavailable |
| Protocol/cache failures | Automated contract tests cover event fragmentation, malformed/no terminal result, crash/exit behavior, owner/session matching, cache traversal/corruption/relocation and complete-status requirements; these are automated evidence, not native process-crash acceptance |

Matching fingerprints mean **no detected change**, not identical live pixels.
Provenance includes topology, image knobs/animation/expressions, settings,
root/colour context, explicitly referenced custom root knobs (recursively),
and known media size/mtime identities. Every matching numbered sequence member is
recorded so TimeOffset/TimeWarp requests outside the capture timeline cannot
escape source tracking. Changing or adding an unused sequence member can
therefore conservatively mark a result stale. The actual retimed Read test
verified timeline frame 5 consuming file 3, without requiring nonexistent file
5; modifying consumed file 3 invalidated the fingerprint. Root custom OCIO
configuration file identities are also tracked. Replacing external
content with unchanged metadata may evade this lightweight freshness check;
recapture or explicit dependency hashing is required for stronger assurance.
Caches themselves are protected by recorded full SHA-256 checksums. Python
expressions whose dependencies cannot be established block strict rendering.

## GUI and real GPU evidence

The real GUI run used the installed Nuke 17.1v2 GUI and PySide6 QProcess. It ran
probe → foreground capture → external preflight → controlled CPU worker with
normal CLI export → bridge validation → active result. It verified graded/
retimed RGB capture, raw alpha/range mapping, ownership through rename,
duplicate/second-node rejection, clean worker error and cooperative cancellation.
See the GUI JSON for exact command arguments, owned child PIDs and outcomes.
The separate actual native GUI run also completed probe → capture → preflight
→ unmodified real GPU worker → validation → activation in Nuke 17.1v2. It
compared native worker alpha at five corner/interior positions on all three
frames, preserved original RGB, checked range boundaries and strict freshness,
and retained controller ownership through rename. Controlled GUI failure tests
also verified stale completion stays inactive, the 30-second force-stop offer,
owned resistant-worker termination, node deletion/undo ownership and File New/
normal GUI exit reaping the exact owned PID. See
[nuke-gui-native.json](nuke-gui-native.json). Its native GPU path used no
controlled worker substitution.

The actual external probe found an **NVIDIA GeForce RTX 5090, 32 GB VRAM** and
resolved the existing installed model files. The real worker, without mocked
inference or fallback, completed:

| Request | Model temporal frames | Worker wall time | Result |
| --- | --- | --- | --- |
| 3 frames, segmented | 9 padded | 26.046 s | 3 outputs, complete and fully bridge-validated |
| 250 frames, segmented | 121 / 121 / 49 | 66.510 s | 250 outputs, complete and fully bridge-validated |
| 250 frames, experimental full range | 257 padded, one pass | 26.354 s | 250 outputs, complete and fully bridge-validated; no fallback |

These runs used 16×12 Nuke-written coordinate/Grade captures, fitted/padded to a
64×64 model raster. Long runs repeated the three source frames. They prove
execution/frame-count contracts at this reduced size, not performance or VRAM
capacity for normal 544-high work. All three results were decoded in actual
Nuke; every timeline frame was checked against frozen capture RGB and native
matte red, including outside-range zero alpha. Real matte values below zero
were preserved. The input was reconstructed from immutable captures for this
inspection; original live-graph freshness was not claimed.

Run the native cache inspection again with:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/smoke.py \
  --native-manifest /absolute/path/to/completed/integration.json
```

## User footage: bam030

Reproduce the actual GUI runs with the launcher below. It creates a temporary
Nuke startup path, leaves user startup files untouched, and checks the saved
report as well as the Nuke exit code. The controlled run substitutes CPU
inference for failure testing; `--native` and `--source` use the real worker.

```bash
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --report docs/nuke-gui-workflow.json --timeout 240
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --native --report docs/nuke-gui-native.json --timeout 240
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --source /home/gambit/Desktop/-----------/bam030_DN_v001 \
  --cache test-runs/nuke-bam030 --height 384 \
  --report docs/nuke-bam030-gui.json --timeout 1800
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/reopen_footage.py \
  --gui-report docs/nuke-bam030-gui.json --report docs/nuke-bam030-reopen.json
```

The user's ACEScg sequence `/home/gambit/Desktop/-----------/bam030_DN_v001`
also completed the actual Nuke 17.1v2 GUI → external native GPU workflow:
**1001–1025**, all 25 frames, original **4096×2850** captures. Read was explicitly
raw and the input colour selection was `acescg`. Generation used height 384,
fit 552×384 and padded model raster **576×384**; completed mattes were restored
to 4096×2850. The recorded final GUI workflow elapsed time was 53.389 seconds.

Seven positions on every frame preserved original/captured raw RGB and matched
output alpha to actual backend matte red. Full cache checksum/native image
validation passed, alpha was zero outside the selected range and proxy/frame
were restored. [nuke-bam030-gui.json](nuke-bam030-gui.json) records source paths,
model dimensions, process outcomes and the saved
`test-runs/nuke-bam030/bam030_review.nk` review comp. Full source-versus-capture
RGB comparison was bit-exact on every pixel of all 25 frames.
The saved comp also passed fresh-process headless reopen, strict cache/freshness
validation and an actual frame-1001 render with the external interpreter
deliberately unavailable; no backend, Torch or process adapter was imported.
Every RGB pixel in that rendered frame was bit-exact to the original ACEScg
source, and every alpha pixel was bit-exact to the backend matte red channel.

Reduced source/matte/checker-composite previews at 1001, 1013 and 1025 were
inspected in [the contact sheet](nuke-bam030-preview.png). The dominant man is
extracted across those frames. The woman entering from the right is also
included, with partial coverage/holes around her upper body; edges are soft
at generation height 384. This three-frame inspection is not final matte
quality acceptance. Full-motion consistency and delivery-resolution edges
still require artist review.

## Farm deployment contract

The integration must load in every render process. A native saved Group alone
cannot enforce Python validation when its package is absent. Use the documented
startup/plugin-path installation and an explicit farm import/preflight:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/render_cached.py \
  --script /project/comp.nk --write Write1 --first=1001 --last=1003
```

Use `--first=...` and `--last=...`: Nuke can consume a trailing standalone
integer as its own frame-range argument. This entry point imports the package,
registers callbacks, opens the comp, validates the requested Write/range and
renders. No ShotBox worker or inference process is launched.

Actual independent `Nuke -x -X FixtureWrite -F 0 cached.nk` processes were also
tested with `NUKE_PATH=<repo>/integrations/nuke` and a deliberately unavailable
ShotBox interpreter. Valid cache rendering exited 0. A missing active manifest
exited 1 despite the saved native Read retaining its old valid path. An explicit
farm import with the integration absent from the plugin path failed with
`ModuleNotFoundError` and exit 1, as required.

## Remaining verification and intended limits

Windows, NukeX, other Nuke builds, show-specific OCIO configurations and
production matte quality have not been accepted by these runs. Segment joins,
subject consistency, portrait/widescreen production shots and the experimental
full-range quality require visual review on representative footage. Full-size
250-frame GPU capacity is not established by the reduced-raster test.

The full failure matrix is broader than the host cases run here: forced kill
during export commit, real late-cancel-after-commit races, host shutdown during
an active GPU export, actual disk exhaustion, and every animated format/view/
deep-input variant still require dedicated acceptance. Pure automated tests of
those applicable state/error contracts do not replace those host tests. These
limits are recorded rather than described as production-ready acceptance.
