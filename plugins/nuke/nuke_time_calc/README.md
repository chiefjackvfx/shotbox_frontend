# nuke_time_calc — experimental collector

A small automatic **comp activity estimator**, not a timesheet or proof of work.
The original schema-1 collector and local diagnostic bootstrap remain available.
An explicit schema-2 mode now emits active spans for Shotbox; `outbox.py`,
`transport.py`, `context.py`, `managed_setup.py` and `runtime.py` provide opt-in
local persistence, authenticated delivery, verified shot mapping and startup.
The desktop settings page exposes explicit setup controls. Nothing uploads or
installs just by importing this package. Production ingestion remains disabled
pending the deployment/pilot gate. See `INTEGRATION.md` for setup and limits.

**Platform limit:** the durable outbox currently supports POSIX only and fails
closed on Windows. Windows private-storage support and real-host acceptance
remain rollout blockers, not completed cross-platform functionality.

Importing modules does nothing to Nuke. `install()` defaults to disabled.
The optional `local_test.register()` bootstrap creates **shotbox → nuke_time_calc**
and automatically enables diagnostic collection in GUI sessions only.

## Installed local test

The authorised local installation is registered in `/home/gambit/.nuke/menu.py`.
Open a **new Nuke session** to load it; existing sessions were not changed.
Menu commands: **Status** (prints to Script Editor), **Enable local test**,
**Disable local test**, and **Open diagnostics folder**. Disable applies to this
session; startup enables again next launch. No timer operation is required.

Plain per-session JSONL records are saved under
`~/.local/state/shotbox/nuke_time_calc/`, outside VFX folders, with restricted
permissions. These are local diagnostic records, **not Shotbox reports**.
Identity is explicitly labelled Jack diagnostic/not authenticated; project is
`local-test`, and the script path is only a diagnostic grouping, not a Shotbox ID.
Do not add these test records to production hours. The sink fsyncs successful
writes, but queued/unsealed data can still be lost on crash; partial failed writes
and retry duplicates need reconciliation before any analysis.

To remove startup installation, remove only the block between
`BEGIN SHOTBOX NUKE_TIME_CALC LOCAL TEST` and its matching `END` marker in
`~/.nuke/menu.py`. A pre-install backup is alongside it at
`menu.py.pre-nuke_time_calc-20261113-130916.bak`; do not restore over later edits.
Source is loaded directly from `shotbox_frontend/plugins/nuke/nuke_time_calc/`,
not copied into `.nuke`. The existing Matte startup is unchanged; this experimental
tracker still uses the separate local opt-in startup block, not automatic
deployment to everyone using the shared integration. New Nuke sessions pick up
relocation and the `machine` hostname field. Existing log records are unchanged.

## Run the stdlib tests

From this directory, with system Python (no packages to install):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=..:../../.. python -m unittest discover -s tests -v
```

The frontend root is included for the desktop launch-handoff contract tests.
The remaining API/timing examples below describe **legacy schema-1 diagnostic
mode**. The new schema-2 delivery contract and verification are in `INTEGRATION.md`.

`tests/test_core.py` exercises the real core with injected clocks.
`tests/test_adapter.py` uses explicitly fake Nuke and Qt objects; these are
**adapter contract tests, not live Nuke validation**. Ten additional tests cover
the menu bootstrap and real filesystem sink using fake host objects. With the hostname regression test, all 43 pass.
Separate Nuke 17.1v2 host and GUI startup/save/teardown probes passed; see
`VALIDATION.md`. Real editing, idle/focus and render-cancellation acceptance
still needs manual testing.

## Manual developer setup / public API

Add the **parent directory** (`shotbox_frontend/plugins/nuke`), not this package directory,
to the Nuke Python path. Run the following only after explicitly opting in,
on Nuke's main GUI thread. Do not add it to startup by default.

```python
import sys
sys.path.insert(0, "/path/to/shotbox_frontend/plugins/nuke")

import json
from nuke_time_calc.nuke_adapter import install

# Explicit diagnostic-only binding. This does not resolve real Shotbox shots.
# Every script uses this test context until the developer deliberately changes it.
test_context = {"project_id": "local-test", "shot_id": "manual-sandbox"}

def resolve_context():
    return test_context  # return None if the current script has no known binding

def diagnostic_sink(record):
    print(json.dumps(record, allow_nan=False))  # replace with an owned local sink

