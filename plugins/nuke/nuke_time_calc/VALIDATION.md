# Validation evidence

## Relocation and machine logging

Package moved to `plugins/nuke/nuke_time_calc`; local startup now imports from
`plugins/nuke`. All 43 tests pass, including the hostname snapshot/retry test
which failed before implementation. A fresh Nuke 17.1v2 GUI probe confirmed
imports from the new location, menu, automatic startup, node activity, save
context, clean disable and reopened JSONL with `machine: gambit-local`.
Evidence: `/home/gambit/.hermes/cache/scratch/nuke_time_calc_relocated_probe/result.json`.
Existing records are not rewritten. Hostname is a diagnostic label, not a
globally unique machine ID or authenticated artist identity.


## Parent verification and local installation

Independently executed full suite: `Ran 42 tests in 0.117s`, `OK`.
The added ten tests cover local menu/bootstrap and JSONL filesystem behavior.
Independent core/adapter review found no material blocker for local experimentation.

Real Nuke 17.1v2 / Qt 6.5.3 probes:
- Isolated `--safe --tg` process: actual node callbacks, positive elapsed record,
  adapter teardown passed. This terminal probe alone does not validate GUI menus.
- Separate GUI process using normal user startup: top-level `shotbox` and all four
  submenu commands found, automatic enable succeeded, real node callbacks fired,
  save updated script context, diagnostic file reopened successfully, disable
  removed the adapter with no pending records. Screenshot confirms top-level menu.
- Final JSONL file contained four records with sequences 1–4 and mode 0600.
- Probe saved only a scratch empty script. Original Nuke PID remained running.

Local startup registration is in `~/.nuke/menu.py`, with a verified pre-edit backup.
Probe artifacts: `/home/gambit/.hermes/cache/scratch/nuke_time_calc_gui_probe/`.
`result.json` records machine-readable assertions and `nuke-menu.png` the UI.
The existing environment emitted OFX/OpenVR/GPU warnings, but probe assertions passed.
Full interactive/render-cancellation/cross-process acceptance is not established.

## Original collector-only checks

From `nuke_time_calc`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.. python -m unittest discover -s tests -v
```

Actual final result on system **Python 3.14.7**:

```text
Ran 32 tests in 0.019s
OK
```

These comprise 13 pure-core tests and 19 fake-host adapter contract tests.
No dependency installation was needed. No Nuke process was launched or modified.

A separate standalone smoke invocation used the real monotonic/UTC clocks,
explicit test identity/context, a JSON-printing sink, `start()`, `activity()`,
a short `time.sleep(0.02)`, and `stop()`. Actual emitted `comp_seconds` was
`0.020060680981259793`, sequence was `1`, pending count was `0`, and the collector
was stopped. It printed `STANDALONE_SMOKE_PASS`. This validates execution and
JSON output, **not live Nuke event behavior**.

## Observed vertical RED → GREEN slices

Each new behavior was added as a test, executed before the corresponding
implementation, then followed by a passing full suite. Missing-symbol checks
were not used as the sole evidence for later state-machine behavior.
Representative failures observed in those actual runs:

| Slice | Observed RED before fix | Subsequent GREEN |
| --- | --- | --- |
| Initial activity record | Core absent | Actual activity emitted a JSON interval |
| Idle overlap | `[17.0, 10.0] != [17, 5]` | Correct overlap before renewed activity |
| Focus settlement | `16.0 != 7` | Prior focused time settled; no background/refocus credit |
| Render settlement | `21.0 != 7` | Blocking render interval excluded |
| Long monotonic gap | `[5.0, 15.0] != [5, 0]` | Sleep/stall gap excluded |
| Suspend with paused monotonic | `1.0 != 0` | UTC gap also detects suspension conservatively |
| Lifecycle | `2.0 != 5` | Duplicate start preserves interval; stop flushes once |
| Context switch | `0 != 1: Old context must flush immediately` | Old attribution emitted before switching |
| Invalid configuration | `ValueError not raised` | Explicit valid identity/context/sink required |
| Sink failure | Sink exception escaped test | Pending immutable FIFO snapshots retained/retried |
| Queue bound | `BufferError not raised` | 128 records plus held accumulator; pause without loss |
| Stop under backpressure | `True is not false` for running | Stop fails closed and preserves held interval |
| Adapter timer | `0 != 1: Timer must emit a checkpoint` | Tick settlement and scheduled emission |
| Adapter focus | `9.0 != 4` | Initial state and focus signal honored |
| Adapter render | `17.0 != 3` | Real core used through fake render callbacks |
| Cancellation fallback | Rendering remained `True` | Timer settles before clearing canceled render |
| Adapter context save | `'s1' != 's2'` | Resolver refresh flushes old shot |
| Uninstall | `0 != 1` emitted records | Flush once; detach timer, Qt, Nuke hooks |
| Host callback backpressure | BufferError escaped callback | Status shows paused queue and retained data |
| Closed-script timer | `3 != 1` emitted records | No phantom checkpoints while stopped |
| Partial registration | Remaining callbacks were truthy | Failed installation rolls back registrations |

Additional executed slices cover disabled imports, lazy PySide2/PySide6 loading,
duplicate install refusal, invalid configuration, unsupported callback preflight,
resolver exceptions, unresolved context recovery, and uninstall with a full queue.
All pass in the final suite above.

## Still not validated / intentionally not implemented

- Live validation is limited to the parent probes above, not full manual acceptance.
- The local bootstrap is installed for this user only, not workers or Windows.
- No Shotbox API/HTTP, authentication, server changes, durable spool, or crash
  recovery. Sink acceptance is not necessarily durable. See README limitations.
- Host main-thread semantics, particular Nuke version callbacks, real foreground
  render cancellation, and actual user interaction coverage require the planned
  manual Nuke smoke test.
