# Local checkpoints and five-minute uploads — phase 2

## Evidence (Linux, 2026-11-18)

Baseline: frontend `50927d5`, before production edits, 206 tests run in 15.553 s,
OK, 9 skipped. The new hour-trace acceptance test then failed on all three row
counts (60/60/180). Final integrated plugin suite: 219 tests run in 21.787 s,
OK, 9 skipped; Python 3.14.7, SQLite 3.53.1. Times are illustrative, not SLOs.

Each trace drives 3,600 seconds of deterministic Nuke/Qt callbacks through the
real `build_runtime`, adapter, collector, private storage checks and on-disk
SQLite outbox. The host, clock and offline worker are substitutes. Samples are
before lifecycle close. Active = activity every 10 s; saves = same work plus a
save every 30 s. First ready = simulated time to a deliverable row (30 s probes),
not HTTP latency. Counts are immutable uploads, **not measured server inserts**.

| 60-minute trace | Uploads old → new | Active µs old = new | DB bytes old → new | First ready (s) old → new |
|---|---:|---:|---:|---:|
| Active | 60 → 12 | 3,600,000,000 | 57,344 → 32,768 | 60 → 300 |
| Idle | 60 → 1 | 0 | 49,152 → 24,576 | 60 → 300 |
| Same-path saves | 180 → 12 | 3,600,000,000 | 135,168 → 32,768 | 30 → 300 |

Independent sweep over two partially overlapping hour traces checks exact
active endpoints and microseconds for each active shot set against the minute
collector, including ambiguous intervals. Separate tests cover Save As, clock
reset, real subprocess exit after two checkpoints or immediately after five-minute
promotion, concurrent process startup, exact legacy queue preservation, SQLite
FULL on promotion and duplicate delivery after a lost real loopback HTTP ACK.
The fixtures neither require nor contact Shotbox.

### Reproduce from frontend root (POSIX shell)

```sh
export HOME=$(mktemp -d)  # use a private scratch TMPDIR, not a serving profile
export XDG_CONFIG_HOME=$HOME/config XDG_DATA_HOME=$HOME/data
export PYTHONPATH=.:plugins/nuke:plugins/nuke/nuke_time_calc/tests
python -m unittest discover -s plugins/nuke/nuke_time_calc/tests -q
python -m unittest test_runtime_batching
old=$(mktemp -d)
git archive 50927d5 plugins/nuke/nuke_time_calc | tar -x -C "$old"
PYTHONPATH="$old/plugins/nuke:.:plugins/nuke/nuke_time_calc/tests" python -c \
  "from test_runtime_batching import Trace; [Trace().trace(m) for m in ('active','idle','saves')]"
```

## Release gates — not cleared by the isolated suite

1. Verify private outbox backup/restore and enough free disk, including SQLite's
   rollback journal. No fingerprint pruning: a high-water mark alone cannot
   detect a changed replay. Monitor `acknowledged_fingerprints`, `draft_records`,
   pending/quarantined counts, storage bytes vs byte limit, and collector errors.
   A full queue fails closed. Do not delete it to free space.
2. Before expansion pilot ONE workstation. With actual Nuke and isolated pilot
   credentials on each supported OS, repeat offline save, Save As, concurrent
   instances, abrupt restart then reconnect. Confirm server rows, contiguous
   per-session sequences, report credit/ambiguity, disk growth and end-to-end
   reporting lag. Include old and new clients and rollback rehearsal.
3. Before downgrade, stop **all** producers for each affected binding; recover
   with this version under the original binding, drain to exact acknowledgements,
   confirm zero drafts/pending and review quarantines. Back up the whole private
   partition. If offline, full, or a binding cannot be recovered, keep the new
   recovery client and delay downgrade. Old clients cannot read drafts. Never
   rebind, manually rewrite SQLite, or discard pending data or fingerprints.
4. No serving configuration, backend reports, production volumes, physical
   power-cut guarantees or workstation smoke tests were validated here.
