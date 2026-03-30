from __future__ import annotations

# page_nukedash.py
"""
ShotBox NukeDash Page
"""
import os
import json
import hashlib
import random
import time
from pathlib import Path

from PyQt6 import QtWidgets, uic
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot, Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6.QtWidgets import (
    QMainWindow,
    QProgressBar,
    QDialog,
    QMenu,
    QVBoxLayout,
    QLabel,
    QFrame,
    QWidget,
    QSpacerItem,
    QSizePolicy,
    QMessageBox,
)

import widgets
import http_help
import filesIO
import nuke_detector
import project_load_profiler
from nuke_lock_utils import display_owner_name, parse_lock_info
from settings import get_settings_manager
from timeline_matchmove_dialog import TimelineMatchmoveCandidate, TimelineMatchmoveDialog


# Get the directory where this script is located (for cross-platform path handling)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_ROOT_DIRECTORY_NAMES = {"vfx", "nuke"}


class LoadingDialog(QDialog):
    """A popup loading dialog with an animated progress bar."""
    
    def __init__(self, parent=None, message="Loading..."):
        super().__init__(parent)
        self.setWindowTitle("Loading")
        self.setFixedSize(350, 120)
        self._parent_ref = None
        
        # Make it a tool window that floats above parent
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        
        # Set object name for QSS styling
        self.setObjectName("LoadingDialog")
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        
        # Message label
        self._label = QLabel(message)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)
        
        # Detail label (for showing current item)
        self._detail_label = QLabel("")
        self._detail_label.setObjectName("detail_label")
        self._detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._detail_label)
        
        # Progress bar
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setMinimumWidth(300)
        layout.addWidget(self._progress)
    
    def set_message(self, message: str):
        """Update the loading message."""
        self._label.setText(message)
        # Process events to update UI immediately
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
    
    def set_detail(self, detail: str):
        """Update the detail text (current item being loaded)."""
        self._detail_label.setText(detail)
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
    
    def set_progress(self, current: int, total: int):
        """Set determinate progress."""
        self._progress.setRange(0, total)
        self._progress.setValue(current)
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
    
    def set_indeterminate(self):
        """Set indeterminate (animated) mode."""
        self._progress.setRange(0, 0)
    
    def center_on_parent(self):
        """Re-center on parent window."""
        if self._parent_ref:
            parent_geo = self._parent_ref.geometry()
            x = parent_geo.x() + (parent_geo.width() - self.width()) // 2
            y = parent_geo.y() + (parent_geo.height() - self.height()) // 2
            self.move(x, y)
    
    def show_loading(self, parent=None, message=None):
        """Show the dialog centered on the parent window."""
        if parent:
            self._parent_ref = parent
        
        self.center_on_parent()
        
        if message:
            self._label.setText(message)
        
        self._detail_label.setText("")
        self.set_indeterminate()
        self.show()
        self.raise_()
        self.activateWindow()
        # Process events to ensure dialog is painted
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
    
    def hide_loading(self):
        """Hide the dialog."""
        self._detail_label.setText("")
        self.hide()


class ChunkedJobLoader(QObject):
    """Loads job data in chunks to keep UI responsive."""
    
    finished = pyqtSignal()
    progress = pyqtSignal(int, int, str)  # current, total, message
    detail = pyqtSignal(str)  # detail message for current item
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._process_next)
        self._current_idx = 0
        self._total = 0
        self._callback = None
        self._batch_size = 1  # Process one item at a time for smoother UI
    
    def start_loading(self, tasks: list, callback=None, batch_size=1):
        """
        Start processing tasks. Each task is a tuple of (callable, description).
        callback is called when all done.
        """
        self._queue = tasks
        self._total = len(tasks)
        self._current_idx = 0
        self._callback = callback
        self._batch_size = batch_size
        
        if self._total == 0:
            self.finished.emit()
            if callback:
                callback()
            return
        
        # Emit initial progress
        self.progress.emit(0, self._total, f"Loading 0/{self._total}...")
        
        # Start processing - use 1ms timer to yield to event loop between tasks
        self._timer.start(1)
    
    def _process_next(self):
        """Process the next task in the queue."""
        if self._current_idx >= self._total:
            self._timer.stop()
            self.finished.emit()
            if self._callback:
                self._callback()
            return
        
        # Get task - can be callable or tuple of (callable, description)
        task_item = self._queue[self._current_idx]
        if isinstance(task_item, tuple):
            task, description = task_item
            self.detail.emit(description)
        else:
            task = task_item
            description = ""
        
        # Emit progress
        self.progress.emit(self._current_idx + 1, self._total, f"Loading {self._current_idx + 1}/{self._total}...")
        
        # Execute the task
        try:
            task()
        except Exception as e:
            pass  # Silent fail
        
        self._current_idx += 1
    
    def stop(self):
        """Stop loading."""
        self._timer.stop()
        self._queue = []


def _sig(obj) -> str:
    try:
        payload = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        payload = str(obj)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class ApiWorker(QObject):
    data_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.api = http_help.DjangoAPI()

    @pyqtSlot()
    def fetch(self):
        try:
            data = self.api.getAPI()
            if not isinstance(data, list):
                raise ValueError("API did not return a list")
            self.data_ready.emit(data)
        except Exception as e:
            self.error.emit(str(e))


def resolve_effective_shots_layout_mode(
    base_mode: str,
    compact_enabled: bool,
    available_width: int,
    breakpoint: int = 1200,
) -> str:
    """Resolve the active layout mode from preferences plus compact-view state."""
    normalized = "grid" if str(base_mode).lower() == "grid" else "list"
    if compact_enabled:
        return "grid"
    return normalized


def _normalize_task_style(style: str) -> str:
    return "checklist" if str(style).lower() == "checklist" else "card"


