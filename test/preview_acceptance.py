"""Opt-in native Make All Previews acceptance; cache outputs and mocked API only."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

import app_cache
import nuke_headless_tasks as tasks_module
import page_nukedash as dash
from test.preview_benchmark import compare_previews


@app_cache.temporary_cache_task
def acceptance(executable, movie, sequence, reference=None):
    app = QApplication.instance() or QApplication([])
    directory = Path(tempfile.mkdtemp(prefix="preview-acceptance-", dir=app_cache.cache_directory("temp")))
    print("PREVIEW_ACCEPTANCE " + str(directory), flush=True)
    requests = [
        {"input_path": str(movie), "colourspace": "ARRI LogC3 (EI800)", "fps": 25, "version": 1, "plate_name": "plate_01"},
        {"input_path": str(sequence), "colourspace": "ACEScg", "fps": 24, "version": 3, "plate_name": None},
        {"input_path": str(movie), "colourspace": "ARRI LogC3 (EI800)", "fps": 30, "version": 1, "plate_name": "plate_02"},
    ]
    settings = {"preview_quality": "medium", "preview_overwrite": False, "nuke_exe_path": executable}
    results = []

    for scenario in ("success", "failure-recovery", "cancel"):
        root = directory / scenario
        root.mkdir()
        selected = requests if scenario == "success" else requests[:2]
        if scenario == "failure-recovery":
            bad = root / "corrupt.mov"
            bad.write_bytes(b"invalid movie for failure isolation acceptance")
            selected = [{**requests[0], "input_path": str(bad)}, requests[1]]
        generated, workers, processes, working_dirs, summaries, progress_values = [], [], [], [], [], []

        class RecordingGenerator(tasks_module.PreviewGenerator):
            def generate_preview(self, **arguments):
                result = super().generate_preview(**arguments)
                generated.append(result)
                return result

            def end_preview_batch(self):
                worker = self._preview_worker
                workers.append(worker)
                processes.append(worker.process)
                if worker._temporary is not None:
                    working_dirs.append(Path(worker._temporary.name))
                super().end_preview_batch()

        class Progress(QProgressDialog):
            def setValue(self, value):
                progress_values.append(value)
                super().setValue(value)
                if scenario == "cancel" and 0 < value < self.maximum():
                    self.cancel()

        class Harness(QWidget):
            _on_make_all_previews_clicked = dash.page_nukedash._on_make_all_previews_clicked

            def __init__(self):
                super().__init__()
                self._active_job_id = 1
                self._jobs_by_id = {1: {"id": 1, "title": "Preview benchmark"}}
                self.paused = set()
                self.refreshes = 0
                self.ticks = 0

            def _collect_preview_tasks(self, *args, **kwargs):
                return [{"shot": {"id": index + 1, "title": "sho010", "base_path": str(root / "shot outputs"),
                                   "colourspace": request["colourspace"], "fps": request["fps"]},
                         "source_path": request["input_path"], "source_type": "original_clip" if request["plate_name"] else "render",
                         "version": request["version"], "plate_name": request["plate_name"],
                         "job_name": "Preview benchmark"} for index, request in enumerate(selected)]

            def set_auto_refresh_paused(self, paused, *, reason):
                self.paused.add(reason) if paused else self.paused.discard(reason)

            def _request_api_refresh(self):
                self.refreshes += 1

            def tick(self):
                self.ticks += 1

        harness = Harness()
        api = mock.Mock()
        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(harness.tick)
        timer.start()
        started = time.monotonic()
        with mock.patch.object(dash, "get_settings_manager", return_value=settings), \
                mock.patch.object(dash.http_help, "DjangoAPI", return_value=api), \
                mock.patch.object(tasks_module, "PreviewGenerator", RecordingGenerator), \
                mock.patch("PyQt6.QtWidgets.QProgressDialog", Progress), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, "information", side_effect=lambda parent, title, message: summaries.append(message)), \
                mock.patch.object(QMessageBox, "warning", side_effect=AssertionError("Unexpected warning")):
            harness._on_make_all_previews_clicked()
        elapsed = time.monotonic() - started
        timer.stop()
        assert len(workers) == 1 and workers[0].launches == 1
        assert processes[0].poll() is not None
        assert not any(path.exists() for path in working_dirs)
        assert not harness.paused and harness.refreshes == 1 and harness.ticks > 5
        assert progress_values and summaries
        if scenario == "success":
            assert len(generated) == 3 and all(result.success for result in generated)
            assert api.update_shot.call_count == 3
            assert "Completed: 3\nFailed: 0\nSkipped: 0" in summaries[0]
        elif scenario == "failure-recovery":
            assert len(generated) == 2 and not generated[0].success and generated[1].success
            assert api.update_shot.call_count == 1
            assert "Completed: 1\nFailed: 1\nSkipped: 0" in summaries[0]
        else:
            assert len(generated) == 1 and not generated[0].success
            assert "cancelled" in generated[0].error.lower()
            assert not api.update_shot.called and summaries[0].startswith("Cancelled.")
        for update, result in zip(api.update_shot.call_args_list, (result for result in generated if result.success)):
            assert update.kwargs == {"preview_video": result.relative_path}
        report = {"scenario": scenario, "elapsed_seconds": round(elapsed, 3), "nuke_launches": workers[0].launches,
                  "qt_timer_ticks": harness.ticks, "summary": summaries[0], "success": True,
                  "tasks": [{"task": request, "success": result.success, "error": result.error,
                             "output": str(result.output_path) if result.output_path else None}
                            for request, result in zip(selected, generated)]}
        (root / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        results.append(report)
        print(json.dumps(report), flush=True)
        harness.close()
        app.processEvents()
    if reference:
        comparison = compare_previews(reference, directory / "success")
        assert all(item["identical_decoded_pixels"] for item in comparison["previews"])
    report = {"measured_at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
              "scenarios": results, "success": True}
    (directory / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", required=True)
    parser.add_argument("--movie", type=Path, required=True)
    parser.add_argument("--sequence", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    arguments = parser.parse_args()
    acceptance(arguments.nuke, arguments.movie, arguments.sequence, arguments.reference)
