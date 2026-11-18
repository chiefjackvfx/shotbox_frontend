"""Deterministic offline traces through the installed runtime factory and real SQLite."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nuke_time_calc.nuke_adapter import install
from nuke_time_calc.runtime import build_runtime
from test_adapter import FakeNuke, Timer, FakeApp
from test_core import Clock


class Trace(unittest.TestCase):
    def runtime(self, folder):
        clock, host = Clock(), FakeNuke()
        host.path = '/trace/a.nk'
        host.root = lambda: SimpleNamespace(name=lambda: host.path)
        qt = SimpleNamespace(QTimer=Timer, Qt=SimpleNamespace(ApplicationActive=1))
        config = dict(enabled=True, server='https://offline.invalid', device_id='test',
                      artist_id=1, outbox_dir=folder, token_file='unused',
                      mappings=[dict(base_path='/trace', job_id=1, shot_id=2)])
        rt = build_runtime(config, host,
            uploader_factory=lambda *a, **kw: SimpleNamespace(start=lambda: None, stop=lambda **k: None),
            installer=lambda **kw: install(**kw, qt_core=qt, app=FakeApp(),
                                            monotonic=clock.monotonic, utc_now=clock.utc))
        rt.resolve.loader = lambda path: config
        rt.enable()
        self.assertIsNotNone(rt.adapter, rt.last_error)
        return rt, clock, host

    def trace(self, mode):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            rt, clock, host = self.runtime(Path(tmp)/'queue')
            first_ready = None
            for second in range(3600):
                if mode != 'idle' and second % 10 == 0:
                    host.fire('KnobChanged')
                clock.advance(1)
                rt.adapter.timer.timeout.emit()
                if mode == 'saves' and (second + 1) % 30 == 0:
                    host.fire('OnScriptSave')
                if first_ready is None and (second + 1) % 30 == 0 and rt.outbox.pending():
                    first_ready = second + 1
            rows = rt.outbox.pending()
            status = rt.outbox.status()
            rt.disable()
            credit = sum(b['comp_microseconds'] for b in rows)
            print(mode, len(rows), credit, status['storage_bytes'], 'first_ready_s', first_ready)
            self.assertEqual([b['sequence'] for b in rows], list(range(1, len(rows)+1)))
            return len(rows), credit

    def test_playback_and_saves_do_not_extend_grace(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            rt, clock, host = self.runtime(Path(tmp)/'queue')
            host.fire('OnScriptLoad')
            for second in range(0, 660, 30):
                if second == 60:
                    host.fire('OnUserCreate')
                for node, knob in [('Root', 'frame'), ('Viewer', 'frame'),
                                   ('Grade', 'frameChanged'), ('Grade', 'updateUI')]:
                    host.thisNode = lambda: SimpleNamespace(Class=lambda: node)
                    host.thisKnob = lambda: SimpleNamespace(name=lambda: knob)
                    host.fire('KnobChanged')
                clock.advance(30)
                rt.adapter.timer.timeout.emit()
                host.fire('OnScriptSave')
            host.fire('OnScriptClose')
            spans = [s for r in rt.outbox.pending() for s in r['active_spans']]
            self.assertEqual(sum(s['duration_microseconds'] for s in spans), 300_000_000)
            from datetime import timedelta, datetime
            self.assertEqual(datetime.fromisoformat(spans[0]['start']), clock.utc() - timedelta(seconds=600))
            self.assertEqual(datetime.fromisoformat(spans[-1]['end']), clock.utc() - timedelta(seconds=300))
            rt.disable()

    def test_upgrade_preserves_legacy_upload_and_draft(self):
        from test_outbox import batch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            folder = Path(tmp)/'queue'
            old, _, _ = self.runtime(folder)
            old.disable()
            pending = batch()
            draft = dict(batch(), reason='checkpoint',
                         session_id='0c65c284-ec25-4088-a3eb-c3848dace012')
            old.outbox.put(pending)
            old.outbox.checkpoint(draft)
            self.assertEqual(old.outbox.status()['draft_records'], 1)
            rt, clock, host = self.runtime(folder)
            host.fire('KnobChanged')
            for i in range(20):
                clock.advance(30)
                rt.adapter.timer.timeout.emit()
                if i == 7:
                    host.fire('KnobChanged')
            rt.disable()
            rows = rt.outbox.pending()
            self.assertIn(pending, rows)
            self.assertIn(draft, rows)
            fresh = [r for r in rows if r['policy_version'] == 'comp-v2' and r['comp_microseconds']]
            self.assertEqual([(r['sequence'], r['comp_microseconds']) for r in fresh],
                             [(1, 300_000_000), (2, 240_000_000)])
            self.assertEqual(len({r['session_id'] for r in fresh}), 1)
            self.assertNotIn(fresh[0]['session_id'], (pending['session_id'], draft['session_id']))

    def test_process_exit_and_restart(self):
        import subprocess
        import sys
        from test_outbox import batch
        for minutes in (2, 5):
            with self.subTest(minutes=minutes), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                folder = Path(tmp)/'queue'
                rt, _, _ = self.runtime(folder)
                rt.outbox.put(batch())
                subprocess.run([sys.executable, '-c',
                    'from test_runtime_batching import *; import sys; '
                    'rt,c,h=Trace().runtime(Path(sys.argv[1])); '
                    '[ (h.fire("KnobChanged"), c.advance(10), rt.adapter.timer.timeout.emit()) '
                    'for _ in range(int(sys.argv[2])*6) ]; os._exit(0)',
                    str(folder), str(minutes)], check=True,
                    env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
                for _ in range(2):
                    recovered, _, _ = self.runtime(folder)
                    rows = recovered.outbox.pending()
                    self.assertEqual(rows[0], batch())
                    self.assertEqual([(b['sequence'], b['comp_microseconds']) for b in rows[1:]],
                                     [(1, minutes * 60000000)])

    def test_two_shots_match_minute_collector_spans_and_ambiguity(self):
        from datetime import datetime
        from nuke_time_calc.core import Collector
        from collections import defaultdict
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            folder = Path(tmp)/'queue'
            legacy = []
            for shot in (1, 2):
                rt, clock, host = self.runtime(folder)
                ctx = dict(job_id=1, shot_id=shot, script_path='/trace/%s.nk' % shot)
                rt.resolve = lambda path: ctx
                host.fire('OnScriptLoad')
                old = Collector(schema_version=2, identity='old', context=ctx, emit=legacy.append,
                                monotonic=clock.monotonic, utc_now=clock.utc)
                old.start()
                for second in range(3600):
                    if (second + 7*shot) % (30 + shot) == 0:
                        old.activity()
                        host.fire('KnobChanged')
                    clock.advance(1)
                    old.tick()
                    rt.adapter.timer.timeout.emit()
                    if (second + 1) % 60 == 0:
                        old.flush()
                old.stop()
                rt.disable()
            def sweep(rows):
                events = defaultdict(lambda: defaultdict(int))
                for b in rows:
                    for s in b['active_spans']:
                        for k, delta in [('start', 1), ('end', -1)]:
                            events[datetime.fromisoformat(s[k])][b['shot_id']] += delta
                counts, totals, endpoints = defaultdict(int), defaultdict(int), []
                previous = None
                for t, changes in sorted(events.items()):
                    active = tuple(sorted(k for k, v in counts.items() if v))
                    if previous is not None:
                        totals[active] += (t-previous) // timedelta(microseconds=1)
                    for k, delta in changes.items():
                        counts[k] += delta
                    now = tuple(sorted(k for k, v in counts.items() if v))
                    if now != active:
                        endpoints.append((t, now))
                    previous = t
                return totals, endpoints
            from datetime import timedelta
            reference, result = sweep(legacy), sweep(rt.outbox.pending())
            self.assertGreater(reference[0][(1, 2)], 0)
            self.assertEqual(reference, result)

    def test_save_as_and_gap_flush_distinct_windows(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            rt, c, host = self.runtime(Path(tmp)/'queue')
            host.fire('KnobChanged')
            c.advance(10)
            host.fire('OnScriptSave')
            host.path = '/trace/b.nk'
            host.fire('OnScriptSave')
            host.fire('KnobChanged')
            c.advance(7)
            rt.adapter.timer.timeout.emit()
            c.advance(70)
            rt.adapter.timer.timeout.emit()
            host.fire('OnScriptClose')
            rows = rt.outbox.pending()
            self.assertEqual([(b['script_path'], b['comp_microseconds']) for b in rows[:2]],
                             [('/trace/a.nk', 10000000), ('/trace/b.nk', 7000000)])
            self.assertEqual(sum(b['excluded_gap_microseconds'] for b in rows), 70000000)
            self.assertNotEqual(rows[0]['session_id'], rows[-1]['session_id'])

    def test_checkpoint_edges_do_not_fragment_contiguous_activity(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            rt, clock, host = self.runtime(Path(tmp)/'queue')
            for _ in range(30):
                host.fire('KnobChanged')
                clock.advance(10)
                rt.adapter.timer.timeout.emit()
            row, = rt.outbox.pending()
            self.assertEqual(row['active_spans'], [dict(start=row['started_at'], end=row['ended_at'],
                                                       duration_microseconds=300000000)])

    def test_monotonic_reset_keeps_minute_recovery(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            rt, clock, host = self.runtime(Path(tmp)/'queue')
            for _ in range(10):
                clock.advance(60)
                rt.adapter.timer.timeout.emit()
            clock.now = 0
            rt.adapter.timer.timeout.emit()
            for _ in range(6):
                host.fire('KnobChanged')
                clock.advance(10)
                rt.adapter.timer.timeout.emit()
            self.assertEqual(rt.outbox.status()['draft_records'], 1)

    def test_idle_epoch_boundary_keeps_clock_diagnostic(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            rt, clock, host = self.runtime(Path(tmp)/'queue')
            for _ in range(5):
                clock.advance(60)
                rt.adapter.timer.timeout.emit()
            clock.advance(70)
            rt.adapter.timer.timeout.emit()
            self.assertEqual([b['reason'] for b in rt.outbox.pending()],
                             ['checkpoint', 'clock_epoch_end', 'excluded_gap'])

    def test_second_process_recovery_does_not_duplicate_live_delta(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            folder = Path(tmp)/'queue'
            rt, clock, host = self.runtime(folder)
            def minute():
                for _ in range(6):
                    host.fire('KnobChanged')
                    clock.advance(10)
                    rt.adapter.timer.timeout.emit()
            minute()
            subprocess.run([sys.executable, '-c',
                'from test_runtime_batching import *; import sys; '
                'rt,c,h=Trace().runtime(Path(sys.argv[1])); h.fire("OnScriptClose")', str(folder)],
                check=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
            minute()
            host.fire('OnScriptClose')
            own = [b for b in rt.outbox.pending() if b['session_id'] == rt.adapter.collector.session_id]
            self.assertEqual([(b['sequence'], b['comp_microseconds']) for b in own],
                             [(1, 60000000), (2, 60000000)])
            self.assertEqual(own[0]['ended_at'], own[1]['started_at'])

    def test_hour(self):
        for mode, expected in [('active', (12, 3600000000)), ('idle', (1, 0)), ('saves', (12, 3600000000))]:
            with self.subTest(mode=mode):
                self.assertEqual(self.trace(mode), expected)