class page_nukedash(QMainWindow):
    jobs_data_updated = pyqtSignal(list)
    active_job_changed = pyqtSignal(dict)
    active_timeline_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        ui_path = os.path.join(SCRIPT_DIR, "basic.ui")
        uic.loadUi(ui_path, self)

        self._load_timing_context = None
        self._load_timing_started_at = None
        self._set_loaded_time_text("--")


        # Populate preview size options
        self.comboBox_preview_size.addItems(["NoThumb", "Tiny", "Small", "Medium", "Large"])
        self.comboBox_preview_size.setCurrentText("Medium")

        sort_combo = getattr(self, "comboBox_sort", None)
        if sort_combo is not None:
            sort_combo.clear()
            sort_combo.addItem("A-Z", "title_asc")
            sort_combo.addItem("Z-A", "title_desc")
            sort_combo.addItem("Highest Tasks", "task_count_desc")
            sort_combo.addItem("Lowest Tasks", "task_count_asc")
            default_sort_index = sort_combo.findData("title_asc")
            sort_combo.setCurrentIndex(default_sort_index if default_sort_index >= 0 else 0)

        self.comboBox_jobs.currentIndexChanged.connect(self._on_job_selected)
        self.filesIO = filesIO.Folders()

        if hasattr(self, "btn_open_timeline_assets") and self.btn_open_timeline_assets:
            self.btn_open_timeline_assets.clicked.connect(self._on_open_timeline_assets_clicked)
            self.btn_open_timeline_assets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.btn_open_timeline_assets.customContextMenuRequested.connect(
                self._on_timeline_assets_context_menu
            )
        if hasattr(self, "btn_open_job_assets") and self.btn_open_job_assets:
            self.btn_open_job_assets.clicked.connect(self._on_open_job_assets_clicked)

        # --- Loading Dialog Setup ---
        self._loading_dialog = LoadingDialog(self, "Loading ShotBox...")
        self._initial_load = True  # Track if this is the first load
        
        # --- Chunked Loader Setup ---
        self._chunked_loader = ChunkedJobLoader(self)
        self._chunked_loader.progress.connect(self._on_load_progress)
        self._chunked_loader.detail.connect(self._on_load_detail)
        self._chunked_loader.finished.connect(self._on_load_finished)

        # Define mapping of labels to pixel widths
        self._preview_size_map = {
            "NoThumb": 240,
            "Tiny" : 80,
            "Small": 160,
            "Medium": 240,
            "Large": 360
        }

        self._card_spacing = 8
        self._row_height = 0

        # Connect change signal
        self.comboBox_preview_size.currentTextChanged.connect(self._on_preview_size_changed)



        self.show_hidden_shots = bool(getattr(self, "checkBox_hidden_shot", None) and self.checkBox_hidden_shot.isChecked())
        self.show_hidden_tasks = bool(getattr(self, "checkBox_hidden_tasks", None) and self.checkBox_hidden_tasks.isChecked())
        self.show_to_conform = bool(getattr(self, "checkBox_show_to_conform", None) and self.checkBox_show_to_conform.isChecked())
        # print(self.show_hidden_shots, self.show_hidden_tasks)  # Debug only

        enable_filters_checkbox = getattr(self, "checkBox_enable_filters", None)

        if hasattr(self, "checkBox_hidden_shot") and self.checkBox_hidden_shot:
            self.checkBox_hidden_shot.stateChanged.connect(self._on_toggle_hidden)
        if hasattr(self, "checkBox_hidden_tasks") and self.checkBox_hidden_tasks:
            self.checkBox_hidden_tasks.stateChanged.connect(self._on_toggle_hidden)
        if hasattr(self, "checkBox_show_to_conform") and self.checkBox_show_to_conform:
            self.checkBox_show_to_conform.stateChanged.connect(self._on_toggle_to_conform)
        
        # --- Filtering Setup ---
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(500)  # 500ms after typing stops
        self._search_timer.timeout.connect(self._apply_filters)
        
        # Timer for debouncing hidden checkbox toggle
        self._toggle_hidden_timer = QTimer(self)
        self._toggle_hidden_timer.setSingleShot(True)
        self._toggle_hidden_timer.setInterval(200)  # 200ms debounce
        self._toggle_hidden_timer.timeout.connect(self._do_toggle_hidden)
        
        # --- Scroll debouncing for filter application ---
        self._is_scrolling = False
        self._scroll_end_timer = QTimer(self)
        self._scroll_end_timer.setSingleShot(True)
        self._scroll_end_timer.setInterval(150)  # 150ms after scroll stops
        self._scroll_end_timer.timeout.connect(self._on_scroll_ended)
        self._pending_filter_apply = False
        
        self.Search_bar.textChanged.connect(self._on_search_changed)
        if sort_combo is not None:
            sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.comboBox_sort_artist.currentIndexChanged.connect(self._apply_filters)
        self.comboBox_sort_status.currentIndexChanged.connect(self._on_status_combo_index_changed)
        
        # Update results count when switching timeline tabs
        self.timelines_tabs.currentChanged.connect(self._on_timeline_tab_changed)

        self.btn_refresh.clicked.connect(self.refresh_clicked)
        
        # --- Make All Previews Button ---
        # Add button programmatically next to refresh button
        from PyQt6.QtWidgets import QPushButton
        #self.btn_make_previews = QPushButton("Make All Previews")
        self.btn_make_previews.setToolTip("Generate previews for all shots with original clips or latest renders")
        self.btn_make_previews.clicked.connect(self._on_make_all_previews_clicked)
        

        self.jobs_container = self.frame_jobs
        self.timelines_layout = self.scrollArea_frame.layout()

        self._thread = QThread(self)
        self._worker = ApiWorker()
        self._worker.moveToThread(self._thread)
        self._worker.data_ready.connect(self._on_data)
        self._worker.error.connect(self._on_error)
        self._thread.start()

        self._timer = QTimer(self)
        self._timer.setInterval(5000) # timer 5
        self._timer.timeout.connect(self._worker.fetch)

        # --- Auto Refresh Checkbox Setup ---
        self.checkBox_refresh.setChecked(False)  # Disabled by default
        self.checkBox_refresh.stateChanged.connect(self._on_auto_refresh_changed)

        self._last_jobs_sig = None
        self._jobs_by_id = {}     # latest API snapshot keyed by id
        self._active_job_id = None
        self._dropdowns_populated = False  # Track if dropdowns are populated
        self._is_chunked_loading = False  # Flag to prevent background updates during loading
        self._pending_refresh_jobs = None  # Store pending refresh data if received during loading
        
        # --- Session Restore Setup ---
        self._settings_manager = get_settings_manager()
        saved_layout_mode = self._settings_manager.get("shots_layout_mode", "list")
        self._shots_layout_mode = "grid" if str(saved_layout_mode).lower() == "grid" else "list"
        self._task_style = _normalize_task_style(
            self._settings_manager.get("nukedash_task_style", "card")
        )
        self._compact_view_enabled = False
        self._compact_auto_grid_breakpoint = 1200
        if hasattr(self, "checkBox_compact_view") and self.checkBox_compact_view:
            self.checkBox_compact_view.stateChanged.connect(self._on_compact_view_changed)
            self.checkBox_compact_view.setChecked(
                bool(self._settings_manager.get("compact_view_enabled", False))
            )
        self._session_restored = False  # Track if session has been restored
        self._pending_scroll_restore = None  # Store scroll position to restore after loading
        self._restoring_filter_controls = False
        self._saved_filter_state = self._load_saved_filter_state()
        self._lock_interval_ms = 45_000
        self._lock_release_grace_sec = 90
        self._owned_locks_by_shot_id = {}
        self._project_load_profiler = project_load_profiler.ProjectLoadProfiler(
            enabled=bool(
                self._settings_manager.get("debug_modes.project_load_profiler", False)
            )
        )
        project_load_profiler.install_profiler(self._project_load_profiler)
        self._project_load_report_timer = QTimer(self)
        self._project_load_report_timer.setSingleShot(True)
        self._project_load_report_timer.timeout.connect(self._emit_project_load_report_if_ready)
        self._project_load_report_poll_ms = 50
        self._project_load_report_grace_seconds = 2.0
        self._task_materialize_batch_size = 1
        self._task_materialize_queue = []
        self._task_materialize_timer = QTimer(self)
        self._task_materialize_timer.setSingleShot(True)
        self._task_materialize_timer.timeout.connect(self._process_task_materialize_queue)
        self._set_task_loading_text("Tasks loaded")

        self._lock_sync_timer = QTimer(self)
        self._lock_sync_timer.setInterval(self._lock_interval_ms)
        self._lock_sync_timer.timeout.connect(self._on_lock_state_sync_tick)

        self._lock_detector_timer = QTimer(self)
        self._lock_detector_timer.setInterval(self._lock_interval_ms)
        self._lock_detector_timer.timeout.connect(self._on_lock_detector_tick)

        self._lock_heartbeat_timer = QTimer(self)
        self._lock_heartbeat_timer.setInterval(self._lock_interval_ms)
        self._lock_heartbeat_timer.timeout.connect(self._on_lock_heartbeat_tick)

        self._apply_saved_filter_state_to_controls()
        if enable_filters_checkbox is not None:
            enable_filters_checkbox.stateChanged.connect(self._on_enable_filters_changed)
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater()
        self._start_load_timing("startup")
        self._set_filter_controls_enabled(self._filters_enabled())
        self._worker.fetch()
        self._start_lock_maintenance_timers()
        
        # Connect scroll detection after UI is loaded
        QTimer.singleShot(100, self._setup_scroll_detection)
    
    def _setup_scroll_detection(self):
        """Connect to scroll area's scrollbar signals to detect scrolling."""
        # Find the scroll area in timelines_tabs
        tabs = self.timelines_tabs
        for i in range(tabs.count()):
            widget = tabs.widget(i)
            if widget:
                # Look for QScrollArea children
                from PyQt6.QtWidgets import QScrollArea
                scroll_areas = widget.findChildren(QScrollArea)
                for scroll_area in scroll_areas:
                    vbar = scroll_area.verticalScrollBar()
                    if vbar:
                        vbar.valueChanged.connect(self._on_scroll_activity)
        
        # Also connect to the main scroll area if it exists
        if hasattr(self, 'scrollArea_frame'):
            scroll_area = self.scrollArea_frame
            if hasattr(scroll_area, 'verticalScrollBar'):
                vbar = scroll_area.verticalScrollBar()
                if vbar:
                    vbar.valueChanged.connect(self._on_scroll_activity)

    def _set_loaded_time_text(self, value: str) -> None:
        label = getattr(self, "label_loaded_time", None)
        if label is not None:
            label.setText(f"Loaded in: {value}")

    def _set_task_loading_text(self, value: str) -> None:
        label = getattr(self, "label_task_loading", None)
        if label is not None:
            label.setText(value)
            label.show()

    def _prepare_task_loading_progress(self, timeline_index: int | None = None) -> None:
        total = 0
        for entry in getattr(self, "_task_materialize_queue", []):
            total += len(entry.get("task_ids", []) or [])
        if total > 0:
            self._start_task_loading_timing(timeline_index)
        else:
            self._finish_task_loading_timing(timeline_index)

    def _active_task_loading_key(self, timeline_index: int | None = None):
        if timeline_index is None and self._task_loading_timeline_key is not None:
            return self._task_loading_timeline_key
        return self._timeline_task_loading_key(timeline_index)

    def _timeline_task_loading_key(self, index: int | None = None):
        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return f"tab-{0 if index is None else index}"
        if index is None:
            index = tabs.currentIndex()
        if index is None:
            index = 0
        widget = tabs.widget(index) if 0 <= index < tabs.count() else None
        if widget is not None:
            name = widget.objectName()
            if name:
                return name
            timeline = getattr(widget, "_last_timeline", None)
            if isinstance(timeline, dict) and timeline.get("id") is not None:
                return f"timeline-{timeline.get('id')}"
        return f"tab-{index}"

    def _start_task_loading_timing(self, timeline_index: int | None = None) -> None:
        self._set_task_loading_text("Loading tasks...")

    def _finish_task_loading_timing(self, timeline_index: int | None = None) -> None:
        self._set_task_loading_text("Tasks loaded")

    def _sync_task_loading_status(self, timeline_index: int | None = None) -> None:
        if getattr(self, "_task_materialize_queue", []):
            self._start_task_loading_timing(timeline_index)
            return
        self._finish_task_loading_timing(timeline_index)

    def _start_load_timing(self, context: str) -> None:
        self._load_timing_context = context
        self._load_timing_started_at = time.perf_counter()
        self._set_loaded_time_text("loading...")

    def _finish_load_timing(self) -> None:
        if self._load_timing_started_at is None:
            self._load_timing_context = None
            return
        elapsed = max(0.0, time.perf_counter() - self._load_timing_started_at)
        self._set_loaded_time_text(f"{elapsed:.1f}s")
        self._load_timing_context = None
        self._load_timing_started_at = None

    def _fail_load_timing(self) -> None:
        if self._load_timing_context is None:
            return
        self._set_loaded_time_text("failed")
        self._load_timing_context = None
        self._load_timing_started_at = None

    def _finish_timed_load_after_data(self) -> None:
        if self._load_timing_context == "manual_refresh":
            self._finish_load_timing()
        elif self._load_timing_context == "startup" and self.comboBox_jobs.count() == 0:
            self._finish_load_timing()

    def _default_saved_filter_state(self) -> dict:
        return {
            "enabled": False,
            "sort_mode": "title_asc",
            "artist_id": None,
            "status_values": [],
            "show_hidden_shots": False,
            "show_hidden_tasks": False,
            "show_to_conform": False,
        }

    def _normalize_saved_filter_state(self, state: dict | None) -> dict:
        normalized = self._default_saved_filter_state()
        if isinstance(state, dict):
            normalized.update(state)
        normalized["enabled"] = bool(normalized.get("enabled", False))
        normalized["sort_mode"] = str(normalized.get("sort_mode", "title_asc") or "title_asc")
        normalized["artist_id"] = normalized.get("artist_id")
        normalized["status_values"] = [
            value
            for value in normalized.get("status_values", []) or []
            if value not in (None, "")
        ]
        normalized["show_hidden_shots"] = bool(normalized.get("show_hidden_shots", False))
        normalized["show_hidden_tasks"] = bool(normalized.get("show_hidden_tasks", False))
        normalized["show_to_conform"] = bool(normalized.get("show_to_conform", False))
        return normalized

    def _load_saved_filter_state(self) -> dict:
        if not hasattr(self, "_settings_manager") or self._settings_manager is None:
            return self._default_saved_filter_state()
        if not self._settings_manager.get("remember_last_session", True):
            return self._default_saved_filter_state()
        saved = self._settings_manager.get("nukedash_filter_state", self._default_saved_filter_state())
        return self._normalize_saved_filter_state(saved)

    def _current_structured_filter_state(self) -> dict:
        sort_combo = getattr(self, "comboBox_sort", None)
        return self._normalize_saved_filter_state(
            {
                "enabled": self._filters_enabled(),
                "sort_mode": (
                    sort_combo.currentData()
                    if sort_combo is not None and sort_combo.currentData() is not None
                    else "title_asc"
                ),
                "artist_id": self.comboBox_sort_artist.currentData(),
                "status_values": sorted(getattr(self, "_status_filter_values", set())),
                "show_hidden_shots": bool(getattr(self, "show_hidden_shots", False)),
                "show_hidden_tasks": bool(getattr(self, "show_hidden_tasks", False)),
                "show_to_conform": bool(getattr(self, "show_to_conform", False)),
            }
        )

    def _persist_filter_state(self) -> None:
        if getattr(self, "_restoring_filter_controls", False):
            return
        if not hasattr(self, "_settings_manager") or self._settings_manager is None:
            return
        if not self._settings_manager.get("remember_last_session", True):
            self._saved_filter_state = self._default_saved_filter_state()
            self._settings_manager.set(
                "nukedash_filter_state", self._saved_filter_state, save=False
            )
            return
        self._saved_filter_state = self._current_structured_filter_state()
        self._settings_manager.set(
            "nukedash_filter_state", self._saved_filter_state, save=False
        )

    def _on_remember_last_session_changed(self, enabled: bool) -> None:
        if not hasattr(self, "_settings_manager") or self._settings_manager is None:
            return
        if enabled:
            self._persist_filter_state()
            return
        self._saved_filter_state = self._default_saved_filter_state()
        self._settings_manager.set("nukedash_filter_state", self._saved_filter_state, save=False)

    def _apply_saved_artist_filter(self) -> None:
        target_artist_id = self._saved_filter_state.get("artist_id")
        combo = getattr(self, "comboBox_sort_artist", None)
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            for index in range(combo.count()):
                if combo.itemData(index) == target_artist_id:
                    combo.setCurrentIndex(index)
                    return
            all_index = combo.findData(None)
            combo.setCurrentIndex(all_index if all_index >= 0 else 0)
        finally:
            combo.blockSignals(False)

    def _apply_saved_status_filter_values(self) -> None:
        if not hasattr(self, "_status_filter_items"):
            return
        target_values = {
            value
            for value in self._saved_filter_state.get("status_values", []) or []
            if value in self._status_filter_items
        }
        self._status_filter_blocked = True
        try:
            for value, item in self._status_filter_items.items():
                item.setCheckState(
                    Qt.CheckState.Checked
                    if value in target_values
                    else Qt.CheckState.Unchecked
                )
        finally:
            self._status_filter_blocked = False
        self._status_filter_values = set(target_values)
        self._update_status_filter_label()

    def _apply_saved_filter_state_to_controls(self) -> None:
        self._restoring_filter_controls = True
        try:
            if hasattr(self, "checkBox_enable_filters") and self.checkBox_enable_filters:
                self.checkBox_enable_filters.blockSignals(True)
                self.checkBox_enable_filters.setChecked(
                    bool(self._saved_filter_state.get("enabled", False))
                )
                self.checkBox_enable_filters.blockSignals(False)
            if hasattr(self, "checkBox_hidden_shot") and self.checkBox_hidden_shot:
                self.checkBox_hidden_shot.blockSignals(True)
                self.checkBox_hidden_shot.setChecked(
                    bool(self._saved_filter_state.get("show_hidden_shots", False))
                )
                self.checkBox_hidden_shot.blockSignals(False)
            if hasattr(self, "checkBox_hidden_tasks") and self.checkBox_hidden_tasks:
                self.checkBox_hidden_tasks.blockSignals(True)
                self.checkBox_hidden_tasks.setChecked(
                    bool(self._saved_filter_state.get("show_hidden_tasks", False))
                )
                self.checkBox_hidden_tasks.blockSignals(False)
            if hasattr(self, "checkBox_show_to_conform") and self.checkBox_show_to_conform:
                self.checkBox_show_to_conform.blockSignals(True)
                self.checkBox_show_to_conform.setChecked(
                    bool(self._saved_filter_state.get("show_to_conform", False))
                )
                self.checkBox_show_to_conform.blockSignals(False)
            sort_combo = getattr(self, "comboBox_sort", None)
            if sort_combo is not None:
                sort_combo.blockSignals(True)
                idx = sort_combo.findData(self._saved_filter_state.get("sort_mode", "title_asc"))
                sort_combo.setCurrentIndex(idx if idx >= 0 else 0)
                sort_combo.blockSignals(False)
            if hasattr(self, "Search_bar") and self.Search_bar:
                self.Search_bar.blockSignals(True)
                self.Search_bar.clear()
                self.Search_bar.blockSignals(False)
            self.show_hidden_shots = bool(
                getattr(self, "checkBox_hidden_shot", None)
                and self.checkBox_hidden_shot.isChecked()
            )
            self.show_hidden_tasks = bool(
                getattr(self, "checkBox_hidden_tasks", None)
                and self.checkBox_hidden_tasks.isChecked()
            )
            self.show_to_conform = bool(
                getattr(self, "checkBox_show_to_conform", None)
                and self.checkBox_show_to_conform.isChecked()
            )
            self._apply_saved_artist_filter()
            self._apply_saved_status_filter_values()
        finally:
            self._restoring_filter_controls = False

    def _current_task_render_state(
        self,
        *,
        filters_enabled: bool | None = None,
        search_text: str | None = None,
        artist_filter=None,
        status_filter_values=None,
        hide_hidden_tasks: bool | None = None,
    ) -> dict:
        return {
            "filters_enabled": self._filters_enabled() if filters_enabled is None else bool(filters_enabled),
            "search_text": (
                self.Search_bar.text().strip().lower()
                if search_text is None
                else str(search_text or "").strip().lower()
            ),
            "artist_filter": self.comboBox_sort_artist.currentData() if artist_filter is None else artist_filter,
            "status_filter_values": sorted(
                set(getattr(self, "_status_filter_values", set()) if status_filter_values is None else status_filter_values)
            ),
            "hide_hidden_tasks": (
                (not self.show_hidden_tasks)
                if hide_hidden_tasks is None
                else bool(hide_hidden_tasks)
            ),
        }

    def _task_matches_filters(
        self,
        task_data: dict,
        *,
        search_text: str = "",
        artist_filter=None,
        status_filter_values=None,
        hide_hidden_tasks: bool = True,
    ) -> bool:
        if not isinstance(task_data, dict):
            return False
        if hide_hidden_tasks and task_data.get("hidden", False):
            return False
        if artist_filter is not None and task_data.get("artist") != artist_filter:
            return False
        status_values = set(status_filter_values or set())
        if status_values and task_data.get("status") not in status_values:
            return False
        if search_text:
            searchable = " ".join(
                str(part or "")
                for part in (task_data.get("title"), task_data.get("notes"))
                if part
            ).lower()
            if search_text not in searchable:
                return False
        return True

    def _visible_task_ids_for_shot(
        self,
        shot_data: dict,
        *,
        search_text: str = "",
        artist_filter=None,
        status_filter_values=None,
        hide_hidden_tasks: bool = True,
        filters_enabled: bool = True,
    ) -> list:
        visible_ids = []
        for task in shot_data.get("tasks", []) or []:
            task_id = task.get("id")
            if task_id is None:
                continue
            if not filters_enabled:
                visible_ids.append(task_id)
                continue
            if self._task_matches_filters(
                task,
                search_text=search_text,
                artist_filter=artist_filter,
                status_filter_values=status_filter_values,
                hide_hidden_tasks=hide_hidden_tasks,
            ):
                visible_ids.append(task_id)
        return visible_ids

    def _shot_matches_search(self, shot_data: dict, search_text: str) -> bool:
        if not search_text:
            return True
        searchable = " ".join(
            str(part or "")
            for part in (shot_data.get("title"), shot_data.get("notes"))
            if part
        ).lower()
        return search_text in searchable

    def _reset_task_materialize_queue(self) -> None:
        timer = getattr(self, "_task_materialize_timer", None)
        if timer is not None:
            timer.stop()
        self._task_materialize_queue = []

    def _enqueue_task_materialization(self, shot_card: widgets.ShotCard, task_ids: list) -> None:
        if not task_ids:
            return
        if not hasattr(self, "_task_materialize_queue"):
            self._task_materialize_queue = []
        self._task_materialize_queue.append(
            {"shot_card": shot_card, "task_ids": list(task_ids)}
        )

    def _process_task_materialize_queue(self, max_widgets: int | None = None) -> None:
        if not hasattr(self, "_task_materialize_queue"):
            self._task_materialize_queue = []
        if self._task_materialize_queue:
            self._start_task_loading_timing()
        batch_size = getattr(self, "_task_materialize_batch_size", 20)
        budget = batch_size if max_widgets is None else max(0, int(max_widgets))
        while budget > 0 and self._task_materialize_queue:
            entry = self._task_materialize_queue[0]
            shot_card = entry.get("shot_card")
            pending_ids = list(entry.get("task_ids", []))
            if shot_card is None:
                self._task_materialize_queue.pop(0)
                continue
            try:
                created_ids = shot_card.materialize_task_ids(pending_ids, max_count=budget)
            except RuntimeError:
                self._task_materialize_queue.pop(0)
                continue
            if created_ids:
                created_set = set(created_ids)
                entry["task_ids"] = [
                    task_id for task_id in pending_ids if task_id not in created_set
                ]
                budget -= len(created_ids)
            else:
                entry["task_ids"] = []
            if not entry["task_ids"]:
                self._task_materialize_queue.pop(0)
        timer = getattr(self, "_task_materialize_timer", None)
        if self._task_materialize_queue and timer is not None:
            timer.start(0)
        self._sync_task_loading_status()

    def _set_project_load_profiler_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if not enabled and self._project_load_profiler.has_active_session():
            self._project_load_profiler.mark_incomplete("disabled")
            self._emit_project_load_report_if_ready(force=True)
        self._project_load_profiler.set_enabled(enabled)

    def _start_project_switch_profile(self, job_data: dict) -> None:
        if not self._project_load_profiler.enabled:
            return
        if self._project_load_profiler.has_active_session():
            self._project_load_profiler.mark_incomplete("replaced_by_new_switch")
            self._emit_project_load_report_if_ready(force=True)
        self._project_load_report_timer.stop()
        self._project_load_profiler.start_session(
            job_data.get("id"), job_data.get("title", "Untitled Job")
        )

    def _complete_project_switch_profile(self) -> None:
        if not self._project_load_profiler.has_active_session():
            return
        self._project_load_profiler.mark_success()
        self._emit_project_load_report_if_ready()

    def _fail_project_switch_profile(self, reason: str) -> None:
        if not self._project_load_profiler.has_active_session():
            return
        self._project_load_profiler.mark_failed(reason)
        self._emit_project_load_report_if_ready(force=True)

    def _emit_project_load_report_if_ready(self, force: bool = False) -> None:
        emitted = self._project_load_profiler.emit_report_if_ready(
            grace_seconds=self._project_load_report_grace_seconds,
            force=force,
        )
        if emitted or not self._project_load_profiler.has_pending_report():
            self._project_load_report_timer.stop()
            return
        self._project_load_report_timer.start(self._project_load_report_poll_ms)
    
    def _on_scroll_activity(self, value):
        """Called when user scrolls - defer expensive operations."""
        self._is_scrolling = True
        # Reset the scroll end timer
        self._scroll_end_timer.stop()
        self._scroll_end_timer.start()
    
    def _on_scroll_ended(self):
        """Called when scrolling has stopped for 150ms."""
        self._is_scrolling = False
        # If there's a pending filter apply, do it now
        if self._pending_filter_apply:
            self._pending_filter_apply = False
            self._apply_filters()
        
        # Save session state when scrolling stops (but not during initial load)
        if not self._initial_load and self._session_restored:
            self._save_session_state()

    def _start_lock_maintenance_timers(self):
        for timer in (self._lock_sync_timer, self._lock_detector_timer, self._lock_heartbeat_timer):
            jitter_ms = random.randint(0, 5000)
            QTimer.singleShot(jitter_ms, timer.start)

    def _normalize_script_path(self, value: str | None) -> str:
        if not value:
            return ""
        return os.path.normcase(os.path.normpath(str(value)))

    def _active_job_data(self) -> dict | None:
        jobs_by_id = getattr(self, "_jobs_by_id", None)
        active_job_id = getattr(self, "_active_job_id", None)
        if isinstance(jobs_by_id, dict) and active_job_id in jobs_by_id:
            job_data = jobs_by_id.get(active_job_id)
            if isinstance(job_data, dict):
                return job_data

        pending_job_data = getattr(self, "_pending_job_data", None)
        if isinstance(pending_job_data, dict):
            return pending_job_data

        combo = getattr(self, "comboBox_jobs", None)
        combo_job_id = combo.currentData() if combo is not None else None
        if isinstance(jobs_by_id, dict) and combo_job_id in jobs_by_id:
            job_data = jobs_by_id.get(combo_job_id)
            if isinstance(job_data, dict):
                return job_data
        return None

    def _active_timeline_data(self) -> dict | None:
        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return None

        current_widget = tabs.currentWidget()
        current_timeline = getattr(current_widget, "_last_timeline", None)
        if isinstance(current_timeline, dict):
            return current_timeline

        job_data = self._active_job_data()
        timelines = job_data.get("timelines", []) if isinstance(job_data, dict) else []

        if current_widget is not None:
            name = str(current_widget.objectName() or "")
            if name.startswith("timeline-"):
                try:
                    timeline_id = int(name.split("timeline-", 1)[1])
                except ValueError:
                    timeline_id = None
                if timeline_id is not None:
                    for timeline in timelines:
                        if timeline.get("id") == timeline_id:
                            return timeline

        index = tabs.currentIndex()
        if isinstance(index, int) and 0 <= index < len(timelines):
            timeline = timelines[index]
            if isinstance(timeline, dict):
                return timeline
        return None

    def _path_from_base_path(self, value: str | None) -> Path | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            normalized = self.filesIO.convert_path(normalized)
        except Exception:
            pass
        normalized = str(normalized or "").strip()
        return Path(normalized) if normalized else None

    def _first_shot_dir_for_timeline(self, timeline_data: dict | None) -> Path | None:
        shots = timeline_data.get("shots", []) if isinstance(timeline_data, dict) else []
        for shot in shots:
            shot_dir = self._path_from_base_path((shot or {}).get("base_path"))
            if shot_dir is not None:
                return shot_dir
        return None

    def _first_shot_dir_for_job(self, job_data: dict | None) -> Path | None:
        timelines = job_data.get("timelines", []) if isinstance(job_data, dict) else []
        for timeline in timelines:
            shot_dir = self._first_shot_dir_for_timeline(timeline)
            if shot_dir is not None:
                return shot_dir
        return None

    def _infer_vfx_root_from_shot_dir(self, shot_dir: Path | None) -> Path | None:
        if shot_dir is None:
            return None
        timeline_root = shot_dir.parent
        if not timeline_root or timeline_root == shot_dir:
            return None
        if timeline_root.name.lower() in ASSET_ROOT_DIRECTORY_NAMES:
            return timeline_root
        vfx_root = timeline_root.parent
        if not vfx_root or vfx_root == timeline_root:
            return None
        return vfx_root

    def _job_uses_timeline_directories(self, shot_dir: Path | None) -> bool:
        if shot_dir is None:
            return False
        timeline_root = shot_dir.parent
        return bool(
            timeline_root and timeline_root.name.lower() not in ASSET_ROOT_DIRECTORY_NAMES
        )

    def _resolve_job_assets_dir(self) -> Path | None:
        shot_dir = self._first_shot_dir_for_job(self._active_job_data())
        vfx_root = self._infer_vfx_root_from_shot_dir(shot_dir)
        if vfx_root is None:
            return None
        return vfx_root / "Job_Assets"

    def _resolve_timeline_assets_dir(self) -> Path | None:
        timeline_data = self._active_timeline_data()
        shot_dir = self._first_shot_dir_for_timeline(timeline_data)
        if shot_dir is not None:
            timeline_root = shot_dir.parent
            if timeline_root and timeline_root.name.lower() not in ASSET_ROOT_DIRECTORY_NAMES:
                return timeline_root / "Timeline_Assets"

        job_data = self._active_job_data()
        reference_shot_dir = self._first_shot_dir_for_job(job_data)
        if not self._job_uses_timeline_directories(reference_shot_dir):
            return None

        vfx_root = self._infer_vfx_root_from_shot_dir(reference_shot_dir)
        timeline_title = str((timeline_data or {}).get("title") or "").strip()
        if vfx_root is None or not timeline_title:
            return None
        return vfx_root / timeline_title / "Timeline_Assets"

    def _resolved_timeline_matchmove_dir(self) -> Path | None:
        timeline_data = self._active_timeline_data()
        configured_path = str((timeline_data or {}).get("matchmove_path") or "").strip()
        if configured_path:
            try:
                configured_path = self.filesIO.convert_path(configured_path)
            except Exception:
                pass
            try:
                configured_dir = Path(str(configured_path))
                if configured_dir.exists():
                    return configured_dir
            except OSError:
                pass

        timeline_assets_dir = self._resolve_timeline_assets_dir()
        if timeline_assets_dir is None:
            return None
        return timeline_assets_dir / "matchmove"

    def _discover_timeline_matchmove_candidates(
        self,
        timeline_data: dict | None = None,
    ) -> list[TimelineMatchmoveCandidate]:
        timeline_payload = timeline_data if isinstance(timeline_data, dict) else self._active_timeline_data()
        if not isinstance(timeline_payload, dict):
            return []

        candidates: list[TimelineMatchmoveCandidate] = []
        seen_folders: set[str] = set()
        for shot in timeline_payload.get("shots", []) or []:
            shot_root = self._path_from_base_path((shot or {}).get("base_path"))
            if shot_root is None:
                continue
            shot_title = str((shot or {}).get("title") or shot_root.name or "Shot")
            shot_id = (shot or {}).get("id")
            for sequence_info in widgets.list_valid_precomp_sequences(str(shot_root)):
                folder_key = os.path.normcase(os.path.normpath(sequence_info.folder_path))
                if folder_key in seen_folders:
                    continue
                seen_folders.add(folder_key)
                candidates.append(
                    TimelineMatchmoveCandidate(
                        shot_id=shot_id,
                        shot_title=shot_title,
                        shot_root=str(shot_root),
                        sequence_info=sequence_info,
                    )
                )

        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.shot_title.lower(),
                Path(candidate.sequence_info.folder_path).name.lower(),
            ),
        )

    def _apply_timeline_payload(self, timeline_payload: dict | None) -> None:
        if not isinstance(timeline_payload, dict):
            return

        timeline_id = timeline_payload.get("id")
        try:
            timeline_id_int = int(timeline_id)
        except (TypeError, ValueError):
            return

        for job_data in getattr(self, "_jobs_by_id", {}).values():
            timelines = job_data.get("timelines", []) if isinstance(job_data, dict) else []
            for index, timeline in enumerate(timelines):
                if timeline.get("id") != timeline_id_int:
                    continue
                merged = dict(timeline)
                merged.update(timeline_payload)
                timelines[index] = merged
                break

        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return
        for index in range(tabs.count()):
            widget = tabs.widget(index)
            current_payload = getattr(widget, "_last_timeline", None)
            if isinstance(current_payload, dict) and current_payload.get("id") == timeline_id_int:
                merged = dict(current_payload)
                merged.update(timeline_payload)
                widget._last_timeline = merged

    def _open_timeline_matchmove_project(self, project_path: str) -> None:
        try:
            widgets.open_3de_project(project_path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Open Matchmove",
                f"Could not open the 3DE project.\n\n{project_path}\n\n{exc}",
            )

    def _show_timeline_assets_matchmove_menu(self, global_pos) -> bool:
        timeline_data = self._active_timeline_data()
        if not isinstance(timeline_data, dict):
            return False

        try:
            try:
                widgets._load_matchmove_helpers()
            except Exception as exc:
                widgets._show_matchmove_unavailable(self, exc)
                return True

            matchmove_dir = self._resolved_timeline_matchmove_dir()
            if matchmove_dir is None:
                return False

            existing_projects = widgets.list_matchmove_projects(str(matchmove_dir))
            candidates = self._discover_timeline_matchmove_candidates(timeline_data)

            menu = QMenu(self)
            matchmove_menu = menu.addMenu("Matchmove")
            for project_path in existing_projects:
                action = matchmove_menu.addAction(f"Open {project_path.name}")
                action.triggered.connect(
                    lambda checked=False, path=str(project_path): self._open_timeline_matchmove_project(path)
                )

            if existing_projects:
                matchmove_menu.addSeparator()

            if candidates:
                create_action = matchmove_menu.addAction("Create New 3DE Project")
                create_action.triggered.connect(
                    lambda checked=False, items=list(candidates): self._create_timeline_matchmove_project(items)
                )
            else:
                empty_action = matchmove_menu.addAction("No valid EXR precomp folders found")
                empty_action.setEnabled(False)

            menu.exec(global_pos)
            return True
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Timeline Assets Menu Error",
                f"Opening the Timeline Assets matchmove menu failed.\n\n{exc}",
            )
            return True

    def _on_timeline_assets_context_menu(self, pos) -> None:
        try:
            global_pos = self.btn_open_timeline_assets.mapToGlobal(pos)
            self._show_timeline_assets_matchmove_menu(global_pos)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Timeline Assets Menu Error",
                f"Opening the Timeline Assets matchmove menu failed.\n\n{exc}",
            )

    def _create_timeline_matchmove_project(
        self,
        candidates: list[TimelineMatchmoveCandidate] | None = None,
    ) -> None:
        timeline_data = self._active_timeline_data()
        if not isinstance(timeline_data, dict):
            return

        timeline_id = timeline_data.get("id")
        try:
            timeline_id_int = int(timeline_id)
        except (TypeError, ValueError):
            return

        matchmove_dir = self._resolved_timeline_matchmove_dir()
        if matchmove_dir is None:
            QMessageBox.warning(
                self,
                "Create Matchmove",
                "No timeline matchmove folder could be resolved from the current selection.",
            )
            return

        if candidates is None:
            try:
                candidates = self._discover_timeline_matchmove_candidates(timeline_data)
            except Exception as exc:
                widgets._show_matchmove_unavailable(self, exc)
                return

        if not candidates:
            QMessageBox.information(
                self,
                "Create Matchmove",
                "No valid EXR precomp folders were found for the active timeline.",
            )
            return

        dialog = TimelineMatchmoveDialog(
            timeline_title=str(timeline_data.get("title") or "Timeline"),
            candidates=list(candidates),
            matchmove_dir=str(matchmove_dir),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        request = dialog.built_request
        if request is None:
            return

        Path(request.matchmove_dir).mkdir(parents=True, exist_ok=True)
        Path(request.project_dir).mkdir(parents=True, exist_ok=True)
        Path(request.export_dir).mkdir(parents=True, exist_ok=True)

        log_lines: list[str] = []
        result = None
        try:
            result = widgets.run_headless_3de(request, log_lines.append)
        except Exception as exc:
            detail = str(exc)
            if log_lines:
                detail = f"{detail}\n\n" + "\n".join(log_lines[-20:])
            QMessageBox.critical(self, "Create Matchmove", detail)
            return
        finally:
            if result is not None:
                widgets.cleanup_headless_artifacts(result)

        open_warning = None
        try:
            widgets.open_3de_project(request.project_path)
        except Exception as exc:
            open_warning = str(exc)

        patch_warning = None
        timeline_payload = None
        api = getattr(getattr(self, "_worker", None), "api", None)
        if api is None:
            api = http_help.DjangoAPI()
        try:
            timeline_payload = api.update_timeline(
                timeline_id_int,
                matchmove_path=request.matchmove_dir,
            )
        except Exception as exc:
            patch_warning = str(exc)
            timeline_payload = {"id": timeline_id_int, "matchmove_path": request.matchmove_dir}

        if not timeline_payload:
            timeline_payload = {"id": timeline_id_int, "matchmove_path": request.matchmove_dir}

        self._apply_timeline_payload(timeline_payload)
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater()

        message_lines = [
            f"Created 3DE project: {Path(request.project_path).name}",
            f"Matchmove folder: {request.matchmove_dir}",
        ]
        if open_warning:
            message_lines.append(f"Created project, but opening it in 3DE failed: {open_warning}")
        if patch_warning:
            message_lines.append(
                f"Created project, but saving matchmove_path to ShotBox failed: {patch_warning}"
            )
        QMessageBox.information(self, "Create Matchmove", "\n\n".join(message_lines))

    def _set_assets_button_state(
        self,
        button_name: str,
        folder_label: str,
        folder_path: Path | None,
        *,
        loading: bool = False,
    ) -> None:
        button = getattr(self, button_name, None)
        if button is None:
            return
        if loading:
            button.setEnabled(False)
            button.setProperty("folder_path", "")
            button.setToolTip(f"Loading {folder_label.lower()}...")
            return
        if folder_path is None:
            button.setEnabled(False)
            button.setProperty("folder_path", "")
            button.setToolTip(
                f"No {folder_label.lower()} folder could be resolved from the current selection."
            )
            return
        button.setEnabled(True)
        button.setProperty("folder_path", str(folder_path))
        button.setToolTip(f"Open {folder_label.lower()}\n{folder_path}")

    def _update_assets_action_buttons(self, *, loading: bool = False) -> None:
        if loading:
            self._set_assets_button_state(
                "btn_open_timeline_assets", "Timeline Assets", None, loading=True
            )
            self._set_assets_button_state(
                "btn_open_job_assets", "Job Assets", None, loading=True
            )
            return

        self._set_assets_button_state(
            "btn_open_timeline_assets",
            "Timeline Assets",
            self._resolve_timeline_assets_dir(),
        )
        self._set_assets_button_state(
            "btn_open_job_assets",
            "Job Assets",
            self._resolve_job_assets_dir(),
        )

    def _open_assets_directory(self, folder_path: Path | None, folder_label: str) -> None:
        if folder_path is None:
            QMessageBox.information(
                self,
                folder_label,
                f"No {folder_label.lower()} folder could be resolved from the current selection.",
            )
            return
        try:
            folder_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                folder_label,
                f"Could not prepare the folder.\n\n{folder_path}\n\n{exc}",
            )
            return
        try:
            self.filesIO.openFileLocation(str(folder_path))
        except Exception as exc:
            QMessageBox.warning(
                self,
                folder_label,
                f"Could not open the folder.\n\n{folder_path}\n\n{exc}",
            )

    def _on_open_timeline_assets_clicked(self) -> None:
        self._open_assets_directory(self._resolve_timeline_assets_dir(), "Timeline Assets")

    def _on_open_job_assets_clicked(self) -> None:
        self._open_assets_directory(self._resolve_job_assets_dir(), "Job Assets")

    def _iter_shot_cards(self):
        tabs = self.timelines_tabs
        for i in range(tabs.count()):
            timeline_widget = tabs.widget(i)
            if not timeline_widget or not hasattr(timeline_widget, "shots_layout"):
                continue
            layout = timeline_widget.shots_layout
            for j in range(layout.count()):
                item = layout.itemAt(j)
                card = item.widget() if item else None
                if isinstance(card, widgets.ShotCard):
                    yield card

    def _find_shot_card_by_id(self, shot_id: int):
        for card in self._iter_shot_cards():
            if getattr(card, "_shot_id", None) == shot_id:
                return card
        return None

    def _update_jobs_cache_lock_value(self, shot_id: int, nuke_in_use):
        for job_data in self._jobs_by_id.values():
            timelines = job_data.get("timelines", []) if isinstance(job_data, dict) else []
            for timeline in timelines:
                for shot in timeline.get("shots", []):
                    if shot.get("id") == shot_id:
                        shot["nuke_in_use"] = nuke_in_use
                        return

    def _apply_shot_lock_value(self, shot_id: int, nuke_in_use):
        self._update_jobs_cache_lock_value(shot_id, nuke_in_use)
        card = self._find_shot_card_by_id(shot_id)
        if card:
            card.data["nuke_in_use"] = nuke_in_use
            card.refresh_lock_state_from_data()

    def _apply_shot_lock_payload(self, shot_payload: dict):
        if not isinstance(shot_payload, dict):
            return
        shot_id = shot_payload.get("id")
        if shot_id is None:
            return
        self._apply_shot_lock_value(int(shot_id), shot_payload.get("nuke_in_use"))

    def _warn_lock_conflict(self, shot_title: str, lock_status: dict | None, detail: str | None):
        owner = display_owner_name((lock_status or {}).get("owner"))
        msg = detail or "This Nuke script is currently locked by another system."
        QMessageBox.warning(
            self,
            "Nuke Lock Conflict",
            f"{msg}\n\nShot: {shot_title}\nLocked by: {owner}",
        )

    def _handle_nuke_open_request(self, shot_card: widgets.ShotCard, nk_path: str):
        shot_data = shot_card.data or {}
        shot_id = shot_data.get("id")
        shot_title = shot_data.get("title", "Unknown Shot")
        if shot_id is None or not nk_path:
            return

        try:
            fresh_shot = self._worker.api.get_shot(int(shot_id))
        except Exception as exc:
            QMessageBox.warning(self, "Open Nuke", f"Could not refresh lock state.\n\n{exc}")
            return

        self._apply_shot_lock_payload(fresh_shot)
        lock_info = parse_lock_info(fresh_shot.get("nuke_in_use"))
        force_take = False
        system_id = self._worker.api.get_system_id()
        if lock_info:
            if lock_info.matches_system(system_id):
                popup = QMessageBox(self)
                popup.setWindowTitle("Already Locked By You")
                popup.setIcon(QMessageBox.Icon.Warning)
                popup.setText(
                    f"This script is already locked by your machine.\n\n"
                    f"Shot: {shot_title}\n"
                    f"Script: {os.path.basename(nk_path)}\n\n"
                    f"Opening again can create duplicate Nuke sessions."
                )
                cancel_btn = popup.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
                continue_btn = popup.addButton("Open Anyway", QMessageBox.ButtonRole.AcceptRole)
                popup.setDefaultButton(cancel_btn)
                popup.exec()
                if popup.clickedButton() is not continue_btn:
                    return
            else:
                owner = lock_info.display_owner
                stale_note = "\nStatus: heartbeat stale." if lock_info.is_stale() else ""
                popup = QMessageBox(self)
                popup.setWindowTitle("Nuke Script In Use")
                popup.setIcon(QMessageBox.Icon.Warning)
                popup.setText(
                    f"This script is currently locked by another machine.\n\n"
                    f"Shot: {shot_title}\n"
                    f"Script: {os.path.basename(nk_path)}\n"
                    f"Locked by: {owner}{stale_note}"
                )
                cancel_btn = popup.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
                force_btn = popup.addButton("Force Take", QMessageBox.ButtonRole.AcceptRole)
                popup.setDefaultButton(cancel_btn)
                popup.exec()
                if popup.clickedButton() is not force_btn:
                    return
                force_take = True

        lock_result = self._worker.api.update_shot_lock(int(shot_id), release=False, force=force_take)
        if not lock_result.get("ok"):
            if lock_result.get("nuke_in_use") is not None:
                self._apply_shot_lock_value(int(shot_id), lock_result.get("nuke_in_use"))
            if lock_result.get("conflict"):
                self._warn_lock_conflict(
                    shot_title=shot_title,
                    lock_status=lock_result.get("lock_status"),
                    detail=lock_result.get("detail"),
                )
            else:
                QMessageBox.warning(
                    self,
                    "Open Nuke",
                    lock_result.get("detail") or "Could not claim Nuke lock.",
                )
            return

        shot_payload = lock_result.get("shot")
        if isinstance(shot_payload, dict):
            self._apply_shot_lock_payload(shot_payload)

        try:
            shot_card.filesIO.open_file(str(nk_path))
        except Exception as exc:
            self._worker.api.update_shot_lock(int(shot_id), release=True, force=False)
            self._owned_locks_by_shot_id.pop(int(shot_id), None)
            QMessageBox.warning(
                self,
                "Open Nuke",
                f"Failed to launch Nuke after claiming lock.\nLock was released.\n\n{exc}",
            )
            return

        self._owned_locks_by_shot_id[int(shot_id)] = {
            "script_path": str(nk_path),
            "last_seen_open_monotonic": time.monotonic(),
        }

    def _on_lock_state_sync_tick(self):
        if self._active_job_id is None:
            return
        try:
            job_data = self._worker.api.get_job(int(self._active_job_id))
        except Exception:
            return
        if not isinstance(job_data, dict):
            return

        active_job = self._jobs_by_id.get(int(self._active_job_id))
        if not isinstance(active_job, dict):
            return

        local_system_id = self._worker.api.get_system_id()
        now = time.monotonic()
        remote_timelines = {tl.get("id"): tl for tl in job_data.get("timelines", [])}
        for timeline in active_job.get("timelines", []):
            remote_timeline = remote_timelines.get(timeline.get("id"))
            if not remote_timeline:
                continue
            remote_shots = {shot.get("id"): shot for shot in remote_timeline.get("shots", [])}
            for shot in timeline.get("shots", []):
                shot_id = shot.get("id")
                if shot_id not in remote_shots:
                    continue
                try:
                    shot_id_int = int(shot_id)
                except (TypeError, ValueError):
                    continue
                remote_lock = remote_shots[shot_id].get("nuke_in_use")
                shot["nuke_in_use"] = remote_lock
                self._apply_shot_lock_value(shot_id_int, remote_lock)

                # Track local-owned locks from sync so detector can auto-release even after UI/app restarts.
                lock_info = parse_lock_info(remote_lock)
                if lock_info and lock_info.matches_system(local_system_id):
                    existing = self._owned_locks_by_shot_id.get(shot_id_int, {})
                    script_path = existing.get("script_path")
                    card = self._find_shot_card_by_id(shot_id_int)
                    if card is not None and hasattr(card, "btn_open_nuke"):
                        card_script_path = card.btn_open_nuke.property("file_path")
                        if card_script_path:
                            script_path = str(card_script_path)
                    self._owned_locks_by_shot_id[shot_id_int] = {
                        "script_path": script_path or "",
                        "last_seen_open_monotonic": existing.get("last_seen_open_monotonic", now),
                    }
                else:
                    self._owned_locks_by_shot_id.pop(shot_id_int, None)

    def _on_lock_detector_tick(self):
        if not self._owned_locks_by_shot_id:
            return
        try:
            detections = nuke_detector.detect_open_nuke_scripts(current_user_only=True)
        except Exception:
            detections = []
        detected_paths = {
            self._normalize_script_path(item.script_path)
            for item in detections
            if getattr(item, "script_path", None)
        }

        now = time.monotonic()
        for shot_id, lock_state in list(self._owned_locks_by_shot_id.items()):
            tracked_script = self._normalize_script_path(lock_state.get("script_path"))
            if tracked_script and tracked_script in detected_paths:
                lock_state["last_seen_open_monotonic"] = now
                continue

            last_seen = lock_state.get("last_seen_open_monotonic", now)
            if now - last_seen < self._lock_release_grace_sec:
                continue

            result = self._worker.api.update_shot_lock(int(shot_id), release=True, force=False)
            self._owned_locks_by_shot_id.pop(int(shot_id), None)
            if result.get("shot"):
                self._apply_shot_lock_payload(result["shot"])
            elif result.get("nuke_in_use") is not None:
                self._apply_shot_lock_value(int(shot_id), result.get("nuke_in_use"))

    def _on_lock_heartbeat_tick(self):
        if not self._owned_locks_by_shot_id:
            return
        for shot_id in list(self._owned_locks_by_shot_id.keys()):
            result = self._worker.api.update_shot_lock(int(shot_id), release=False, force=False)
            if result.get("ok"):
                if result.get("shot"):
                    self._apply_shot_lock_payload(result["shot"])
                continue

            self._owned_locks_by_shot_id.pop(int(shot_id), None)
            if result.get("shot"):
                self._apply_shot_lock_payload(result["shot"])
            elif result.get("nuke_in_use") is not None:
                self._apply_shot_lock_value(int(shot_id), result.get("nuke_in_use"))

    def _populate_filter_dropdowns(self):
        """Populate artist and status filter dropdowns."""
        # Only populate once
        if self._dropdowns_populated:
            return
        
        # Populate artist dropdown
        self.comboBox_sort_artist.blockSignals(True)
        self.comboBox_sort_artist.clear()
        self.comboBox_sort_artist.addItem("All Artists", None)
        
        # Get unique artists from API
        try:
            users = self._worker.api.get_users()
            for user in users:
                user_id = user.get("id")
                username = user.get("username", f"User {user_id}")
                self.comboBox_sort_artist.addItem(username, user_id)
        except Exception:
            pass
        
        self.comboBox_sort_artist.blockSignals(False)
        
        # Populate status dropdown (multi-select with checkboxes)
        self._setup_status_filter_dropdown()
        self._apply_saved_filter_state_to_controls()
        
        self._dropdowns_populated = True

    def _setup_status_filter_dropdown(self) -> None:
        if hasattr(self, "_status_filter_ready") and self._status_filter_ready:
            return
        self._status_filter_ready = True

        self._status_filter_values = set()
        self._status_filter_items = {}
        self._status_filter_blocked = False
        self._status_label_map = {value: label for value, label in http_help.TASK_STATUS_CHOICES}

        model = QStandardItemModel(self.comboBox_sort_status)

        all_item = QStandardItem("All")
        all_item.setData("all", Qt.ItemDataRole.UserRole)
        all_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        model.appendRow(all_item)

        none_item = QStandardItem("None")
        none_item.setData("none", Qt.ItemDataRole.UserRole)
        none_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        model.appendRow(none_item)

        for value, label in http_help.TASK_STATUS_CHOICES:
            item = QStandardItem(label)
            item.setData(value, Qt.ItemDataRole.UserRole)
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Unchecked)
            model.appendRow(item)
            self._status_filter_items[value] = item

        self.comboBox_sort_status.setModel(model)
        self.comboBox_sort_status.setEditable(True)
        if self.comboBox_sort_status.lineEdit():
            self.comboBox_sort_status.lineEdit().setReadOnly(True)
            self.comboBox_sort_status.lineEdit().setPlaceholderText("Any Status")

        self.comboBox_sort_status.view().pressed.connect(self._on_status_popup_pressed)
        self.comboBox_sort_status.activated.connect(self._on_status_combo_activated)
        model.itemChanged.connect(self._on_status_item_changed)
        self._update_status_filter_label()

    def _on_status_popup_pressed(self, index) -> None:
        if not hasattr(self, "_status_filter_ready") or not self._status_filter_ready:
            return
        if not index.isValid():
            return
        model = self.comboBox_sort_status.model()
        if not model:
            return
        item = model.itemFromIndex(index)
        if not item:
            return
        if not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return
        next_state = (
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(next_state)

    def _on_status_combo_index_changed(self, index: int) -> None:
        if hasattr(self, "_status_filter_ready") and self._status_filter_ready:
            self._update_status_filter_label()
            return
        self._apply_filters()

    def _on_status_combo_activated(self, index: int) -> None:
        if not hasattr(self, "_status_filter_ready") or not self._status_filter_ready:
            return
        model = self.comboBox_sort_status.model()
        if not model:
            return
        item = model.item(index)
        if not item:
            return
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        if action == "all":
            self._set_all_status_filters(True)
        elif action == "none":
            self._set_all_status_filters(False)

    def _set_all_status_filters(self, checked: bool) -> None:
        if not hasattr(self, "_status_filter_items"):
            return
        self._status_filter_blocked = True
        for value, item in self._status_filter_items.items():
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._status_filter_blocked = False
        if checked:
            self._status_filter_values = set(self._status_filter_items.keys())
        else:
            self._status_filter_values = set()
        self._update_status_filter_label()
        self._apply_filters()

    def _on_status_item_changed(self, item: QStandardItem) -> None:
        if not hasattr(self, "_status_filter_ready") or not self._status_filter_ready:
            return
        if self._status_filter_blocked:
            return
        if not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return
        value = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._status_filter_values.add(value)
        else:
            self._status_filter_values.discard(value)
        self._update_status_filter_label()
        self._apply_filters()

    def _update_status_filter_label(self) -> None:
        if not hasattr(self, "_status_filter_items"):
            return
        selected = sorted(self._status_filter_values)
        total = len(self._status_filter_items)
        selected_count = len(selected)
        if selected_count == 0:
            text = "Any Status"
        elif selected_count == total:
            text = "All Statuses"
        elif selected_count <= 2:
            labels = [self._status_label_map.get(v, str(v)) for v in selected]
            text = ", ".join(labels)
        else:
            text = f"{selected_count} Statuses"
        if self.comboBox_sort_status.isEditable():
            self.comboBox_sort_status.setEditText(text)
            if self.comboBox_sort_status.lineEdit():
                self.comboBox_sort_status.lineEdit().setCursorPosition(0)

    def _on_sort_changed(self, index: int) -> None:
        if hasattr(self, "_last_filter_sig"):
            del self._last_filter_sig
        self._apply_filters(force=True)

    def _filters_enabled(self) -> bool:
        checkbox = getattr(self, "checkBox_enable_filters", None)
        if checkbox is None:
            return True
        return bool(checkbox.isChecked())

    def _set_filter_controls_enabled(self, enabled: bool) -> None:
        for name in (
            "checkBox_show_to_conform",
            "checkBox_hidden_shot",
            "checkBox_hidden_tasks",
            "comboBox_sort",
            "comboBox_sort_artist",
            "comboBox_sort_status",
            "Search_bar",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _set_all_task_widgets_visible(self) -> None:
        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return
        for i in range(tabs.count()):
            timeline_widget = tabs.widget(i)
            if not timeline_widget or not hasattr(timeline_widget, "shots_layout"):
                continue
            shots_layout = timeline_widget.shots_layout
            for j in range(shots_layout.count()):
                item = shots_layout.itemAt(j)
                shot_card = item.widget() if item else None
                if not shot_card or not hasattr(shot_card, "frame_tasks"):
                    continue
                layout = shot_card.frame_tasks.layout()
                if layout is None:
                    continue
                for k in range(layout.count()):
                    task_item = layout.itemAt(k)
                    task_widget = task_item.widget() if task_item else None
                    if isinstance(task_widget, widgets.TaskWidget) and not task_widget.isVisible():
                        task_widget.setVisible(True)

    def _restore_timeline_api_order(self, timeline_widget: QWidget) -> None:
        if not timeline_widget or not hasattr(timeline_widget, "shots_layout"):
            return
        timeline_data = getattr(timeline_widget, "_last_timeline", {}) or {}
        desired_names = []
        for shot in timeline_data.get("shots", []) or []:
            shot_id = shot.get("id")
            if shot_id is None:
                continue
            desired_names.append(f"shot-{shot_id}")
        if desired_names:
            widgets._ensure_order(timeline_widget.shots_layout, desired_names)

    def _on_enable_filters_changed(self, state) -> None:
        enabled = self._filters_enabled()
        self._set_filter_controls_enabled(enabled)
        if not enabled:
            self._search_timer.stop()
            self._toggle_hidden_timer.stop()
            self._pending_filter_apply = False
            self._set_all_task_widgets_visible()
        if hasattr(self, "_last_filter_sig"):
            del self._last_filter_sig
        self._apply_filters(force=True)

    def _current_sort_mode(self) -> str:
        sort_combo = getattr(self, "comboBox_sort", None)
        if sort_combo is not None:
            mode = sort_combo.currentData()
            if mode in ("title_asc", "title_desc", "task_count_desc", "task_count_asc"):
                return mode
        return "title_asc"

    def _count_sortable_tasks(self, shot_data: dict, hide_hidden_tasks: bool) -> int:
        tasks = shot_data.get("tasks", []) or []
        if not hide_hidden_tasks:
            return len(tasks)
        return sum(1 for task in tasks if not task.get("hidden", False))

    def _sort_shot_entries(self, shot_entries: list[dict]) -> list[dict]:
        if len(shot_entries) <= 1:
            return list(shot_entries)
        if not self._filters_enabled():
            return list(shot_entries)

        sorted_entries = list(shot_entries)
        sort_mode = self._current_sort_mode()

        sorted_entries.sort(key=lambda entry: entry["shot_id"])
        sorted_entries.sort(key=lambda entry: entry["title"])

        if sort_mode == "title_desc":
            sorted_entries.sort(key=lambda entry: entry["title"], reverse=True)
        elif sort_mode == "task_count_desc":
            sorted_entries.sort(key=lambda entry: entry["task_count"], reverse=True)
        elif sort_mode == "task_count_asc":
            sorted_entries.sort(key=lambda entry: entry["task_count"])

        return sorted_entries

    def _apply_sort_to_timeline(self, timeline_widget: QWidget, hide_hidden_tasks: bool) -> None:
        if not timeline_widget or not hasattr(timeline_widget, "shots_layout"):
            return

        shots_layout = timeline_widget.shots_layout
        shot_entries = []

        for i in range(shots_layout.count()):
            item = shots_layout.itemAt(i)
            if not item:
                continue
            shot_card = item.widget()
            if not shot_card or not hasattr(shot_card, "data"):
                continue

            data = shot_card.data or {}
            title = str(data.get("title", "") or "")
            shot_id = data.get("id")
            try:
                shot_id_value = int(shot_id)
            except (TypeError, ValueError):
                shot_id_value = 0

            shot_entries.append(
                {
                    "name": shot_card.objectName(),
                    "title": title.lower(),
                    "shot_id": shot_id_value,
                    "task_count": self._count_sortable_tasks(data, hide_hidden_tasks),
                }
            )

        desired_names = [entry["name"] for entry in self._sort_shot_entries(shot_entries) if entry["name"]]
        if desired_names:
            widgets._ensure_order(shots_layout, desired_names)
    
    def _on_search_changed(self, text):
        """Restart search timer when user types."""
        self._search_timer.stop()
        self._search_timer.start()
    
    def _on_timeline_tab_changed(self, index):
        """Update the results count when user switches timeline tabs."""
        # Clear the filter signature to force re-count for the new tab
        if hasattr(self, '_last_filter_sig'):
            del self._last_filter_sig
        # Reapply filters to update the count for current timeline
        self._apply_filters()
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater()
        self.active_timeline_changed.emit(index)
        
        # Save session state when timeline tab changes (but not during initial load)
        if not self._initial_load and self._session_restored:
            self._save_session_state()
    
    def _apply_filters(self, force=False):
        """Apply all filter criteria to shots. This is the single source of truth for shot visibility.
        
        Args:
            force: If True, apply filters even while scrolling
        """
        # Defer filter application while user is scrolling (unless forced)
        if not force and self._is_scrolling:
            self._pending_filter_apply = True
            return
        if force and hasattr(self, "_last_filter_sig"):
            delattr(self, "_last_filter_sig")

        tabs = self.timelines_tabs
        current_tab_index = tabs.currentIndex()
        filters_enabled = self._filters_enabled()
        search_text = self.Search_bar.text().strip().lower()
        artist_filter = self.comboBox_sort_artist.currentData()
        status_filter_values = set(getattr(self, "_status_filter_values", set()))
        show_to_conform = bool(getattr(self, "show_to_conform", False))
        hide_hidden_tasks = not self.show_hidden_tasks
        self._persist_filter_state()

        render_state = self._current_task_render_state(
            filters_enabled=filters_enabled,
            search_text=search_text,
            artist_filter=artist_filter,
            status_filter_values=status_filter_values,
            hide_hidden_tasks=hide_hidden_tasks,
        )
        if not self._filters_enabled():
            filter_sig = ("filters_disabled", current_tab_index)
            if hasattr(self, "_last_filter_sig") and self._last_filter_sig == filter_sig:
                return
            self._last_filter_sig = filter_sig
            self._reset_task_materialize_queue()

            current_timeline_visible_count = 0
            for i in range(tabs.count()):
                timeline_widget = tabs.widget(i)
                if not timeline_widget or not hasattr(timeline_widget, "shots_layout"):
                    continue

                is_current_timeline = i == current_tab_index
                shots_layout = timeline_widget.shots_layout
                timeline_shot_count = 0

                for j in range(shots_layout.count()):
                    item = shots_layout.itemAt(j)
                    if not item:
                        continue
                    shot_card = item.widget()
                    if not shot_card or not hasattr(shot_card, "data"):
                        continue
                    timeline_shot_count += 1
                    if not shot_card.isVisible():
                        shot_card.setVisible(True)
                    if hasattr(shot_card, "set_task_render_state"):
                        shot_card.set_task_render_state(render_state)
                    visible_task_ids = self._visible_task_ids_for_shot(
                        shot_card.data or {},
                        filters_enabled=False,
                    )
                    pending_task_ids = shot_card.set_visible_task_ids(visible_task_ids)
                    if is_current_timeline:
                        self._enqueue_task_materialization(shot_card, pending_task_ids)

                self._restore_timeline_api_order(timeline_widget)
                if is_current_timeline:
                    current_timeline_visible_count = timeline_shot_count

            if hasattr(self, "Label_results"):
                self.Label_results.setText(f"{current_timeline_visible_count} results")
            self._prepare_task_loading_progress(current_tab_index)
            self._process_task_materialize_queue(self._task_materialize_batch_size)
            return

        # Build a signature of current filter state to avoid redundant work
        # Include current tab index so results update when switching timelines
        filter_sig = (
            search_text,
            self._current_sort_mode(),
            artist_filter,
            tuple(sorted(status_filter_values)),
            self.show_hidden_shots,
            self.show_hidden_tasks,
            show_to_conform,
            current_tab_index,
        )
        if hasattr(self, '_last_filter_sig') and self._last_filter_sig == filter_sig:
            # Filters haven't changed, skip the expensive iteration
            return
        self._last_filter_sig = filter_sig
        self._reset_task_materialize_queue()
        
        current_timeline_visible_count = 0
        
        # Pre-compute whether we have any active filters for early exit optimization
        has_search = bool(search_text)
        has_artist = artist_filter is not None
        has_status = bool(status_filter_values)
        has_task_filters = has_artist or has_status
        hide_hidden_shots = (not self.show_hidden_shots) and (not show_to_conform)
        
        for i in range(tabs.count()):
            timeline_widget = tabs.widget(i)
            if not timeline_widget or not hasattr(timeline_widget, 'shots_layout'):
                continue
            
            is_current_timeline = (i == current_tab_index)
            shots_layout = timeline_widget.shots_layout
            
            for j in range(shots_layout.count()):
                item = shots_layout.itemAt(j)
                if not item:
                    continue
                shot_card = item.widget()
                if not shot_card or not hasattr(shot_card, 'data'):
                    continue
                
                # Start assuming visible, then check each filter
                visible = True
                data = shot_card.data or {}
                if hasattr(shot_card, "set_task_render_state"):
                    shot_card.set_task_render_state(render_state)
                is_shot_hidden = bool(data.get("hidden", False))
                visible_task_ids = []

                if hide_hidden_shots and is_shot_hidden:
                    shot_card.set_visible_task_ids([])
                    setattr(shot_card, "_pending_task_materialize_ids", [])
                    if shot_card.isVisible():
                        shot_card.setVisible(False)
                    continue

                if show_to_conform:
                    visible_task_ids = self._visible_task_ids_for_shot(
                        data,
                        hide_hidden_tasks=hide_hidden_tasks,
                        filters_enabled=True,
                    )
                    pending_task_ids = shot_card.set_visible_task_ids(visible_task_ids)
                    setattr(shot_card, "_pending_task_materialize_ids", pending_task_ids)
                    visible = self._shot_needs_conform(shot_card, data)
                else:
                    # Filter 1: Search text
                    if has_search:
                        if not self._shot_matches_search(data, search_text):
                            visible = False

                    visible_task_count = 0
                    if visible:
                        visible_task_count = self._apply_task_visibility(
                            shot_card,
                            hide_hidden_tasks,
                            search_text=search_text,
                            artist_filter=artist_filter,
                            status_filter_values=status_filter_values,
                        )
                        visible_task_ids = getattr(shot_card, "_visible_task_ids", [])
                        if has_search and not visible_task_count and not self._shot_matches_search(data, search_text):
                            visible = False
                        if has_task_filters and visible_task_count == 0:
                            visible = False
                    else:
                        shot_card.set_visible_task_ids([])
                        setattr(shot_card, "_pending_task_materialize_ids", [])
                
                # Only call setVisible if state actually changed
                if shot_card.isVisible() != visible:
                    shot_card.setVisible(visible)
                
                if visible:
                    # Only count for current timeline
                    if is_current_timeline:
                        current_timeline_visible_count += 1
                    pending_task_ids = getattr(shot_card, "_pending_task_materialize_ids", [])
                    if is_current_timeline:
                        self._enqueue_task_materialization(shot_card, pending_task_ids)
                else:
                    shot_card.set_visible_task_ids([])
                    setattr(shot_card, "_pending_task_materialize_ids", [])

            self._apply_sort_to_timeline(timeline_widget, hide_hidden_tasks)
        
        # Update results label - show current timeline count
        if hasattr(self, 'Label_results'):
            self.Label_results.setText(f"{current_timeline_visible_count} results")
        self._prepare_task_loading_progress(current_tab_index)
        self._process_task_materialize_queue(self._task_materialize_batch_size)

    def _apply_task_visibility(
        self,
        shot_card: QWidget,
        hide_hidden: bool,
        search_text: str = "",
        artist_filter=None,
        status_filter_values=None,
    ) -> int:
        if not hasattr(shot_card, "data"):
            return 0
        visible_task_ids = self._visible_task_ids_for_shot(
            shot_card.data or {},
            search_text=search_text,
            artist_filter=artist_filter,
            status_filter_values=status_filter_values,
            hide_hidden_tasks=hide_hidden,
            filters_enabled=True,
        )
        if hasattr(shot_card, "set_visible_task_ids"):
            pending_ids = shot_card.set_visible_task_ids(visible_task_ids)
            setattr(shot_card, "_pending_task_materialize_ids", pending_ids)
        return len(visible_task_ids)

    def _shot_needs_conform(self, shot_card: QWidget, shot_data: dict) -> bool:
        latest_render = None
        if hasattr(shot_card, "btn_latest_render") and shot_card.btn_latest_render:
            latest_render = (
                shot_card.btn_latest_render.property("render_display")
                or shot_card.btn_latest_render.text()
            )
        if not latest_render or latest_render.strip() in ("No Render", "None"):
            return False

        last_conform = shot_data.get("last_conform")
        if not last_conform or str(last_conform).strip() in ("None", ""):
            return True

        if hasattr(shot_card, "_normalize_render_label"):
            normalize = shot_card._normalize_render_label
        else:
            normalize = self._normalize_render_label
        return normalize(latest_render) != normalize(str(last_conform))

    def _normalize_render_label(self, value: str) -> str:
        if not value:
            return ""
        text = value.strip()
        upper = text.upper()
        if upper.endswith("(MOV)") or upper.endswith("(EXR)"):
            text = text.rsplit("(", 1)[0].strip()
        parts = text.rsplit(" ", 1)
        if len(parts) == 2:
            tail = parts[1]
            if "-" in tail:
                left, right = tail.split("-", 1)
                if left.isdigit() and right.isdigit():
                    return parts[0].strip()
        return text
    
    def _get_searchable_text(self, shot_data: dict, tasks=None) -> str:
        """Build searchable text from shot data."""
        parts = []
        
        # Shot fields
        parts.append(shot_data.get("title", ""))
        parts.append(shot_data.get("notes", ""))
        
        # Task fields
        for task in (tasks if tasks is not None else shot_data.get("tasks", [])):
            parts.append(task.get("title", ""))
            parts.append(task.get("notes", ""))
        
        return " ".join(str(p) for p in parts if p)

    def _on_toggle_hidden(self, state):
        """Handle checkbox state change with debouncing."""
        if hasattr(self, "checkBox_hidden_shot") and self.checkBox_hidden_shot:
            self.show_hidden_shots = bool(self.checkBox_hidden_shot.isChecked())
        if hasattr(self, "checkBox_hidden_tasks") and self.checkBox_hidden_tasks:
            self.show_hidden_tasks = bool(self.checkBox_hidden_tasks.isChecked())
        # Stop any pending toggle
        self._toggle_hidden_timer.stop()
        # Start timer to execute actual toggle after 200ms
        self._toggle_hidden_timer.start()
    
    def _do_toggle_hidden(self):
        """Actually perform the toggle after debounce delay."""
        # Just apply filters - they handle hidden state
        self._apply_filters()

    def _on_toggle_to_conform(self, state):
        if hasattr(self, "checkBox_show_to_conform") and self.checkBox_show_to_conform:
            self.show_to_conform = bool(self.checkBox_show_to_conform.isChecked())
        else:
            self.show_to_conform = bool(state)
        self._apply_filters()



    def _on_preview_size_changed(self, text):
        width = self._preview_size_map.get(text, 240)
        thumbnails_enabled = text != "NoThumb"
        if hasattr(widgets, "set_global_thumbnail_mode"):
            widgets.set_global_thumbnail_mode(thumbnails_enabled, width)

        tabs = self.timelines_tabs
        for i in range(tabs.count()):
            container = tabs.widget(i)
            # find all ShotCard descendants in this tab
            for shot in container.findChildren(widgets.ShotCard):
                if hasattr(shot, "set_thumbnails_enabled"):
                    shot.set_thumbnails_enabled(thumbnails_enabled)
                if not thumbnails_enabled:
                    continue
                if hasattr(shot, "set_thumbnail_width"):
                    shot.set_thumbnail_width(width)

    def _apply_density_to_timeline(self, timeline_widget: QWidget):
        if not timeline_widget or not hasattr(timeline_widget, "shots_layout"):
            return
        layout = timeline_widget.shots_layout
        if self._card_spacing is not None:
            layout.setSpacing(max(0, int(self._card_spacing)))

        if self._compact_view_enabled or self._row_height is None:
            target_height = 0
        else:
            target_height = int(self._row_height)

        for i in range(layout.count()):
            item = layout.itemAt(i)
            if not item or not item.widget():
                continue
            card = item.widget()
            if not isinstance(card, widgets.ShotCard):
                continue
            if target_height > 0:
                card.setMinimumHeight(target_height)
            else:
                card.setMinimumHeight(0)

    def _available_shots_width(self) -> int:
        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return self.width()
        current = tabs.currentWidget()
        if current and current.width() > 0:
            return current.width()
        if tabs.width() > 0:
            return tabs.width()
        return self.width()

    def _effective_shots_layout_mode(self, available_width: int | None = None) -> str:
        width = self._available_shots_width() if available_width is None else available_width
        return resolve_effective_shots_layout_mode(
            self._shots_layout_mode,
            self._compact_view_enabled,
            width,
            self._compact_auto_grid_breakpoint,
        )

    def _reapply_current_preview_size(self) -> None:
        preview_combo = getattr(self, "comboBox_preview_size", None)
        if preview_combo is None:
            return
        self._on_preview_size_changed(preview_combo.currentText())

    def _collect_preview_tasks(self, active_job, files_io):
        from nuke_headless_tasks import preview_version_exists

        preview_tasks = []
        job_name = active_job.get("title", "Project")

        for timeline in active_job.get("timelines", []):
            for shot in timeline.get("shots", []):
                shot_id = shot.get("id")
                shot_name = shot.get("title", f"shot_{shot_id}")
                shot_dir = shot.get("base_path", "")
                original_clip = str(shot.get("original_clip") or "").strip()

                if not shot_dir:
                    continue

                local_shot_dir = files_io.convert_path(shot_dir)

                if original_clip and original_clip not in {"None", "—"}:
                    if not preview_version_exists(local_shot_dir, shot_name, 1):
                        preview_tasks.append({
                            "shot": shot,
                            "source_type": "original_clip",
                            "source_path": original_clip,
                            "version": 1,
                            "job_name": job_name,
                        })

                render_info = files_io.latest_render_info(shot_dir)
                if not render_info:
                    continue

                render_path = render_info.get("sequence_path") or render_info.get("render_path")
                render_version = render_info.get("version")
                if not render_path or render_version is None:
                    continue
                if preview_version_exists(local_shot_dir, shot_name, int(render_version)):
                    continue

                preview_tasks.append({
                    "shot": shot,
                    "source_type": "render",
                    "source_path": render_path,
                    "version": int(render_version),
                    "media_type": render_info.get("type"),
                    "job_name": job_name,
                })

        return preview_tasks

    def _persist_compact_view_setting(self) -> None:
        if not hasattr(self, "_settings_manager") or not self._settings_manager:
            return
        current = bool(self._settings_manager.get("compact_view_enabled", False))
        if current == self._compact_view_enabled:
            return
        self._settings_manager.set("compact_view_enabled", self._compact_view_enabled, save=False)
        self._settings_manager.save()

    def _on_compact_view_changed(self, state):
        checkbox = getattr(self, "checkBox_compact_view", None)
        if checkbox is not None:
            self._compact_view_enabled = bool(checkbox.isChecked())
        else:
            self._compact_view_enabled = bool(state)
        self._persist_compact_view_setting()
        self._apply_compact_view_state()

    def _apply_compact_view_state(self) -> None:
        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return

        effective_mode = self._effective_shots_layout_mode()
        for i in range(tabs.count()):
            widget = tabs.widget(i)
            if not widget:
                continue
            if hasattr(widget, "set_layout_mode"):
                widget.set_layout_mode(effective_mode, self._card_spacing)
            if hasattr(widget, "set_task_style"):
                widget.set_task_style(self._task_style)
            if hasattr(widget, "set_compact_mode"):
                widget.set_compact_mode(self._compact_view_enabled)
            self._apply_density_to_timeline(widget)

        self._reapply_current_preview_size()

    def apply_ui_density_settings(self, preview_size=None, card_spacing=None, row_height=None):
        """Apply UI density settings (preview size, spacing, row height)."""
        if preview_size:
            if preview_size in self._preview_size_map:
                self.comboBox_preview_size.blockSignals(True)
                self.comboBox_preview_size.setCurrentText(preview_size)
                self.comboBox_preview_size.blockSignals(False)
                self._on_preview_size_changed(preview_size)

        if card_spacing is not None:
            self._card_spacing = card_spacing

        if row_height is not None:
            self._row_height = row_height

        tabs = self.timelines_tabs
        for i in range(tabs.count()):
            self._apply_density_to_timeline(tabs.widget(i))

        if self._compact_view_enabled:
            self._apply_compact_view_state()

    def apply_task_style(self, style: str) -> None:
        self._task_style = _normalize_task_style(style)
        tabs = getattr(self, "timelines_tabs", None)
        if tabs is None:
            return
        for i in range(tabs.count()):
            widget = tabs.widget(i)
            if widget and hasattr(widget, "set_task_style"):
                widget.set_task_style(self._task_style)
        self._apply_filters(force=True)

    def apply_shots_layout_mode(self, mode: str):
        """Switch between list and grid layouts for shot cards."""
        self._shots_layout_mode = "grid" if str(mode).lower() == "grid" else "list"
        self._apply_compact_view_state()

    def resizeEvent(self, event):
        previous_mode = self._effective_shots_layout_mode()
        super().resizeEvent(event)
        if not self._compact_view_enabled:
            return
        current_mode = self._effective_shots_layout_mode()
        if current_mode != previous_mode:
            self._apply_compact_view_state()


    def refresh_clicked(self):
        self._start_load_timing("manual_refresh")
        parent_window = self.window()
        self._loading_dialog.show_loading(parent_window, "Refreshing...")
        self._worker.fetch()

    def _on_make_all_previews_clicked(self):
        """Generate previews for all shots that need them in the current job."""
        from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QApplication
        from PyQt6.QtCore import Qt
        
        # Import the preview generator
        try:
            from nuke_headless_tasks import (
                PreviewGenerator,
                preview_input_exists,
                resolve_preview_input_colourspace,
            )
        except ImportError:
            QMessageBox.warning(
                self, "Make All Previews",
                "nuke_headless_tasks module not found.\n\nEnsure it's in the application directory."
            )
            return

        settings = get_settings_manager()
        preview_quality = settings.get("preview_quality", "medium")
        overwrite_enabled = bool(settings.get("preview_overwrite", False))
        nuke_exe_path = settings.get("nuke_exe_path", "")
        
        # Check if we have an active job
        if self._active_job_id is None:
            QMessageBox.warning(self, "Make All Previews", "No job selected.")
            return
        
        # Get the active job data
        active_job = self._jobs_by_id.get(self._active_job_id)
        if not active_job:
            QMessageBox.warning(self, "Make All Previews", "Job data not found.")
            return
        
        # Create the preview generator and check Nuke availability
        generator = PreviewGenerator(nuke_path=nuke_exe_path or None)
        if not generator.nuke_available:
            QMessageBox.warning(
                self, "Make All Previews",
                "Nuke executable not found.\n\nPlease ensure Nuke is installed."
            )
            return
        
        # Collect all shots that need previews
        files_io = filesIO.Folders()
        preview_tasks = self._collect_preview_tasks(active_job, files_io)
        
        if not preview_tasks:
            QMessageBox.information(
                self, "Make All Previews",
                "All shots already have up-to-date previews."
            )
            return
        
        # Confirm with user
        msg = f"Generate {len(preview_tasks)} preview(s)?\n\n"
        
        # Count by type
        v01_count = sum(1 for t in preview_tasks if t["source_type"] == "original_clip")
        render_count = sum(1 for t in preview_tasks if t["source_type"] == "render")
        
        if v01_count:
            msg += f"• {v01_count} from original clips (v001)\n"
        if render_count:
            msg += f"• {render_count} from renders\n"
        
        msg += "\nThis may take a while."
        
        reply = QMessageBox.question(
            self, "Make All Previews", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Create progress dialog
        progress = QProgressDialog(
            "Generating previews...", "Cancel", 0, len(preview_tasks), self
        )
        progress.setWindowTitle("Make All Previews")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        # Process each task
        api = http_help.DjangoAPI()
        completed = 0
        failed = 0
        skipped = 0
        
        for i, task in enumerate(preview_tasks):
            if progress.wasCanceled():
                break
            
            shot = task["shot"]
            shot_id = shot.get("id")
            shot_name = shot.get("title", f"shot_{shot_id}")
            shot_dir = shot.get("base_path", "")
            source_path = task["source_path"]
            version = task["version"]
            job_name = task["job_name"]
            source_type = task["source_type"]
            media_type = task.get("media_type")
            
            # Update progress
            progress.setValue(i)
            progress.setLabelText(
                f"Processing {shot_name} ({source_type})...\n\n"
                f"Completed: {completed} | Failed: {failed} | Skipped: {skipped}"
            )
            QApplication.processEvents()
            
            # Convert paths for local filesystem
            local_shot_dir = files_io.convert_path(shot_dir)
            
            # Build full source path
            local_source = files_io.convert_path(source_path)
            
            # Validate source exists
            if not preview_input_exists(local_source):
                skipped += 1
                print(f"[SKIP] {shot_name}: Source not found - {local_source}")
                continue
            
            # Get shot metadata
            colourspace = resolve_preview_input_colourspace(
                source_type=source_type,
                media_type=media_type,
                input_path=local_source,
                requested_colourspace=shot.get("colourspace", "sRGB"),
            )
            fps = shot.get("fps", 25)
            
            # Check for cancelled
            def check_cancelled():
                QApplication.processEvents()
                return progress.wasCanceled()
            
            # Generate the preview (with overwrite)
            render_fn = generator.generate_preview_with_overwrite if overwrite_enabled else generator.generate_preview
            result = render_fn(
                input_path=local_source,
                shot_dir=local_shot_dir,
                shot_name=shot_name,
                project=job_name,
                artist="ShotBox",
                colourspace=colourspace,
                fps=fps,
                quality=preview_quality,
                version=version,
                on_output=lambda line: print(line),
                check_cancelled=check_cancelled,
            )
            
            if progress.wasCanceled():
                break
            
            if result.error == "FILE_EXISTS" and not overwrite_enabled:
                skipped += 1
                print(f"[SKIP] {shot_name}: Preview already exists")
                continue

            if result.success:
                completed += 1
                # Update the shot's preview_video field in the database
                try:
                    api.update_shot(shot_id, preview_video=result.relative_path)
                except Exception as e:
                    print(f"[WARN] Could not update preview_video for {shot_name}: {e}")
            else:
                failed += 1
                print(f"[FAIL] {shot_name}: {result.error}")
                if result.output_lines:
                    print("[NUKE OUTPUT]")
                    for line in result.output_lines[-20:]:
                        print(line)
        
        progress.close()
        
        # Show summary
        if progress.wasCanceled():
            summary = f"Cancelled.\n\nCompleted: {completed}\nFailed: {failed}\nSkipped: {skipped}"
        else:
            summary = f"Done!\n\nCompleted: {completed}\nFailed: {failed}\nSkipped: {skipped}"
        if failed:
            summary += "\n\nSee console output for Nuke command details."
        
        QMessageBox.information(self, "Make All Previews", summary)
        
        # Refresh to show updated previews
        self._worker.fetch()

    def _on_auto_refresh_changed(self, state):
        """Toggle API polling timer on/off based on checkbox state."""
        if state:
            # Start polling timers
            self._timer.start()
        else:
            # Stop polling timers
            self._timer.stop()

    def set_auto_refresh_paused(self, paused: bool) -> None:
        """Temporarily pause auto-refresh without changing user preference."""
        if paused:
            if self._timer:
                self._timer.stop()
            return
        if self.checkBox_refresh.isChecked() and self._timer:
            self._timer.start()

    @pyqtSlot(str)
    def _on_error(self, msg):
        self._loading_dialog.hide_loading()  # Hide loading dialog on error
        reset_queue = getattr(self, "_reset_task_materialize_queue", None)
        if callable(reset_queue):
            reset_queue()
        self._fail_load_timing()
        self._fail_project_switch_profile(str(msg))
        pass  # Could log to file if needed
        # print("API error:", msg)

    @pyqtSlot(list)
    def _on_data(self, jobs):
        
        # BUGFIX: Defer processing if chunked loading is in progress
        # This prevents duplicate shots from being created by background refresh
        if self._is_chunked_loading:
            self._pending_refresh_jobs = jobs
            return
        
        # Hide loading dialog now that data has arrived (but only after initial load completes)
        if not self._initial_load:
            self._loading_dialog.hide_loading()
        
        # Populate filter dropdowns on first data load
        if not self._dropdowns_populated:
            self._populate_filter_dropdowns()

        # --- populate comboBox_jobs ---
        # Save the currently selected job ID before clearing
        previously_selected_job_id = self.comboBox_jobs.currentData()
        
        self.comboBox_jobs.blockSignals(True)  # avoid triggering anything while updating
        self.comboBox_jobs.clear()

        for job in jobs:
            job_id = job.get("id")
            title = job.get("title", f"Untitled Job {job_id}")
            self.comboBox_jobs.addItem(title, job_id)

        # Restore the previously selected job if it still exists
        if previously_selected_job_id is not None:
            for i in range(self.comboBox_jobs.count()):
                if self.comboBox_jobs.itemData(i) == previously_selected_job_id:
                    self.comboBox_jobs.setCurrentIndex(i)
                    break
        
        self.comboBox_jobs.blockSignals(False)
        
        # index by id for fast lookup
        by_id = {}
        for job in jobs:
            job_id = job.get("id")
            if job_id is None:
                continue
            by_id[job_id] = job
        
        # AUTO-SELECT JOB ON INITIAL LOAD
        # Try to restore last session first, otherwise select first job
        if self._active_job_id is None and self.comboBox_jobs.count() > 0:
            target_job_id = None
            target_job = None
            
            # Check if we should restore last session
            if not self._session_restored and self._settings_manager.get("remember_last_session", True):
                last_job_id = self._settings_manager.get("last_job_id")
                if last_job_id is not None and last_job_id in by_id:
                    target_job_id = last_job_id
                    target_job = by_id[last_job_id]
                    # Store timeline/scroll to restore after job loads
                    self._pending_scroll_restore = {
                        "timeline_index": self._settings_manager.get("last_timeline_index", 0),
                        "scroll_position": self._settings_manager.get("last_scroll_position", 0)
                    }
                    self._session_restored = True
            
            # Fall back to first job if no session to restore
            if target_job is None:
                target_job_id = self.comboBox_jobs.itemData(0)
                target_job = by_id.get(target_job_id)
                self._session_restored = True  # Mark as restored even if just using first job
            
            if target_job:
                # Find and select the job in the combobox
                for i in range(self.comboBox_jobs.count()):
                    if self.comboBox_jobs.itemData(i) == target_job_id:
                        self.comboBox_jobs.setCurrentIndex(i)
                        break
                
                # Store for delayed activation
                self._pending_job = target_job
                self._jobs_by_id = by_id  # Set this early so activation can use it
                # Update loading message
                job_title = target_job.get("title", "job")
                self._loading_dialog.set_message(f"Loading {job_title}...")
                # Use timer to let UI update, then start chunked loading
                QTimer.singleShot(50, self._do_activate_pending_job)
                return  # Exit early, rest will happen after activation

        # update job buttons list in place and avoid full clear
        self._reconcile_jobs_list(jobs)

        # if a job is open, update it using chunked loading (silently, no dialog)
        if self._active_job_id is not None and self._active_job_id in by_id:
            # For background updates, just update without showing dialog
            self._silent_update_job(by_id[self._active_job_id])

        self._jobs_by_id = by_id
        
        # Clear the filter signature cache so filters will be reapplied
        # (but only if data actually changed - _silent_update_job handles this)
        if hasattr(self, '_last_filter_sig'):
            del self._last_filter_sig
        
        # Reapply filters after data update (with small delay to ensure widgets are ready)
        # Use force=True to apply even if user happens to be scrolling
        QTimer.singleShot(50, lambda: self._apply_filters(force=True))
        self.jobs_data_updated.emit(jobs)
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater()
        self._finish_timed_load_after_data()
        
    def _on_job_selected(self, index):
        if index < 0:
            return
        job_id = self.comboBox_jobs.itemData(index)
        job = self._jobs_by_id.get(job_id)
        if job:
            is_explicit_switch = not (
                self._initial_load and self._load_timing_context == "startup"
            )
            if is_explicit_switch:
                self._start_project_switch_profile(job)
            with self._project_load_profiler.measure_wall("job_lookup"):
                if is_explicit_switch:
                    self._start_load_timing("job_switch")
                # Stop any existing loading and reset the loading flag
                self._chunked_loader.stop()
                self._is_chunked_loading = False  # BUGFIX: Reset flag when stopping
                
                # Clear pending scroll restore since user is manually selecting a job
                self._pending_scroll_restore = None
                
                # Find the top-level window for proper centering
                parent_window = self.window()
                
                # Show loading dialog
                job_title = job.get("title", "job")
                self._loading_dialog.show_loading(parent_window, f"Loading {job_title}...")
                
            # Start chunked loading after dialog is shown
            QTimer.singleShot(50, lambda: self._start_job_loading(job))
    
    def _start_job_loading(self, job_data: dict):
        """Start loading a job using chunked loader with per-shot-card progress."""
        self._is_chunked_loading = True  # BUGFIX: Prevent background updates during loading
        self._reset_task_materialize_queue()
        if hasattr(self, "_last_filter_sig"):
            delattr(self, "_last_filter_sig")
        self._active_job_id = job_data.get("id")
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater(loading=True)
        effective_layout_mode = self._effective_shots_layout_mode()
        render_state = self._current_task_render_state()

        with self._project_load_profiler.measure_wall("timeline_build"):
            # Build list of tasks to execute - each task is (callable, description)
            tasks = []
            tabs = self.timelines_tabs
            
            # First, collect existing tabs
            existing_tabs = {tabs.widget(i).objectName(): tabs.widget(i)
                        for i in range(tabs.count())
                        if tabs.widget(i) and tabs.widget(i).objectName()}
            
            timelines = job_data.get("timelines", [])
            keep_tab_names = set()
            
            # We'll store references to timeline widgets we create/find
            self._timeline_widgets = {}
            
            # Phase 1: Create/prepare timeline containers (without shots)
            for tl in timelines:
                tid = tl.get("id")
                name = f"timeline-{tid}"
                title = tl.get("title", f"Timeline {tid}")
                keep_tab_names.add(name)
                
                if name in existing_tabs:
                    # Store existing widget reference
                    self._timeline_widgets[name] = existing_tabs[name]
                    # Task: clear existing shots to prepare for reload
                    w = existing_tabs[name]
                    def prepare_existing_task(widget=w, t=title):
                        idx = tabs.indexOf(widget)
                        if idx >= 0:
                            tabs.setTabText(idx, t)
                        if hasattr(widget, "set_nuke_open_handler"):
                            widget.set_nuke_open_handler(self._handle_nuke_open_request)
                        if hasattr(widget, "set_layout_mode"):
                            widget.set_layout_mode(effective_layout_mode, self._card_spacing)
                        if hasattr(widget, "set_task_style"):
                            widget.set_task_style(self._task_style)
                        if hasattr(widget, "set_compact_mode"):
                            widget.set_compact_mode(self._compact_view_enabled)
                        # Clear existing shot cards
                        if hasattr(widget, 'shots_layout'):
                            layout = widget.shots_layout
                            layout.setSpacing(max(0, int(self._card_spacing)))
                            while layout.count():
                                item = layout.takeAt(0)
                                if item.widget():
                                    item.widget().deleteLater()
                    tasks.append((prepare_existing_task, f"Preparing {title}..."))
                else:
                    # Task: create new empty timeline container
                    def create_timeline_task(data=tl, t=title, n=name, tid_=tid):
                        # Create a minimal TimelineFrame without populating shots
                        w = widgets.TimelineFrame.__new__(widgets.TimelineFrame)
                        QWidget.__init__(w)
                        w.show_hidden = self.show_hidden_shots
                        w._last_timeline = data
                        w._layout_mode = effective_layout_mode
                        w._compact_mode = self._compact_view_enabled
                        w._task_style = self._task_style
                        w._nuke_open_handler = self._handle_nuke_open_request
                        w.setObjectName(n)

                        # Set up the basic layout structure
                        w.frame = QFrame()
                        w.frame.setFrameShape(QFrame.Shape.StyledPanel)
                        frame_layout = QVBoxLayout()
                        w.shots_layout = widgets._create_shots_layout(effective_layout_mode, self._card_spacing)
                        w.shots_layout.setSpacing(max(0, int(self._card_spacing)))
                        frame_layout.addLayout(w.shots_layout)
                        w.frame.setLayout(frame_layout)
                        
                        main_layout = QVBoxLayout(w)
                        main_layout.addWidget(w.frame)
                        w.setLayout(main_layout)
                        
                        tabs.addTab(w, t)
                        self._timeline_widgets[n] = w
                    tasks.append((create_timeline_task, f"Creating {title}..."))
            
            # Tasks: remove stale tabs
            for name, w in existing_tabs.items():
                if name not in keep_tab_names:
                    def remove_task(widget=w, tab_name=name):
                        idx = tabs.indexOf(widget)
                        if idx >= 0:
                            tabs.removeTab(idx)
                        widget.deleteLater()
                    tasks.append((remove_task, "Cleaning up..."))
            
            # Phase 2: Create individual shot cards for each timeline
            hide_hidden_tasks = not self.show_hidden_tasks
            for tl in timelines:
                tid = tl.get("id")
                tl_name = f"timeline-{tid}"
                tl_title = tl.get("title", f"Timeline {tid}")
                shots = tl.get("shots", [])

                shot_entries = []
                for shot in shots:
                    shot_id = shot.get("id")
                    try:
                        shot_id_value = int(shot_id)
                    except (TypeError, ValueError):
                        shot_id_value = 0
                    shot_entries.append(
                        {
                            "shot": shot,
                            "title": str(shot.get("title", "") or "").lower(),
                            "shot_id": shot_id_value,
                            "task_count": self._count_sortable_tasks(shot, hide_hidden_tasks),
                        }
                    )

                for entry in self._sort_shot_entries(shot_entries):
                    shot = entry["shot"]
                    shot_id = shot.get("id")
                    shot_title = shot.get("title", f"Shot {shot_id}")
                    
                    # Task: create one shot card
                    def create_shot_task(shot_data=shot, timeline_name=tl_name):
                        timeline_widget = self._timeline_widgets.get(timeline_name)
                        if timeline_widget and hasattr(timeline_widget, 'shots_layout'):
                            # BUGFIX: Check for existing shot card to prevent duplicates
                            shot_name = f"shot-{shot_data.get('id')}"
                            layout = timeline_widget.shots_layout
                            for i in range(layout.count()):
                                item = layout.itemAt(i)
                                card_widget = item.widget() if item else None
                                if card_widget and card_widget.objectName() == shot_name:
                                    # Already exists, update it instead of creating duplicate
                                    card_widget.update_from_data(shot_data)
                                    if hasattr(card_widget, "set_task_render_state"):
                                        card_widget.set_task_render_state(render_state)
                                    if hasattr(card_widget, "set_nuke_open_handler"):
                                        card_widget.set_nuke_open_handler(self._handle_nuke_open_request)
                                    if hasattr(card_widget, "set_task_style"):
                                        card_widget.set_task_style(self._task_style)
                                    return

                            with self._project_load_profiler.measure_work("shot_card_create"):
                                card = widgets.ShotCard(shot_data, task_style=self._task_style)
                            card.setObjectName(shot_name)
                            if hasattr(card, "set_task_render_state"):
                                card.set_task_render_state(render_state)
                            card.set_nuke_open_handler(self._handle_nuke_open_request)
                            card.set_compact_mode(self._compact_view_enabled)
                            if (
                                (not self._compact_view_enabled)
                                and self._row_height
                                and int(self._row_height) > 0
                            ):
                                card.setMinimumHeight(int(self._row_height))
                            # Don't set visibility here - _apply_filters will handle it after loading
                            timeline_widget.shots_layout.addWidget(card)
                    
                    tasks.append((create_shot_task, f"{tl_title}: {shot_title}"))
            
            # Phase 3: Add spacers to each timeline after shots are loaded
            for tl in timelines:
                tid = tl.get("id")
                tl_name = f"timeline-{tid}"
                
                def add_spacer_task(timeline_name=tl_name):
                    timeline_widget = self._timeline_widgets.get(timeline_name)
                    if timeline_widget and hasattr(timeline_widget, 'shots_layout'):
                        from PyQt6.QtWidgets import QSpacerItem, QSizePolicy
                        if effective_layout_mode == "list":
                            spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
                            timeline_widget.shots_layout.addItem(spacer)
                tasks.append((add_spacer_task, "Finalizing..."))
            
            # Store job data for after loading completes
            self._pending_job_data = job_data
            
            # Start the chunked loader
            self._chunked_loader.start_loading(tasks, self._on_job_load_complete)
    
    def _on_load_progress(self, current: int, total: int, message: str):
        """Handle progress updates from chunked loader."""
        self._loading_dialog.set_progress(current, total)
        self._loading_dialog.set_message(f"Loading shots ({current}/{total})")
    
    def _on_load_detail(self, detail: str):
        """Handle detail updates from chunked loader."""
        self._loading_dialog.set_detail(detail)
    
    def _on_load_finished(self):
        """Called when chunked loader finishes all tasks."""
        pass  # Actual completion handled by callback
    
    def _on_job_load_complete(self):
        """Called when job loading is fully complete."""
        # BUGFIX: Clear loading flag first
        self._is_chunked_loading = False
        
        # Reconcile jobs list
        if hasattr(self, '_pending_job_data'):
            self._reconcile_jobs_list([self._pending_job_data])
        
        # Hide loading dialog
        self._loading_dialog.hide_loading()
        self._finish_load_timing()
        self._complete_project_switch_profile()
        self._initial_load = False
        
        # Restore timeline tab and scroll position if pending
        if self._pending_scroll_restore:
            restore_data = self._pending_scroll_restore
            self._pending_scroll_restore = None
            timeline_index = restore_data.get("timeline_index", 0)
            tabs = self.timelines_tabs
            if tabs.count() > 0 and 0 <= timeline_index < tabs.count():
                tabs.blockSignals(True)
                tabs.setCurrentIndex(timeline_index)
                tabs.blockSignals(False)
            scroll_position = restore_data.get("scroll_position", 0)
            if scroll_position > 0:
                QTimer.singleShot(50, lambda: self._restore_scroll_position(scroll_position))
        else:
            # No session to restore, save current state (user selected a new job)
            QTimer.singleShot(150, self._save_session_state)

        self._apply_compact_view_state()
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater()
        # Reapply filters and task materialization after the new shot cards exist.
        QTimer.singleShot(50, lambda: self._apply_filters(force=True))
        
        # BUGFIX: Process any refresh data that arrived during loading
        if self._pending_refresh_jobs is not None:
            pending = self._pending_refresh_jobs
            self._pending_refresh_jobs = None
            # Process with a small delay to let UI settle
            QTimer.singleShot(100, lambda: self._on_data(pending))
        if hasattr(self, "_pending_job_data") and self._pending_job_data:
            self.active_job_changed.emit(self._pending_job_data)
    
    def _restore_session_state(self, restore_data: dict):
        """Restore timeline tab and scroll position from saved session."""
        # Restore timeline tab
        timeline_index = restore_data.get("timeline_index", 0)
        tabs = self.timelines_tabs
        if tabs.count() > 0 and timeline_index < tabs.count():
            tabs.setCurrentIndex(timeline_index)
        
        # Restore scroll position with a small delay to ensure tab is active
        scroll_position = restore_data.get("scroll_position", 0)
        if scroll_position > 0:
            QTimer.singleShot(50, lambda: self._restore_scroll_position(scroll_position))
    
    def _restore_scroll_position(self, position: int):
        """Restore scroll position for the current timeline tab."""
        tabs = self.timelines_tabs
        current_widget = tabs.currentWidget()
        if current_widget:
            # Find QScrollArea in the current tab
            from PyQt6.QtWidgets import QScrollArea
            scroll_areas = current_widget.findChildren(QScrollArea)
            for scroll_area in scroll_areas:
                vbar = scroll_area.verticalScrollBar()
                if vbar:
                    vbar.setValue(position)
                    break
    
    def _save_session_state(self):
        """Save current session state (job, timeline, scroll) to settings."""
        if not self._settings_manager.get("remember_last_session", True):
            return
        
        # Save current job ID
        if self._active_job_id is not None:
            self._settings_manager.set("last_job_id", self._active_job_id, save=False)
        
        # Save current timeline tab index
        tabs = self.timelines_tabs
        self._settings_manager.set("last_timeline_index", tabs.currentIndex(), save=False)
        
        # Save scroll position of current timeline
        scroll_position = 0
        current_widget = tabs.currentWidget()
        if current_widget:
            from PyQt6.QtWidgets import QScrollArea
            scroll_areas = current_widget.findChildren(QScrollArea)
            for scroll_area in scroll_areas:
                vbar = scroll_area.verticalScrollBar()
                if vbar:
                    scroll_position = vbar.value()
                    break
        self._settings_manager.set("last_scroll_position", scroll_position, save=False)
        
        # Save to file
        self._settings_manager.save()
    
    def _silent_update_job(self, job_data: dict):
        """Update job data silently without showing loading dialog (for background refreshes)."""
        # Check if data has actually changed using signature
        new_sig = _sig(job_data)
        if hasattr(self, '_last_job_sig') and self._last_job_sig == new_sig:
            # Data hasn't changed, skip the update entirely
            return
        self._last_job_sig = new_sig
        self._reset_task_materialize_queue()
        
        tabs = self.timelines_tabs
        
        existing = {tabs.widget(i).objectName(): tabs.widget(i)
                    for i in range(tabs.count())
                    if tabs.widget(i) and tabs.widget(i).objectName()}

        timelines = job_data.get("timelines", [])
        keep_names = set()
        effective_layout_mode = self._effective_shots_layout_mode()

        for tl in timelines:
            tid = tl.get("id")
            name = f"timeline-{tid}"
            title = tl.get("title", f"Timeline {tid}")
            keep_names.add(name)
            
            if name in existing:
                w = existing[name]
                if hasattr(w, "set_nuke_open_handler"):
                    w.set_nuke_open_handler(self._handle_nuke_open_request)
                if hasattr(w, "set_task_style"):
                    w.set_task_style(self._task_style)
                if hasattr(w, "update_from_data"):
                    w.update_from_data(tl)
                idx = tabs.indexOf(w)
                if idx >= 0:
                    tabs.setTabText(idx, title)
            else:
                w = widgets.TimelineFrame(
                    tl,
                    self.show_hidden_shots,
                    layout_mode=effective_layout_mode,
                    card_spacing=self._card_spacing,
                    compact_mode=self._compact_view_enabled,
                    task_style=self._task_style,
                    nuke_open_handler=self._handle_nuke_open_request,
                )
                w.setObjectName(name)
                if hasattr(w, "shots_layout"):
                    w.shots_layout.setSpacing(max(0, int(self._card_spacing)))
                tabs.addTab(w, title)
        
        # Remove stale tabs
        for name, w in existing.items():
            if name not in keep_names:
                idx = tabs.indexOf(w)
                if idx >= 0:
                    tabs.removeTab(idx)
                w.deleteLater()

        self._apply_compact_view_state()
        updater = getattr(self, "_update_assets_action_buttons", None)
        if callable(updater):
            updater()
    
    def _do_activate_pending_job(self):
        """Actually activate the job after dialog has shown."""
        if hasattr(self, '_pending_job') and self._pending_job:
            self._start_job_loading(self._pending_job)
            self._pending_job = None


    def _reconcile_jobs_list(self, jobs_list):
        layout = self.jobs_container.layout()
        mapping = {}
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is not None and w.objectName():
                mapping[w.objectName()] = w

        desired_order = []
        for job in jobs_list:
            jid = job.get("id")
            title = job.get("title", "Untitled Job")
            if jid is None:
                continue
            name = f"job-{jid}"
            desired_order.append(name)
            if name in mapping:
                # update button text if changed
                if mapping[name].text() != title:
                    mapping[name].setText(title)
                # update stored data for later activations
                mapping[name].data = job
            """else:
                btn = widgets.JobBtn(
                    title=title,
                    data=job,
                    frame_layout=self.timelines_layout,
                    on_activate=self._activate_job
                )
                btn.setObjectName(name)
                layout.addWidget(btn)
                

        # remove stale
        for name, w in list(mapping.items()):
            if name not in desired_order:
                layout.removeWidget(w)
                w.deleteLater()"""

        # order
        widgets._ensure_order(layout, desired_order)
        # --- add vertical spacer at the end ---
        spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        layout.addItem(spacer)
