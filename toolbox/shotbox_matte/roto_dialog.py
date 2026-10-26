"""Compact, non-modal precomp workflow backed by the existing Matte planner."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from flow_layout import FlowLayout
from app_cache import temporary_cache_task

from .shotbox_matte.core import JobOptions, plan_job
from .shotbox_matte.gui import _generation_size_for_height, parse_worker_line, temporal_summary
from .shotbox_matte.models import DEFAULT_MODEL_ROOT, MODEL_FILES, discover_models
from .shotbox_matte.runtime import configured_model_preferences
from .shotbox_matte.sequences import discover_sequences
from .shotbox_matte.worker import MatteWorker


class _Signals(QObject):
    completed = pyqtSignal(object)


class _Task(QRunnable):
    def __init__(self, key, operation):
        super().__init__()
        self.key, self.operation = key, operation
        self.signals = _Signals()

    def run(self):
        try:
            response = (self.key, self.operation(), "")
        except Exception as exc:
            response = (self.key, None, str(exc) or type(exc).__name__)
        self.signals.completed.emit(response)


class RotoDialog(QDialog):
    def __init__(self, parent, source_frame, *, settings, runtime):
        super().__init__(parent)
        self.setObjectName("roto_dialog")
        self.setWindowTitle("Generate Roto")
        self.setModal(False)
        self.settings, self.runtime = settings, runtime
        self.source_frame = ""
        self._source = None
        self._models = {}
        self._model_error = ""
        self._plan = None
        self._result = None
        self._worker = None
        self._worker_result = None
        self._worker_error = ""
        self._cancel_requested = False
        self._closing = False
        self._token = 0
        self._task_id = 0
        self._tasks = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh_plan)
        self._build_ui()
        self.load_source(source_frame)
        self.resize(self.sizeHint())

    def sizeHint(self):
        size = QSize(640, 660)
        if self.screen():
            available = self.screen().availableGeometry()
            size.setWidth(min(size.width(), max(1, available.width() - 32)))
            size.setHeight(min(size.height(), max(1, available.height() - 64)))
        return size

    @staticmethod
    def _label(text=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _spin(low, high, step=1):
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        return spin

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        self.source_path = self._label()
        self.source_info = self._label()
        layout.addWidget(self.source_path)
        layout.addWidget(self.source_info)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        form.setContentsMargins(0, 8, 0, 8)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 120)
        self.fps.setDecimals(3)
        form.addRow("Frames per second", self.fps)
        self.color = QComboBox()
        for label, value in (("ACEScg", "acescg"), ("Linear sRGB / Rec.709", "linear-srgb"),
                             ("sRGB display", "srgb"), ("Auto · EXR linear", "auto")):
            self.color.addItem(label, value)
        form.addRow("Source colourspace", self.color)
        range_row = QHBoxLayout()
        self.range_enabled = QCheckBox("Use In / Out")
        self.source_start = self._spin(0, 2147483647)
        self.source_end = self._spin(0, 2147483647)
        range_row.addWidget(self.range_enabled)
        range_row.addWidget(self.source_start)
        range_row.addWidget(QLabel("–"))
        range_row.addWidget(self.source_end)
        form.addRow("Source range", range_row)
        self.range_enabled.toggled.connect(self._range_changed)
        size_row = QHBoxLayout()
        self.generation_width = self._spin(32, 1920, 32)
        self.generation_height = self._spin(32, 1088, 32)
        size_row.addWidget(self.generation_width)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.generation_height)
        form.addRow("Generation bounds", size_row)
        form.addRow("", self._label(
            "Change height to fit the source aspect ratio. Model padding is automatic.\n"
            "1088 px is the maximum height and gives the best quality, but takes longer to generate."
        ))
        self.seed = self._spin(0, 2147483647)
        form.addRow("Seed", self.seed)
        self.existing = QComboBox()
        self.existing.addItem("Version up", "version-up")
        self.existing.addItem("Overwrite source version", "overwrite")
        form.addRow("Existing folder", self.existing)
        self.suffix = QLineEdit()
        form.addRow("Task suffix", self.suffix)
        layout.addWidget(self.controls)

        summary = QFrame()
        summary.setObjectName("roto_summary")
        summary_layout = QVBoxLayout(summary)
        self.frame_summary = self._label("Reading sequence…")
        self.frame_summary.setObjectName("roto_frame_summary")
        self.output_title = self._label("OUTPUT LOCATION · PROVISIONAL UNTIL RESERVED")
        self.output_title.setObjectName("roto_output_title")
        self.output_preview = self._label()
        self.validation_info = self._label()
        for widget in (self.frame_summary, self.output_title, self.output_preview, self.validation_info):
            summary_layout.addWidget(widget)
        layout.addWidget(summary)
        self.stage_label = self._label()
        layout.addWidget(self.stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.details = QToolButton()
        self.details.setText("Diagnostic details")
        self.details.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details.setCheckable(True)
        self.details.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.details)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(2000)
        self.log.setMaximumHeight(160)
        self.log.hide()
        self.details.toggled.connect(self.log.setVisible)
        self.details.toggled.connect(lambda opened: self.details.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        layout.addWidget(self.log)
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        footer = FlowLayout(margin=0, h_spacing=6, v_spacing=6)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        self.open_output = QPushButton("Open Output Folder")
        self.open_output.setEnabled(False)
        self.open_output.clicked.connect(self._open_output)
        footer.addWidget(self.open_output)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        self.queue_button = QPushButton("Add to Queue")
        self.queue_button.setEnabled(False)
        self.queue_button.setToolTip("Coming later")
        footer.addWidget(self.queue_button)
        self.generate_button = QPushButton("Generate Now")
        self.generate_button.setObjectName("roto_generate")
        # The cache decorator accepts *args, so Qt forwards clicked(bool) to it.
        # Discard that signal argument before calling the parameterless action.
        self.generate_button.clicked.connect(lambda _checked=False: self.generate())
        footer.addWidget(self.generate_button)
        outer.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        for spin in (self.fps, self.source_start, self.source_end,
                     self.generation_width, self.generation_height, self.seed):
            spin.valueChanged.connect(self._schedule_plan)
        self.generation_height.valueChanged.connect(self._sync_generation_width)
        self.color.currentIndexChanged.connect(self._schedule_plan)
        self.existing.currentIndexChanged.connect(self._schedule_plan)
        self.suffix.textChanged.connect(self._schedule_plan)

    def _task(self, operation, callback):
        self._task_id += 1
        task = _Task(self._task_id, operation)
        self._tasks[task.key] = (task, callback)
        task.signals.completed.connect(self._task_completed)
        self._pool.start(task)

    def _task_completed(self, response):
        key, value, error = response
        _, callback = self._tasks.pop(key)
        if self._closing:
            if not self.is_busy():
                self.close()
            return
        callback(value, error)

    def load_source(self, source_frame):
        if self._worker is not None:
            return
        self._token += 1
        self._timer.stop()
        self.source_frame = str(Path(source_frame).resolve())
        self._source, self._plan, self._result = None, None, None
        self._models, self._model_error = {}, ""
        self.source_path.setText(str(Path(self.source_frame).parent))
        self.source_info.setText("Reading sequence…")
        self.output_preview.clear()
        self.validation_info.clear()
        self.frame_summary.setText("Reading sequence…")
        self.stage_label.setText("")
        self.open_output.setEnabled(False)
        self.progress_bar.hide()
        self.log.clear()
        self.details.setChecked(False)
        self.controls.setEnabled(False)
        # These workflow defaults never write to the full window's preferences.
        self.fps.setValue(25)
        self.color.setCurrentIndex(self.color.findData("acescg"))
        self.range_enabled.setChecked(False)
        self.source_start.setEnabled(False)
        self.source_end.setEnabled(False)
        self.generation_width.setValue(960)
        self.generation_height.setValue(544)
        self.seed.setValue(42)
        self.existing.setCurrentIndex(0)
        self.suffix.setText("_roto")
        self._update_generate()
        token, source = self._token, self.source_frame
        try:
            preferences = json.loads(self.settings.value("preferences", "{}"))
        except (ValueError, TypeError):
            preferences = {}
        if not isinstance(preferences, dict):
            preferences = {}

        def inspect():
            sequence = discover_sequences(source)[0]
            try:
                values = configured_model_preferences(preferences, self.runtime)
                root = values.get("model_root") or str(DEFAULT_MODEL_ROOT)
                models = discover_models(Path(root)).to_dict()
                explicit = values.get("models", {})
                if isinstance(explicit, dict):
                    models.update({key: value for key, value in explicit.items() if isinstance(value, str) and value})
                return sequence, models, ""
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                return sequence, {}, f"Matte model configuration needs attention: {exc}"

        def loaded(value, error):
            if token != self._token:
                return
            if error:
                self.frame_summary.setText("Input needs attention")
                self.validation_info.setText(error)
                self.source_info.setText("Could not read sequence")
                return
            self._source, self._models, self._model_error = value
            sequence = self._source
            self.source_info.setText(f"{sequence.id} · {sequence.width} × {sequence.height} · "
                                     f"{sequence.count:,} frames · {sequence.source_start}–{sequence.source_end}")
            self.source_start.setValue(sequence.source_start)
            self.source_end.setValue(sequence.source_end)
            self._sync_generation_width()
            missing = [key.replace("_", " ") for key in MODEL_FILES
                       if not Path(self._models.get(key) or "").is_file()]
            if missing and not self._model_error:
                self._model_error = "Missing Matte weights: " + ", ".join(missing) + "."
            if not self.runtime.worker_python.is_file():
                self._model_error = "Shotbox's Python is missing. Run the Shotbox installer or updater."
            if self._model_error:
                self.stage_label.setText(self._model_error + " Check Settings → Plugins or Toolbox → Shotbox Matte.")
            self.controls.setEnabled(True)
            self._schedule_plan()

        self._task(inspect, loaded)

    def _range_changed(self, enabled):
        self.source_start.setEnabled(enabled)
        self.source_end.setEnabled(enabled)
        self._schedule_plan()

    def _sync_generation_width(self, *_):
        if self._source is None:
            return
        width, height = _generation_size_for_height(
            self._source.width, self._source.height, self.generation_height.value())
        blocked = self.generation_height.blockSignals(True)
        self.generation_height.setValue(height)
        self.generation_height.blockSignals(blocked)
        self.generation_width.setValue(width)

    def options(self):
        return JobOptions(
            input_path=self.source_frame, sequence=self._source.id if self._source else None,
            fps=self.fps.value(), source_color=self.color.currentData(),
            source_start=self.source_start.value() if self.range_enabled.isChecked() else None,
            source_end=self.source_end.value() if self.range_enabled.isChecked() else None,
            generation_width=self.generation_width.value(), generation_height=self.generation_height.value(),
            seed=self.seed.value(), existing=self.existing.currentData(), suffix=self.suffix.text(), models=dict(self._models),
        )

    def _schedule_plan(self, *_):
        self._token += 1
        self._plan = None
        self._result = None
        self.open_output.setEnabled(False)
        self._update_generate()
        if self._source is not None and self._worker is None and not self._closing:
            self.frame_summary.setText("Updating plan…")
            self.output_preview.clear()
            self.output_title.setText("OUTPUT LOCATION · PROVISIONAL UNTIL RESERVED")
            self._timer.start()

    def _refresh_plan(self):
        if self._source is None or self._worker is not None or self._closing:
            return
        token, options = self._token, self.options()

        def planned(plan, error):
            if token != self._token:
                return
            if error:
                self._plan = None
                self.frame_summary.setText("Input needs attention")
                self.validation_info.setText(error)
            else:
                self._plan = plan
                self.frame_summary.setText(temporal_summary(
                    plan["selected_count"], plan["padded_count"], plan["chunk_count"], plan["overlap_count"]))
                self.output_preview.setText(plan["output_preview"])
                self.validation_info.setText(
                    f"{plan['source_count']:,} original frames · first export {plan['first_frame']} · "
                    f"RGB grayscale EXR · {plan['source_color']}" +
                    (" · segmented: review transitions" if plan["chunk_count"] > 1 else ""))
                if not self._model_error:
                    self.stage_label.setText("Ready to generate")
            self._update_generate()

        self._task(lambda: plan_job(options).to_dict(), planned)

    def _update_generate(self):
        self.generate_button.setEnabled(bool(self._plan is not None and self._worker is None
                                             and not self._model_error and not self._closing))

    @temporary_cache_task
    def generate(self):
        if not self.generate_button.isEnabled():
            return
        from dataclasses import asdict
        try:
            worker = MatteWorker(self, asdict(self.options()), self.runtime)
        except (OSError, ValueError) as exc:
            self.stage_label.setText(f"Could not prepare worker: {exc}")
            return
        self._worker = worker
        self._worker_result, self._worker_error = None, ""
        self._cancel_requested = False
        self._result = None
        self._timer.stop()
        self.output_title.setText("OUTPUT LOCATION · PROVISIONAL UNTIL RESERVED")
        self.controls.setEnabled(False)
        self.open_output.setEnabled(False)
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        self.stage_label.setText("Starting Alpha Gen…")
        self._update_generate()
        worker.stdout_line.connect(self._handle_worker_line)
        worker.stderr_line.connect(self._append_log)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _handle_worker_line(self, line):
        self._append_log(line)
        event = parse_worker_line(line)
        if event is None:
            return
        kind = event["type"]
        if kind == "progress":
            if not self._cancel_requested:
                stage = str(event.get("stage", "Processing")).replace("_", " ").capitalize()
                self.stage_label.setText(f"{stage} · {event.get('message', '')}")
            total, current = event.get("total", 0), event.get("current", 0)
            if isinstance(total, (int, float)) and total > 0 and isinstance(current, (int, float)):
                self.progress_bar.setRange(0, 1000)
                self.progress_bar.setValue(max(0, min(1000, int(current / total * 1000))))
            else:
                self.progress_bar.setRange(0, 0)
        elif kind == "result" and isinstance(event.get("result"), dict):
            self._worker_result = event["result"]
        elif kind == "error":
            self._worker_error = str(event.get("message", "Generation failed"))
        elif kind == "cancelled":
            self._cancel_requested = True

    def _append_log(self, line):
        from PyQt6.QtGui import QTextCursor
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertPlainText(line + "\n")

    def _worker_finished(self, code, status):
        from PyQt6.QtCore import QProcess
        self._worker.cleanup()
        self._worker = None
        self.controls.setEnabled(True)
        self.cancel_button.hide()
        self.progress_bar.setRange(0, 100)
        if code == 0 and status == QProcess.ExitStatus.NormalExit and self._worker_result:
            self._result = self._worker_result
            self.progress_bar.setValue(100)
            self.output_title.setText("OUTPUT LOCATION · COMPLETED EXPORT")
            self.output_preview.setText(self._result.get("output_dir", ""))
            self.open_output.setEnabled(bool(self._result.get("output_dir")))
            self.stage_label.setText(f"Complete · {self._result.get('frame_count', 0):,} RGB matte frames exported")
        elif self._cancel_requested:
            self.progress_bar.setValue(0)
            self.stage_label.setText("Cancelled · output is not marked complete")
        else:
            self.progress_bar.setValue(0)
            self.stage_label.setText(self._worker_error or f"Generation failed · worker exited with code {code}")
            self.details.setChecked(True)
        self._update_generate()
        if self._closing and not self.is_busy():
            self.close()

    def cancel(self):
        if self._worker is None or self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.stage_label.setText("Cancelling · waiting for the current operation…")
        self._worker.cancel()

    def _open_output(self):
        if self._result and self._result.get("output_dir"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._result["output_dir"]))

    def is_busy(self):
        return self._worker is not None or bool(self._tasks)

    def reject(self):
        # Escape follows the same worker-safe shutdown as the window close button.
        self.close()

    def closeEvent(self, event):
        self._closing = True
        self._token += 1
        self._timer.stop()
        if self._worker is not None:
            self.cancel()
        if self.is_busy():
            event.ignore()
        else:
            event.accept()
