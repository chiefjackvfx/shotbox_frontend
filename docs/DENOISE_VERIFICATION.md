# Background denoise verification

Verified on 26 October 2026 with Rocky Linux 9.8, NukeX **17.1v2**, Neat Video
**6.1.2 Studio**, and the frontend's Python **3.13.15 / PyQt6 6.11.0** runtime.
Xvfb and Openbox came from the system installation; Python Xlib is installed in
the frontend environment.

## Automated acceptance

From the frontend directory:

```bash
venv/bin/python test/denoise_acceptance.py --nuke /opt/Nuke17.1v2/Nuke17.1
```

The opt-in test creates synthetic 512×512 RGB footage for frames 1001–1003,
scripts, renders, screenshots, and reports under `.cache/temp`. The fixture
includes ModelBuilder outside the denoise branch. The Qt controller starts with
another artist's shot lock in its metadata and does not contact a lock API.

The completed run verified:

- Authenticated private-display startup and automatic Auto Profile → Apply.
- A changed profile saved into the original script and rendered on fresh reopen.
- NukeX launch mode loaded and preserved ModelBuilder on fresh reopen.
- Root frame 1001, proxy setting, output path, and Write frame limits preserved.
- Full-range rendering despite an existing Write limit of frame 1002 only.
- Relative media/output paths and a script path containing spaces.
- Existing output frames replaced with readable, finite, full-resolution EXRs.
- Only `WriteDN1` rendered; an unrelated Write produced no output.
- A responsive Qt event loop during the background render.
- Cancellation before saving preserved the original script digest.
- Existing shot locks were ignored and their metadata remained unchanged.
- Cancellation after saving retained the profile and completed child cleanup.
- Killing the supervisor stopped its children and preserved the script.

The successful report was produced in
`.cache/temp/denoise-verification-rqsrvz6t/verification.json`; the cache may be
cleared. Run the command above to produce fresh evidence. The permanent harness
does not depend on that directory.

## Dashboard batch button

The **Denoise** button beside **mp4s** queues each shot's latest saved script
across the active job's timelines, including hidden shots. Duplicate shot IDs
and resolved script paths are deduplicated. Missing scripts are counted as
skipped. Confirmation describes profile saving and replacement of matching
`WriteDN1` frames before the queue starts.

The existing controller runs one clip at a time, retaining its busy state
between clips. The toolbar button and shot-card action cannot start another
denoise job while the queue is active. Each clip completes child cleanup and
releases its cache lease before the next starts. Failures are counted and
processing continues. Cancel and application shutdown discard the remaining
queue after stopping the active clip; refresh resumes only after batch cleanup.
The progress dialog shows clip position and completed/failed/skipped counts.

The batch regression checks cover latest-version selection across timelines,
hidden shots, duplicate scripts, missing scripts, declining confirmation,
toolbar/refresh state, sequential launches, failure recovery, startup failures,
cancellation during and between clips, and shutdown. Run:

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest \
  test.test_denoise_ui test.test_nuke_denoise \
  test.test_nukedash_regressions test.test_page_nukedash_thumbnails
