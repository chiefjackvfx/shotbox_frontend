"""Shotbox Toolbox Matte window.

Importing this module never imports Qt. ``create_window`` loads the desktop
dependency inside Shotbox; internal command entry points stay headless.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
import uuid

from .diagnostics import log_level
from .models import DEFAULT_MODEL_ROOT as PROJECT_MODEL_ROOT
from .models import MODEL_FILES
from .presets import validated_preset, validated_presets
from .queueing import JobQueue
from .timing import JobHistory, StageTimer


MODEL_FIELDS = (
    ("transformer", "Distilled transformer"),
    ("text_encoder", "Text encoder"),
    ("video_vae", "Video VAE"),
    ("audio_vae", "Audio VAE"),
    ("spatial_upsampler", "Spatial upsampler"),
    ("alpha_lora", "Alpha Gen adapter"),
)
DEFAULT_MODEL_ROOT = str(PROJECT_MODEL_ROOT)
MODEL_PATHS_VERSION = 2
_QT_TYPES: dict[str, Any] | None = None


def _portable_directory() -> Path | None:
    value = os.environ.get("SHOTBOX_PORTABLE_DIR", "").strip()
    return Path(value).expanduser().resolve() if value else None


def _stored_relative_path(value: str, root: str) -> Path | None:
    """Understand settings from Windows as well as Linux after a folder move."""
    path_type = PureWindowsPath if PureWindowsPath(root).drive or "\\" in root else PurePosixPath
    try:
        relative = path_type(value).relative_to(path_type(root))
    except ValueError:
        return None
    # A manually edited setting must never escape the relocated application.
    if ".." in relative.parts:
        return None
    return Path(*relative.parts)


def _portable_preferences(values: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Relocate bundled model references while retaining custom external paths."""
    restored = dict(values)
    saved_models = values.get("models", {})
    saved_models = saved_models if isinstance(saved_models, dict) else {}
    models = dict(saved_models)
    prior_directory = values.get("portable_dir")
    for key in MODEL_FILES:
        value = saved_models.get(key)
        if not isinstance(value, str) or not value:
            continue
        if isinstance(prior_directory, str) and prior_directory:
            relative = _stored_relative_path(value, prior_directory)
            if relative is not None:
                models[key] = str(directory / relative)
    restored["models"] = models
    model_root = values.get("model_root")
    if isinstance(model_root, str) and model_root:
        relative = (_stored_relative_path(model_root, prior_directory)
                    if isinstance(prior_directory, str) and prior_directory else None)
        restored["model_root"] = str(directory / relative) if relative is not None else model_root
    else:
        restored["model_root"] = str(directory / "models")
    restored["portable_dir"] = str(directory)
    return restored


def parse_worker_line(line: str) -> dict[str, Any] | None:
    """Recognize a worker event without treating native library logs as JSON."""
    try:
        value = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("type") not in {
        "progress", "result", "error", "cancelled",
    }:
        return None
    return value


def temporal_summary(selected_count: int, padded_count: int, chunk_count: int = 1,
                     overlap_count: int = 0, temporal_mode: str = "segmented") -> str:
    if temporal_mode == "full-range":
        return f"{selected_count:,} selected → {padded_count:,} model frames in one pass · full range (experimental)"
    if chunk_count > 1:
        note = " · overlaps blended" if overlap_count else ""
        return f"{selected_count:,} selected → {padded_count:,} model frames across {chunk_count} segments{note}"
    added = padded_count - selected_count
    return f"{selected_count:,} selected → {padded_count:,} model frames · {added:,} added copies"


def accepts_input_path(path: Path | str) -> bool:
    """Use the processing backend's exact source format allowlist."""
    from .media import SUPPORTED_EXTENSIONS

    candidate = Path(path)
    return candidate.is_dir() or candidate.suffix.lower() in SUPPORTED_EXTENSIONS


def _generation_size_for_height(width: int, height: int, target_height: int) -> tuple[int, int]:
    """Match source proportions within the desktop's supported generation bounds."""
    from .imaging import spatial_plan

    plan = spatial_plan(width, height, 1920, target_height)
    return max(32, plan.resized_width), max(32, plan.resized_height)


def _preview_data(
    path: str, source_color: str, matte: bool = False, prepared: bool = False,
) -> tuple[int, int, bytes]:
    """Read and prepare a bounded preview in a background thread."""
    import numpy as np
    from PIL import Image

    from .imaging import prepare_color
    from .media import default_source_color, read_frame

    pixels = read_frame(Path(path), index=0)
    if not matte and not prepared:
        color = default_source_color(Path(path)) if source_color == "auto" else source_color
        pixels = prepare_color(pixels, color)
    pixels = np.nan_to_num(pixels, nan=0.0, posinf=1.0, neginf=0.0)
    preview = Image.fromarray((np.clip(pixels, 0.0, 1.0) * 255).astype(np.uint8))
    preview.thumbnail((1600, 1000), Image.Resampling.LANCZOS)
    return preview.width, preview.height, preview.tobytes()


