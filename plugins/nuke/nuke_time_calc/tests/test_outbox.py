"""Durable queue tests; all state lives under the caller's TMPDIR."""
import os
import tempfile
import unittest
from pathlib import Path


def batch(sequence=1):
    return dict(session_id='3f170ce8-aaca-45d0-b5b9-fb9b3ab46f48', sequence=sequence,
                machine='test', collector_version='2.0', policy_version='comp-v1',
                job_id=1, shot_id=1, script_path='/test.nk', reason='flush',
                started_at='2026-01-01T00:00:00Z', ended_at='2026-01-01T00:00:01Z',
                comp_microseconds=1000000, excluded_gap_microseconds=0,
                active_spans=[dict(start='2026-01-01T00:00:00Z', end='2026-01-01T00:00:01Z', duration_microseconds=1000000)])


class OutboxTests(unittest.TestCase):
    def test_windows_database_uri_preserves_extended_prefix_and_special_characters(self):
        from nuke_time_calc import outbox
        from pathlib import PureWindowsPath
        from types import SimpleNamespace
        from unittest.mock import patch
        from urllib.parse import urlsplit, unquote, parse_qs
        path = PureWindowsPath('C:/Studio café #1/shotbox_frontend')
        path = path.joinpath(*(['nested-' + 'a' * 60] * 5), 'outbox.sqlite3')
        with patch.object(outbox, 'os', SimpleNamespace(name='nt')):
            uri = outbox._database_uri(path)
        parsed = urlsplit(uri)
        self.assertEqual(parsed.scheme, 'file')
        self.assertEqual(parsed.netloc, '')
        self.assertEqual(parsed.fragment, '')
        self.assertEqual(unquote(parsed.path), '\\\\?\\' + str(path))
        self.assertEqual(parse_qs(parsed.query), {'mode': ['rw'], 'vfs': ['win32-longpath']})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'

    def make(self, **kwargs):
        from nuke_time_calc.outbox import Outbox
        return Outbox(self.root, server='https://example.test', artist_id='artist-1', device_id='device-1', **kwargs)

    def test_full_sqlite_promotion_rolls_back_then_retries(self):
        from nuke_time_calc.outbox import QueueFull
        q = self.make()
        q.enable_drafts()
        first = dict(batch(), reason='checkpoint')
        q.checkpoint(first)
        q.max_bytes = q.status()['storage_bytes']
        end = dict(batch(2), reason='script_close', started_at=first['ended_at'],
                   active_spans=first['active_spans'] * 250, comp_microseconds=250000000)
        with self.assertRaises(QueueFull):
            q.checkpoint(end)
        self.assertEqual(q.pending(), [])
        self.assertEqual(q.status()['draft_records'], 1)
        q.max_bytes *= 16
        q.checkpoint(end)
        q.checkpoint(end)
        row, = q.pending()
        self.assertEqual((row['sequence'], row['comp_microseconds']), (1, 251000000))

    def test_corrupt_draft_fails_closed_before_recovery(self):
        from nuke_time_calc.outbox import CorruptOutbox
        q = self.make()
        q.enable_drafts()
        q.checkpoint(dict(batch(), reason='checkpoint'))
        with q._connect() as db:
            db.execute("UPDATE checkpoints SET draft=REPLACE(draft,'/test.nk','/lost.nk')")
        with self.assertRaises(CorruptOutbox):
            q.enable_drafts()
        self.assertEqual(q.status()['draft_records'], 1)
        self.assertEqual(q.pending(), [])

    def test_window_limit_cannot_absorb_earlier_draft(self):
        q = self.make()
        q.enable_drafts()
        first = dict(batch(), reason='script_save')
        q.checkpoint(first)
        q.checkpoint(dict(first, sequence=2, reason='window_limit', started_at=first['ended_at'],
                          ended_at='2026-01-02T00:00:01Z', active_spans=[], comp_microseconds=0))
        self.assertEqual([b['sequence'] for b in q.pending()], [1, 2])

    def test_draft_reserves_slot_until_atomic_publish(self):
        from nuke_time_calc.outbox import QueueFull
        q = self.make(max_records=1)
        q.enable_drafts()
        fragment = dict(batch(), reason='checkpoint')
        q.checkpoint(fragment)
        with self.assertRaises(QueueFull):
            q.put(batch(2))
        q.enable_drafts()
        q.checkpoint(fragment)  # ambiguous local acknowledgement after recovery
        self.assertEqual(q.pending(), [fragment])

    def test_limits_fail_closed_and_partitions_do_not_mix(self):
        from nuke_time_calc.outbox import QueueFull, Outbox
        q = self.make(max_records=1)
        q.put(batch())
        with self.assertRaises(QueueFull):
            q.put(batch(2))
        self.assertEqual(q.pending(), [batch()])
        self.assertEqual(q.status()['pending_records'], 1)
        for change in [dict(server='https://other.test'), dict(artist_id='other'), dict(device_id='other')]:
            options = dict(server='https://example.test', artist_id='artist-1', device_id='device-1')
            options.update(change)
            self.assertEqual(Outbox(self.root, **options).pending(), [])
        tiny = Outbox(self.root / 'tiny', server='https://example.test', artist_id='a', device_id='d', max_bytes=16384)
        huge = batch()
        huge['script_path'] = 'x' * 32000
        with self.assertRaises(QueueFull):
            tiny.put(huge)
        self.assertEqual(tiny.pending(), [])

    def test_corruption_is_observable_and_never_removed(self):
        from nuke_time_calc.outbox import CorruptOutbox
        import sqlite3
        q = self.make()
        q.put(batch())
        from contextlib import closing
        with closing(sqlite3.connect(str(q.path))) as db:
            db.execute("UPDATE batches SET payload='{}'")
            db.commit()
        with self.assertRaises(CorruptOutbox):
            q.pending()
        self.assertTrue(q.path.exists())

    def test_schema_profile_rejects_secret_fields_and_bad_keys(self):
        q = self.make()
        for mutate in [dict(token='secret'), dict(schema_version=2), dict(sequence=True), dict(session_id='invalid')]:
            value = batch()
            value.update(mutate)
            with self.assertRaises(ValueError):
                q.put(value)
        self.assertEqual(q.pending(), [])

    def test_independent_processes_can_initialize_and_write_same_queue(self):
        import subprocess
        script = """
import sys, time, sqlite3
from nuke_time_calc.outbox import Outbox
from test_outbox import batch
for attempt in range(100):
    try:
        q = Outbox(sys.argv[1], server='https://example.test', artist_id='artist-1', device_id='device-1')
        q.put(batch(int(sys.argv[2])))
        break
    except sqlite3.OperationalError:
        time.sleep(.01)
else:
    raise RuntimeError('locked')
"""
        env = dict(os.environ)
        env['PYTHONPATH'] += os.pathsep + str(Path(__file__).parent)
        workers = [subprocess.Popen([os.sys.executable, '-c', script, str(self.root), str(i)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(1, 9)]
        results = [(worker, worker.communicate(timeout=15)) for worker in workers]
        for worker, (out, err) in results:
            self.assertEqual(worker.returncode, 0, err.decode())
        self.assertEqual(len(self.make().pending()), 8)

    def test_binding_is_read_only_and_none_identity_is_rejected(self):
        from nuke_time_calc.outbox import Outbox
        q = self.make()
        with self.assertRaises(AttributeError):
            q.binding = ('https://other.test', 'other', 'other')
        with self.assertRaises(ValueError):
            Outbox(self.root, server='https://example.test', artist_id=None, device_id='device')

    def test_new_nested_directories_are_private_and_symlink_ancestors_rejected(self):
        from nuke_time_calc.outbox import Outbox
        deep = self.root / 'nested' / 'state'
        from unittest.mock import patch
        with patch('os.fsync', wraps=os.fsync) as sync:
            Outbox(deep, server='https://example.test', artist_id='a', device_id='d')
        self.assertGreaterEqual(sync.call_count, 4)
        for directory in [self.root, self.root / 'nested', deep]:
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            Outbox(alias / 'new', server='https://example.test', artist_id='a', device_id='d')

    def test_truncated_database_is_not_reinitialized(self):
        from nuke_time_calc.outbox import CorruptOutbox
        q = self.make()
        q.put(batch())
        q.path.write_bytes(b'')
        with self.assertRaises(CorruptOutbox):
            self.make()
        self.assertEqual(q.path.stat().st_size, 0)

    def test_invalid_capacity_is_rejected_before_creating_state(self):
        from nuke_time_calc.outbox import Outbox
        for config in [dict(max_records=0), dict(max_records=True), dict(max_bytes=8192), dict(max_bytes=float('inf'))]:
            with self.assertRaises(ValueError):
                self.make(**config)
        self.assertFalse(self.root.exists())

    def test_nested_unknown_fields_and_nonprimitive_profile_are_not_persisted(self):
        q = self.make()
        bad = batch()
        bad['active_spans'][0]['token'] = 'must-not-persist'
        for value in [bad, dict(batch(), machine={'token': 'must-not-persist'}), dict(batch(), collector_version='1.0'), dict(batch(), shot_id=True)]:
            with self.assertRaises(ValueError):
                q.put(value)
        self.assertEqual(q.pending(), [])

    def test_disk_failure_retains_previous_committed_batches(self):
        from unittest.mock import patch
        q = self.make()
        q.put(batch())
        with patch.object(q, '_connect', side_effect=OSError('isolated disk failure')):
            with self.assertRaises(OSError):
                q.put(batch(2))
        self.assertEqual(self.make().pending(), [batch()])

    def test_crashed_process_keeps_commit_but_rolls_back_uncommitted_write(self):
        import subprocess
        q = self.make()
        script = """
import os, sqlite3, sys
from nuke_time_calc.outbox import Outbox
from test_outbox import batch
q = Outbox(sys.argv[1], server='https://example.test', artist_id='artist-1', device_id='device-1')
q.put(batch())
db = sqlite3.connect(str(q.path))
db.execute('BEGIN IMMEDIATE')
db.execute("INSERT INTO batches VALUES ('uncommitted', 2, '{}', 'invalid', 1)")
os._exit(23)
"""
        env = dict(os.environ)
        env['PYTHONPATH'] += os.pathsep + str(Path(__file__).parent)
        result = subprocess.run([os.sys.executable, '-c', script, str(self.root)], env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr.decode())
        self.assertEqual(self.make().pending(), [batch()])

    def test_byte_saturation_preserves_every_preceding_record(self):
        from nuke_time_calc.outbox import QueueFull
        q = self.make(max_bytes=16384)
        accepted = []
        for sequence in range(1, 100):
            value = batch(sequence)
            try:
                q.put(value)
            except QueueFull:
                break
            accepted.append(value)
        else:
            self.fail('queue did not enforce byte cap')
        self.assertTrue(accepted)
        self.assertEqual(q.pending(), accepted)
        self.assertLessEqual(q.path.stat().st_size, 16384)
        for value in accepted:
            q.acknowledge(value['session_id'], value['sequence'])
        self.assertEqual(q.pending(), [])

    def test_linked_database_cannot_cross_binding_partitions(self):
        q = self.make()
        q.put(batch())
        alias = q.path.parent / 'linked.sqlite3'
        os.link(q.path, alias)
        with self.assertRaises(ValueError):
            self.make()
        self.assertTrue(alias.exists())

    def test_copied_database_cannot_be_rebound_to_another_account(self):
        import shutil
        from nuke_time_calc.outbox import Outbox, CorruptOutbox
        first = self.make()
        first.put(batch())
        other = Outbox(self.root, server='https://example.test', artist_id='other', device_id='device-1')
        shutil.copyfile(first.path, other.path)
        with self.assertRaises(CorruptOutbox):
            Outbox(self.root, server='https://example.test', artist_id='other', device_id='device-1')
        self.assertEqual(first.pending(), [batch()])

    def test_unsupported_platform_fails_before_creating_state(self):
        from unittest.mock import patch
        with patch('nuke_time_calc.storage.supported_platform', return_value=False):
            with self.assertRaises(OSError):
                self.make()
        self.assertFalse(self.root.exists())

    def test_key_is_immutable_even_after_ack(self):
        from nuke_time_calc.outbox import BatchConflict
        q = self.make()
        q.put(batch())
        q.put(batch())
        changed = batch()
        changed['reason'] = 'changed'
        with self.assertRaises(BatchConflict):
            q.put(changed)
        q.acknowledge(batch()['session_id'], 1)
        self.assertEqual(q.pending(), [])
        q.put(batch())
        self.assertEqual(q.pending(), [])
        with self.assertRaises(BatchConflict):
            q.put(changed)

    def test_sink_survives_restart_with_private_storage(self):
        q = self.make()
        q.put(batch())
        self.assertEqual(self.make().pending(), [batch()])
        self.assertEqual(q.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(q.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