venv/bin/python test/denoise_batch_acceptance.py --nuke /opt/Nuke17.1v2/Nuke17.1
```

Native batch acceptance completed two generated 512×512 clips through real
NukeX/Neat Video in 18.363 seconds, with 733 Qt timer ticks. Both profiles were
saved; all six EXRs were readable, full resolution, and finite. The unrelated
Write produced no output. One final summary reported two completions, zero
failures/skips, and no remaining clips. The busy signal stayed true until both
jobs and their children finished. All inputs, scripts, outputs, and logs were
cache-only; no production media, scripts, or database were changed. Evidence:
`.cache/temp/denoise-batch-acceptance-git3xr1s/acceptance.json`.

## Windows implementation and native verification gate

Windows 10/11 uses `nuke_denoise_windows.py`. It creates a unique desktop in the
current window station without switching the visible desktop. `CreateProcessW`
receives a real `STARTUPINFOEXW.lpDesktop`; Python's `subprocess.STARTUPINFO`
does not expose that field. A `PROC_THREAD_ATTRIBUTE_JOB_LIST` assigns Nuke to
the kill-on-close Job Object atomically at process creation. Nuke starts
suspended so its PID and creation time can be recorded before its first thread
resumes. Only duplicated standard-stream handles are inherited; the supervisor
retains the only job handle. This also prevents surviving render children if
the supervisor crashes during launch or processing.

The backend captures the plugin client area with bounded `WM_PRINT` messages
into a DIB and sends mouse messages to the matched control's window. Handles
must still belong to the private desktop before input is sent. There is no
`SwitchDesktop`, `SetForegroundWindow`, `SetCursorPos`, or `SendInput` path.
The existing Neat 6.1.2 control matching, fresh-profile receipt, disabled-control
checks, source-script digest guard, saving, full-range rendering, queue,
cancellation, and cache leases remain shared with Linux. `TEMP` and `TMP` are
also redirected into each job's frontend cache directory on Windows.

Reference contracts:

- [Microsoft desktop creation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createdesktopw)
- [Native process startup fields](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-startupinfow)
- [Atomic job assignment and restricted handle inheritance](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)
- [Job lifetime and child processes](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Bounded window messages](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)
- [CPython startup-field handling](https://github.com/python/cpython/blob/3.13/Modules/_winapi.c)
- [Foundry Qt terminal mode](https://learn.foundry.com/nuke/content/comp_environment/configuring_nuke/command_line_operations.html)

132 tests passed on the Linux development machine across the denoise,
Windows-adapter, detector, benchmark, dashboard, and thumbnail suites. Windows
coverage includes 64-bit ABI/handle declarations, the actual native startup
structure and attribute contents, atomic job assignment, restricted standard
handles, suspended-process receipts, process waits and exit codes, targeted
control coordinates, window capture, profile readiness, cancellation, UI
entry points, no-Xlib operation, saved Windows error codes, and truthful verifier reports. These simulated
Win32 checks do not establish that the installed Windows plugin UI works.

The real Linux single-job acceptance was rerun after the platform split,
covering save/render/reopen, cancellation before and after saving, crash cleanup,
relative paths, and preserved state. Native two-clip batch acceptance also
passed after the platform split. Evidence is under the frontend cache; it is
disposable and not committed.

**Windows native qualification is pending.** Run the following from the updated
frontend folder on the Windows machine with NukeX 17.1v2 and licensed Neat Video
6.1.2 installed:

```powershell
.\venv\Scripts\python.exe test\denoise_windows_acceptance.py
```

Optionally append `--nuke "C:\path\to\Nuke17.1.exe"`. The verifier uses generated
512×512 footage and scripts under `.cache/temp` only. It runs single-job
profiling, saving, rendering, fresh reopen, cancellation before/after saving,
forced supervisor-crash cleanup, and two sequential batch clips. All produced
EXRs are checked for full dimensions and finite pixels. It emits a combined
`verification.json` and console log under `.cache/temp/denoise-windows-verification-*`.
Even fixture/reopen Nuke processes use owned private desktops and jobs; their
launch does not rebind a verification thread that already owns Qt windows.
The final gate is a successful native report and correct behavior of the
installed Windows Neat Video controls, including capture on an inactive desktop.

## User-supplied test comp

The complete Qt-controller workflow also passed on
`/home/gambit/Desktop/-----------/shotboxtest/sho010/scripts/sho010_v001.nk`:

- Auto Profile at middle frame 1030, followed by Apply and saving the same script.
- A changed profile compared with the job's original-script recovery copy.
- All 59 frames, 1001–1059, rendered through `WriteDN1` at 4096 × 2850.
- All output EXR headers readable; first, middle, and last pixels finite.
- Saved root frame 1001 preserved, with existing shot-lock metadata ignored.

The controller report is
`.cache/temp/denoise-sho010-verification-75vhr6wf/gui.json`; its job logs and
recovery copy from the console verification run are under
`.cache/temp/denoise-54mewelz`. The captured console output is
`.cache/temp/denoise-sho010-verification-75vhr6wf/console-debug.log`.
That repeat run also bypassed an existing newer artist autosave while using
the saved `.nk` on disk.

The popup verification reran all 59 frames and checked the visible loading,
Prepare Noise Profile, Auto Profile, Apply, saving, and rendering states.
The popup shows eight steps, elapsed time, an activity history, and frame-based
render progress. Screenshots and the completed controller report are in
`.cache/temp/denoise-popup-verification-i8ke4ebd`; all 22 focused denoise tests
passed after this UI change.

The lock-monitor follow-up verified that an actual supervised NukeX process was
excluded from editing-session detection without an unidentified-script warning.
The job was cancelled during loading, leaving the source script unchanged and
rendering no frames. Its report is
`.cache/temp/denoise-lock-monitor-051l74yz/verification.json`. All 96 tests across
the detector, lock-worker, and denoise suites passed; coverage includes normal
editing sessions, stale process identities, and unreadable process environments.

## Timed denoise benchmark

On 26 October 2026 the supplied `sho010_v001.nk` was benchmarked through the
same supervisor and Neat Video Auto Profile → Apply → save → `WriteDN1`
workflow. Every run processed all 59 frames, 1001–1059, at 4096 × 2850.
The machine has a Threadripper 9980X with 64 physical cores / 128 logical
processors and an RTX 5090. Native GPU access was confirmed.

Each run used an isolated copy with the original script filename, its
`project_directory` rebased to the original script directory for source reads,
and only `WriteDN1.file` redirected into the benchmark's cache directory.
Source-script digests stayed unchanged. Existing shot renders were not used as
benchmark outputs. Denoise, format, frame range, and EXR settings were preserved.

| Nuke rendering threads | Total seconds | Render seconds | Render fps |
| --- | ---: | ---: | ---: |
| 4, original setting | 44.331 | 19.846 | 2.973 |
| 4, repeated after other runs | 43.141 | 19.037 | 3.099 |
| 16 | 34.893 | 11.829 | 4.988 |
| 32 | 36.093 | 12.231 | 4.824 |
| 64 | 37.497 | 15.235 | 3.873 |

These are sequential local measurements, not a general hardware guarantee.
Timings use supervisor event reception with approximately 0.2-second polling
resolution. Render time ends at the final completed-frame event and excludes
process shutdown. Full elapsed time includes private-display setup, Nuke startup,
loading, frame preparation, profiling, saving, rendering, and cleanup.

The initial measured recommendation for this machine and shot was **16 Nuke threads**
(`-m 16`) in place of the previous hardcoded four: approximately 19–21% less total
time and 38–40% less render time than the two four-thread runs. Thirty-two and
64 threads did not improve on 16 in these runs. Those initial timing-only tests
left the production default unchanged. The follow-up below implements faster
startup and adopts a rendering-thread budget capped at 16.

The 16-thread run spent about 10.2 seconds starting Nuke, 2.4 loading the script,
2.4 preparing its frame, 3.8 profiling/applying, 11.8 rendering, and 4.1 shutting
down. A future batch workflow could reuse one isolated Nuke worker across shots
to avoid repeated startup/shutdown. That change has not been implemented or
timed, so its potential savings are an estimate rather than a measured result.

Run a fresh isolated benchmark from the frontend directory:

```bash
venv/bin/python test/denoise_benchmark.py \
  --script /path/to/shot/scripts/shot_v001.nk \
  --nuke /opt/Nuke17.1v2/Nuke17.1 --threads 16
