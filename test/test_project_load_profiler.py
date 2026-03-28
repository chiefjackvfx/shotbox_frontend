from __future__ import annotations

from pathlib import Path
import sys
import unittest

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

import project_load_profiler


class PerfCounterStub:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class WallClockStub:
    def __init__(self, value: float):
        self._value = float(value)

    def __call__(self):
        return self._value


class ProjectLoadProfilerTests(unittest.TestCase):
    def test_report_includes_sorted_work_categories_and_zero_categories(self):
        lines = []
        profiler = project_load_profiler.ProjectLoadProfiler(
            enabled=True,
            perf_counter=PerfCounterStub([1.0, 2.0, 2.0]),
            wall_time=WallClockStub(1_700_000_000.0),
            printer=lines.append,
        )

        profiler.start_session(7, "Job Seven")
        profiler.record_wall_duration("job_lookup", 0.250)
        profiler.record_wall_duration("timeline_build", 0.750)
        profiler.record_work_duration("shot_card_create", 1.500, count=3)
        profiler.record_work_duration("disk_latest_nk", 2.250, count=4)
        profiler.mark_success()
        emitted = profiler.emit_report_if_ready(force=True)

        self.assertTrue(emitted)
        work_lines = [line for line in lines if "count=" in line]
        self.assertTrue(work_lines[0].endswith("avg=0.562s"))
        self.assertIn("disk_latest_nk", work_lines[0])
        self.assertIn("shot_card_create", work_lines[1])
        self.assertTrue(
            any("disk_latest_preview: count=0 total=0.000s avg=0.000s" in line for line in work_lines)
        )

    def test_thumbnail_report_shows_requested_count_when_async_work_is_incomplete(self):
        lines = []
        profiler = project_load_profiler.ProjectLoadProfiler(
            enabled=True,
            perf_counter=PerfCounterStub([10.0, 10.5, 11.0, 11.0]),
            wall_time=WallClockStub(1_700_000_100.0),
            printer=lines.append,
        )

        profiler.start_session(4, "Job Four")
        profiler.start_async_work("thumbnail_load")
        profiler.mark_success()
        emitted = profiler.emit_report_if_ready(force=True)

        self.assertTrue(emitted)
        thumbnail_lines = [line for line in lines if "thumbnail_load:" in line]
        self.assertEqual(len(thumbnail_lines), 1)
        self.assertIn("count=0", thumbnail_lines[0])
        self.assertIn("requested=1", thumbnail_lines[0])

    def test_failed_session_report_includes_failure_reason(self):
        lines = []
        profiler = project_load_profiler.ProjectLoadProfiler(
            enabled=True,
            perf_counter=PerfCounterStub([5.0, 6.0, 6.0]),
            wall_time=WallClockStub(1_700_000_200.0),
            printer=lines.append,
        )

        profiler.start_session(11, "Broken Job")
        profiler.mark_failed("boom")
        emitted = profiler.emit_report_if_ready(force=True)

        self.assertTrue(emitted)
        self.assertIn("status=failed", lines[0])
        self.assertIn("reason=boom", lines[0])


if __name__ == "__main__":
    unittest.main()
