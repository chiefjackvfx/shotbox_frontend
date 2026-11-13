"""Pure-core tests: deterministic clocks, no Nuke or Qt required."""
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone


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


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.records = []

    def make_collector(self, **kwargs):
        from nuke_time_calc.core import Collector
        options = dict(identity="artist-test", context={"project_id": "p1", "shot_id": "s1"},
                       emit=self.records.append, monotonic=self.clock.monotonic,
                       utc_now=self.clock.utc)
        options.update(kwargs)
        return Collector(**options)

    def test_basic_activity_emits_json_interval(self):
        self.assertIsNotNone(importlib.util.find_spec("nuke_time_calc.core"),
                             "The collector core has not been implemented")
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(4)
        c.flush("checkpoint")
        self.assertEqual(self.records[0]["comp_seconds"], 4)
        self.assertEqual(self.records[0]["context"]["shot_id"], "s1")
        self.assertEqual(self.records[0]["identity"], "artist-test")
        self.assertEqual(self.records[0]["started_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(self.records[0]["ended_at"], "2026-01-01T00:00:04+00:00")
        json.dumps(self.records, allow_nan=False)

    def test_machine_is_captured_once_and_preserved_on_retry(self):
        from unittest.mock import patch
        attempts = []

        def sink(record):
            attempts.append(record)
            if len(attempts) == 1:
                raise OSError('temporary sink failure')

        with patch('socket.gethostname', return_value='workstation-test') as hostname:
            c = self.make_collector(emit=sink)
            c.start()
            c.activity()
            self.clock.advance(2)
            c.flush()
            self.assertEqual(attempts[0].get('machine'), 'workstation-test')
            hostname.return_value = 'renamed-after-session-start'
            c.retry_pending()
            c.flush()
            self.assertEqual([r['machine'] for r in attempts], ['workstation-test'] * 3)
            hostname.assert_called_once_with()
            self.assertEqual(attempts[0], attempts[1])

    def test_idle_overlap_is_clamped_before_new_activity(self):
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(17)
        c.flush()
        self.clock.advance(8)
        c.activity()  # settle only [17, 20], not the whole eight seconds
        self.clock.advance(2)
        c.flush()
        self.assertEqual([r["comp_seconds"] for r in self.records], [17, 5])


    def test_focus_transition_settles_old_state_and_requires_fresh_activity(self):
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(5)
        self.assertTrue(hasattr(c, "set_focus"), "Focus transition is missing")
        c.set_focus(False)
        self.clock.advance(6)
        c.activity()  # background callback is not user work
        c.set_focus(True)
        self.clock.advance(3)
        c.activity()
        self.clock.advance(2)
        c.flush()
        self.assertEqual(self.records[0]["comp_seconds"], 7)


    def test_render_boundaries_do_not_count_render_or_resume_automatically(self):
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(3)
        self.assertTrue(hasattr(c, "set_rendering"), "Render transition is missing")
        c.set_rendering(True)
        self.clock.advance(12)  # Qt timer need not fire during a render
        c.activity()  # programmatic render knob changes are not work
        c.set_rendering(False)
        self.clock.advance(2)
        c.activity()
        self.clock.advance(4)
        c.flush()
        self.assertEqual(self.records[0]["comp_seconds"], 7)


    def test_long_gap_is_excluded_instead_of_counting_idle_allowance(self):
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(5)
        c.flush()
        self.clock.advance(120)
        c.flush()
        self.assertEqual([r["comp_seconds"] for r in self.records], [5, 0])
        self.assertEqual(self.records[1]["excluded_gap_seconds"], 120)
        c.activity()
        self.clock.advance(2)
        c.flush()
        self.assertEqual(self.records[-1]["comp_seconds"], 2)


    def test_suspend_with_paused_monotonic_clock_is_excluded(self):
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(1)
        self.clock.wall += timedelta(hours=1)
        c.flush()
        self.assertEqual(self.records[0]["comp_seconds"], 0)
        self.assertEqual(self.records[0]["excluded_gap_seconds"], 3601)


    def test_lifecycle_is_explicit_idempotent_and_flushes_before_stop(self):
        c = self.make_collector()
        self.assertFalse(c.running)
        c.activity()
        self.clock.advance(5)
        c.start()
        c.activity()
        self.clock.advance(3)
        c.start()  # duplicate lifecycle callback must not reset a live interval
        self.clock.advance(2)
        c.flush()
        self.assertEqual(self.records[0]["comp_seconds"], 5)
        self.clock.advance(4)
        c.stop()
        self.assertEqual(self.records[-1]["comp_seconds"], 4)
        self.assertEqual(self.records[-1]["reason"], "stop")
        c.stop()
        self.assertEqual(len(self.records), 2)
        self.clock.advance(5)
        c.activity()
        c.start()
        self.clock.advance(2)
        c.stop()
        self.assertEqual(self.records[-1]["comp_seconds"], 0)
        self.assertEqual([r["sequence"] for r in self.records], [1, 2, 3])
        self.assertEqual(len({r["session_id"] for r in self.records}), 1)


    def test_context_switch_flushes_old_shot_without_activity_leak(self):
        c = self.make_collector()
        c.start()
        c.activity()
        self.clock.advance(3)
        context = {"project_id": "p1", "shot_id": "s2"}
        self.assertTrue(hasattr(c, "switch_context"), "Context switching is missing")
        c.switch_context(context)
        self.assertEqual(len(self.records), 1, "Old context must flush immediately")
        self.clock.advance(2)
        c.activity()
        self.clock.advance(4)
        c.flush()
        self.assertEqual([r["context"]["shot_id"] for r in self.records], ["s1", "s2"])
        self.assertEqual([r["comp_seconds"] for r in self.records], [3, 4])
        self.assertEqual(self.records[0]["reason"], "context_switch")
        self.assertEqual(self.records[0]["session_id"], self.records[1]["session_id"])
        self.assertEqual([r["sequence"] for r in self.records], [1, 2])


    def test_configuration_requires_explicit_identity_context_and_sink(self):
        for invalid in ("", None, 42):
            with self.subTest(identity=invalid), self.assertRaises(ValueError):
                self.make_collector(identity=invalid)
        for context in ({}, {"shot_id": "s"}, {"project_id": "p", "shot_id": ""},
                        {"project_id": "p", "shot_id": "s", "bad": object()}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.make_collector(context=context)
        with self.assertRaises(ValueError):
            self.make_collector(emit=None)


    def test_failed_sink_retains_immutable_ordered_records_for_retry(self):
        failing = [True]
        def sink(record):
            if failing[0]:
                record["context"]["shot_id"] = "sink-mutated"
                raise OSError("offline")
            self.records.append(record)
        c = self.make_collector(emit=sink)
        c.start()
        c.activity()
        self.clock.advance(3)
        try:
            c.flush()
        except OSError:
            self.fail("Sink failures must retain pending records, not escape")
        self.assertEqual(c.pending_count, 1)
        self.assertEqual(c.last_error, "OSError")
        self.clock.advance(2)
        c.flush()
        self.assertEqual(c.pending_count, 2)
        failing[0] = False
        c.retry_pending()
        self.assertEqual(c.pending_count, 0)
        self.assertIsNone(c.last_error)
        self.assertEqual([r["comp_seconds"] for r in self.records], [3, 2])
        self.assertEqual([r["sequence"] for r in self.records], [1, 2])
        self.assertEqual([r["context"]["shot_id"] for r in self.records], ["s1", "s1"])


    def test_full_queue_blocks_tracking_and_context_change_without_loss(self):
        failing = [True]
        def sink(record):
            if failing[0]:
                raise OSError("offline")
            self.records.append(record)
        c = self.make_collector(emit=sink)
        c.start()
        for _ in range(128):
            c.flush()
        c.activity()
        self.clock.advance(3)
        with self.assertRaises(BufferError):
            c.switch_context({"project_id": "p1", "shot_id": "s2"})
        self.assertEqual(c.pending_count, 128)
        self.assertTrue(c.blocked)
        self.assertEqual(c.context["shot_id"], "s1")
        self.clock.advance(8)
        c.activity()
        failing[0] = False
        c.switch_context({"project_id": "p1", "shot_id": "s2"})
        self.assertEqual(self.records[-1]["comp_seconds"], 3)
        self.assertEqual(self.records[-1]["context"]["shot_id"], "s1")
        self.assertEqual(c.pending_count, 0)
        self.assertFalse(c.blocked)
        self.assertEqual(c.context["shot_id"], "s2")


    def test_context_validation_and_copy_apply_on_switch(self):
        c = self.make_collector()
        c.start()
        with self.assertRaises(ValueError):
            c.switch_context({"shot_id": "missing-project"})
        self.assertEqual(self.records, [])
        context = {"project_id": "p", "shot_id": "s", "metadata": {"task": "comp"}}
        c.switch_context(context)
        context["metadata"]["task"] = "changed"
        c.flush()
        self.assertEqual(self.records[-1]["context"]["metadata"]["task"], "comp")


    def test_stop_with_full_queue_stops_and_preserves_unsealed_interval(self):
        def sink(record):
            raise OSError("offline")
        c = self.make_collector(emit=sink)
        c.start()
        for _ in range(128):
            c.flush()
        c.activity()
        self.clock.advance(2)
        with self.assertRaises(BufferError):
            c.stop()
        self.assertFalse(c.running)
        with self.assertRaises(BufferError):
            c.start()
        self.assertEqual(c.comp_seconds, 2)
        c.emit = self.records.append
        c.flush("recovery")
        self.assertEqual(self.records[-1]["comp_seconds"], 2)
        c.start()
        self.clock.advance(3)
        c.stop()
        self.assertEqual(self.records[-1]["comp_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
