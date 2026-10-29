# Mixed Deadline denoise verification — 28 October 2026

Linux Shotbox submission and local Neat profiling were exercised against the
existing Deadline repository `/mnt/DeadlineRepository10`. Nuke path mapping is
now enabled; its prior value and unchanged mapping rules are preserved in
[DEADLINE_MAPPING_ROLLBACK.json](DEADLINE_MAPPING_ROLLBACK.json).

## Native worker qualification

| Worker | Platform | Result |
| --- | --- | --- |
| gambit-local | Linux | Four clips, 331 frames, 34 tasks completed |
| Giger | Windows 10 Pro | Same four saved profiles, 331 frames, 34 tasks completed |
| GRADING-SUITE | Windows 11 Pro | Not qualified: stale October 2 status; smoke task never started |

The remaining discovered workers (Cerebro, delta, Huxley, rocky and Rogue) were
reported offline at inventory time and were not tested. Configuration entries
alone were never treated as qualification evidence.

Nuke **17.1v2**, the Neat v6 node with its embedded Linux-created profile, MXF
reading, and EXR writing worked natively on both qualified workers. The Giger
log confirms `C:\Program Files\Nuke17.1v2\Nuke17.1.exe`, mapped `Z:/PROJECTS/...`
Read/Write paths, and a worker-local `jobsData/.../profiled.nk`. This establishes
rendering under the actual Worker account without access to the submitting
machine's cache. The precise Windows Neat patch version was not reported;
qualification applies to the installation tested, not arbitrary Neat versions.

Linux used one concurrent task. Windows started with one, then the test jobs
were changed to four tasks on its 64-CPU / 128-GiB machine. Product defaults
remain ten frames per task and one concurrent task. All scripts retained the
full source range for temporal filtering.

`gambit_settings.yaml` now explicitly lists `gambit-local` and `Giger` as
qualified workers. Existing pool preference `linux` was preserved. Choose
`houdini` to include both. The idle-looking GRADING-SUITE test was suspended
(job `6ae1eeac0c2291139af3156c`) so it cannot unexpectedly render on a later
startup. No other worker was automatically admitted or reconfigured.

## Output checks

Every frame was decoded and checked for expected dimensions, finite half-float
RGBA pixels, and single-scanline ZIP compression. Comparisons used continuous
local renders of the identical saved profiles and included first/middle/last
frames and both sides of every ten-frame task boundary.

| Comparison | Frames decoded | Frames compared | Mean absolute channel difference | Maximum difference |
| --- | ---: | ---: | ---: | ---: |
| Linux farm vs continuous local | 331 | 71 | 0 | 0 |
| Windows farm vs continuous local | 331 | 71 | 0.00028947357 | 0.6875 |

Windows output is **not pixel-identical** to Linux. These measurements are
reported without inventing a perceptual acceptance threshold. A visual review
of 512-pixel centre crops at frames 1010/1011 of the first clip showed no obvious
chunk seam; this limited inspection is not a full sequence review. The diagnostic
used the same simple display gamma for both inputs and a 20× amplified difference.

Machine-readable aggregates are in
[DEADLINE_MIXED_RESULTS.json](DEADLINE_MIXED_RESULTS.json). Detailed disposable
evidence remains under `shotbox_frontend/.cache/temp/`:

- `deadline-mixed-jfz2l8_0/acceptance.json`: eight native jobs and separate output versions.
- `deadline-output-check-w9jdrsn6/verification.json`: Linux checks.
- `deadline-output-check-l5difeq3/verification.json`: first two Windows clips.
- `deadline-output-check-i1i_gi4l/verification.json`: final two Windows clips.
- `mixed-farm/boundary-review.png`: inspected boundary crops.
- `mixed-farm/windows-smoke.log`: mapped paths and native executable evidence.

## Dashboard and regression checks

The actual dashboard Denoise button and destination dialog submitted four freshly
profiled clips with zero failures, skips or uncertain submissions. Only backend
data and settings were isolated to a fixture timeline; Qt widgets, local GUI
profiling, the batch controller and Deadline submission were real. The normal
settings file was not used by that harness. Its report is
`deadline-ui-w6h4vpxg/acceptance.json`.

Live asynchronous queue refresh displayed `Giger (win32)` and
`gambit-local (linux)` in the existing `houdini` pool. Shotbox's output discovery
resolved the populated versioned EXR sequences from the shared test shot folders.
The Linux worker went offline after completing its native qualification; it was
not restarted by this task. The dashboard batch has completed `sho010` and `sho020`; `sho030`
(`6ae1f0f7bc30e07ea62d7cb0`) and `sho040` (`6ae1f0f8ba1bb34ee37c4296`)
remain active on Giger at handoff. These additional test jobs were also set to
four concurrent tasks after confirming the UI submitted its default of one.
They are distinct from the eight fully completed and verified qualification jobs.

Focused denoise/controller/Windows/Deadline tests passed **173 tests**. Settings
persistence and dashboard regressions passed **73 tests**, for **246 total**.
They cover explicit qualification, queue intersection (including `none`
exclusions), mapping failures, exact builds, selected-platform guards,
cancellation, partial success, duplicate prevention and uncertain outcomes.
Ruff and whitespace checks passed. Test harnesses use leased app-cache temporary
files; persistent farm receipts remain beside the test shots.

## Native job IDs

Linux: `6ae1ef0736596ef038cec9ac`, `6ae1ef092b40d02b036c7e65`,
`6ae1ef0a4202e2c603270d43`, `6ae1ef0b8e209176ef07f69b`.

Windows: `6ae1ef0c790ba6ef6c519a0f`, `6ae1ef0e3800e483ebb88667`,
`6ae1ef0f6a2d75a20e24a4eb`, `6ae1ef11103b0737bde4b0c7`.