```

The harness keeps the cloned script, recovery copy, renders, timestamped events,
and `benchmark.json` in `.cache/temp/denoise-benchmark-*`. It requires exactly
one top-level `WriteDN1` with an existing output path and root project directory,
and fails before running if it cannot isolate those paths. Current reports:

- Four threads: `.cache/temp/denoise-benchmark-rq4y7kmj/benchmark.json`.
- Four-thread repeat: `.cache/temp/denoise-benchmark-5g07givb/benchmark.json`.
- Sixteen threads: `.cache/temp/denoise-benchmark-j6v30nq7/benchmark.json`.
- Thirty-two threads: `.cache/temp/denoise-benchmark-mjjtx_mj/benchmark.json`.
- Sixty-four threads: `.cache/temp/denoise-benchmark-prtdqmim/benchmark.json`.

Every pixel and channel in all 59 four-thread baseline and 16-thread output
EXRs was decoded and checked: the images are finite and 4096 × 2850, with the
same frame and channel sets. They are **not pixel-identical**; the largest
absolute difference over the full sequence was 0.0625 in scene-linear values.
The complete comparison is
`.cache/temp/denoise-benchmark-j6v30nq7/pixel-comparison.json`.
On representative frames 1001, 1030, and 1059, RGB mean absolute differences
were approximately 1.75e-8, 1.07e-5, and 1.61e-8. The unchanged four-thread
repeat also differed from its baseline on those frames, so separate fresh
profiling/render runs are not bit-reproducible here. This establishes valid
full-resolution outputs and measured speed, not lossless pixel equivalence.

The initial benchmark and existing denoise/UI suites passed all 26 focused tests.
New coverage checks source preservation, isolated output paths, unchanged filter
settings, rejection of ambiguous paths, and separation of rendering from shutdown.

## Implemented 50-percent duration reduction

The follow-up now defaults to interactive NukeX **Qt terminal mode** (`--tg`)
on the same authenticated private display, with full-frame scheduling
(`--topdown`). It loads normal `init.py` plugin setup and the complete comp,
then opens the same Neat Video editor to perform fresh Auto Profile → Apply.
It avoids desktop/menu startup and quits its Qt event loop after the normal
save, render, and cleanup sequence. The rendering-thread budget is half the
reported logical CPU count, bounded between one and 16, with four as the
unknown-CPU fallback.

Foundry documents [Qt terminal mode and full-frame scheduling](https://learn.foundry.com/nuke/content/comp_environment/configuring_nuke/command_line_operations.html).
The flags use an interactive license and do not persist the command-line
render-mode choice into the saved script. No new runtime cache category is
introduced; requests, bootstrap files, and logs stay in the existing job folders.

The supervisor now polls at 100 ms. Apply requires the enabled Apply button,
Build Profile control, and ready adjustment controls to remain ready for at
least 300 ms. Any missing or disabled control resets that interval. This removes
the old one-second hold after readiness while retaining multiple readiness
checks and the fresh-profile receipt required before saving. Older requests
without the Qt flag still follow their desktop startup path.

| Workflow | Total seconds | Render seconds | Source changed |
| --- | ---: | ---: | --- |
| Original desktop, four threads | 44.331 | 19.846 | No |
| Original desktop, four threads, repeat | 43.141 | 19.037 | No |
| Optimized defaults, run 1 | 20.747 | 11.450 | No |
| Optimized defaults, run 2 | 20.840 | 11.547 | No |
| Optimized normal Shotbox Qt controller | 20.989 | — | No |

The two supervisor runs reduce total duration by **51.7–53.2%** against the two
original baselines. The normal-controller run includes UI startup, child-process
launch, rendering, progress updates, successful popup dismissal, and cleanup;
its duration is **51.3–52.7% lower** than the original supervisor baselines.
Every run profiles frame 1030 and renders all 59 frames at 4096 × 2850 with the
existing filter and EXR settings. These remain local measurements for this
machine and shot, rather than a performance guarantee for every comp.

The optimized supervisor's Nuke startup takes about 1.0 second instead of the
desktop's 10.2 seconds, and shutdown takes 0.6–0.7 seconds instead of 2.9–4.1.
The original full-image middle-frame evaluation is retained.

Verification artifacts:

- Run 1: `.cache/temp/denoise-benchmark-fa5j2anw/benchmark.json`.
- Run 2: `.cache/temp/denoise-benchmark-8u6duqi0/benchmark.json`.
- Normal controller: `.cache/temp/denoise-optimized-controller-x19lxnw0/timing.json`.
- Fresh-process graph/root comparison: `.cache/temp/denoise-optimized-controller-x19lxnw0/preservation.json`.
- Full-sequence pixel validation: `.cache/temp/denoise-optimized-controller-x19lxnw0/pixel-comparison.json`.
- EXR metadata comparison: `.cache/temp/denoise-optimized-controller-x19lxnw0/metadata-comparison.json`.
- Native acceptance: `.cache/temp/denoise-verification-b_wfz0c8/verification.json`.

A fresh NukeX process opened the original and saved full-shot comp and confirmed
all **300 nodes**, node classes, ordered input connections, root frame/proxy,
frame range, FPS, format, and pixel aspect were preserved. The original script
digest was unchanged. The native synthetic acceptance reran the actual
controller, persisted and reopened the fresh profile, preserved ModelBuilder,
kept paths and saved state intact, and verified cancellation before/after saving
and supervisor-crash cleanup.

All pixels and channels in the normal-controller run's **59 EXRs** were decoded
and checked against the original baseline: all are finite and 4096 × 2850.
Every frame also preserves channel precision, ZIPS compression, data/display
windows, and pixel aspect. Fresh Auto Profile runs remain non-identical at the
pixel level, as already observed between unchanged four-thread runs; the full
sequence's maximum absolute difference was again 0.0625 in scene-linear values.
The timing improvement does not establish bit-exact pixel equivalence.

All **138 tests** passed across the denoise host/UI, benchmark, detector,
lock-worker, and dashboard suites, including 32 focused denoise tests. The
filesystem sandbox produced read-only warnings for unrelated lock-identity
state persistence; that storage path was not changed. Ruff and
`git diff --check` passed. New regression coverage includes Qt application
validation, interactive licensing, bounded thread selection, legacy requests,
and resetting profile readiness when Apply becomes disabled.

The benchmark defaults now exercise the optimized production path. To reproduce
the old desktop launch for comparison, add `--desktop --classic --threads 4`.
The `--qt-terminal` and `--topdown` options explicitly select the optimized modes.

## Earlier regression checks

98 tests passed across the denoise, Nuke lock worker, and dashboard regression
suites. Denoise covers both normal and hovered Apply buttons, open-session
independence, existing locks, cancellation, cache-lease cleanup, and autosave
redirection before loading the script.
New Python modules passed Ruff, and
`git diff --check` passed.

Control-image assets in `ui/denoise` are small crops of the installed Neat Video
6.1.2 controls. Matching is restricted to the plugin window's control regions.
Qualification covers English controls at normal scaling on Linux. This test
establishes workflow behavior; production denoise quality still depends on the
footage and the node's filter settings.