def _build_qt_types() -> dict[str, Any]:
    global _QT_TYPES
    if _QT_TYPES is not None:
        return _QT_TYPES

    # Python must identify its main thread before an OIIO/Pillow import from a
    # Qt-owned pool thread initializes the threading module there instead.
    import threading

    threading.current_thread()

    try:
        from PyQt6.QtCore import (
            QObject, QProcess, QRunnable, QSettings, Qt, QThreadPool, QTimer, QUrl, pyqtSignal,
        )
        from PyQt6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPixmap, QTextCursor
        from PyQt6.QtWidgets import (
            QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
            QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView,
            QMainWindow, QProgressBar, QPushButton, QScrollArea,
            QSizePolicy, QSpinBox, QSplitter, QTabWidget, QTextEdit, QToolButton,
            QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
        )
    except ImportError as exc:
        raise RuntimeError(
            "The Matte window requires Shotbox frontend dependencies. "
            "Run the Shotbox frontend installer or updater, then open Toolbox → Shotbox Matte."
        ) from exc

    class TaskSignals(QObject):
        completed = pyqtSignal(object)

    class Task(QRunnable):
        def __init__(self, key: int, operation: Any):
            super().__init__()
            self.key = key
            self.operation = operation
            self.signals = TaskSignals()

        def run(self) -> None:
            try:
                result = {"key": self.key, "value": self.operation()}
            except Exception as exc:
                result = {"key": self.key, "error": str(exc) or type(exc).__name__}
            self.signals.completed.emit(result)

    class DropArea(QFrame):
        path_dropped = pyqtSignal(str)
        paths_dropped = pyqtSignal(list)
        browse_requested = pyqtSignal()

        def __init__(self, batch: bool = False) -> None:
            super().__init__()
            self.setObjectName("dropArea")
            self.setAcceptDrops(True)
            self.setMinimumHeight(104)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(22, 18, 22, 18)
            copy = QVBoxLayout()
            headline = QLabel("Drop movies or sequence folders" if batch else "Drop a sequence, MOV, MP4 or MXF")
            headline.setObjectName("dropHeadline")
            headline.setWordWrap(True)
            copy.addWidget(headline)
            hint = QLabel("Drop several sources together. One job per movie or image sequence."
                          if batch else "Drop multiple sources to add a batch. Sources stay in place.")
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            copy.addWidget(hint)
            layout.addLayout(copy, 1)
            button = QPushButton("Add files…" if batch else "Browse…")
            button.setObjectName("secondaryButton")
            button.clicked.connect(self.browse_requested)
            layout.addWidget(button)

        def dragEnterEvent(self, event: Any) -> None:
            if self.input_paths(event):
                self.setProperty("dragging", True)
                self.style().unpolish(self)
                self.style().polish(self)
                event.acceptProposedAction()

        @staticmethod
        def input_paths(event: Any) -> list[str]:
            urls = event.mimeData().urls()
            if (urls and all(url.isLocalFile() for url in urls)
                    and any(accepts_input_path(url.toLocalFile()) for url in urls)):
                return [url.toLocalFile() for url in urls]
            return []

        def dragLeaveEvent(self, event: Any) -> None:
            self._reset_drag()
            event.accept()

        def dropEvent(self, event: Any) -> None:
            self._reset_drag()
            paths = self.input_paths(event)
            if paths:
                self.paths_dropped.emit(paths)
                if len(paths) == 1:
                    self.path_dropped.emit(paths[0])
                event.acceptProposedAction()

        def _reset_drag(self) -> None:
            self.setProperty("dragging", False)
            self.style().unpolish(self)
            self.style().polish(self)

    class BatchTree(QTreeWidget):
        paths_dropped = pyqtSignal(list)

        def __init__(self) -> None:
            super().__init__()
            self.setAcceptDrops(True)
            self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)

        def dragEnterEvent(self, event: Any) -> None:
            if DropArea.input_paths(event):
                event.acceptProposedAction()

        def dragMoveEvent(self, event: Any) -> None:
            if DropArea.input_paths(event):
                event.acceptProposedAction()

        def dropEvent(self, event: Any) -> None:
            paths = DropArea.input_paths(event)
            if paths:
                self.paths_dropped.emit(paths)
                event.acceptProposedAction()

    class PreviewCanvas(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.pixmap: Any = None
            self.setMinimumSize(300, 210)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        def set_image(self, data: tuple[int, int, bytes] | None) -> None:
            if data is None:
                self.pixmap = None
            else:
                width, height, rgb = data
                image = QImage(rgb, width, height, width * 3, QImage.Format.Format_RGB888).copy()
                self.pixmap = QPixmap.fromImage(image)
            self.update()

        def paintEvent(self, event: Any) -> None:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#101215"))
            if self.pixmap is None:
                painter.setPen(QColor("#797f8b"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Your first frame appears here")
                return
            bounds = self.rect().adjusted(18, 18, -18, -18)
            size = self.pixmap.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
            left = bounds.x() + (bounds.width() - size.width()) // 2
            top = bounds.y() + (bounds.height() - size.height()) // 2
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(left, top, size.width(), size.height(), self.pixmap)

    class Collapsible(QFrame):
        def __init__(self, title: str, expanded: bool = True) -> None:
            super().__init__()
            self.setObjectName("card")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 12, 16, 16)
            layout.setSpacing(12)
            self.toggle = QToolButton()
            self.toggle.setText(title)
            self.toggle.setCheckable(True)
            self.toggle.setChecked(expanded)
            self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.body = QWidget()
            self.body_layout = QVBoxLayout(self.body)
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(10)
            layout.addWidget(self.toggle)
            layout.addWidget(self.body)
            self.toggle.toggled.connect(self._toggle)
            self._toggle(expanded)

        def _toggle(self, expanded: bool) -> None:
            self.body.setVisible(expanded)
            self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    class MainWindow(QMainWindow):
        def __init__(self, settings: Any = None, runtime: Any = None) -> None:
            super().__init__()
            self.runtime = runtime
            self._closing = False
            self._portable_directory = None if runtime is not None else _portable_directory()
            self._default_model_root = str(self._portable_directory / "models") if self._portable_directory else DEFAULT_MODEL_ROOT
            if settings is not None:
                self.settings = settings
            elif self._portable_directory is not None:
                self.settings = QSettings(str(self._portable_directory / "settings.ini"), QSettings.Format.IniFormat)
            else:
                self.settings = QSettings("ShotBox", "ShotBox Matte")
            self._pool = QThreadPool(self)
            self._pool.setMaxThreadCount(2)
            self._task_number = 0
            self._tasks: dict[int, Any] = {}
            self._source_token = 0
            self._preview_token = 0
            self._plan_token = 0
            self._sequences: list[Any] = []
            self._plan: dict[str, Any] | None = None
            self._result: dict[str, Any] | None = None
            self._input_path = ""
            self._process: Any = None
            self._worker: Any = None
            self._worker_directory: Any = None
            self._worker_path: Path | None = None
            self._worker_result: dict[str, Any] | None = None
            self._worker_error = ""
            self._cancel_requested = False
            self._close_after_worker = False
            self._source_preview: Any = None
            self._matte_preview: Any = None
            self.queue = JobQueue()
            self._queue_sources: dict[str, Any] = {}
            self._batch_inspections = 0
            self._active_options: dict[str, Any] | None = None
            self._presets: dict[str, dict[str, Any]] = {}
            self._timing = StageTimer()
            self._history = JobHistory()
            self._progress_stage: tuple[str, Any] | None = None
            self._log_path: Path | None = None
            self._build_ui()
            self._initialize_log()
            self._restore_settings()
            self._plan_timer = QTimer(self)
            self._plan_timer.setSingleShot(True)
            self._plan_timer.setInterval(300)
            self._plan_timer.timeout.connect(self._refresh_plan)
            self._connect_controls()
            self._render_queue()
            self._timing_timer = QTimer(self)
            self._timing_timer.setInterval(500)
            self._timing_timer.timeout.connect(self._update_timing)
            QTimer.singleShot(0, lambda: self._discover_models(initial=True))

        def _build_ui(self) -> None:
            self.setWindowTitle("ShotBox Matte")
            self.resize(1290, 940)
            self.setMinimumSize(960, 730)
            self.setStyleSheet(STYLE)
            root = QWidget()
            self.setCentralWidget(root)
            outer = QVBoxLayout(root)
            outer.setContentsMargins(26, 20, 26, 18)
            outer.setSpacing(18)
            heading = QHBoxLayout()
            branding = QVBoxLayout()
            wordmark = QLabel("SHOTBOX  /  MATTE")
            wordmark.setObjectName("wordmark")
            branding.addWidget(wordmark)
            subtitle = QLabel("EXR, images or video in. Production mattes out.")
            subtitle.setObjectName("muted")
            branding.addWidget(subtitle)
            heading.addLayout(branding, 1)
            badge = QLabel("LTX-2.5  ·  ALPHA GEN")
            badge.setObjectName("badge")
            heading.addWidget(badge)
            from .build_info import build_label
            self.build_label = QLabel(build_label())
            self.build_label.setObjectName("muted")
            self.build_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            heading.addWidget(self.build_label)
            outer.addLayout(heading)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            outer.addWidget(splitter, 1)
            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 8, 0)
            left_layout.setSpacing(14)
            self.drop_area = DropArea()
            self.drop_area.paths_dropped.connect(self._input_paths_dropped)
            self.drop_area.browse_requested.connect(self._browse_input)
            left_layout.addWidget(self.drop_area)

            source_card = QFrame()
            source_card.setObjectName("card")
            source_layout = QVBoxLayout(source_card)
            source_layout.setContentsMargins(16, 14, 16, 14)
            source_layout.setSpacing(8)
            self.source_path = QLineEdit()
            self.source_path.setPlaceholderText("Or paste a sequence folder, image, MOV, MP4 or MXF path")
            self.source_path.returnPressed.connect(lambda: self.load_input(self.source_path.text()))
            self.source_path.setObjectName("sourcePath")
            source_layout.addWidget(self.source_path)
            self.sequence_combo = QComboBox()
            self.sequence_combo.setPlaceholderText("Sequence")
            self.sequence_combo.setEnabled(False)
            self.sequence_combo.currentIndexChanged.connect(self._sequence_changed)
            source_layout.addWidget(self.sequence_combo)
            self.source_info = QLabel("Choose a source to detect dimensions, range and frame count.")
            self.source_info.setObjectName("muted")
            self.source_info.setWordWrap(True)
            source_layout.addWidget(self.source_info)
            left_layout.addWidget(source_card)

            preview_card = QFrame()
            preview_card.setObjectName("card")
            preview_layout = QVBoxLayout(preview_card)
            preview_layout.setContentsMargins(0, 0, 0, 0)
            preview_layout.setSpacing(0)
            preview_header = QHBoxLayout()
            preview_header.setContentsMargins(16, 12, 16, 12)
            preview_title = QLabel("FIRST FRAME")
            preview_title.setObjectName("sectionTitle")
            preview_header.addWidget(preview_title, 1)
            self.preview_mode = QComboBox()
            self.preview_mode.addItems(["Source", "Matte"])
            self.preview_mode.setEnabled(False)
            self.preview_mode.currentIndexChanged.connect(self._show_preview)
            preview_header.addWidget(self.preview_mode)
            preview_layout.addLayout(preview_header)
            self.preview_canvas = PreviewCanvas()
            preview_layout.addWidget(self.preview_canvas, 1)
            self.preview_caption = QLabel("Display preview only · exported mattes retain raw values")
            self.preview_caption.setObjectName("previewCaption")
            self.preview_caption.setWordWrap(True)
            preview_layout.addWidget(self.preview_caption)
            left_layout.addWidget(preview_card, 1)

            from .qc_preview import create_qc_preview
            qc_group = Collapsible("FRAME REVIEW · SOURCE / MATTE / OVERLAY", expanded=False)
            self.qc_group = qc_group
            self.qc_preview = create_qc_preview()
            qc_group.body_layout.addWidget(self.qc_preview)
            left_layout.addWidget(qc_group)

            batch_page = QWidget()
            batch_layout = QVBoxLayout(batch_page)
            batch_layout.setContentsMargins(0, 10, 8, 0)
            batch_layout.setSpacing(12)
            self.batch_drop_area = DropArea(batch=True)
            self.batch_drop_area.paths_dropped.connect(self.add_paths_to_queue)
            self.batch_drop_area.browse_requested.connect(self._browse_queue_files)
            batch_layout.addWidget(self.batch_drop_area)
            queue_group = Collapsible("BATCH QUEUE", expanded=True)
            self.queue_group = queue_group
            self.queue_tree = BatchTree()
            self.queue_tree.paths_dropped.connect(self.add_paths_to_queue)
            self.queue_tree.setHeaderLabels(["Source", "Type", "Frames", "Status"])
            self.queue_tree.setRootIsDecorated(False)
            self.queue_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
            self.queue_tree.setMinimumHeight(120)
            self.queue_tree.setAlternatingRowColors(True)
            self.queue_tree.header().setStretchLastSection(False)
            self.queue_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column in (1, 2, 3):
                self.queue_tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            queue_add = QGridLayout()
            self.queue_add_current = QPushButton("Add current shot")
            self.queue_add_current.clicked.connect(self.add_current_to_queue)
            queue_add.addWidget(self.queue_add_current, 1, 1)
            self.queue_add_files = QPushButton("Add movies / files…")
            self.queue_add_files.clicked.connect(self._browse_queue_files)
            queue_add.addWidget(self.queue_add_files, 0, 0)
            self.queue_add_folder = QPushButton("Add sequence folders…")
            self.queue_add_folder.clicked.connect(self._browse_queue_folders)
            queue_add.addWidget(self.queue_add_folder, 0, 1)
            self.queue_scan_folder = QPushButton("Scan parent folder…")
            self.queue_scan_folder.clicked.connect(self._browse_queue_parent)
            queue_add.addWidget(self.queue_scan_folder, 1, 0)
            queue_group.body_layout.addLayout(queue_add)
            queue_group.body_layout.addWidget(self.queue_tree, 1)
            queue_actions = QGridLayout()
            self.queue_start = QPushButton("Start queue")
            self.queue_start.clicked.connect(self.start_queue)
            queue_actions.addWidget(self.queue_start, 0, 0)
            self.queue_pause = QPushButton("Pause after current")
            self.queue_pause.clicked.connect(self.pause_queue)
            queue_actions.addWidget(self.queue_pause, 0, 1)
            self.queue_remove = QPushButton("Remove pending")
            self.queue_remove.clicked.connect(self.remove_pending_queue)
            queue_actions.addWidget(self.queue_remove, 1, 0)
            self.queue_clear = QPushButton("Clear finished")
            self.queue_clear.clicked.connect(self.clear_finished_queue)
            queue_actions.addWidget(self.queue_clear, 1, 1)
            queue_group.body_layout.addLayout(queue_actions)
            batch_hint = QLabel("Set the controls on the right, then add sources. Each job keeps those settings. "
                                "Folders include nested sequences; selecting several frames adds their sequence once.")
            batch_hint.setObjectName("muted")
            batch_hint.setWordWrap(True)
            queue_group.body_layout.addWidget(batch_hint)
            self.queue_note = QLabel("Add multiple movies, sequence folders or individual sequence frames.")
            self.queue_note.setObjectName("muted")
            self.queue_note.setWordWrap(True)
            queue_group.body_layout.addWidget(self.queue_note)
            batch_layout.addWidget(queue_group, 1)

            summary_card = QFrame()
            summary_card.setObjectName("card")
            summary_layout = QVBoxLayout(summary_card)
            summary_layout.setContentsMargins(16, 14, 16, 14)
            self.frame_summary = QLabel("Frame preparation will appear here")
            self.frame_summary.setObjectName("frameSummary")
            self.frame_summary.setWordWrap(True)
            summary_layout.addWidget(self.frame_summary)
            self.output_title = QLabel("OUTPUT LOCATION · PROVISIONAL UNTIL RESERVED")
            self.output_title.setObjectName("sectionTitle")
            summary_layout.addWidget(self.output_title)
            self.output_preview = QLabel("—")
            self.output_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.output_preview.setWordWrap(True)
            summary_layout.addWidget(self.output_preview)
            self.validation_info = QLabel("")
            self.validation_info.setWordWrap(True)
            self.validation_info.setObjectName("muted")
            summary_layout.addWidget(self.validation_info)
            left_layout.addWidget(summary_card)
            self.left_scroll = QScrollArea()
            self.left_scroll.setWidgetResizable(True)
            self.left_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.left_scroll.setMinimumWidth(400)
            self.left_scroll.setWidget(left)
            self.workspace_tabs = QTabWidget()
            self.workspace_tabs.setMinimumWidth(400)
            self.workspace_tabs.addTab(self.left_scroll, "Single shot")
            self.batch_scroll = QScrollArea()
            self.batch_scroll.setWidgetResizable(True)
            self.batch_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.batch_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.batch_scroll.setWidget(batch_page)
            self.workspace_tabs.addTab(self.batch_scroll, "Batch queue")
            self.workspace_tabs.currentChanged.connect(self._workspace_changed)
            splitter.addWidget(self.workspace_tabs)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setMinimumWidth(365)
            scroll_content = QWidget()
            controls_layout = QVBoxLayout(scroll_content)
            controls_layout.setContentsMargins(8, 0, 0, 0)
            controls_layout.setSpacing(14)
            self._controls = scroll_content
            presets_group = Collapsible("WORKFLOW PRESETS", expanded=False)
            preset_row = QHBoxLayout()
            self.preset_combo = QComboBox()
            self.preset_combo.setEditable(True)
            self.preset_combo.setPlaceholderText("Preset name")
            preset_row.addWidget(self.preset_combo, 1)
            self.preset_save = QPushButton("Save")
            self.preset_save.clicked.connect(self.save_preset)
            preset_row.addWidget(self.preset_save)
            self.preset_apply = QPushButton("Apply")
            self.preset_apply.clicked.connect(self.apply_preset)
            preset_row.addWidget(self.preset_apply)
            self.preset_delete = QPushButton("Delete")
            self.preset_delete.clicked.connect(self.delete_preset)
            preset_row.addWidget(self.preset_delete)
            presets_group.body_layout.addLayout(preset_row)
            preset_note = QLabel("Saves workflow settings and ranges. The selected source and model paths stay as configured.")
            preset_note.setObjectName("muted")
            preset_note.setWordWrap(True)
            presets_group.body_layout.addWidget(preset_note)
            controls_layout.addWidget(presets_group)
            self.prepare_group = Collapsible("SOURCE & GENERATION")
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setHorizontalSpacing(15)
            form.setVerticalSpacing(10)
            self.color = QComboBox()
            self.color.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.color.setMinimumContentsLength(12)
            for label, key in [
                ("Auto · EXR linear / images + video sRGB", "auto"),
                ("Linear sRGB / Rec.709", "linear-srgb"),
                ("sRGB display", "srgb"),
                ("ACEScg", "acescg"),
            ]:
                self.color.addItem(label, key)
            self.color.setToolTip("Auto treats EXRs as linear and other images/video as sRGB. Override to match your source colour space.")
            form.addRow("Source colour", self.color)
            self.fps = QDoubleSpinBox()
            self.fps.setRange(1, 120)
            self.fps.setDecimals(3)
            self.fps.setValue(24)
            form.addRow("Frames / second", self.fps)
            range_row = QHBoxLayout()
            self.range_enabled = QCheckBox("Use In / Out")
            range_row.addWidget(self.range_enabled)
            self.source_start = self._spin(0, 2147483647, 1)
            self.source_end = self._spin(0, 2147483647, 1)
            for endpoint in (self.source_start, self.source_end):
                endpoint.setMinimumWidth(72)
                endpoint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.source_start.setEnabled(False)
            self.source_end.setEnabled(False)
            range_row.addWidget(self.source_start)
            range_row.addWidget(QLabel("–"))
            range_row.addWidget(self.source_end)
            form.addRow("Source range", range_row)
            self.range_hint = QLabel("Inclusive source frame numbers; movies and unnumbered images use positions starting at 1.")
            self.range_hint.setObjectName("muted")
            self.range_hint.setWordWrap(True)
            form.addRow("", self.range_hint)
            self.frame_skip = self._spin(0, 10000, 0)
            self.frame_skip.setToolTip("Skip N frames between selected frames; 0 keeps every frame.")
            form.addRow("Frame skip", self.frame_skip)
            self.frame_cap = self._spin(0, 1000000, 0)
            self.frame_cap.setSpecialValueText("All frames")
            self.frame_cap.setToolTip("Up to 250 selected frames per shot. Choose segments or experimental full-range processing below.")
            form.addRow("Frame cap", self.frame_cap)
            self.temporal_mode = QComboBox()
            self.temporal_mode.addItem("Segments · recommended", "segmented")
            self.temporal_mode.addItem("Full range · experimental", "full-range")
            form.addRow("Frame processing", self.temporal_mode)
            self.temporal_mode_hint = QLabel()
            self.temporal_mode_hint.setObjectName("muted")
            self.temporal_mode_hint.setWordWrap(True)
            self._update_temporal_mode_hint()
            form.addRow("", self.temporal_mode_hint)
            size = QHBoxLayout()
            self.generation_width = self._spin(32, 1920, 960, 32)
            self.generation_height = self._spin(32, 1088, 544, 32)
            self.generation_width.setToolTip("Changing height updates width to match the selected source's aspect ratio.")
            self.generation_height.setToolTip("Width follows the source aspect ratio. Height is reduced if width would exceed 1920 pixels.")
            size.addWidget(self.generation_width)
            size.addWidget(QLabel("×"))
            size.addWidget(self.generation_height)
            form.addRow("Generation bounds", size)
            size_hint = QLabel("Change height to match width to the source aspect ratio. Size stays within 1920 × 1088; model padding is added automatically.")
            size_hint.setObjectName("muted")
            size_hint.setWordWrap(True)
            form.addRow("", size_hint)
            self.seed = self._spin(0, 2147483647, 42)
            form.addRow("Seed", self.seed)
            self.prepare_group.body_layout.addLayout(form)
            controls_layout.addWidget(self.prepare_group)

            self.export_group = Collapsible("EXR EXPORT")
            export_form = QFormLayout()
            export_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            export_form.setVerticalSpacing(10)
            self.suffix = QLineEdit("_roto")
            export_form.addRow("Task suffix", self.suffix)
            first_row = QHBoxLayout()
            self.auto_first = QCheckBox("From source")
            self.auto_first.setChecked(True)
            self.first_frame = self._spin(0, 2147483647, 1001)
            self.first_frame.setEnabled(False)
            self.auto_first.toggled.connect(lambda value: self.first_frame.setEnabled(not value))
            first_row.addWidget(self.auto_first)
            first_row.addWidget(self.first_frame)
            export_form.addRow("First frame", first_row)
            self.padding = self._spin(1, 8, 4)
            export_form.addRow("Number padding", self.padding)
            self.precision = QComboBox()
            self.precision.addItem("Half float · 16 bit", "half")
            self.precision.addItem("Full float · 32 bit", "float")
            export_form.addRow("Precision", self.precision)
            self.existing = QComboBox()
            self.existing.addItem("Version up", "version-up")
            self.existing.addItem("Overwrite source version", "overwrite")
            export_form.addRow("Existing folder", self.existing)
            self.export_group.body_layout.addLayout(export_form)
            export_note = QLabel("RGB grayscale matte · ZIP compression · original source dimensions")
            export_note.setWordWrap(True)
            export_note.setObjectName("muted")
            self.export_group.body_layout.addWidget(export_note)
            controls_layout.addWidget(self.export_group)

            self.models_group = Collapsible("LOCAL MODEL WEIGHTS", expanded=False)
            model_note = QLabel("Model files are stored with this app in its models folder.")
            model_note.setWordWrap(True)
            model_note.setObjectName("muted")
            self.models_group.body_layout.addWidget(model_note)
            root_row = QHBoxLayout()
            self.model_root = QLineEdit(self._default_model_root)
            self.model_root.setPlaceholderText("This application's models folder")
            root_row.addWidget(self.model_root, 1)
            discover_button = QPushButton("Find")
            discover_button.clicked.connect(lambda: self._discover_models(initial=False))
            root_row.addWidget(discover_button)
            self.models_group.body_layout.addLayout(root_row)
            self.model_paths: dict[str, Any] = {}
            for key, label in MODEL_FIELDS:
                self.models_group.body_layout.addWidget(QLabel(label))
                row = QHBoxLayout()
                field = QLineEdit()
                field.setPlaceholderText("Select existing local weights…")
                field.setObjectName(f"model_{key}")
                field.textChanged.connect(lambda _value: self._update_model_badge())
                row.addWidget(field, 1)
                browse = QPushButton("…")
                browse.setFixedWidth(34)
                browse.clicked.connect(lambda _checked=False, name=key: self._browse_model(name))
                row.addWidget(browse)
                self.models_group.body_layout.addLayout(row)
                self.model_paths[key] = field
            self.model_status = QLabel("Checking existing local models…")
            self.model_status.setWordWrap(True)
            self.model_status.setObjectName("muted")
            self.models_group.body_layout.addWidget(self.model_status)
            controls_layout.addWidget(self.models_group)

            memory = Collapsible("MEMORY", expanded=False)
            memory_form = QFormLayout()
            memory_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            self.offload = QComboBox()
            self.offload.addItem("CPU offload", "cpu")
            self.offload.addItem("Disk offload", "disk")
            self.offload.addItem("Keep on GPU", "none")
            memory_form.addRow("Model placement", self.offload)
            self.low_vram = QCheckBox("Enable supported low VRAM mode")
            memory_form.addRow(self.low_vram)
            memory.body_layout.addLayout(memory_form)
            controls_layout.addWidget(memory)
            controls_layout.addStretch()
            scroll.setWidget(scroll_content)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            splitter.addWidget(scroll)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
            splitter.setSizes([740, 450])

            footer = QFrame()
            footer.setObjectName("footer")
            footer_layout = QGridLayout(footer)
            footer_layout.setContentsMargins(0, 0, 0, 0)
            footer_layout.setHorizontalSpacing(14)
            self.stage_label = QLabel("Ready when you are")
            self.stage_label.setObjectName("stageLabel")
            footer_layout.addWidget(self.stage_label, 0, 0)
            self.open_output = QPushButton("Open output folder")
            self.open_output.setEnabled(False)
            self.open_output.clicked.connect(self._open_output)
            footer_layout.addWidget(self.open_output, 0, 1, 2, 1)
            self.cancel_button = QPushButton("Cancel")
            self.cancel_button.setEnabled(False)
            self.cancel_button.clicked.connect(self.cancel)
            footer_layout.addWidget(self.cancel_button, 0, 2, 2, 1)
            self.generate_button = QPushButton("Generate matte")
            self.generate_button.setObjectName("generateButton")
            self.generate_button.setEnabled(False)
            self.generate_button.setMinimumWidth(164)
            self.generate_button.clicked.connect(self._generate_clicked)
            footer_layout.addWidget(self.generate_button, 0, 3, 2, 1)
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(False)
            self.progress_bar.setFixedHeight(5)
            footer_layout.addWidget(self.progress_bar, 1, 0)
            self.timing_label = QLabel("Elapsed — · Stage ETA unknown")
            self.timing_label.setObjectName("muted")
            self.timing_label.setWordWrap(True)
            footer_layout.addWidget(self.timing_label, 2, 0, 1, 4)
            outer.addWidget(footer)
            self.log_group = Collapsible("PROCESS LOG", expanded=False)
            self.log = QTextEdit()
            self.log.setReadOnly(True)
            self.log.setAcceptRichText(False)
            self.log.document().setMaximumBlockCount(1500)
            self.log.setFixedHeight(155)
            self.log_group.body_layout.addWidget(self.log)
            log_actions = QHBoxLayout()
            self.copy_log = QPushButton("Copy log")
            self.copy_log.clicked.connect(self._copy_log)
            log_actions.addWidget(self.copy_log)
            self.open_logs = QPushButton("Open logs")
            self.open_logs.clicked.connect(self._open_logs)
            log_actions.addWidget(self.open_logs)
            self.log_notice = QLabel("Optional library warnings are labelled. Raw worker output is saved in full.")
            self.log_notice.setWordWrap(True)
            self.log_notice.setObjectName("muted")
            log_actions.addWidget(self.log_notice, 1)
            self.log_group.body_layout.addLayout(log_actions)
            outer.addWidget(self.log_group)

        @staticmethod
        def _spin(minimum: int, maximum: int, value: int, step: int = 1) -> Any:
            widget = QSpinBox()
            widget.setRange(minimum, maximum)
            widget.setSingleStep(step)
            widget.setValue(value)
            return widget

        def _connect_controls(self) -> None:
            self.generation_height.valueChanged.connect(self._sync_generation_width)
            for widget in (
                self.fps, self.frame_skip, self.frame_cap, self.generation_width,
                self.generation_height, self.seed, self.first_frame, self.padding,
                self.source_start, self.source_end,
            ):
                widget.valueChanged.connect(self._schedule_plan)
            for widget in (self.precision, self.existing, self.offload, self.temporal_mode):
                widget.currentIndexChanged.connect(self._schedule_plan)
            self.temporal_mode.currentIndexChanged.connect(self._update_temporal_mode_hint)
            self.color.currentIndexChanged.connect(self._color_changed)
            self.suffix.textChanged.connect(self._schedule_plan)
            self.auto_first.toggled.connect(self._schedule_plan)
            self.low_vram.toggled.connect(self._schedule_plan)
            self.range_enabled.toggled.connect(self._range_changed)

        def _update_temporal_mode_hint(self, _index: int = 0) -> None:
            if self.temporal_mode.currentData() == "full-range":
                self.temporal_mode_hint.setText("One pass for the selected range. Experimental: longer clips may leak source colour into the matte or run out of GPU memory.")
            else:
                self.temporal_mode_hint.setText("Long ranges use overlapping segments with blended joins; short ranges run in one pass.")

        def _sync_generation_width(self, _height: int = 0) -> None:
            index = self.sequence_combo.currentIndex()
            if not 0 <= index < len(self._sequences):
                return
            sequence = self._sequences[index]
            width, height = _generation_size_for_height(
                sequence.width, sequence.height, self.generation_height.value(),
            )
            # A wide source can also cap height; avoid re-entering this handler.
            blocked = self.generation_height.blockSignals(True)
            self.generation_height.setValue(height)
            self.generation_height.blockSignals(blocked)
            self.generation_width.setValue(width)

        def _range_changed(self, enabled: bool) -> None:
            self.source_start.setEnabled(enabled)
            self.source_end.setEnabled(enabled)
            self._schedule_plan()

        def _input_paths_dropped(self, paths: list[str]) -> None:
            if len(paths) == 1:
                self.load_input(paths[0])
            elif paths:
                self.add_paths_to_queue(paths)

        def _workspace_changed(self, _index: int = 0) -> None:
            if hasattr(self, "generate_button"):
                self._update_generate_button()

        def _update_generate_button(self) -> None:
            batch = self.workspace_tabs.currentIndex() == 1
            self.generate_button.setText("Start batch" if batch else "Generate matte")
            ready = (any(job.status == "pending" for job in self.queue.jobs)
                     and not self.queue.running and not self._batch_inspections) if batch else self._plan is not None
            self.generate_button.setEnabled(not self._is_running() and ready)

        def _generate_clicked(self) -> None:
            if self.workspace_tabs.currentIndex() == 1:
                self.start_queue()
            else:
                self.generate()

        @staticmethod
        def _queue_identity(options: dict[str, Any]) -> tuple[str, str]:
            from .media import VIDEO_EXTENSIONS
            path = Path(options.get("input_path", "")).expanduser().resolve()
            if path.suffix.casefold() in VIDEO_EXTENSIONS:
                return str(path), str(options.get("sequence") or path.name)
            return str(path if path.is_dir() else path.parent), str(options.get("sequence", ""))

        def add_current_to_queue(self) -> None:
            if self._plan is None or self._is_running():
                self.queue_note.setText("Load a valid source and settings before adding the current job.")
                return
            job = self.queue.add(self.options())
            index = self.sequence_combo.currentIndex()
            if 0 <= index < len(self._sequences):
                self._queue_sources[job.id] = self._sequences[index]
            self.workspace_tabs.setCurrentIndex(1)
            self._render_queue()

        def add_paths_to_queue(self, paths: list[str]) -> None:
            """Inspect all selected sources off-thread, capturing controls at add time."""
            if not paths:
                return
            template = json.loads(json.dumps(self.options()))
            template.pop("sequence", None)
            self.workspace_tabs.setCurrentIndex(1)
            self._batch_inspections += 1
            self._render_queue()
            self.queue_note.setText("Inspecting sources to add…")

            def inspect() -> Any:
                from .batch import discover_batch_sources
                return discover_batch_sources(paths)

            def loaded(result: Any) -> None:
                self._batch_inspections -= 1
                seen = {self._queue_identity(job.options) for job in self.queue.jobs
                        if job.status in {"pending", "running"}}
                added, duplicates = 0, result.duplicate_count
                for sequence in result.sequences:
                    options = json.loads(json.dumps(template))
                    # The first image identifies only its sequence, even when
                    # other sequences/movies in the same folder are unreadable.
                    options.update(input_path=str(sequence.video_path or sequence.files[0]), sequence=sequence.id)
                    options["generation_width"], options["generation_height"] = _generation_size_for_height(
                        sequence.width, sequence.height, template["generation_height"],
                    )
                    identity = self._queue_identity(options)
                    if identity in seen:
                        duplicates += 1
                        continue
                    seen.add(identity)
                    job = self.queue.add(options)
                    self._queue_sources[job.id] = sequence
                    added += 1
                for message in result.errors:
                    self._append_log(f"Batch source error: {message}")
                self._render_queue()
                summary = f"Added {added} shot(s)."
                if duplicates:
                    summary += f" {duplicates} duplicate selection(s) skipped."
                if result.errors:
                    summary += f" {len(result.errors)} source error(s); see log."
                self.queue_note.setText(summary)

            def failed(message: str) -> None:
                self._batch_inspections -= 1
                self._render_queue()
                self.queue_note.setText(f"Could not inspect batch sources: {message}")
                self._append_log(f"Batch source error: {message}")

            self._task(inspect, loaded, failed)

        def _browse_queue_files(self) -> None:
            from .media import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS

            def patterns(extensions: Any) -> str:
                return " ".join(f"*{ext} *{ext.upper()}" for ext in sorted(extensions))
            filters = ";;".join([
                f"All supported sources ({patterns(SUPPORTED_EXTENSIONS)})",
                f"MOV / MP4 / MXF files ({patterns(VIDEO_EXTENSIONS)})",
                "EXR sequences (*.exr *.EXR)",
                f"Image sequences ({patterns(IMAGE_EXTENSIONS)})",
            ])
            paths, _filter = QFileDialog.getOpenFileNames(self, "Select multiple movies or sequence frames", "", filters)
            if paths:
                self.add_paths_to_queue(paths)

        def _browse_queue_folders(self) -> None:
            dialog = QFileDialog(self, "Select sequence folders · Ctrl / Shift for multiple")
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setLabelText(QFileDialog.DialogLabel.Accept, "Add selected folders")
            for view in (*dialog.findChildren(QListView), *dialog.findChildren(QTreeView)):
                view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
            if dialog.exec():
                self.add_paths_to_queue(dialog.selectedFiles())

        def _browse_queue_folder(self) -> None:
            self._browse_queue_folders()

        def _browse_queue_parent(self) -> None:
            path = QFileDialog.getExistingDirectory(self, "Scan a parent folder for movies and nested image sequences")
            if path:
                self.add_paths_to_queue([path])

        def _render_queue(self) -> None:
            selected = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.queue_tree.selectedItems()}
            self.queue_tree.clear()
            for job in self.queue.jobs:
                options = job.options
                path = options.get("input_path", "")
                source = self._queue_sources.get(job.id)
                if source is not None:
                    label = source.video_path.name if source.video_path else f"{source.folder.name} / {source.id}"
                    kind = source.files[0].suffix[1:].upper()
                    try:
                        from .sequences import select_sequence_indices
                        selected_count = len(select_sequence_indices(source, options.get("frame_skip", 0),
                                             options.get("frame_cap", 0), options.get("source_start"), options.get("source_end")))
                        count = f"{selected_count:,}"
                        frame_detail = f"Frames: {selected_count:,} selected / {source.count:,} source"
                    except ValueError as exc:
                        count, frame_detail = "—", str(exc)
                else:
                    label, kind, count, frame_detail = Path(path).name, Path(path).suffix[1:].upper(), "—", ""
                item = QTreeWidgetItem([label, kind, count, job.status.capitalize()])
                item.setData(0, Qt.ItemDataRole.UserRole, job.id)
                item.setToolTip(0, f"{path}\nSequence: {options.get('sequence', '')}\n{job.message}")
                item.setToolTip(2, frame_detail)
                item.setToolTip(3, job.output_dir or job.message)
                self.queue_tree.addTopLevelItem(item)
                item.setSelected(job.id in selected)
            pending = sum(job.status == "pending" for job in self.queue.jobs)
            self.queue_start.setEnabled(pending > 0 and not self._is_running() and not self.queue.running and not self._batch_inspections)
            self.queue_pause.setEnabled(self.queue.running)
            self.queue_note.setText(f"{pending} pending · " + ("Running sequentially" if self.queue.running else "Paused / ready"))
            self.workspace_tabs.setTabText(1, f"Batch queue ({len(self.queue.jobs)})" if self.queue.jobs else "Batch queue")
            live_ids = {job.id for job in self.queue.jobs}
            self._queue_sources = {key: value for key, value in self._queue_sources.items() if key in live_ids}
            self._update_generate_button()

        def start_queue(self) -> None:
            if self._process is not None or self._batch_inspections:
                return
            self._save_settings()
            self.queue.running = True
            self._advance_queue()

        def pause_queue(self) -> None:
            self.queue.pause()
            self._render_queue()
            self.queue_note.setText("Pausing after the current job." if self._is_running() else "Queue paused.")

        def remove_pending_queue(self) -> None:
            ids = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.queue_tree.selectedItems()}
            self.queue.remove_pending(ids)
            self._render_queue()

        def clear_finished_queue(self) -> None:
            self.queue.clear_finished()
            self._render_queue()

        def _advance_queue(self) -> None:
            if self._process is not None or self._close_after_worker:
                return
            job = self.queue.next()
            self._render_queue()
            if job is not None and not self._start_job(job.options):
                self.queue.finish("failed", self._worker_error or "Job could not start; see log.")
                self._render_queue()
                if self.queue.running:
                    QTimer.singleShot(0, self._advance_queue)

        def _refresh_preset_names(self, selected: str = "") -> None:
            self.preset_combo.clear()
            self.preset_combo.addItems(sorted(self._presets, key=str.casefold))
            self.preset_combo.setCurrentText(selected)

        def save_preset(self) -> None:
            name = self.preset_combo.currentText().strip()
            if not name or len(name) > 120:
                self._append_log("Enter a preset name of 1–120 characters before saving.")
                return
            self._presets[name] = validated_preset(self.options())
            self._refresh_preset_names(name)
            self._save_settings()
            self._append_log(f"Saved preset: {name}")

        def apply_preset(self) -> None:
            name = self.preset_combo.currentText().strip()
            if name not in self._presets or self._is_running():
                return
            self._apply_workflow_settings(validated_preset(self._presets[name]))
            self._schedule_plan()
            self._load_source_preview()
            self._append_log(f"Applied preset: {name}")

        def delete_preset(self) -> None:
            name = self.preset_combo.currentText().strip()
            if name in self._presets:
                del self._presets[name]
                self._refresh_preset_names()
                self._save_settings()
                self._append_log(f"Deleted preset: {name}")

        def _task(self, operation: Any, success: Any, error: Any = None) -> None:
            if self._closing:
                return
            self._task_number += 1
            task = Task(self._task_number, operation)
            self._tasks[self._task_number] = (task, success, error)
            task.signals.completed.connect(self._task_completed)
            self._pool.start(task)

        def _task_completed(self, response: dict[str, Any]) -> None:
            entry = self._tasks.pop(response["key"], None)
            if entry is None:
                return
            _task, success, error = entry
            if self._closing:
                QTimer.singleShot(0, self.close)
                return
            if "error" in response:
                if error is not None:
                    error(response["error"])
                else:
                    self._append_log(response["error"])
            else:
                success(response["value"])

        def _browse_input(self) -> None:
            from .media import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS

            def patterns(extensions: Any) -> str:
                return " ".join(f"*{ext} *{ext.upper()}" for ext in sorted(extensions))

            dialog = QFileDialog(self, "Choose source media")
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setNameFilters([
                f"Supported source media ({patterns(SUPPORTED_EXTENSIONS)})",
                "EXR sequences (*.exr *.EXR)",
                f"Image sequences ({patterns(IMAGE_EXTENSIONS)})",
                f"MOV / MP4 / MXF ({patterns(VIDEO_EXTENSIONS)})",
            ])
            folder_button = QPushButton("Choose folder instead…", dialog)
            dialog.layout().addWidget(folder_button)

            def folder() -> None:
                path = QFileDialog.getExistingDirectory(self, "Choose an EXR or image sequence folder")
                if path:
                    dialog.reject()
                    self.load_input(path)

            folder_button.clicked.connect(folder)
            if dialog.exec() and dialog.selectedFiles():
                self.load_input(dialog.selectedFiles()[0])

        def load_input(self, path: str) -> None:
            if self._is_running():
                return
            if not path.strip():
                return
            path = str(Path(path.strip()).expanduser())
            self._input_path = path
            self.source_path.setText(path)
            self._source_token += 1
            token = self._source_token
            self._preview_token += 1
            self._plan_token += 1
            self._plan_timer.stop()
            self._plan = None
            self._result = None
            self._source_preview = None
            self._matte_preview = None
            self.qc_preview.clear()
            self.preview_canvas.set_image(None)
            self.preview_mode.setCurrentIndex(0)
            self.preview_mode.setEnabled(False)
            self.open_output.setEnabled(False)
            self._update_generate_button()
            self.sequence_combo.blockSignals(True)
            self.sequence_combo.clear()
            self.sequence_combo.setEnabled(False)
            self.sequence_combo.blockSignals(False)
            self._sequences = []
            self.frame_summary.setText("Inspecting sequence…")
            self.output_title.setText("OUTPUT LOCATION · PROVISIONAL UNTIL RESERVED")
            self.output_preview.setText("—")
            self.validation_info.setText("")
            self.source_info.setText("Inspecting source media…")
            self.stage_label.setText("Loading source…")

            def discover() -> Any:
                from .sequences import discover_sequences
                return discover_sequences(Path(path))

            def loaded(sequences: list[Any]) -> None:
                if token != self._source_token:
                    return
                self._sequences = sequences
                self.sequence_combo.blockSignals(True)
                for sequence in sequences:
                    self.sequence_combo.addItem(f"{sequence.id}  ·  {len(sequence.files):,} frames", sequence.id)
                if sequences:
                    self.sequence_combo.setCurrentIndex(0)
                self.sequence_combo.blockSignals(False)
                self.sequence_combo.setEnabled(len(sequences) > 1)
                if not sequences:
                    failed("No supported EXR, image sequence, MOV, MP4 or MXF found in this location.")
                    return
                self._sequence_changed()

            def failed(message: str) -> None:
                if token == self._source_token:
                    self.source_info.setText(message)
                    self.stage_label.setText("Source could not be loaded")
                    self.frame_summary.setText("Choose supported source media")
                    self._append_log(message)

            self._task(discover, loaded, failed)

        def _sequence_changed(self, _index: int = 0) -> None:
            index = self.sequence_combo.currentIndex()
            if index < 0 or index >= len(self._sequences):
                return
            sequence = self._sequences[index]
            self._sync_generation_width()
            numbers = [n for n in sequence.frame_numbers if n is not None]
            frame_range = f"{numbers[0]}–{numbers[-1]}" if numbers else "unnumbered"
            media_type = getattr(sequence, "media_type", "sequence")
            detected_fps = getattr(sequence, "fps", None)
            if media_type == "video":
                video = getattr(sequence, "video_path", None) or sequence.files[0]
                frame_range = f"{Path(video).suffix[1:].upper()} video"
                if detected_fps:
                    frame_range += f" · {detected_fps:g} FPS detected"
                    self.fps.setValue(detected_fps)
            self.source_info.setText(
                f"{sequence.width:,} × {sequence.height:,} px  ·  "
                f"{len(sequence.files):,} source frames  ·  {frame_range}"
            )
            if not self.range_enabled.isChecked():
                self.source_start.setValue(sequence.source_start)
                self.source_end.setValue(sequence.source_end)
            mode = "source frame numbers" if sequence.source_range_mode == "frame-number" else "1-based frame positions"
            self.range_hint.setText(f"Inclusive {mode}: {sequence.source_start}–{sequence.source_end}. Range is applied before skip and cap.")
            self._result = None
            self._matte_preview = None
            self.preview_mode.setCurrentIndex(0)
            self.preview_mode.setEnabled(False)
            self.open_output.setEnabled(False)
            self._load_source_preview()
            self._update_qc_source()
            self._schedule_plan()

        def _update_qc_source(self) -> None:
            index = self.sequence_combo.currentIndex()
            if 0 <= index < len(self._sequences):
                selected = self._plan.get("selected_indices") if self._plan else None
                self.qc_preview.set_source(self._sequences[index], self.color.currentData(), selected)
                self.qc_preview.set_fps(self.fps.value())

        def _load_source_preview(self) -> None:
            index = self.sequence_combo.currentIndex()
            if index < 0 or index >= len(self._sequences):
                return
            self._preview_token += 1
            token = self._preview_token
            sequence = self._sequences[index]
            path = str(getattr(sequence, "video_path", None) or sequence.files[0])
            color = self.color.currentData()

            def loaded(data: Any) -> None:
                if token == self._preview_token:
                    self._source_preview = data
                    self._show_preview()

            def failed(message: str) -> None:
                if token == self._preview_token:
                    self._append_log(f"Preview unavailable: {message}")

            self._task(lambda: _preview_data(path, color), loaded, failed)

        def _color_changed(self, _index: int = 0) -> None:
            self._load_source_preview()
            self._update_qc_source()
            self._schedule_plan()

        def _show_preview(self, _index: int = 0) -> None:
            data = self._matte_preview if self.preview_mode.currentIndex() == 1 else self._source_preview
            self.preview_canvas.set_image(data)

        def options(self) -> dict[str, Any]:
            return {
                "input_path": self._input_path,
                "sequence": self.sequence_combo.currentData(),
                "source_color": self.color.currentData(),
                "fps": self.fps.value(),
                "frame_skip": self.frame_skip.value(),
                "frame_cap": self.frame_cap.value(),
                "temporal_mode": self.temporal_mode.currentData(),
                "source_start": self.source_start.value() if self.range_enabled.isChecked() else None,
                "source_end": self.source_end.value() if self.range_enabled.isChecked() else None,
                "generation_width": self.generation_width.value(),
                "generation_height": self.generation_height.value(),
                "seed": self.seed.value(),
                "suffix": self.suffix.text(),
                "first_frame": None if self.auto_first.isChecked() else self.first_frame.value(),
                "padding": self.padding.value(),
                "precision": self.precision.currentData(),
                "existing": self.existing.currentData(),
                "models": {key: field.text().strip() for key, field in self.model_paths.items()},
                "offload": self.offload.currentData(),
                "low_vram": self.low_vram.isChecked(),
            }

        def _schedule_plan(self, *_args: Any) -> None:
            self._plan_token += 1
            self._plan = None
            self._update_generate_button()
            if self._input_path and self._sequences and not self._is_running():
                self.validation_info.setText("Updating plan…")
                self._plan_timer.start()

        def _refresh_plan(self) -> None:
            if not self._sequences or self._is_running():
                return
            token = self._plan_token
            options = self.options()

            def calculate() -> dict[str, Any]:
                from .core import JobOptions, plan_job
                return plan_job(JobOptions(**options)).to_dict()

            def loaded(plan: dict[str, Any]) -> None:
                if token != self._plan_token:
                    return
                self._plan = plan
                self._update_qc_source()
                self.frame_summary.setText(temporal_summary(plan["selected_count"], plan["padded_count"], plan.get("chunk_count", 1), plan.get("overlap_count", 0), plan.get("temporal_mode", "segmented")))
                self.output_title.setText("OUTPUT LOCATION · PROVISIONAL UNTIL RESERVED")
                self.output_preview.setText(str(plan["output_preview"]))
                first = plan.get("first_frame", 1001)
                resolved_color = plan.get("source_color")
                color_note = f" · {resolved_color}" if resolved_color else ""
                chunk_note = " · segmented: review transitions" if plan.get("chunk_count", 1) > 1 else ""
                if plan.get("experimental_full_range"):
                    chunk_note = " · experimental full range: quality and memory limits may be exceeded"
                self.validation_info.setText(f"{plan['source_count']:,} original frames · first export {first} · RGB grayscale EXR{color_note}{chunk_note}")
                self._update_generate_button()
                self.stage_label.setText("Ready to generate")

            def failed(message: str) -> None:
                if token != self._plan_token:
                    return
                self._plan = None
                self.validation_info.setText(message)
                self.frame_summary.setText("Input needs attention")
                self._update_generate_button()
                self.stage_label.setText("Check the sequence settings")

            self._task(calculate, loaded, failed)

        def _browse_model(self, key: str) -> None:
            path, _filter = QFileDialog.getOpenFileName(
                self, f"Choose {dict(MODEL_FIELDS)[key]}", self.model_paths[key].text() or self.model_root.text(),
                "Model weights (*.safetensors *.pt *.pth *.bin);;All files (*)",
            )
            if path:
                self.model_paths[key].setText(path)

        def _discover_models(self, initial: bool = False) -> None:
            root_path = self.model_root.text().strip()
            if not root_path:
                return
            self.model_status.setText("Searching existing local weights…")

            def discover() -> dict[str, Any]:
                from .models import discover_models
                config = discover_models(Path(root_path))
                return config.to_dict() if hasattr(config, "to_dict") else dict(config)

            def loaded(values: dict[str, Any]) -> None:
                for key, field in self.model_paths.items():
                    if values.get(key) and (not initial or not field.text().strip()):
                        field.setText(str(values[key]))
                self._update_model_badge()
                self._append_log(f"Searched local model library: {root_path}")

            def failed(message: str) -> None:
                self._update_model_badge()
                self._append_log(f"Local model discovery: {message}")

            self._task(discover, loaded, failed)

        def _update_model_badge(self) -> None:
            found = sum(bool(field.text().strip()) for field in self.model_paths.values())
            self.models_group.toggle.setText(f"LOCAL MODEL WEIGHTS  ·  {found}/{len(MODEL_FIELDS)}")
            if hasattr(self, "model_status"):
                self.model_status.setText("All component paths configured." if found == len(MODEL_FIELDS) else "Choose any missing components below. Existing local weights are reused.")

        def _is_running(self) -> bool:
            return self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning

        def generate(self) -> None:
            if self._is_running() or self._plan is None:
                return
            self.queue.pause()
            self._save_settings()
            self._start_job(self.options())

        def _start_job(self, options: dict[str, Any]) -> bool:
            if self._process is not None:
                return False
            self._worker_error = ""
            missing = [label for key, label in MODEL_FIELDS if not Path(options.get("models", {}).get(key, "")).is_file()]
            if missing:
                self.models_group.toggle.setChecked(True)
                message = "Choose existing model files for: " + ", ".join(missing)
                self.model_status.setText(message)
                self._append_log(message)
                self.stage_label.setText("Model files need attention")
                self._worker_error = message
                return False
            self._active_options = json.loads(json.dumps(options))
            if self.runtime is not None and not self.runtime.worker_python.is_file():
                self._worker_error = "Shotbox's Python is missing. Run the Shotbox installer or updater."
                self.stage_label.setText(self._worker_error)
                return False
            from .worker import MatteWorker
            try:
                self._worker = MatteWorker(self, options, self.runtime)
            except OSError as exc:
                self._worker_error = str(exc)
                self.stage_label.setText(f"Could not prepare worker: {exc}")
                return False
            self._worker_directory = self._worker.directory
            self._worker_path = self._worker.path
            self._worker_result = None
            self._worker_error = ""
            self._cancel_requested = False
            self._result = None
            self._matte_preview = None
            self.qc_preview.stop()
            self.preview_mode.setEnabled(False)
            self.preview_mode.setCurrentIndex(0)
            self.open_output.setEnabled(False)
            self._set_busy(True)
            self.stage_label.setText("Starting Alpha Gen…")
            self.progress_bar.setRange(0, 0)
            self._progress_stage = None
            self._timing.start()
            self._history.start(options)
            self._timing_timer.start()
            self._update_timing()
            self._append_log("Starting native LTX-2.5 Alpha Gen in a separate process.")
            self._process = self._worker.process
            self._worker.raw_output.connect(self._record_raw)
            self._worker.stdout_line.connect(self._handle_worker_line)
            self._worker.stderr_line.connect(lambda line: self._append_log(line, persist=False))
            self._worker.finished.connect(self._worker_finished)
            self._worker.start()
            self._render_queue()
            return True

        def _set_busy(self, busy: bool) -> None:
            self._controls.setEnabled(not busy)
            self.drop_area.setEnabled(not busy)
            self.source_path.setEnabled(not busy)
            self.sequence_combo.setEnabled(not busy and len(self._sequences) > 1)
            self._update_generate_button()
            self.cancel_button.setEnabled(busy)
            self.queue_add_current.setEnabled(not busy)
            self._render_queue()

        def _handle_worker_line(self, line: str) -> None:
            event = parse_worker_line(line)
            if event is None:
                self._append_log(line, persist=False)
                return
            kind = event["type"]
            if kind == "progress":
                stage = str(event.get("stage", "Processing")).replace("_", " ").capitalize()
                message = str(event.get("message", ""))
                if not self._cancel_requested:
                    self.stage_label.setText(f"{stage} · {message}" if message else stage)
                current = event.get("current", 0)
                total = event.get("total", 0)
                if isinstance(total, (int, float)) and total > 0 and isinstance(current, (int, float)):
                    key = (str(event.get("stage", "processing")), total)
                    if key != self._progress_stage:
                        self.progress_bar.setValue(0)
                        self._progress_stage = key
                    self.progress_bar.setRange(0, 1000)
                    self.progress_bar.setValue(max(self.progress_bar.value(), max(0, min(1000, int(current / total * 1000)))))
                    self._timing.observe(str(event.get("stage", "processing")), current, total)
                    self._history.observe(str(event.get("stage", "processing")), current, total)
                else:
                    self.progress_bar.setRange(0, 0)
                    self._timing.observe(str(event.get("stage", "processing")), 0, 0)
                self.progress_bar.setToolTip(f"{stage} progress; each processing stage has its own count.")
                self._update_timing()
                if message:
                    self._append_log(f"{stage}: {message}", persist=False)
            elif kind == "result":
                result = event.get("result")
                if isinstance(result, dict):
                    self._worker_result = result
            elif kind == "error":
                self._worker_error = str(event.get("message", "Processing failed"))
                self._append_log(self._worker_error, persist=False, level="error")
            elif kind == "cancelled":
                self._cancel_requested = True
                self.queue.pause()
                self._append_log(str(event.get("message", "Processing cancelled")), persist=False)

        def _worker_finished(self, code: int, status: Any) -> None:
            self.progress_bar.setRange(0, 100)
            self._timing.finish()
            self._timing_timer.stop()
            self._update_timing()
            outcome, outcome_message = "failed", self._worker_error
            if code == 0 and status == QProcess.ExitStatus.NormalExit and self._worker_result:
                self._result = self._worker_result
                self.stage_label.setText(f"Complete · {self._result.get('frame_count', 0):,} RGB matte frames exported")
                self.progress_bar.setValue(100)
                self.output_preview.setText(str(self._result.get("output_dir", "")))
                self.output_title.setText("OUTPUT LOCATION · COMPLETED EXPORT")
                self.open_output.setEnabled(bool(self._result.get("output_dir")))
                self._append_log(f"Complete: {self._result.get('output_dir', '')}")
                outcome = "complete"
                if self._history.record_success(self._timing):
                    self.settings.setValue("timing_history", json.dumps(self._history.records))
                    self.settings.sync()
                self._update_timing()
                color = (self._active_options or {}).get("source_color", self.color.currentData())
                self.qc_preview.set_result(self._result, color)
                self._load_completed_previews()
            elif self._cancel_requested:
                self.stage_label.setText("Cancelled · output is not marked complete")
                self.progress_bar.setValue(0)
                self._append_log("Cancelled. Any partial output is not a completed export.")
                outcome = "cancelled"
            else:
                message = self._worker_error or f"Worker exited with code {code}. See the process log."
                self.stage_label.setText("Generation failed · see process log")
                self.validation_info.setText(message)
                self.progress_bar.setValue(0)
                self.log_group.toggle.setChecked(True)
                self._append_log(message, level="error")
                outcome_message = message
            self._set_busy(False)
            self._process = None
            if self._worker is not None:
                self._worker.cleanup()
                self._worker = None
            self._worker_directory = None
            self._worker_path = None
            for stage, seconds in self._timing.stage_totals.items():
                self._append_log(f"Stage timing: {stage.replace('_', ' ')} {seconds:.1f}s")
            if self.queue.active_id is not None:
                self.queue.finish(outcome, outcome_message, str((self._result or {}).get("output_dir", "")))
            self._render_queue()
            if self._close_after_worker:
                self.close()
            else:
                # Keep the completed folder visible until the user changes settings.
                self._update_generate_button()
                if self.queue.running:
                    QTimer.singleShot(0, self._advance_queue)

        def _load_completed_previews(self) -> None:
            result = dict(self._result or {})
            self._preview_token += 1
            token = self._preview_token
            color = (self._active_options or {}).get("source_color", self.color.currentData())

            def load() -> tuple[Any, Any]:
                source = result.get("source_preview") or result.get("first_source")
                matte = result.get("matte_preview") or result.get("first_output")
                return (
                    _preview_data(str(source), color, prepared=bool(result.get("source_preview"))) if source else None,
                    _preview_data(str(matte), color, matte=True) if matte else None,
                )

            def loaded(images: tuple[Any, Any]) -> None:
                if token != self._preview_token:
                    return
                source, matte = images
                if source is not None:
                    self._source_preview = source
                self._matte_preview = matte
                self.preview_mode.setEnabled(matte is not None)
                self.preview_mode.setCurrentIndex(1 if matte is not None else 0)
                self._show_preview()

            self._task(load, loaded, lambda message: self._append_log(f"Completed preview unavailable: {message}"))

        def cancel(self) -> None:
            self.queue.pause()
            self._render_queue()
            if not self._is_running() or self._cancel_requested:
                return
            self._cancel_requested = True
            self.cancel_button.setEnabled(False)
            self.stage_label.setText("Cancelling · waiting for the current operation…")
            self._append_log("Cancellation requested; waiting for native inference to stop.")
            if self._worker is not None:
                self._worker.cancel()

        def _open_output(self) -> None:
            if self._result and self._result.get("output_dir"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result["output_dir"])))

        def _initialize_log(self) -> None:
            from .export import job_storage_root
            try:
                directory = self.runtime.state_directory / "logs" if self.runtime else job_storage_root().parent / "ui"
                directory.mkdir(parents=True, exist_ok=True)
                self._log_path = directory / f"session-{uuid.uuid4().hex}.log"
                self._log_path.touch()
                self._append_log(f"ShotBox Matte build {self.build_label.text()} · full log: {self._log_path}")
            except OSError as exc:
                self._log_path = None
                self._append_log(f"Warning: persistent UI log unavailable: {exc}", persist=False)

        def _record_raw(self, text: str | bytes) -> None:
            if text and self._log_path is not None:
                try:
                    with self._log_path.open("ab") as handle:
                        handle.write(text.encode("utf-8") if isinstance(text, str) else text)
                except OSError as exc:
                    self._log_path = None
                    self._append_log(f"Warning: persistent log could not be written: {exc}", persist=False)

        def _copy_log(self) -> None:
            text = self.log.toPlainText()
            if self._log_path is not None:
                try:
                    text = self._log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
            QApplication.clipboard().setText(text)

        def _open_logs(self) -> None:
            from .export import job_storage_root
            directory = self._log_path.parent.parent if self._log_path else job_storage_root().parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

        def _update_timing(self) -> None:
            self.timing_label.setText(self._timing.text() + " · " + self._history.text(self._timing))
            if self._timing.stage_totals:
                self.timing_label.setToolTip("\n".join(f"{stage.replace('_', ' ')}: {seconds:.1f}s" for stage, seconds in self._timing.stage_totals.items()))

        def _append_log(self, message: str, persist: bool = True, level: str | None = None) -> None:
            if message.strip():
                if persist:
                    self._record_raw(message.rstrip() + "\n")
                category = level or log_level(message)
                prefix = {"optional": "[OPTIONAL LIBRARY] ", "warning": "[WARNING] ", "error": "[ERROR] "}.get(category, "")
                self.log.moveCursor(QTextCursor.MoveOperation.End)
                self.log.insertPlainText(prefix + message.rstrip() + "\n")
                self.log.ensureCursorVisible()

        def _apply_workflow_settings(self, raw: dict[str, Any]) -> None:
            values = validated_preset(raw)
            for key, widget in {
                "fps": self.fps, "frame_skip": self.frame_skip, "frame_cap": self.frame_cap,
                "generation_width": self.generation_width, "generation_height": self.generation_height,
                "seed": self.seed, "padding": self.padding,
                "source_start": self.source_start, "source_end": self.source_end,
            }.items():
                if key in values and values[key] is not None:
                    widget.setValue(values[key])
            for key, widget in {
                "source_color": self.color, "precision": self.precision,
                "existing": self.existing, "offload": self.offload, "temporal_mode": self.temporal_mode,
            }.items():
                if key in values:
                    widget.setCurrentIndex(widget.findData(values[key]))
            self._update_temporal_mode_hint()
            if "suffix" in values:
                self.suffix.setText(values["suffix"])
            if "first_frame" in values:
                self.auto_first.setChecked(values["first_frame"] is None)
                if values["first_frame"] is not None:
                    self.first_frame.setValue(values["first_frame"])
            if "low_vram" in values:
                self.low_vram.setChecked(values["low_vram"])
            if "source_start" in values or "source_end" in values:
                enabled = values.get("source_start") is not None or values.get("source_end") is not None
                self.range_enabled.setChecked(enabled)
                self.source_start.setEnabled(enabled)
                self.source_end.setEnabled(enabled)
            self._sync_generation_width()

        def _restore_settings(self) -> None:
            raw = self.settings.value("preferences", "")
            try:
                values = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                values = {}
            if not isinstance(values, dict):
                values = {}
            # Preserve explicit model paths across preference versions. Only
            # paths actually inside a previous portable app are relocated.
            if self._portable_directory is not None:
                values = _portable_preferences(values, self._portable_directory)
            from .runtime import configured_model_preferences
            try:
                values = configured_model_preferences(values, self.runtime)
            except (OSError, ValueError, AttributeError) as exc:
                self._append_log(f"Model configuration needs attention: {exc}")
            # Each launch starts with Version up, including older preferences
            # that remembered Overwrite. Explicit preset selections still apply.
            values["existing"] = "version-up"
            self._apply_workflow_settings(values)
            model_root = values.get("model_root", self._default_model_root)
            self.model_root.setText(model_root if isinstance(model_root, str) else self._default_model_root)
            models = values.get("models", {})
            models = models if isinstance(models, dict) else {}
            for key, field in self.model_paths.items():
                model = models.get(key)
                field.setText(model if isinstance(model, str) else "")
            self._update_model_badge()
            try:
                stored_presets = json.loads(self.settings.value("presets", "{}"))
            except (ValueError, TypeError):
                stored_presets = {}
            self._presets = validated_presets(stored_presets)
            self._refresh_preset_names()
            try:
                stored_history = json.loads(self.settings.value("timing_history", "{}"))
            except (ValueError, TypeError):
                stored_history = {}
            self._history = JobHistory(stored_history)
            self._update_timing()
            geometry = self.settings.value("geometry")
            if geometry is not None:
                try:
                    self.restoreGeometry(geometry)
                except TypeError:
                    pass

        def _save_settings(self) -> None:
            values = self.options()
            values.pop("input_path", None)
            values.pop("sequence", None)
            values.pop("existing", None)
            values["model_root"] = self.model_root.text()
            values["model_paths_version"] = MODEL_PATHS_VERSION
            if self._portable_directory is not None:
                values["portable_dir"] = str(self._portable_directory)
            self.settings.setValue("preferences", json.dumps(values))
            self.settings.setValue("presets", json.dumps(self._presets))
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                self._append_log(f"Preferences could not be saved to {self.settings.fileName()}. Check that the application folder is writable.")

        def is_busy(self) -> bool:
            return bool(self._process is not None or self._tasks or self.queue.running
                        or self.qc_preview._active is not None)

        def closeEvent(self, event: Any) -> None:
            self._save_settings()
            self._closing = True
            self.queue.pause()
            self._plan_timer.stop()
            self.qc_preview.dispose()
            if self._is_running():
                self._close_after_worker = True
                self.cancel()
                event.ignore()
            elif self._tasks or self.qc_preview._active is not None:
                QTimer.singleShot(50, self.close)
                event.ignore()
            else:
                event.accept()

    _QT_TYPES = {"MainWindow": MainWindow, "QApplication": QApplication, "Task": Task}
    return _QT_TYPES


