"""Dashboard-owned, nonmodal background denoise UI, independent of edit locks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

from PyQt6.QtCore import QObject, QProcess, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QVBoxLayout

import app_cache
from nuke_denoise import create_request, parse_event
from nuke_denoise_display import discover_tools, stop_recorded_children
from nuke_headless_tasks import find_nuke_executable
from settings import get_settings_manager


def choose_output_mode(parent, count, skipped=0):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Denoise")
    layout = QVBoxLayout(dialog)
    label = QLabel(f"Denoise {count} source clip(s).\nMaximum width: 4096 pixels; smaller footage stays unchanged.")
    label.setWordWrap(True)
    layout.addWidget(label)
    overwrite = QRadioButton("Overwrite", dialog)
    overwrite.setToolTip("Replace the latest matching denoise version; create v001 if none exists.")
    overwrite.setChecked(True)
    new = QRadioButton("Version up", dialog)
    new.setToolTip("Create the next unused denoise version and keep existing versions.")
    layout.addWidget(overwrite)
    layout.addWidget(new)
    layout.addWidget(QLabel("Overwrite replaces the latest matching denoise version.\nVersion up creates the next unused version.", dialog))
    if skipped:
        layout.addWidget(QLabel(f"Skipped (missing source or shot folder): {skipped}", dialog))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Denoise")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return "overwrite" if overwrite.isChecked() else "new"
    finally:
        dialog.deleteLater()


class DenoiseProgress(QDialog):
    def __init__(self, controller, title):
        super().__init__(controller.window)
        self.controller = controller
        self.active = True
        self.setWindowTitle(f"Denoise — {title}")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.batch_status = QLabel(self)
        self.batch_status.setWordWrap(True)
        self.batch_status.setVisible(controller._batch is not None)
        if controller._batch is not None:
            batch = controller._batch
            self.batch_status.setText(
                f"Clip {batch['index']} of {len(batch['tasks'])} · "
                f"Completed: {batch['completed']} · Failed: {batch['failed']} · Skipped: {batch['skipped']}"
            )
        layout.addWidget(self.batch_status)
        self.label = QLabel("Starting hidden NukeX…", self)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.detail = QLabel(self)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.step = 1
        self.started = time.monotonic()
        self.timing = QLabel(self)
        layout.addWidget(self.timing)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 8)
        self.progress.setValue(1)
        self.progress.setFormat("Step 1 of 8")
        layout.addWidget(self.progress)
        self.activity = QPlainTextEdit(self)
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(250)
        self.activity.setFixedHeight(160)
        self.activity.setAccessibleName("Denoise activity")
        layout.addWidget(self.activity)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_timing)
        self.elapsed_timer.start()
        self._update_timing()
        self.record("Starting NukeX…")
        buttons = QHBoxLayout()
        logs = QPushButton("Open Logs", self)
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(controller.directory))))
        buttons.addWidget(logs)
        buttons.addStretch()
        self.button = QPushButton("Cancel", self)
        self.button.clicked.connect(self._clicked)
        buttons.addWidget(self.button)
        layout.addLayout(buttons)

    def record(self, message):
        self.activity.appendPlainText(f"{time.strftime('%H:%M:%S')}  {message}")

    def set_status(self, message, detail="", *, step=None, frames=None):
        self.label.setText(message)
        self.detail.setText(detail)
        if step is not None:
            self.step = step
        if frames is not None:
            completed, total = frames
            self.progress.setRange(0, total)
            self.progress.setValue(completed)
            self.progress.setFormat("%v / %m frames (%p%)")
        else:
            self.progress.setRange(0, 8)
            self.progress.setValue(self.step)
            self.progress.setFormat(f"Step {self.step} of 8")
        self._update_timing()
        self.record(message)

    def _update_timing(self):
        elapsed = int(time.monotonic() - self.started)
        self.timing.setText(f"Step {self.step} of 8 · Elapsed {elapsed // 60:02d}:{elapsed % 60:02d}")

    def _clicked(self):
        self.reject()

    def closeEvent(self, event):
        if self.active:
            self.controller.cancel()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if self.active:
            self.controller.cancel()
        else:
            super().reject()


class DenoiseController(QObject):
    busy_changed = pyqtSignal(bool)
    idle = pyqtSignal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.directory = None
        self.dialog = None
        self.process = None
        self._lease = None
        self._buffer = ""
        self._cancelled = False
        self._finishing = False
        self._pending_result = None
        self._closing = False
        self._batch = None
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._advance_batch)
        self.idle.connect(self._after_idle)

    def start(self, shot, media):
        if self.busy:
            self._debug("A denoise job is already running.")
            if self.dialog:
                self.dialog.show()
                self.dialog.raise_()
            return False
        return self._start_job(shot, media)

    def start_batch(self, tasks, *, skipped=0):
        if self.busy or not tasks:
            return False
        self._batch = {"tasks": list(tasks), "index": 0, "completed": 0, "failed": 0,
                       "skipped": skipped, "errors": []}
        self.busy = True
        self._cancelled = False
        self.busy_changed.emit(True)
        self._advance_batch()
        return True

    def _advance_batch(self):
        batch = self._batch
        if batch is None:
            return
        if self._cancelled or self._closing or batch["index"] == len(batch["tasks"]):
            self._finish_batch()
            return
        task = batch["tasks"][batch["index"]]
        batch["index"] += 1
        if not self._start_job(task["shot"], task["media"]):
            self._record_batch_result(self.last_result)
            self._batch_timer.start(0)

    def _record_batch_result(self, result):
        batch = self._batch
        if result.get("stage") == "complete":
            batch["completed"] += 1
        elif result.get("stage") != "cancelled":
            batch["failed"] += 1
            task = batch["tasks"][batch["index"] - 1]
            title = task["shot"].get("title") or Path(task["media"]["source"]).stem
            error = f"{title}: {result.get('error', 'Denoise failed')}"
            if self.directory is not None:
                error += f"\nLogs: {self.directory}"
            batch["errors"].append(error)

    def _finish_batch(self):
        batch, self._batch = self._batch, None
        self._batch_timer.stop()
        self.last_batch_result = {key: batch[key] for key in ("completed", "failed", "skipped", "errors")}
        self.last_batch_result.update(cancelled=self._cancelled or self._closing,
                                      remaining=len(batch["tasks"]) - batch["index"])
        result = self.last_batch_result
        summary = ("Cancelled." if result["cancelled"] else "Done!") + (
            f"\n\nCompleted: {result['completed']}\nFailed: {result['failed']}\n"
            f"Skipped: {result['skipped']}\nRemaining: {result['remaining']}"
        )
        if result["errors"]:
            summary += "\n\n" + "\n\n".join(result["errors"][:5])
        self.busy = False
        self.busy_changed.emit(False)
        self.idle.emit()
        if not self._closing:
            QMessageBox.information(self.window, "Denoise", summary)

    def _start_job(self, shot, media):
        self.directory = None
        source = media["source"]
        self._debug(f"Requested input: {source}")
        try:
            discover_tools()
            if sys.platform == "linux" and importlib.util.find_spec("Xlib") is None:
                raise RuntimeError("Python Xlib is missing. Run Shotbox's runtime setup to install its dependencies.")
            executable = find_nuke_executable([get_settings_manager().get("nuke_exe_path", "")])
            if not executable:
                raise RuntimeError("Nuke was not found. Configure its executable in Settings.")
            self._lease = app_cache.temporary_cache_lease()
            self._lease.__enter__()
            self.directory = Path(tempfile.mkdtemp(prefix="denoise-", dir=app_cache.cache_directory("temp")))
            self.request_path = create_request(self.directory, media, executable)
        except Exception as exc:
            self._debug(f"Cannot start: {exc}")
            if self._lease:
                self._lease.__exit__(None, None, None)
                self._lease = None
            self.last_result = {"stage": "failed", "error": str(exc)}
            if self._batch is None:
                QMessageBox.warning(self.window, "Denoise", str(exc))
            return False
        if self.dialog:
            self.dialog.deleteLater()
        was_busy = self.busy
        self.busy = True
        self.process = None
        self._buffer = ""
        self._cancelled = False
        self._finishing = False
        self._pending_result = None
        self._debug(f"Nuke executable: {executable} (--nukex)")
        self._debug(f"Job logs and worker files: {self.directory}")
        self._debug("Using a fresh denoise template.")
        self.dialog = DenoiseProgress(self, shot.get("title") or Path(source).stem)
        self.dialog.detail.setText(f"Input: {Path(source).name}")
        self.dialog.detail.setToolTip(source)
        self.dialog.show()
        if not was_busy:
            self.busy_changed.emit(True)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.process.start(sys.executable, [str(Path(__file__).with_name("nuke_denoise.py")),
                                           "--request", str(self.request_path)])
        return True

    def _read(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        with (self.directory / "supervisor.log").open("a", encoding="utf-8") as stream:
            stream.write(data)
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            event = parse_event(line)
            if event:
                self._show_event(event)
            elif line.strip():
                self._debug(f"Supervisor: {line}")

    def _debug(self, message):
        prefix = f"[Denoise {time.strftime('%H:%M:%S')}]"
        for line in str(message).splitlines():
            print(f"{prefix} {line}", flush=True)

    def _show_event(self, event):
        stage = event["stage"]
        if stage == "debug":
            self._debug(f"{event.get('source', 'Supervisor')}: {event.get('message', '')}")
            return
        details = ", ".join(f"{key}={value}" for key, value in event.items()
                            if key not in ("stage", "traceback"))
        self._debug(f"{stage}: {details}" if details else stage)
        if event.get("traceback"):
            self._debug(event["traceback"])
        if self._cancelled:
            return
        labels = {"starting": (1, "Starting NukeX and loading plugins…"),
                  "loading": (2, "Loading the denoise template and source…"),
                  "preparing": (3, "Preparing the first frame for profiling…"),
                  "profiling": (4, "Opening Prepare Noise Profile…"),
                  "waiting_profile": (4, "Waiting for Neat Video to finish loading input frames…"),
                  "auto_profiling": (4, "Auto Profile is analysing the noise…"),
                  "checking_profile": (4, "Profile Check is preparing its preview…"),
                  "applying_profile": (5, "Auto Profile finished. Applying the noise profile…"),
                  "profile_ready": (6, "Noise profile cached. Starting denoised output…")}
        if stage == "rendering":
            detail = str(event.get("output", ""))
            if "frame" in event:
                detail = f"Rendered frame {int(event['frame'])}\n{detail}"
            self.dialog.set_status(f"Rendering denoised output: {event['completed']}/{event['total']} frames",
                                   detail, step=7, frames=(event["completed"], event["total"]))
        elif stage in labels:
            step, message = labels[stage]
            detail = ""
            if stage == "loading":
                detail = str(event.get("script", ""))
            elif "first" in event and "last" in event:
                frame = event.get("frame", (event["first"] + event["last"]) // 2)
                detail = f"Frame {frame} · Script range {event['first']}–{event['last']}"
                if "width" in event:
                    detail += f" · {event['width']} × {event['height']}"
            self.dialog.set_status(message, detail, step=step)

    def cancel(self):
        if not self.busy or self._cancelled:
            return
        self._cancelled = True
        self._debug("Cancellation requested; closing background Nuke and its private display.")
        if self.process is not None:
            (self.directory / "cancel").touch()
        if self.dialog:
            self.dialog.set_status("Cancelling and closing background Nuke…")
            self.dialog.button.setEnabled(False)

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(-1, QProcess.ExitStatus.CrashExit)

    def _finished(self, exit_code, exit_status):
        if not self.busy or self._finishing:
            return
        self._finishing = True
        self._read()
        if self._buffer.strip():
            self._debug(f"Supervisor: {self._buffer}")
        self._buffer = ""
        self._debug(f"Supervisor exited: code={exit_code}, status={exit_status.name}. Cleaning up children.")
        result = {"stage": "failed", "error": "The denoise supervisor stopped unexpectedly. See supervisor.log."}
        result_path = self.directory / "result.json"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        if result.get("stage") == "complete" and (exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit):
            result = {"stage": "failed", "error": "The denoise supervisor crashed after rendering."}
        self._pending_result = result
        self.dialog.set_status("Closing background Nuke and finishing cleanup…", step=8)
        self._cleanup_started = time.monotonic()
        self._cleanup_children()

    def _cleanup_children(self):
        # Finish outstanding writes before releasing the cache lease or closing.
        if stop_recorded_children(self.directory, force=time.monotonic() - self._cleanup_started > 5):
            QTimer.singleShot(200, self._cleanup_children)
            return
        self._finish(self._pending_result)

    def _finish(self, result):
        self.last_result = result
        stage = result.get("stage")
        if stage == "complete":
            message = f"Denoise complete. Rendered {result['total']} frames."
        elif stage == "cancelled":
            message = "Denoise cancelled."
        else:
            message = "Denoise failed. " + str(result.get("error") or "See the job logs.")
        if stage != "complete":
            message += " Any completed EXR frames have been retained."
        self._debug(message)
        self._debug(f"Cleanup complete. Logs: {self.directory}")
        self.dialog.label.setText(message)
        self.dialog.record(message)
        self.dialog.elapsed_timer.stop()
        self.dialog._update_timing()
        self.dialog.progress.setRange(0, 1)
        self.dialog.progress.setValue(1 if stage == "complete" else 0)
        self.dialog.progress.setFormat({"complete": "Complete", "cancelled": "Cancelled"}.get(stage, "Failed"))
        self.dialog.active = False
        self.dialog.button.setText("Close")
        self.dialog.button.setEnabled(True)
        self._finishing = False
        if self._lease:
            self._lease.__exit__(None, None, None)
            self._lease = None
        if self.process:
            self.process.deleteLater()
            self.process = None
        if stage == "complete":
            self.dialog.accept()
        if self._batch is not None:
            self._record_batch_result(result)
            if stage == "cancelled":
                self._cancelled = True
            self._batch_timer.start(0)
            return
        self.busy = False
        self.busy_changed.emit(False)
        self.idle.emit()

    def shutdown(self):
        if self.busy:
            self._closing = True
            self.cancel()
            return False
        return True

    def _after_idle(self):
        if self._closing:
            QTimer.singleShot(0, self.window.close)
