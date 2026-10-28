"""Exercise the actual Qt batch controller on exactly four 000test clips."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtCore import QTimer
import app_cache
import denoise_controller as ui


@app_cache.temporary_cache_task
def benchmark(source_root, executable):
    sources = sorted(source_root.glob("VFX/*/*/plates/*.mxf"))[:4]
    assert len(sources) == 4
    directory = Path(tempfile.mkdtemp(prefix="denoise-four-controller-", dir=app_cache.cache_directory("temp")))
    print(directory, flush=True)
    tasks = []
    for index, source in enumerate(sources, 1):
        metadata = json.loads((source.parent.parent / "shot_assets/timeline_import.json").read_text())
        plate = next(p for p in metadata["plates"] if Path(p.get("copied_path", "")).name == source.name)
        tasks.append({"shot": {"title": source.parent.parent.name}, "media": {
            "source": str(source), "shot_dir": str(directory / str(index) / "shot"),
            "shot_name": source.parent.parent.name, "colourspace": plate["input_colourspace"], "fps": metadata["fps"]}})
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    controller = ui.DenoiseController(window)
    report = {"sources": [str(s) for s in sources], "workers": ui.batch_worker_limit(), "clips": [None] * 4}
    original_finish = ui.DenoiseController._finish

    def finish(worker, result):
        source = json.loads(worker.request_path.read_text())["source"]
        index = report["sources"].index(source)
        report["clips"][index] = {"result": result, "directory": str(worker.directory)}
        print(index + 1, result["stage"], flush=True)
        original_finish(worker, result)

    controller.idle.connect(app.quit)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(controller.cancel)
    timeout.start(1200000)
    started = time.monotonic()
    with mock.patch.object(ui, "find_nuke_executable", return_value=executable), \
            mock.patch.object(ui.DenoiseController, "_finish", finish), \
            mock.patch.object(ui.DenoiseController, "_debug"), \
            mock.patch.object(ui.QMessageBox, "information"):
        controller.start_batch(tasks)
        app.exec()
    report.update(seconds=time.monotonic() - started, result=controller.last_batch_result)
    (directory / "benchmark.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"seconds": report["seconds"], "result": report["result"]}), flush=True)
    assert report["result"]["completed"] == 4 and not report["result"]["failed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--nuke", required=True)
    args = parser.parse_args()
    benchmark(args.source_root, args.nuke)
