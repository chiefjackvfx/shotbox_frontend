"""Opt-in native denoise queue acceptance, using generated cache-only clips."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache

FRONTEND = Path(__file__).resolve().parents[1]


@app_cache.temporary_cache_task
def acceptance(executable, *, expected_version=None, include_failure=False):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    import OpenEXR
    from PIL import Image
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMainWindow
    import denoise_controller as ui
    from nuke_denoise import script_digest
    from test.denoise_acceptance import host_command

    with app_cache.temporary_cache_lease():
        root = Path(tempfile.mkdtemp(prefix="denoise-batch-acceptance-", dir=app_cache.cache_directory("temp")))
        print("DENOISE_BATCH_ACCEPTANCE " + str(root), flush=True)
        environment = dict(os.environ)
        for name in ("NUKE_TEMP_DIR", "NUKE_DISK_CACHE", "TMPDIR", "XDG_CACHE_HOME"):
            target = root / name.lower()
            target.mkdir()
            environment[name] = str(target)
        environment["TEMP"] = environment["TMP"] = environment["TMPDIR"]
        tasks, digests, directories = [], [], []
        for index in range(3 if include_failure else 2):
            directory = root / f"clip-{index + 1}"
            plates = directory / "shot with spaces/plates"
            plates.mkdir(parents=True)
            for frame in range(1001, 1004):
                pixels = np.clip(100 + np.random.default_rng(frame + 100).normal(0, 9, (512, 512, 3)), 0, 255).astype("uint8")
                Image.fromarray(pixels).save(plates / f"plate.{frame}.png")
            host_command(executable, "fixture", directory, environment, expected_version=expected_version)
            data = json.loads((directory / "fixture.json").read_text())
            if include_failure and index == 1:
                # A real failing middle clip must not prevent the final clip.
                script = Path(data["script"])
                text = script.read_text(encoding="utf-8")
                assert text.count("name WriteDN1") == 1
                script.write_text(text.replace("name WriteDN1", "name MissingDenoiseWrite"), encoding="utf-8")
            tasks.append({"shot": {"id": index + 1, "title": f"Clip {index + 1}"}, "script": data["script"]})
            digests.append(script_digest(data["script"]))
            directories.append(directory)

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = QMainWindow()
        controller = ui.DenoiseController(window)
        busy, summaries, jobs = [], [], []
        controller.busy_changed.connect(busy.append)
        original_finish = controller._finish

        def finish(result):
            jobs.append({"result": result, "directory": str(controller.directory)})
            original_finish(result)

        controller._finish = finish
        timer = QTimer()
        ticks = 0
        started = time.monotonic()

        def poll():
            nonlocal ticks
            ticks += 1
            if not controller.busy:
                app.quit()
            elif time.monotonic() - started > 180:
                controller.cancel()

        timer.timeout.connect(poll)
        timer.start(25)
        try:
            with mock.patch.object(ui, "find_nuke_executable", return_value=executable), \
                    mock.patch.object(ui.QMessageBox, "information", side_effect=lambda parent, title, message: summaries.append(message)):
                assert controller.start_batch(tasks)
                app.exec()
            result = controller.last_batch_result
            expected_failures = int(include_failure)
            assert {key: value for key, value in result.items() if key != "errors"} == {
                "completed": 2, "failed": expected_failures, "skipped": 0, "cancelled": False, "remaining": 0}, result
            assert len(result["errors"]) == expected_failures, result
            assert busy == [True, False] and len(jobs) == len(tasks) and len(summaries) == 1
            assert not controller.busy and controller.process is None and controller._lease is None
            assert ticks > 10
            for index, (task, digest, directory) in enumerate(zip(tasks, digests, directories)):
                if include_failure and index == 1:
                    assert script_digest(task["script"]) == digest
                    assert jobs[index]["result"]["stage"] == "failed"
                    assert not list((directory / "shot with spaces/renders").glob("*.exr"))
                    continue
                assert script_digest(task["script"]) != digest
                outputs = sorted((directory / "shot with spaces/renders").glob("denoise.*.exr"))
                assert len(outputs) == 3
                for output in outputs:
                    with OpenEXR.File(str(output)) as image:
                        assert all(channel.pixels.shape[:2] == (512, 512) and np.isfinite(channel.pixels).all()
                                   for channel in image.channels().values())
                assert not list((directory / "shot with spaces/renders").glob("unwanted.*"))
            report = {**result, "jobs": jobs, "qt_timer_ticks": ticks, "elapsed_seconds": round(time.monotonic() - started, 3),
                      "frames_verified": 6, "continued_after_failure": include_failure, "summary": summaries[0]}
            (root / "acceptance.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2), flush=True)
            return report
        finally:
            timer.stop()
            window.deleteLater()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", required=True)
    parser.add_argument("--include-failure", action="store_true", help="Insert a failing clip between two successful clips")
    args = parser.parse_args()
    acceptance(args.nuke, include_failure=args.include_failure)
