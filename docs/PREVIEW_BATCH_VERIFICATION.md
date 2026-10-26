# Make All Previews verification

Verified on 26 October 2026 with Nuke 17.1v2 on Linux, Python 3.13.15,
Threadripper 9980X (128 logical CPUs), and RTX 5090.

## Implementation

The dashboard opens a lazy batch session around its existing preview loop.
One headless Nuke worker receives JSON requests over stdin and sends task results
and existing frame progress over stdout. Each request clears the script and opens
the preview template again; nodes and slate values are recreated for each input.
The worker uses `-m 16 --topdown -t` on this machine. The thread limit is half
the logical CPU count, bounded to 1–16. Single-preview rendering is unchanged.

Existing task collection, input validation, output versions and plate names,
skip/overwrite decisions, quality, slate, progress, and per-success API updates
remain in the dashboard/generator. A media failure does not abort the remaining
batch. A terminated worker can be replaced for the next request; active renders
are never retried merely because output is silent.

An output-reader thread lets the UI poll cancellation every 50 ms during silent
startup/render periods. Cancellation terminates the owned worker process group
on POSIX, escalating to kill after two seconds if necessary. Cleanup waits for
the process before removing `.cache/temp/preview-worker-*` and releasing its cache
lease. Clear Cache preserves those files while the worker is alive. No new
persistent cache category is introduced.

## Timings and fidelity

The same three requests rendered 177 frames in total: a 59-frame camera MOV,
a 59-frame 4096×2850 ACEScg EXR sequence, and the MOV again with another plate
name. All used medium quality and the existing template. The MOV read transform
was ARRI LogC3 (EI800). Versions, plate names, and requested frame rates varied
between tasks. Original media was read only; all MP4s and Nuke temporary files
were written under the frontend cache.

Elapsed time covers all three renders and process cleanup:

| Run | Original, three Nuke launches | Batch, one Nuke launch | Time reduction |
| --- | ---: | ---: | ---: |
| First | 32.562 s | 15.610 s | 52.1% |
| Repeat | 30.788 s | 15.678 s | 49.1% |
| Actual dashboard slot | — | 15.686 s | — |

The actual dashboard slot used real Qt dialogs and native Nuke, with prepared
cache-only tasks and mocked database calls. It reported three successes,
zero failures/skips, three correct preview-path updates, one worker launch,
386 Qt timer ticks, and restored refresh state after cleanup.

PyAV decoded all 177 output frames from both the original and batch runs.
RGB pixels matched exactly, including slate and colour transforms. Codec,
profile, dimensions (1920×1200), pixel format, frame counts, timestamps,
keyframes, duration, colour metadata, and audio-stream counts also matched.
The actual dashboard outputs were checked against the same original reference.
The existing template encoded 25 fps in both paths, including requests with
24/30 fps; this optimization preserves that existing behavior.

These measurements exceed the requested 20% reduction for this batch on this
machine. They do not guarantee the same reduction for every source or machine;
startup savings matter more for shorter previews. No frame or quality reduction
was used to obtain the result.

Cache evidence (disposable, not committed):

- Original: `.cache/temp/preview-benchmark-w05v97hm/benchmark.json`
- Original repeat: `.cache/temp/preview-benchmark-mrwq0gvz/benchmark.json`
- Batch: `.cache/temp/preview-benchmark-58jkakds/benchmark.json`
- Batch repeat and pixel comparison: `.cache/temp/preview-benchmark-ad91ln2g/`
- Dashboard acceptance: `.cache/temp/preview-acceptance-c8j4n7nu/`

## Cancellation, failures, and regressions

Native dashboard acceptance also exercised a deliberately corrupt cache-only
MOV followed by a valid EXR request. It reported one failure and one success
using the same worker, with only the successful preview sent to the mocked API.
Cancelling after the first rendered frame stopped the worker, prevented the
second request and API updates, restored refresh state, and removed working
files. The full cancellation scenario, including Nuke startup, took 1.312 s.

The worker regression suite uses real subprocess pipes to cover reuse, media
failures, child exit/replacement, cancellation with silent stdout, cancellation
before startup, callback exceptions, active-cache clearing, fresh-script reset,
and generator versioning/skip behavior. Existing dashboard tests also verify
session cleanup on cancellation and overwrite.

All 67 targeted tests passed. Ruff passed for the four new modules; compilation
and `git diff --check` passed. Existing modules retain their unrelated baseline
lint findings.

Run the automated checks from the frontend directory:

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest \
  test.test_nuke_headless_tasks test.test_page_nukedash_preview_tasks \
  test.test_nukedash_regressions test.test_preview_batch
ruff check preview_batch.py test/test_preview_batch.py \
  test/preview_benchmark.py test/preview_acceptance.py
```

The opt-in native tools take explicit read-only media paths:

```bash
venv/bin/python test/preview_benchmark.py --nuke /path/to/Nuke \
  --movie /path/to/source.mov --sequence '/path/to/source_####.exr'
venv/bin/python test/preview_benchmark.py --nuke /path/to/Nuke \
  --movie /path/to/source.mov --sequence '/path/to/source_####.exr' --batch
venv/bin/python test/preview_acceptance.py --nuke /path/to/Nuke \
  --movie /path/to/source.mov --sequence '/path/to/source_####.exr' \
  --reference .cache/temp/preview-benchmark-ORIGINAL
```

Run comparisons on the same day so the template's date slate matches.
Acceptance invokes the real dashboard slot with isolated tasks and a mocked
API; it never writes the user's shot folders or database. Performance and native
process behavior have been measured on Linux only; Windows/macOS remain covered
by the existing portable code paths rather than native timing evidence.