tracker = install(
    enabled=True,
    identity="manual-local-test",
    context_resolver=resolve_context,
    emit=diagnostic_sink,
)
```

A parent integration may supply a plain local JSONL diagnostic sink outside VFX
folders instead. That is still **local diagnostics, not live Shotbox reporting**.
The sink must be quick, non-reentrant, and **raise an exception on failure**;
any normal return means accepted. Do not put blocking HTTP in this callback.
It runs synchronously at checkpoints/lifecycle boundaries, never per knob event.

Public interface for a reversible bootstrap/menu:

- `install(enabled=False, identity=None, context_resolver=None, emit=None, ...)`:
  disabled returns `None` without importing Nuke/Qt; enabled requires all three
  explicit inputs and a currently valid context, otherwise installation fails.
  Dependencies are loaded lazily (`PySide2`, then `PySide6` fallback).
- Returned `tracker.status()` returns a JSON-ready snapshot: installed/running,
  session ID, sequence, focus/render flags, pending count, blocked state,
  unsealed comp seconds, and error class. It does not tick or send anything.
- `tracker.refresh_context()` explicitly resolves context, flushes the old shot
  before changing it, and enables the valid context without assuming activity.
  Call at the actual external binding change, not later on the next knob.
  Script load/save also resolve context; knob/timer callbacks never do.
- `tracker.uninstall()` stops the timer, disconnects Qt and Nuke callbacks, and
  flushes the current interval. It is idempotent. Keep the returned object while
  pending data exists; uninstall does not discard its pending queue.
- Re-enable by calling `install(...)` again after uninstall. Installing twice
  concurrently raises `RuntimeError` rather than double-counting.
- `tracker.collector.retry_pending()` retries already sealed records.
  After queue saturation, repair the sink and call
  `tracker.collector.flush("recovery")` to seal the held interval, then
  `tracker.refresh_context()` to resume an installed adapter. Recovery does not
  invent activity. An uninstalled adapter cannot resume through refresh.

`context_resolver()` must return a JSON-serializable dict with nonempty string
`project_id` and `shot_id`; optional `script_path`, `task_id`, or other metadata
are caller-owned. There is **no basename, path, login, or machine-name guessing**.
At load/save, unresolved or failing context stops tracking rather than continuing
on the old shot. Duplicate installation is rejected; partial Nuke callback
registration is rolled back. A normal GUI QApplication/event loop is required.

## Timing and records

`nuke_time_calc.core.Collector` is usable without Nuke or Qt. Construct it with
`identity`, `context`, and `emit`; then call `start()`, `activity()`, `tick()`,
`set_focus(bool)`, `set_rendering(bool)`, `switch_context(dict)`, `flush(reason)`,
and `stop()`. Construction and `start()` alone do not count as activity.
It accepts injected `monotonic` and `utc_now` callables (the latter returning an
aware UTC `datetime`). Calls must be serialized on the owning thread.

The integrated schema-2 plugin uses `comp-v2`: eligible time accrues until
five minutes after the last recognised edit, never five minutes up front.
Opening a script, saving, and watching playback do not count as edits.
See `INTEGRATION.md` for exclusions, upgrade order and native review.

Legacy schema-1 local diagnostic policy (unchanged):

- Activity callbacks: knob changes, user node creation, node destruction.
  While unfocused, stopped, rendering, or blocked, activity is ignored.
- Each activity grants up to 20 seconds of estimated activity; overlap with
  each settled interval is clamped exactly to the idle boundary. A later event
  does not retroactively fill an idle gap.
- Focus and render transitions settle the **old** state before changing flags.
  Refocusing or finishing a render requires a fresh activity callback to resume.
- A 1-second Qt timer settles elapsed state; checkpoints emit every 60 seconds.
  Save, close, context changes, and uninstall also flush.
- A gap **greater than 60 seconds** between settlements is wholly excluded,
  including any remaining idle allowance. Both monotonic and UTC elapsed gaps
  are checked because a suspend can pause the monotonic clock. A large forward
  UTC correction is conservatively excluded too. Durations otherwise use only
  monotonic time; UTC strings are labels and can reflect wall-clock corrections.
- Foreground before/after-render hooks exclude render time even if Qt is blocked
  for the entire render. No render duration is added to comp time. If cancellation
  omits the after-render hook, the next timer checks `nuke.executing()` and settles
  the entire uncertain interval as non-comp **before** clearing rendering. There
  are no guessed abort callback registrations. No per-knob executing poll.

Each emission is a non-cumulative interval record:

| Field | Meaning |
| --- | --- |
| `schema_version`, `component` | `1`, `"nuke_time_calc"` |
| `session_id` | UUID stable for the collector instance, including context/load changes |
| `sequence` | Increasing integer within that session, unchanged on retries |
| `machine` | Hostname captured once per session; diagnostic workstation label, not artist identity |
| `identity`, `context` | Explicit caller-supplied attribution, snapshot per interval |
| `started_at`, `ended_at` | ISO UTC observation labels |
| `comp_seconds` | Estimated active comp time in this interval, not a total |
| `excluded_gap_seconds` | Diagnostic excluded long-gap estimate, not worked time |
| `reason` | E.g. `checkpoint`, `script_save`, `script_close`, `context_switch`, `uninstall` |

A future receiver should deduplicate by `(session_id, sequence)`, because a sink
that accepts a record and then raises can receive the same record again.
Neither this schema nor a future endpoint is an implemented Shotbox API contract.

## Failure behavior and limitations

- Sink exceptions retain immutable record snapshots in FIFO order. Error **class**
  and pending count are visible without printing identities or error payloads.
- At 128 queued records, one unsealed interval accumulator is retained; further
  counting pauses, a context change cannot overwrite old attribution, and the
  core raises `BufferError`. Host callback guards surface this in status and stop
  tracking rather than throwing into Nuke. No unbounded queue growth/drop-oldest.
- This queue is **memory only**. A crash, forced exit, or discarding the object
  loses queued/unsealed data. Even a successful sink return is not durable unless
  that sink makes it so. Disk spool and acknowledgement/retry transport are future
  integration work, not hidden background services.
- Callback activity is approximate: programmatic knob/destruction callbacks can
  look like work, while reading, thinking, viewer-only interaction, or node-graph
  navigation without callbacks may be missed. This is not payroll-grade evidence.
- Qt timers can be delayed by blocking operations. Long stalls are deliberately
  undercounted rather than billed as work. Cancellation recovery may undercount
  the time before the next timer/new activity. No farm/background-render tracking,
  concurrent/nested render accounting, or live Nuke compatibility claim is made.
- The developer must smoke-test normal edits, idle, application switching, render
  completion/cancellation, save/load/close, context changes, sink failure, and
  uninstall in their actual Nuke version before relying on this experiment.

Upstream origin: `UPSTREAM.md`.
Implementation references: Foundry Python callback documentation and Qt timer
semantics (`https://learn.foundry.com/nuke/developers/14.0/pythonreference/callbacks.html`,
`https://doc.qt.io/qtforpython-6/overviews/qtcore-timers.html`).
