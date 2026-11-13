"""Schema-2 contract tests; deterministic real collector, no host dependencies."""
import unittest
from datetime import datetime, timedelta, timezone

from nuke_time_calc.core import Collector


class Clock:
    def __init__(self):
        self.now = 0.0
        self.wall = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def monotonic(self):
        return self.now

    def utc(self):
        return self.wall + timedelta(seconds=self.now)

    def advance(self, seconds):
        self.now += seconds


class Schema2Tests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.records = []

    def make(self, **kwargs):
        options = dict(schema_version=2, identity="local-binding", context={
            "job_id": 7, "shot_id": 11, "script_path": "/show/comp_v001.nk"},
            emit=self.records.append, monotonic=self.clock.monotonic,
            utc_now=self.clock.utc)
        options.update(kwargs)
        return Collector(**options)

    def test_schema2_context_requires_real_integer_ids_and_valid_path(self):
        for context in ({}, None, {"job_id": "7", "shot_id": 11},
                        {"job_id": True, "shot_id": 11}, {"job_id": 7, "shot_id": 0},
                        {"job_id": 7, "shot_id": 1.0}, {"job_id": 2**63, "shot_id": 11},
                        {"job_id": 7, "shot_id": 11, "script_path": ""},
                        {"job_id": 7, "shot_id": 11, "script_path": "bad\u0000path"}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.make(context=context)
        with self.assertRaises(ValueError):
            self.make(schema_version=3)
        c = self.make(context={"job_id": 7, "shot_id": 11})
        self.assertEqual(c.context["script_path"], "unknown")

    def test_spans_coalesce_using_rounded_endpoints_without_idle_focus_or_render(self):
        c = self.make()
        c.start()
        c.activity()
        for _ in range(3):
            self.clock.advance(0.0000006)
            c.tick()
        c.set_focus(False)
        self.clock.advance(2)
        c.set_focus(True)
        self.clock.advance(1)
        c.activity()
        self.clock.advance(3)
        c.set_rendering(True)
        self.clock.advance(5)
        c.set_rendering(False)
        self.clock.advance(1)
        c.activity()
        self.clock.advance(25)
        r = c.flush()
        self.assertEqual(len(r["active_spans"]), 3)
        self.assertEqual([s["duration_microseconds"] for s in r["active_spans"]],
                         [2, 3_000_000, 20_000_000])
        for span in r["active_spans"]:
            delta = datetime.fromisoformat(span["end"]) - datetime.fromisoformat(span["start"])
            self.assertEqual(delta, timedelta(microseconds=span["duration_microseconds"]))
        self.assertEqual(r["comp_microseconds"], 23_000_002)

    def test_clock_corrections_end_epoch_exclude_gap_and_require_fresh_activity(self):
        for jump in (-3600, 3600):
            with self.subTest(jump=jump):
                self.setUp()
                c = self.make()
                c.start()
                c.activity()
                self.clock.advance(3)
                c.tick()
                old_session = c.session_id
                self.clock.advance(2)
                self.clock.wall += timedelta(seconds=jump)
                c.tick()
                self.assertNotEqual(c.session_id, old_session)
                self.assertEqual(sum(r["comp_microseconds"] for r in self.records), 3_000_000)
                self.assertGreaterEqual(sum(r["excluded_gap_microseconds"] for r in self.records), 2_000_000)
                self.clock.advance(4)
                self.assertEqual(c.flush()["comp_microseconds"], 0)
                c.activity()
                self.clock.advance(2)
                self.assertEqual(c.flush()["comp_microseconds"], 2_000_000)

    def test_small_wall_jitter_does_not_move_monotonic_span_endpoints(self):
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(2)
        self.clock.wall += timedelta(microseconds=100)
        r = c.flush()
        self.assertEqual(r["ended_at"], "2026-01-01T00:00:02+00:00")

    def test_context_switch_flushes_old_ids_and_version_up_requires_new_activity(self):
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(3)
        session = c.session_id
        with self.assertRaises(ValueError):
            c.switch_context({"job_id": 7, "shot_id": "bad"})
        context = {"job_id": 8, "shot_id": 12, "script_path": "/show/comp_v002.nk"}
        c.switch_context(context)
        context["shot_id"] = 99
        self.clock.advance(2)
        c.flush()
        self.assertEqual([r["shot_id"] for r in self.records], [11, 12])
        self.assertEqual([r["comp_microseconds"] for r in self.records], [3_000_000, 0])
        self.assertEqual(c.session_id, session)
        self.assertEqual(c.context["shot_id"], 12)
        c.activity()
        self.clock.advance(2)
        c.stop()
        c.stop()
        self.clock.advance(4)
        c.start()
        c.start()
        self.clock.advance(2)
        self.assertEqual(c.flush()["comp_microseconds"], 0)

    def test_span_limit_seals_before_257th_disjoint_span_without_losing_time(self):
        c = self.make()
        c.start()
        for _ in range(257):
            c.activity()
            self.clock.advance(1)
            c.set_focus(False)
            self.clock.advance(1)
            c.set_focus(True)
        c.flush()
        self.assertEqual([len(r["active_spans"]) for r in self.records], [256, 1])
        self.assertEqual(sum(r["comp_microseconds"] for r in self.records), 257_000_000)
        self.assertEqual([r["sequence"] for r in self.records], [1, 2])

    def test_window_limit_splits_continuous_activity_at_exact_day_boundary(self):
        c = self.make()
        c.start()
        for _ in range(8641):
            c.activity()
            self.clock.advance(10)
            c.tick()
        c.flush()
        self.assertEqual([r["comp_microseconds"] for r in self.records],
                         [86_400_000_000, 10_000_000])
        self.assertEqual(self.records[0]["ended_at"], self.records[1]["started_at"])

    def test_multi_day_suspend_is_excluded_in_bounded_windows(self):
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(1)
        self.clock.wall += timedelta(days=2, seconds=3)
        c.tick()
        self.assertEqual(sum(r["comp_microseconds"] for r in self.records), 0)
        self.assertEqual(sum(r["excluded_gap_microseconds"] for r in self.records),
                         172_804_000_000)
        for r in self.records:
            window = datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])
            self.assertLessEqual(window, timedelta(days=1))
            self.assertLessEqual(timedelta(microseconds=r["excluded_gap_microseconds"]), window)

    def test_full_queue_retains_one_bounded_interval_and_retries_immutable_batches(self):
        failing = [True]
        attempts = []
        def sink(record):
            attempts.append(record)
            if failing[0]:
                record["active_spans"].clear()
                raise OSError("offline")
            self.records.append(record)
        c = self.make(emit=sink)
        c.start()
        for _ in range(128):
            c.flush()
        c.activity()
        self.clock.advance(3)
        with self.assertRaises(BufferError):
            c.switch_context({"job_id": 7, "shot_id": 12})
        self.assertEqual(c.pending_count, 128)
        self.assertEqual(c.context["shot_id"], 11)
        self.assertTrue(c.blocked)
        self.clock.advance(200_000)
        c.tick()
        failing[0] = False
        r = c.flush("recovery")
        self.assertEqual(r["comp_microseconds"], 3_000_000)
        self.assertEqual(r["active_spans"][0]["duration_microseconds"], 3_000_000)
        self.assertEqual(datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"]), timedelta(seconds=3))
        self.assertEqual(c.pending_count, 0)
        self.assertFalse(c.blocked)
        r["active_spans"].clear()
        self.assertEqual(self.records[-1]["comp_microseconds"], 3_000_000)
        self.clock.advance(2)
        self.assertEqual(c.flush()["comp_microseconds"], 0)

    def test_invalid_wire_labels_fail_before_settling_or_emitting(self):
        from unittest.mock import patch
        with patch("socket.gethostname", return_value="x" * 256):
            with self.assertRaises(ValueError):
                self.make()
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(3)
        for reason in ("", "x" * 65, "bad\u0000reason", "bad\ud800", 12):
            with self.subTest(reason=repr(reason)), self.assertRaises(ValueError):
                c.flush(reason)
        self.assertEqual(self.records, [])
        self.assertEqual(c.flush()["comp_microseconds"], 3_000_000)

    def test_stopped_flush_does_not_extend_a_sealed_window_over_days(self):
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(3)
        c.stop()
        end = self.records[-1]["ended_at"]
        self.clock.advance(300_000)
        c.tick()
        r = c.flush()
        self.assertEqual(r["started_at"], end)
        self.assertEqual(r["ended_at"], end)
        self.assertEqual(r["comp_microseconds"], 0)
        c.start()
        c.activity()
        self.clock.advance(2)
        self.assertEqual(c.flush()["comp_microseconds"], 2_000_000)

    def test_schema2_clocks_require_finite_monotonic_and_aware_datetimes(self):
        for invalid in (float("nan"), float("inf"), True, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.make(monotonic=lambda: invalid)
        for invalid in (datetime(2026, 1, 1), None, "2026-01-01"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.make(utc_now=lambda: invalid)
        self.clock.wall = self.clock.wall.astimezone(timezone(timedelta(hours=2)))
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(1)
        self.assertEqual(c.flush()["started_at"], "2026-01-01T00:00:00+00:00")

    def test_restart_after_clock_change_rotates_session_without_relabelling_history(self):
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(2)
        c.stop()
        first = self.records[0].copy()
        self.clock.wall -= timedelta(hours=1)
        c.start()
        self.assertNotEqual(c.session_id, first["session_id"])
        c.activity()
        self.clock.advance(1)
        r = c.flush()
        self.assertEqual(r["sequence"], 1)
        self.assertEqual(r["comp_microseconds"], 1_000_000)
        self.assertEqual(self.records[0], first)

    def test_sequence_rollover_starts_new_session_with_valid_signed_integer(self):
        c = self.make()
        c.start()
        previous = c.session_id
        c.sequence = 2**63 - 1
        r = c.flush()
        self.assertEqual(r["sequence"], 1)
        self.assertNotEqual(r["session_id"], previous)

    def test_failed_nonempty_batches_retry_same_keys_content_and_order(self):
        from copy import deepcopy
        failing = [True]
        pristine = []
        def sink(record):
            pristine.append(deepcopy(record))
            if failing[0]:
                record["active_spans"][0]["duration_microseconds"] = 99
                record["job_id"] = 99
                raise OSError("offline")
            self.records.append(record)
        c = self.make(emit=sink)
        c.start()
        c.activity()
        self.clock.advance(3)
        first = c.flush()
        first["active_spans"].clear()
        self.clock.advance(2)
        c.flush()
        self.assertEqual(c.pending_count, 2)
        self.assertEqual(c.last_error, "OSError")
        failing[0] = False
        c.retry_pending()
        self.assertEqual(pristine[0], pristine[1])
        self.assertEqual(pristine[0], pristine[2])
        self.assertEqual([r["sequence"] for r in self.records], [1, 2])
        self.assertEqual([r["comp_microseconds"] for r in self.records], [3_000_000, 2_000_000])
        self.assertEqual(c.pending_count, 0)
        self.assertIsNone(c.last_error)

    def test_missing_observations_and_backwards_monotonic_require_fresh_activity(self):
        for delta in (61, -1):
            with self.subTest(delta=delta):
                self.setUp()
                c = self.make()
                c.start()
                c.activity()
                self.clock.advance(3)
                c.tick()
                session = c.session_id
                self.clock.advance(delta)
                c.tick()
                self.assertNotEqual(c.session_id, session)
                self.clock.advance(2)
                r = c.flush()
                self.assertEqual(r["comp_microseconds"], 0)
                self.assertEqual(sum(r["comp_microseconds"] for r in self.records), 3_000_000)

    def test_seeded_mixed_lifecycle_records_preserve_all_span_invariants(self):
        import random
        rng = random.Random(832917)
        self.clock.wall = self.clock.wall.replace(year=2000)
        c = self.make()
        c.start()
        for _ in range(2000):
            self.clock.advance(rng.choice([0.0000006, 0.0123456, 0.3, 1, 21, 61, 172801]))
            action = rng.randrange(9)
            if action == 0:
                c.activity()
            elif action == 1:
                c.set_focus(rng.choice([True, False]))
            elif action == 2:
                c.set_rendering(rng.choice([True, False]))
            elif action == 3:
                c.flush()
            elif action == 4:
                c.switch_context({"job_id": 7, "shot_id": rng.randint(1, 5)})
            elif action == 5:
                self.clock.wall += timedelta(seconds=rng.choice([-3600, 3600, 0.0001]))
                c.tick()
            elif action == 6:
                c.stop()
                c.start()
            elif action == 7:
                self.clock.now -= 1
                c.tick()
            else:
                c.tick()
        c.stop()
        self.assertGreater(len(self.records), 100)
        keys = set()
        for record in self.records:
            key = (record["session_id"], record["sequence"])
            self.assertNotIn(key, keys)
            keys.add(key)
            start = datetime.fromisoformat(record["started_at"])
            end = datetime.fromisoformat(record["ended_at"])
            self.assertLessEqual(start, end)
            self.assertLessEqual(end - start, timedelta(days=1))
            self.assertLessEqual(len(record["active_spans"]), 256)
            previous, total = start, 0
            for span in record["active_spans"]:
                left, right = map(datetime.fromisoformat, (span["start"], span["end"]))
                self.assertLessEqual(previous, left)
                self.assertLess(left, right)
                self.assertLessEqual(right, end)
                self.assertEqual(right - left, timedelta(microseconds=span["duration_microseconds"]))
                previous = right
                total += span["duration_microseconds"]
            self.assertEqual(total, record["comp_microseconds"])
            self.assertLessEqual(timedelta(microseconds=total + record["excluded_gap_microseconds"]), end - start)

    def test_emits_exact_backend_batch_not_envelope(self):
        c = self.make()
        c.start()
        c.activity()
        self.clock.advance(4)
        record = c.flush("script_save")
        self.assertEqual(set(record), {
            "session_id", "sequence", "machine", "collector_version", "policy_version",
            "job_id", "shot_id", "script_path", "reason", "started_at", "ended_at",
            "comp_microseconds", "excluded_gap_microseconds", "active_spans"})
        self.assertEqual(record["collector_version"], "2.0")
        self.assertEqual(record["policy_version"], "comp-v1")
        self.assertEqual((record["job_id"], record["shot_id"]), (7, 11))
        self.assertEqual(record["comp_microseconds"], 4_000_000)
        self.assertEqual(record["excluded_gap_microseconds"], 0)
        self.assertEqual(record["active_spans"], [{
            "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:00:04+00:00",
            "duration_microseconds": 4_000_000}])
        self.assertEqual(self.records, [record])


if __name__ == "__main__":
    unittest.main()