STYLE = """
QMainWindow, QWidget { background: #16181c; color: #e6e8ed; font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 12px; }
QFrame#card { background: #202329; border: 1px solid #30343c; border-radius: 10px; }
QFrame#card > QWidget { background: transparent; }
QLabel { background: transparent; }
QLabel#wordmark { color: #f7f7f9; font-size: 24px; font-weight: 750; letter-spacing: 3px; }
QLabel#muted { color: #9299a6; font-size: 11px; }
QLabel#badge { background: #32251f; color: #f88a61; border: 1px solid #6c3c2c; border-radius: 5px; padding: 8px 12px; font-size: 10px; font-weight: 650; letter-spacing: 1px; }
QLabel#sectionTitle { color: #9299a6; font-size: 9px; font-weight: 650; letter-spacing: 1px; }
QLabel#frameSummary { font-size: 15px; font-weight: 650; }
QLabel#stageLabel { font-weight: 600; }
QLabel#previewCaption { color: #7f8795; padding: 9px 16px; font-size: 10px; }
QFrame#dropArea { background: #241e1c; border: 1px dashed #94523a; border-radius: 10px; }
QFrame#dropArea[dragging="true"] { background: #3d2a21; border: 2px solid #F5602F; }
QLabel#dropHeadline { font-size: 17px; font-weight: 650; }
QTabWidget::pane { border: none; padding-top: 8px; }
QTabBar::tab { background: #202329; color: #9299a6; padding: 10px 20px; border-bottom: 2px solid #30343c; font-weight: 600; }
QTabBar::tab:selected { color: #ffffff; border-bottom-color: #F5602F; background: #2b2522; }
QTabBar::tab:hover { color: #ffffff; }
QTreeWidget { background: #15171b; alternate-background-color: #1c1f25; border: 1px solid #30343c; border-radius: 5px; }
QTreeWidget::item { padding: 9px 5px; }
QTreeWidget::item:selected { background: #5d3528; }
QHeaderView::section { background: #272b32; color: #aeb6c3; border: none; padding: 7px 5px; font-size: 10px; font-weight: 600; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #15171b; border: 1px solid #373c45; border-radius: 5px; padding: 6px 8px; min-height: 19px; selection-background-color: #F5602F; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #F5602F; }
QComboBox::drop-down { border: none; width: 23px; }
QComboBox QAbstractItemView { background: #202329; border: 1px solid #444a55; selection-background-color: #5d3528; }
QPushButton { background: #30343c; border: 1px solid #454b55; border-radius: 6px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #3d434d; border-color: #646e7c; }
QPushButton:pressed { background: #23272d; }
QPushButton#generateButton { background: #F5602F; color: white; border: 1px solid #F5602F; padding: 13px 18px; }
QPushButton#generateButton:hover { background: #ff7548; }
QPushButton:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: #646b76; border-color: #30343c; background: #23262c; }
QPushButton#generateButton:disabled { background: #553126; color: #9a776a; border-color: #553126; }
QToolButton { background: transparent; border: none; text-align: left; color: #c4c9d3; font-size: 10px; font-weight: 700; letter-spacing: 1px; padding: 4px 0; }
QToolButton:hover { color: #F5602F; }
QCheckBox { background: transparent; spacing: 7px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #525b69; border-radius: 3px; background: #15171b; }
QCheckBox::indicator:checked { background: #F5602F; border-color: #F5602F; }
QProgressBar { border: none; border-radius: 2px; background: #30343c; }
QProgressBar::chunk { background: #F5602F; border-radius: 2px; }
QTextEdit { background: #111317; color: #b4bcc9; border: 1px solid #30343c; border-radius: 5px; padding: 7px; font-family: 'DejaVu Sans Mono', monospace; font-size: 10px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #414752; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: transparent; width: 8px; }
QToolTip { background: #272b33; color: #e6e8ed; border: 1px solid #555d6c; padding: 5px; }
"""


def create_window(settings: Any = None, *, runtime: Any = None) -> Any:
    """Create a window; a QApplication must already exist on the calling thread."""
    return _build_qt_types()["MainWindow"](settings=settings, runtime=runtime)
