"""Dashboard-owned, nonmodal background denoise UI, independent of edit locks."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

from PyQt6.QtCore import QObject, QProcess, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QVBoxLayout

import app_cache
from nuke_denoise import create_request, parse_event
from nuke_denoise_display import discover_tools, stop_recorded_children
from nuke_headless_tasks import find_nuke_executable
from settings import get_settings_manager


def batch_worker_limit():
    """Overlap isolated Linux workers only on hosts with ample spare resources."""
    import psutil
    if (sys.platform != "linux" or (os.cpu_count() or 1) < 64
            or psutil.virtual_memory().available < 32 * 1024**3):
        return 1
    return 1 if os.environ.get("SHOTBOX_DENOISE_BATCH_WORKERS") == "1" else 2


def choose_output_mode(parent, count, skipped=0, *, timeline_name=None, include_destination=False):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Denoise")
    layout = QVBoxLayout(dialog)
    if timeline_name is not None:
        scope = QLabel(f"Timeline: {timeline_name}")
        scope.setTextFormat(Qt.TextFormat.PlainText)
        scope.setWordWrap(True)
        layout.addWidget(scope)
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
    destination = deadline_options = None
    if include_destination:
        from deadline_ui import DeadlineOptions
        destination = QComboBox(dialog)
        destination.addItems(["Local", "Deadline"])
        destination.setAccessibleName("Denoise destination")
        layout.addWidget(QLabel("Run on:"))
        layout.addWidget(destination)
        deadline_options = DeadlineOptions(dialog)
        layout.addWidget(deadline_options)
        deadline_options.hide()

        def changed(index):
            deadline_options.setVisible(index == 1)
            overwrite.setEnabled(index == 0)
            if index == 1:
                new.setChecked(True)

        destination.currentIndexChanged.connect(changed)
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
        if destination is not None and destination.currentIndex() == 1:
            return {"mode": "new", "deadline": deadline_options.options()}
        return "overwrite" if overwrite.isChecked() else "new"
    finally:
        if deadline_options is not None:
            deadline_options.stop()
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
        self.label = QLabel("Starting NukeX…", self)
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
        self.logs_button = QPushButton("Open Logs", self)
        self.logs_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(controller.directory))))
        buttons.addWidget(self.logs_button)
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
        self._deadline_job = False
        self.dialog = None
        self.process = None
        self._lease = None
        self._buffer = ""
        self._cancelled = False
        self._finishing = False
        self._pending_result = None
        self._closing = False
        self._batch = None
        self._batch_owner = None
        self._workers = {}
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
                       "skipped": skipped, "errors": [], "workers": batch_worker_limit(), "submitted": [], "unknown": []}
        self.busy = True
        self._cancelled = False
        self.busy_changed.emit(True)
        self._advance_batch()
        return True

    def _advance_batch(self):
        batch = self._batch
        if batch is None:
            return
        if batch["workers"] > 1:
            self._advance_parallel_batch()
            return
        if self._cancelled or self._closing or batch["index"] == len(batch["tasks"]):
            self._finish_batch()
            return
        task = batch["tasks"][batch["index"]]
        batch["index"] += 1
        if not self._start_job(task["shot"], task["media"]):
            self._record_batch_result(self.last_result)
            self._batch_timer.start(0)

    def _advance_parallel_batch(self):
        batch = self._batch
        while (not self._cancelled and not self._closing
               and len(self._workers) < batch["workers"] and batch["index"] < len(batch["tasks"])):
            task = batch["tasks"][batch["index"]]
            # Serialize shared shot folders, including independently supplied tasks.
            destination = os.path.normcase(str(Path(task["media"]["shot_dir"]).resolve()))
            if any(destination == os.path.normcase(str(Path(t["media"]["shot_dir"]).resolve()))
                   for t in self._workers.values()):
                break
            batch["index"] += 1
            worker = DenoiseController(self.window)
            worker._batch_owner = self
            self._workers[worker] = task
            worker.idle.connect(lambda w=worker: self._parallel_finished(w))
            if worker._start_job(task["shot"], task["media"]):
                self.dialog = worker.dialog
                worker.dialog.batch_status.setVisible(True)
                worker.dialog.batch_status.setText(f"Clip {batch['index']} of {len(batch['tasks'])} · Parallel batch")
            else:
                self._parallel_finished(worker)
        for worker in self._workers:
            worker.dialog.batch_status.setText(
                f"Running: {len(self._workers)} · Completed: {batch['completed']} · "
                f"Failed: {batch['failed']} · Queued: {len(batch['tasks']) - batch['index']}"
            )
        if not self._workers:
            self._finish_batch()

    def _parallel_finished(self, worker):
        task = self._workers.pop(worker)
        result = worker.last_result
        self._record_batch_result(result, task=task, directory=worker.directory)
        if result.get("stage") == "cancelled":
            self.cancel()
        self.dialog = next((w.dialog for w in self._workers), None)
        if worker.dialog is None or result.get("stage") in ("complete", "submitted"):
            if worker.dialog is not None:
                worker.dialog.deleteLater()
            worker.deleteLater()
        else:
            worker.dialog.finished.connect(worker.deleteLater)
            worker.dialog.finished.connect(worker.dialog.deleteLater)
        self._batch_timer.start(0)

    def _record_batch_result(self, result, *, task=None, directory=None):
        batch = self._batch
        if result.get("stage") == "complete":
            batch["completed"] += 1
        elif result.get("stage") == "submitted":
            batch["submitted"].append({"job_id": result["job_id"], "receipt": result.get("receipt")})
        elif result.get("stage") != "cancelled":
            if result.get("stage") == "submission_unknown":
                batch["unknown"].append(result.get("receipt"))
            batch["failed"] += 1
            task = task or batch["tasks"][batch["index"] - 1]
            title = task["shot"].get("title") or Path(task["media"]["source"]).stem
            error = f"{title}: {result.get('error', 'Denoise failed')}"
            directory = directory or self.directory
            if directory is not None and Path(directory).is_dir():
                error += f"\nLogs: {directory}"
            batch["errors"].append(error)

    def _finish_batch(self):
        batch, self._batch = self._batch, None
        self._batch_timer.stop()
        self.last_batch_result = {key: batch[key] for key in ("completed", "failed", "skipped", "errors", "submitted", "unknown")}
        self.last_batch_result.update(cancelled=self._cancelled or self._closing,
                                      remaining=len(batch["tasks"]) - batch["index"])
        result = self.last_batch_result
        summary = ("Cancelled." if result["cancelled"] else "Done!") + (
            f"\n\nCompleted: {result['completed']}\nFailed: {result['failed']}\n"
            f"Skipped: {result['skipped']}\nRemaining: {result['remaining']}"
        )
        if result["errors"]:
            summary += "\n\n" + "\n\n".join(result["errors"][:5])
        if result["submitted"]:
            summary += (f"\n\nSubmitted: {len(result['submitted'])} (queued, not rendered)\n"
                        + "\n".join(item["job_id"] for item in result["submitted"])
                        + "\nManage or cancel these jobs in Deadline Monitor.")
        if result["unknown"]:
            summary += "\n\nUncertain submissions must be reconciled in Deadline Monitor before retrying."
        self.busy = False
        self.busy_changed.emit(False)
        self.idle.emit()
        if not self._closing:
            QMessageBox.information(self.window, "Denoise", summary)

    def _start_job(self, shot, media):
        self.directory = None
        self.request_path = None
        self._deadline_job = bool(media.get("deadline"))
        source = media["source"]
        self._debug(f"Requested input: {source}")
        try:
            if media.get("deadline"):
                from deadline_client import discover_client, within
                options = media["deadline"]
                discover_client(options.get("client", ""))
                for shared_path in (source, media["shot_dir"]):
                    if not options.get("allow_local", False) and not any(within(str(Path(shared_path).resolve()), root) for root in options.get("shared_roots", [])):
                        raise RuntimeError(f"Configure a shared project root in Settings → Deadline Denoise for: {shared_path}")
                media = dict(media, mode="new")
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
            if media.get("deadline"):
                request = json.loads(self.request_path.read_text())
                request["deadline"] = {**media["deadline"], "shot_dir": media["shot_dir"],
                                       "submission_id": uuid.uuid4().hex}
                self.request_path.write_text(json.dumps(request, indent=2))
        except Exception as exc:
            self._debug(f"Cannot start: {exc}")
            if self._deadline_job and self.directory is not None:
                from deadline_denoise import cleanup_workspace
                try:
                    cleanup_workspace(self.directory)
                except OSError as cleanup_error:
                    self._debug(f"Temporary Deadline cleanup failed: {cleanup_error}")
            if self._lease:
                self._lease.__exit__(None, None, None)
                self._lease = None
            self.last_result = {"stage": "failed", "error": str(exc)}
            if self._batch is None and self._batch_owner is None:
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
        worker_script = "deadline_denoise.py" if media.get("deadline") else "nuke_denoise.py"
        self.process.start(sys.executable, [str(Path(__file__).with_name(worker_script)),
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
                  "deadline_preflight": (1, "Checking Deadline repository and shared paths…"),
                  "submitting": (7, "Submitting prepared clip to Deadline…"),
                  "submitted": (8, "Submitted to Deadline: " + str(event.get("job_id", ""))),
                  "display_mode": (1, event.get("message", "Starting Windows denoise…")),
                  "display_fallback": (1, "Hidden profiling did not complete. Retrying visibly…"),
                  "loading": (2, "Loading the denoise template and source…"),
                  "preparing": (3, "Preparing the first frame for profiling…"),
                  "profiling": (4, "Opening Prepare Noise Profile…"),
                  "waiting_profile": (4, "Waiting for Neat Video to finish loading input frames…"),
                  "waiting_visible": (4, "Restore or uncover this job's Neat Video controls to continue…"),
                  "locating_auto_profile": (4, "Neat Video is visible. Locating Auto Profile…"),
                  "auto_profile_found": (4, "Auto Profile found. Sending the mouse click…"),
                  "auto_profiling": (4, "Auto Profile click sent. Waiting for profiling to finish…"),
                  "keyboard_auto_profiling": (4, "F2 sent. Waiting for Auto Profile to finish…"),
                  "keyboard_applying_profile": (5, "Enter sent. Waiting for Neat Video to close…"),
                  "checking_profile": (4, "Profile Check is preparing its preview…"),
                  "applying_profile": (5, "Auto Profile finished. Applying the noise profile…"),
                  "profile_ready": (6, "Noise profile prepared.")}
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
        if self._batch_owner is not None and not self._batch_owner._cancelled:
            self._batch_owner.cancel()
            return
        if not self.busy or self._cancelled:
            return
        self._cancelled = True
        for worker in list(self._workers):
            worker.cancel()
        self._debug("Cancellation requested; closing this job's Nuke process.")
        if self.process is not None:
            (self.directory / "cancel").touch()
        if self.dialog:
            self.dialog.set_status("Cancelling and closing this job's Nuke…")
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
        if self.request_path.is_file():
            request = json.loads(self.request_path.read_text())
            if request.get("deadline"):
                from deadline_denoise import recover_result
                try:
                    result = recover_result(request, result)
                except (OSError, ValueError, KeyError):
                    result = {"stage": "submission_unknown", "error": "Submission receipt unreadable. Inspect Deadline Monitor before retrying."}
        if result.get("stage") == "complete" and (exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit):
            result = {"stage": "failed", "error": "The denoise supervisor crashed after rendering."}
        self._pending_result = result
        self.dialog.set_status("Closing this job's Nuke and finishing cleanup…", step=8)
        self._cleanup_started = time.monotonic()
        self._cleanup_children()

    def _cleanup_children(self):
        # Finish outstanding writes before releasing the cache lease or closing.
        if stop_recorded_children(self.directory, force=time.monotonic() - self._cleanup_started > 5):
            QTimer.singleShot(200, self._cleanup_children)
            return
        self._finish(self._pending_result)

    def _cleanup_deadline_files(self, result):
        if not self._deadline_job or result.get("stage") not in {"submitted", "failed", "cancelled"}:
            return False
        from deadline_denoise import cleanup_submission, cleanup_workspace
        try:
            if self.request_path is not None and self.request_path.is_file():
                request = json.loads(self.request_path.read_text())
                if request.get("deadline") and not cleanup_submission(request, result):
                    return False
            cleaned = cleanup_workspace(self.directory)
            if cleaned:
                result.pop("receipt", None)
                result.pop("directory", None)
            return cleaned
        except (OSError, ValueError, KeyError) as exc:
            self._debug(f"Temporary Deadline cleanup failed: {exc}")
            return False

    def _finish(self, result):
        result = dict(result)
        cleaned = self._cleanup_deadline_files(result)
        self.last_result = result
        stage = result.get("stage")
        if stage == "complete":
            message = f"Denoise complete. Rendered {result['total']} frames."
        elif stage == "submitted":
            message = f"Submitted to Deadline: {result['job_id']}. Queued, not rendered. Manage in Deadline Monitor."
        elif stage == "submission_unknown":
            message = f"Submission outcome unknown. Reconcile in Deadline Monitor before retrying. Receipt: {result.get('receipt')}"
        elif stage == "cancelled":
            message = "Denoise cancelled."
        else:
            message = "Denoise failed. " + str(result.get("error") or "See the job logs.")
        if stage not in ("complete", "submitted", "submission_unknown"):
            message += " Any completed EXR frames have been retained."
        self._debug(message)
        if cleaned:
            self.dialog.logs_button.setEnabled(False)
            self._debug("Temporary Deadline submission files removed.")
        else:
            self._debug(f"Cleanup complete. Logs: {self.directory}")
        self.dialog.label.setText(message)
        self.dialog.record(message)
        self.dialog.elapsed_timer.stop()
        self.dialog._update_timing()
        self.dialog.progress.setRange(0, 1)
        self.dialog.progress.setValue(1 if stage in ("complete", "submitted") else 0)
        self.dialog.progress.setFormat({"complete": "Complete", "submitted": "Submitted", "submission_unknown": "Needs reconciliation", "cancelled": "Cancelled"}.get(stage, "Failed"))
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
        if stage in ("complete", "submitted"):
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
