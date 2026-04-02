from __future__ import annotations

import os
import inspect
import random
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import uic
from PyQt6.QtCore import Qt, QSignalBlocker, QTimer, QUrl, QEvent
from PyQt6.QtGui import QAction, QActionGroup, QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QLayout,
    QComboBox,
    QHBoxLayout,
    QBoxLayout,
    QMenu,
    QToolButton,
    QInputDialog,
    QDialog,
    QPlainTextEdit,
    QDialogButtonBox,
    QSpacerItem,
    QSizePolicy,
    QStackedWidget,
    QMessageBox,
    QLineEdit,
    QDoubleSpinBox,
)
try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False
    print("PyQt6-Multimedia not installed. Video preview will be disabled.")

import http_help
import filesIO
import project_load_profiler
from nuke_lock_utils import parse_lock_info
from task_create_dialog import TaskCreateDialog
from image_loader import ImageLoader
from flow_layout import FlowLayout

if TYPE_CHECKING:
    import matchmove_helpers

# Get the directory where this script is located (for cross-platform path handling)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COLOURSPACE_PRESETS = [
    "Input - ARRI - V3 LogC (EI800) - Wide Gamut",
    "Input - ARRI - V4 LogC (EI800) - Wide Gamut4",
    "Input - Sony - Linear - Venice S-Gamut3.Cine",
    "Input - Sony - S-Log3 - Venice S-Gamut3.Cine",
    "Input - Canon - Curve - Canon-Log3",
    "Input - RED - REDLog3G10 - REDWideGamutRGB",
    "color_picking",
    "Output - Rec.709",
    "ACES - ACEScg",
]

# Import the UI setup functions (replaces uic.loadUi)
from shot_card import setup_shot_card_ui
from task_card import setup_task_widget_ui

BASE_URL = "http://192.168.10.207:8000"
THUMBNAILS_ENABLED = True
THUMBNAIL_TARGET_WIDTH = 240
SHOT_ASSETS_DIRNAME = "Shot_Assets"
MATCHMOVE_DIRNAME = "matchmove"
CAMERA_PRESETS = {
    "Alexa 35": {
        "sensor_width_mm": 27.99,
        "sensor_height_mm": 19.22,
    },
    "Alexa LF": {
        "sensor_width_mm": 36.70,
        "sensor_height_mm": 25.54,
    },
}
DEFAULT_FPS = 25.0
DEFAULT_FOCAL_LENGTH_MM = 35.0
_MATCHMOVE_HELPERS = None
_MATCHMOVE_HELPERS_IMPORT_ERROR = None


def _load_matchmove_helpers():
    global _MATCHMOVE_HELPERS, _MATCHMOVE_HELPERS_IMPORT_ERROR
    global CAMERA_PRESETS, DEFAULT_FOCAL_LENGTH_MM, DEFAULT_FPS

    if _MATCHMOVE_HELPERS is not None:
        return _MATCHMOVE_HELPERS
    if _MATCHMOVE_HELPERS_IMPORT_ERROR is not None:
        raise _MATCHMOVE_HELPERS_IMPORT_ERROR

    try:
        import matchmove_helpers as helpers
    except Exception as exc:
        _MATCHMOVE_HELPERS_IMPORT_ERROR = exc
        raise

    _MATCHMOVE_HELPERS = helpers
    CAMERA_PRESETS = dict(getattr(helpers, "CAMERA_PRESETS", CAMERA_PRESETS))
    DEFAULT_FOCAL_LENGTH_MM = float(getattr(helpers, "DEFAULT_FOCAL_LENGTH_MM", DEFAULT_FOCAL_LENGTH_MM))
    DEFAULT_FPS = float(getattr(helpers, "DEFAULT_FPS", DEFAULT_FPS))
    return helpers


def build_shot_assets_directory(shot_root: str) -> str:
    base_path = os.path.abspath(os.path.expanduser(str(shot_root)))
    return os.path.join(base_path, SHOT_ASSETS_DIRNAME)


def build_shot_matchmove_directory(shot_root: str) -> str:
    return str(Path(build_shot_assets_directory(shot_root)) / MATCHMOVE_DIRNAME)


def find_latest_matchmove_project(matchmove_dir: str, shot_name: str):
    return _load_matchmove_helpers().find_latest_matchmove_project(matchmove_dir, shot_name)


def list_matchmove_projects(matchmove_dir: str):
    return _load_matchmove_helpers().list_matchmove_projects(matchmove_dir)


def list_valid_precomp_sequences(shot_root: str):
    return _load_matchmove_helpers().list_valid_precomp_sequences(shot_root)


def run_headless_3de(request, log_callback):
    return _load_matchmove_helpers().run_headless_3de(request, log_callback)


def cleanup_headless_artifacts(result) -> None:
    _load_matchmove_helpers().cleanup_headless_artifacts(result)


def open_3de_project(project_path: str):
    return _load_matchmove_helpers().open_3de_project(project_path)


def resolve_project_path(matchmove_dir: str, shot_name: str):
    return _load_matchmove_helpers().resolve_project_path(matchmove_dir, shot_name)


def set_global_thumbnail_mode(enabled: bool, width: int | None = None) -> None:
    global THUMBNAILS_ENABLED, THUMBNAIL_TARGET_WIDTH
    THUMBNAILS_ENABLED = bool(enabled)
    if width is not None:
        THUMBNAIL_TARGET_WIDTH = max(1, int(width))

def clear_container(container):
    if isinstance(container, QLayout):
        while container.count():
            item = container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
    elif isinstance(container, QWidget):
        layout = container.layout()
        if layout is not None:
            clear_container(layout)

def _children_by_object_name(layout: QLayout) -> dict[str, QWidget]:
    out = {}
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is not None and w.objectName():
            out[w.objectName()] = w
    return out


def _ordered_widget_names(layout: QLayout) -> list[str]:
    names = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        widget = item.widget() if item else None
        if widget is not None and widget.objectName():
            names.append(widget.objectName())
    return names


def _ensure_order(layout: QLayout, desired_names: list[str]):
    """Move existing widgets to match desired order without recreating."""
    if not desired_names:
        return

    current_names = _ordered_widget_names(layout)
    desired_existing = [name for name in desired_names if name in current_names]
    current_existing = [name for name in current_names if name in set(desired_existing)]
    if desired_existing == current_existing:
        return

    if hasattr(layout, "reorder_by_object_names"):
        if layout.reorder_by_object_names(desired_names):
            layout.invalidate()
        return

    widgets_by_name = _children_by_object_name(layout)
    if not hasattr(layout, "insertWidget"):
        return

    pos = 0
    for desired in desired_names:
        widget = widgets_by_name.get(desired)
        if widget is None:
            continue
        current_index = -1
        for i in range(layout.count()):
            item = layout.itemAt(i)
            candidate = item.widget() if item else None
            if candidate is widget:
                current_index = i
                break
        if current_index < 0:
            continue
        if current_index != pos:
            layout.removeWidget(widget)
            layout.insertWidget(pos, widget)
        pos += 1


def _create_shot_card(
    shot_data: dict,
    *,
    parent=None,
    task_style: str = "card",
    api=None,
    folders=None,
):
    kwargs = {"task_style": task_style}
    init_params = inspect.signature(ShotCard.__init__).parameters
    if "parent" in init_params:
        kwargs["parent"] = parent
    if api is not None and "api" in init_params:
        kwargs["api"] = api
    if folders is not None and "folders" in init_params:
        kwargs["folders"] = folders
    return ShotCard(shot_data, **kwargs)


def _normalize_layout_mode(mode: str) -> str:
    if str(mode).lower() == "grid":
        return "grid"
    return "list"


def _normalize_task_style(style: str) -> str:
    if str(style).lower() == "checklist":
        return "checklist"
    return "card"


def _create_shots_layout(mode: str, spacing: int | None = None) -> QLayout:
    layout_mode = _normalize_layout_mode(mode)
    if layout_mode == "grid":
        layout = FlowLayout(None, margin=0, h_spacing=10, v_spacing=10)
    else:
        layout = QVBoxLayout()
    if spacing is not None:
        layout.setSpacing(max(0, int(spacing)))
    return layout


def _set_dynamic_property(widget: QWidget | None, name: str, value) -> None:
    if widget is None:
        return
    widget.setProperty(name, value)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def _compact_text(value, limit: int) -> str:
    text = str(value or "")
    if limit <= 3 or len(text) <= limit:
        return text
    return f"{text[:limit - 3].rstrip()}..."


def _show_matchmove_unavailable(parent: QWidget | None, exc: Exception) -> None:
    QMessageBox.warning(
        parent,
        "Matchmove Unavailable",
        "The matchmove / 3DE helper could not be loaded.\n\n"
        f"{exc}",
    )


class PlainTextEditDialog(QDialog):
    def __init__(self, parent=None, title="Edit text", text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.edit = QPlainTextEdit(self)
        self.edit.setPlainText(text)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.edit.toPlainText()


class SingleCameraMatchmoveDialog(QDialog):
    def __init__(self, sequence_folder: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Matchmove")

        layout = QVBoxLayout(self)

        sequence_label = QLabel("EXR folder")
        layout.addWidget(sequence_label)

        self.sequence_line_edit = QLineEdit(sequence_folder)
        self.sequence_line_edit.setReadOnly(True)
        layout.addWidget(self.sequence_line_edit)

        camera_label = QLabel("Camera preset")
        layout.addWidget(camera_label)

        self.camera_combo_box = QComboBox()
        self.camera_combo_box.addItems(CAMERA_PRESETS.keys())
        self.camera_combo_box.setCurrentText("Alexa 35")
        layout.addWidget(self.camera_combo_box)

        focal_label = QLabel("Focal length (mm)")
        layout.addWidget(focal_label)

        self.focal_spin_box = QDoubleSpinBox()
        self.focal_spin_box.setRange(1.0, 999.0)
        self.focal_spin_box.setDecimals(2)
        self.focal_spin_box.setValue(DEFAULT_FOCAL_LENGTH_MM)
        layout.addWidget(self.focal_spin_box)

        fps_label = QLabel("FPS")
        layout.addWidget(fps_label)

        self.fps_spin_box = QDoubleSpinBox()
        self.fps_spin_box.setRange(1.0, 120.0)
        self.fps_spin_box.setDecimals(3)
        self.fps_spin_box.setValue(DEFAULT_FPS)
        layout.addWidget(self.fps_spin_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def camera_preset_name(self) -> str:
        return self.camera_combo_box.currentText().strip()

    @property
    def focal_length_mm(self) -> float:
        return float(self.focal_spin_box.value())

    @property
    def fps(self) -> float:
        return float(self.fps_spin_box.value())

class TaskWidget(QWidget):
    def __init__(self, task_data=None, presentation="card"):
        super().__init__()
        task_data = task_data or {}

        self._presentation = _normalize_task_style(presentation)
        setup_task_widget_ui(self, presentation=self._presentation)

        self.setObjectName(f"task-{task_data.get('id','unknown')}")
        self._api = http_help.DjangoAPI()
        self._task_id = task_data.get("id")
        self._data = {}
        self._compact_mode = False
        self._last_non_done_status = None
        self._title_edit_active = False
        self._title_edit_ignore_finish = False
        self._title_edit_original = ""

        self._apply_presentation_properties()

        if getattr(self, "drag_handle", None):
            self.drag_handle.setVisible(False)

        self.btn_status = getattr(self, "btn_status", None)
        self.btn_assigned = getattr(self, "btn_assigned", None)
        self.btn_priority = getattr(self, "btn_priority", None)
        self.btn_budget_hours = getattr(self, "btn_budget_hours", None)

        if hasattr(self, "btn_delete_task") and self.btn_delete_task:
            self.btn_delete_task.clicked.connect(self._on_delete_clicked)

        self._status_menu = QMenu(self)
        for value, label in http_help.TASK_STATUS_CHOICES:
            act = QAction(label, self._status_menu)
            act.setData(value)
            self._status_menu.addAction(act)
        if self.btn_status:
            self.btn_status.clicked.connect(
                lambda: self._open_menu_below(self.btn_status, self._status_menu)
            )
        self._status_menu.triggered.connect(self._on_status_action)

        if hasattr(self, "btn_task_title") and self.btn_task_title:
            self.btn_task_title.clicked.connect(self._on_title_clicked)
        if hasattr(self, "edit_title_inline") and self.edit_title_inline:
            self.edit_title_inline.editingFinished.connect(self._on_inline_title_edit_finished)
            self.edit_title_inline.installEventFilter(self)
        if hasattr(self, "btn_notes") and self.btn_notes:
            self.btn_notes.clicked.connect(self._on_notes_clicked)
        if hasattr(self, "edit_notes_inline") and self.edit_notes_inline:
            self.edit_notes_inline.editingFinished.connect(self._on_inline_notes_edit_finished)

        self._artist_menu = QMenu(self)
        if self.btn_assigned:
            self.btn_assigned.clicked.connect(self._on_assign_clicked)
        self._artist_menu.triggered.connect(self._on_artist_action)

        self._priority_menu = QMenu(self)
        for prio in range(10, 0, -1):
            act = QAction(f"Priority {prio}", self._priority_menu)
            act.setData(prio)
            self._priority_menu.addAction(act)
        if self.btn_priority:
            self.btn_priority.clicked.connect(
                lambda: self._open_menu_below(self.btn_priority, self._priority_menu)
            )
            self._priority_menu.triggered.connect(self._on_priority_action)

        if self.btn_budget_hours:
            self.btn_budget_hours.clicked.connect(self._on_budget_hours_clicked)

        if hasattr(self, "btn_hide_task") and self.btn_hide_task:
            self.btn_hide_task.clicked.connect(self._on_hide_clicked)

        if hasattr(self, "check_done_task") and self.check_done_task:
            self.check_done_task.toggled.connect(self._on_done_toggled)

        self.update_from_data(task_data)

    def _is_checklist_presentation(self) -> bool:
        return self._presentation == "checklist"

    def _apply_presentation_properties(self) -> None:
        for widget in (
            getattr(self, "task_frame", None),
            getattr(self, "btn_task_title", None),
            getattr(self, "edit_title_inline", None),
            getattr(self, "btn_status", None),
            getattr(self, "btn_assigned", None),
            getattr(self, "btn_priority", None),
            getattr(self, "btn_budget_hours", None),
            getattr(self, "btn_notes", None),
            getattr(self, "edit_notes_inline", None),
            getattr(self, "btn_hide_task", None),
            getattr(self, "btn_delete_task", None),
            getattr(self, "check_done_task", None),
        ):
            _set_dynamic_property(widget, "presentation", self._presentation)
        _set_dynamic_property(getattr(self, "task_frame", None), "flash", "false")

    # ----- helpers -----
    def _parent_shot_card(self):
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, ShotCard):
                return parent
            parent = parent.parent()
        return None

    def _refresh_parent_widget_state(self) -> ShotCard | None:
        parent = self._parent_shot_card()
        if parent is not None and hasattr(parent, "_sync_task_widgets_from_data"):
            parent._sync_task_widgets_from_data()
        return parent

    def _sync_parent_task_data(self, updated_task: dict) -> ShotCard | None:
        parent = self._parent_shot_card()
        if parent is None:
            return None
        parent_data = getattr(parent, "data", None) or {}
        tasks = parent_data.get("tasks", [])
        for task in tasks:
            if task.get("id") == self._task_id:
                if isinstance(updated_task, dict):
                    task.update(updated_task)
                break
        return parent

    def _apply_updated_task(self, updated: dict, *, flash_done: bool = False) -> None:
        self.update_from_data(updated)
        self._sync_parent_task_data(updated)
        self._refresh_parent_widget_state()
        if flash_done and self._is_checklist_presentation():
            self._flash_completion_success()
        self._notify_filter_state_changed()

    def _notify_filter_state_changed(self) -> None:
        window = self.window()
        apply_filters = getattr(window, "_apply_filters", None)
        if not callable(apply_filters):
            return
        if hasattr(window, "_last_filter_sig"):
            delattr(window, "_last_filter_sig")
        try:
            apply_filters(force=True)
        except TypeError:
            apply_filters()

    def _open_menu_below(self, widget: QPushButton | None, menu: QMenu):
        if widget is None:
            return
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        menu.exec(pos)

    def _status_label(self, value: str) -> str:
        mapping = dict(http_help.TASK_STATUS_CHOICES)
        return mapping.get(value, value or "")

    def _artist_label(self, user_id) -> str:
        if user_id in (None, "", 0):
            return "Unassigned"
        try:
            name = self._api.username_from_id(user_id)
            return name or "Unassigned"
        except Exception:
            return "Unassigned"

    def _priority_label(self, value) -> str:
        try:
            v = int(value)
        except Exception:
            v = 5
        v = max(1, min(10, v))
        return f"P{v}"

    def _format_hours(self, value) -> str:
        try:
            hours = float(value or 0)
        except Exception:
            hours = 0.0
        return f"{hours:.1f}h"

    def _set_inline_title_text(self, value) -> None:
        if not (hasattr(self, "edit_title_inline") and self.edit_title_inline):
            return
        blocker = QSignalBlocker(self.edit_title_inline)
        self.edit_title_inline.setText(str(value or "").replace("\n", " ").replace("\r", " "))
        del blocker

    def _show_inline_title_editor(self, active: bool) -> None:
        self._title_edit_active = bool(active)
        title_stack = getattr(self, "title_stack", None)
        editor = getattr(self, "edit_title_inline", None)
        title_button = getattr(self, "btn_task_title", None)
        if title_stack is not None and editor is not None and title_button is not None:
            title_stack.setCurrentWidget(editor if self._title_edit_active else title_button)
            return
        if title_button is not None:
            title_button.setVisible(not self._title_edit_active)
        if editor is not None:
            editor.setVisible(self._title_edit_active)

    def _focus_inline_title_editor(self) -> None:
        if not (hasattr(self, "edit_title_inline") and self.edit_title_inline):
            return
        self.edit_title_inline.setFocus(Qt.FocusReason.OtherFocusReason)
        self.edit_title_inline.selectAll()

    def _begin_inline_title_edit(self) -> None:
        if not self._is_checklist_presentation():
            return
        if not (hasattr(self, "edit_title_inline") and self.edit_title_inline):
            return
        current_title = str((self._data or {}).get("title") or "")
        self._title_edit_original = current_title
        self._set_inline_title_text(current_title)
        self._show_inline_title_editor(True)
        QTimer.singleShot(0, self._focus_inline_title_editor)

    def _end_inline_title_edit(self, restore_text: str | None = None) -> None:
        if restore_text is not None:
            self._set_inline_title_text(restore_text)
        self._show_inline_title_editor(False)
        self._title_edit_original = ""

    def _cancel_inline_title_edit(self) -> None:
        if not self._title_edit_active:
            return
        restore_text = self._title_edit_original or str((self._data or {}).get("title") or "")
        self._title_edit_ignore_finish = True
        self._end_inline_title_edit(restore_text=restore_text)
        if hasattr(self, "btn_task_title") and self.btn_task_title:
            self.btn_task_title.setFocus(Qt.FocusReason.OtherFocusReason)

    def _inline_notes_display_text(self, value) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in text:
            return text
        parts = [part.strip() for part in text.split("\n") if part.strip()]
        return " / ".join(parts)

    def _set_inline_notes_text(self, value) -> None:
        if not (hasattr(self, "edit_notes_inline") and self.edit_notes_inline):
            return
        blocker = QSignalBlocker(self.edit_notes_inline)
        self.edit_notes_inline.setText(self._inline_notes_display_text(value))
        del blocker

    def _fallback_undo_status(self) -> str:
        if self._last_non_done_status and self._last_non_done_status != "done":
            return self._last_non_done_status
        artist_id = (self._data or {}).get("artist")
        if artist_id not in (None, "", 0):
            return "in_progress"
        return "unassigned"

    def _set_done_state_properties(self, done: bool) -> None:
        done_value = "true" if done else "false"
        for widget in (
            getattr(self, "task_frame", None),
            getattr(self, "btn_task_title", None),
            getattr(self, "edit_title_inline", None),
            getattr(self, "btn_assigned", None),
            getattr(self, "btn_priority", None),
            getattr(self, "btn_budget_hours", None),
            getattr(self, "btn_notes", None),
            getattr(self, "edit_notes_inline", None),
        ):
            _set_dynamic_property(widget, "done", done_value)
        if hasattr(self, "btn_task_title") and self.btn_task_title:
            font = self.btn_task_title.font()
            font.setStrikeOut(False)
            self.btn_task_title.setFont(font)

    def _flash_completion_success(self) -> None:
        frame = getattr(self, "task_frame", None)
        if frame is None:
            return
        _set_dynamic_property(frame, "flash", "true")
        QTimer.singleShot(180, lambda: _set_dynamic_property(frame, "flash", "false"))

    def _apply_compact_properties(self, enabled: bool) -> None:
        compact_value = "true" if enabled else "false"
        for widget in (
            getattr(self, "task_frame", None),
            getattr(self, "btn_task_title", None),
            getattr(self, "edit_title_inline", None),
            getattr(self, "btn_status", None),
            getattr(self, "btn_assigned", None),
            getattr(self, "btn_priority", None),
            getattr(self, "btn_budget_hours", None),
            getattr(self, "btn_notes", None),
            getattr(self, "edit_notes_inline", None),
            getattr(self, "btn_hide_task", None),
            getattr(self, "btn_delete_task", None),
            getattr(self, "check_done_task", None),
        ):
            _set_dynamic_property(widget, "compact", compact_value)

    def set_compact_mode(self, enabled: bool) -> None:
        self._compact_mode = bool(enabled)
        self._apply_compact_properties(self._compact_mode)

        title = (self._data or {}).get("title") or "Task"
        notes = str((self._data or {}).get("notes") or "")
        status_text = self._status_label((self._data or {}).get("status"))
        artist_text = self._artist_label((self._data or {}).get("artist"))
        priority_text = self._priority_label((self._data or {}).get("priority", 5))
        hours_text = self._format_hours((self._data or {}).get("budget_hours", 0))
        is_hidden = bool((self._data or {}).get("hidden", False))
        is_done = ((self._data or {}).get("status") == "done")

        task_layout = getattr(self, "task_layout", None)
        if task_layout is not None:
            if self._is_checklist_presentation():
                task_layout.setContentsMargins(
                    6 if self._compact_mode else 8,
                    4 if self._compact_mode else 5,
                    6 if self._compact_mode else 8,
                    4 if self._compact_mode else 5,
                )
                task_layout.setSpacing(4 if self._compact_mode else 6)
            else:
                task_layout.setContentsMargins(
                    4 if self._compact_mode else 6,
                    4 if self._compact_mode else 6,
                    4 if self._compact_mode else 6,
                    4 if self._compact_mode else 6,
                )
                task_layout.setSpacing(2 if self._compact_mode else 4)

        if self._is_checklist_presentation():
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)

            if hasattr(self, "btn_task_title") and self.btn_task_title:
                self.btn_task_title.setText(_compact_text(title, 26 if self._compact_mode else 42))
                tooltip_parts = [part for part in (title, notes) if part]
                tooltip_text = "\n".join(tooltip_parts)
                self.btn_task_title.setToolTip(tooltip_text or title)
            if hasattr(self, "edit_title_inline") and self.edit_title_inline:
                self.edit_title_inline.setPlaceholderText("Task name")
                self.edit_title_inline.setMinimumWidth(110 if self._compact_mode else 150)
                self.edit_title_inline.setToolTip(title or "Task name")
            if hasattr(self, "edit_notes_inline") and self.edit_notes_inline:
                self.edit_notes_inline.setPlaceholderText("Add note...")
                self.edit_notes_inline.setMinimumWidth(90 if self._compact_mode else 120)
                self.edit_notes_inline.setToolTip(notes or "Add note")
                self.edit_notes_inline.setVisible(True)
            if self.btn_status:
                self.btn_status.setText(_compact_text(status_text, 12 if self._compact_mode else 18))
                self.btn_status.setToolTip(status_text)
                self.btn_status.setMinimumWidth(54 if self._compact_mode else 64)
            if self.btn_assigned:
                self.btn_assigned.setText(_compact_text(artist_text, 10 if self._compact_mode else 14))
                self.btn_assigned.setToolTip(artist_text)
                self.btn_assigned.setMinimumWidth(60 if self._compact_mode else 70)
            if self.btn_priority:
                self.btn_priority.setText(priority_text)
                self.btn_priority.setVisible(True)
            if self.btn_budget_hours:
                self.btn_budget_hours.setText(hours_text)
                self.btn_budget_hours.setVisible(True)
            if hasattr(self, "btn_notes") and self.btn_notes:
                self.btn_notes.setText("Note")
                self.btn_notes.setVisible(bool(notes))
                self.btn_notes.setToolTip(notes or "Edit notes")
            if hasattr(self, "btn_hide_task") and self.btn_hide_task:
                self.btn_hide_task.setText("U" if is_hidden else "H")
                self.btn_hide_task.setToolTip("Unhide task" if is_hidden else "Hide task")
            if hasattr(self, "btn_delete_task") and self.btn_delete_task:
                self.btn_delete_task.setVisible(True)
            if hasattr(self, "check_done_task") and self.check_done_task:
                self.check_done_task.setToolTip("Mark task done" if not is_done else "Restore task status")
            if hasattr(self, "task_frame") and self.task_frame:
                self.task_frame.setToolTip("\n".join(part for part in (title, notes) if part))
            self._show_inline_title_editor(self._title_edit_active)
        else:
            top_row = getattr(self, "top_row", None)
            middle_row = getattr(self, "middle_row", None)
            planning_row = getattr(self, "planning_row", None)
            notes_row = getattr(self, "notes_row", None)
            for row in (top_row, middle_row, planning_row, notes_row):
                if row is None:
                    continue
                row.setSpacing(2 if self._compact_mode else 4)

            self.setMinimumWidth(120 if self._compact_mode else 160)
            self.setMaximumWidth(160 if self._compact_mode else 240)

            if hasattr(self, "btn_delete_task") and self.btn_delete_task:
                self.btn_delete_task.setVisible(not self._compact_mode)
            if hasattr(self, "btn_notes") and self.btn_notes:
                self.btn_notes.setVisible(not self._compact_mode)
            if getattr(self, "drag_handle", None) and self._compact_mode:
                self.drag_handle.setVisible(False)

            if self.btn_status:
                self.btn_status.setMinimumWidth(56 if self._compact_mode else 70)
                self.btn_status.setMaximumWidth(78 if self._compact_mode else 16777215)
            if self.btn_assigned:
                self.btn_assigned.setMinimumWidth(56 if self._compact_mode else 70)
                self.btn_assigned.setMaximumWidth(84 if self._compact_mode else 16777215)
            if self.btn_priority:
                self.btn_priority.setMaximumWidth(36 if self._compact_mode else 16777215)
            if self.btn_budget_hours:
                self.btn_budget_hours.setMaximumWidth(48 if self._compact_mode else 16777215)
            if hasattr(self, "btn_hide_task") and self.btn_hide_task:
                self.btn_hide_task.setFixedSize(
                    24 if self._compact_mode else 30,
                    18 if self._compact_mode else 20,
                )

            if self._compact_mode:
                self.btn_task_title.setText(_compact_text(title, 14))
                if self.btn_status:
                    self.btn_status.setText(_compact_text(status_text, 10))
                    self.btn_status.setToolTip(status_text)
                if self.btn_assigned:
                    self.btn_assigned.setText(_compact_text(artist_text, 9))
                    self.btn_assigned.setToolTip(artist_text)
                if self.btn_priority:
                    self.btn_priority.setText(priority_text)
                if self.btn_budget_hours:
                    self.btn_budget_hours.setText(hours_text)
                if hasattr(self, "btn_hide_task") and self.btn_hide_task:
                    self.btn_hide_task.setText("U" if is_hidden else "H")
                    self.btn_hide_task.setToolTip("Unhide task" if is_hidden else "Hide task")

                tooltip_parts = [title]
                if notes:
                    tooltip_parts.append(notes)
                tooltip_text = "\n".join(tooltip_parts)
                self.btn_task_title.setToolTip(tooltip_text)
                if hasattr(self, "task_frame") and self.task_frame:
                    self.task_frame.setToolTip(tooltip_text)
            else:
                self.btn_task_title.setText(title)
                self.btn_task_title.setToolTip(title)
                if hasattr(self, "btn_notes") and self.btn_notes:
                    self.btn_notes.setText(notes or "No notes")
                    self.btn_notes.setToolTip(notes)
                if self.btn_status:
                    self.btn_status.setText(status_text)
                    self.btn_status.setToolTip(status_text)
                if self.btn_assigned:
                    self.btn_assigned.setText(artist_text)
                    self.btn_assigned.setToolTip(artist_text)
                if self.btn_priority:
                    self.btn_priority.setText(priority_text)
                if self.btn_budget_hours:
                    self.btn_budget_hours.setText(hours_text)
                if hasattr(self, "btn_hide_task") and self.btn_hide_task:
                    self.btn_hide_task.setText("Unhide" if is_hidden else "Hide")
                    self.btn_hide_task.setToolTip("Hide/Unhide task")
                if hasattr(self, "task_frame") and self.task_frame:
                    self.task_frame.setToolTip(notes)

        self._set_done_state_properties(is_done)

    def _on_delete_clicked(self):
        if not self._task_id:
            return
        try:
            self._api.delete_task(self._task_id)
            parent_shot = self._parent_shot_card()
            if parent_shot is not None:
                parent_shot.remove_task_by_id(self._task_id)
            else:
                self.setParent(None)
                self.deleteLater()
            self._notify_filter_state_changed()
        except Exception:
            pass

    def _on_hide_clicked(self):
        if self._task_id is None:
            return
        current_hidden = bool((self._data or {}).get("hidden", False))
        new_hidden = not current_hidden
        try:
            updated = self._api.update_task(self._task_id, hidden=new_hidden)
            if updated:
                self._apply_updated_task(updated)
            else:
                self._data["hidden"] = new_hidden
                self.set_compact_mode(self._compact_mode)
                self._sync_parent_task_data({"hidden": new_hidden})
                self._refresh_parent_widget_state()
                self._notify_filter_state_changed()
        except Exception:
            pass

    def _on_done_toggled(self, checked: bool) -> None:
        if self._task_id is None:
            return
        current_status = (self._data or {}).get("status")
        if checked and current_status == "done":
            return
        if (not checked) and current_status != "done":
            return
        if checked and current_status not in (None, "", "done"):
            self._last_non_done_status = current_status
        new_status = "done" if checked else self._fallback_undo_status()
        try:
            updated = self._api.update_task(self._task_id, status=new_status)
            self._apply_updated_task(updated, flash_done=checked)
        except Exception:
            if hasattr(self, "check_done_task") and self.check_done_task:
                blocker = QSignalBlocker(self.check_done_task)
                self.check_done_task.setChecked(current_status == "done")
                del blocker

    def _on_status_action(self, action: QAction):
        if self._task_id is None:
            return
        new_value = action.data()
        if not new_value:
            return

        prev_value = (self._data or {}).get("status", None)
        try:
            updated = self._api.update_task(self._task_id, status=new_value)
            self._apply_updated_task(updated)
        except Exception:
            if prev_value is not None and self.btn_status:
                self.btn_status.setText(self._status_label(prev_value))

    def _on_priority_action(self, action: QAction):
        if self._task_id is None:
            return
        new_priority = action.data()
        if new_priority is None:
            return

        prev_priority = (self._data or {}).get("priority", None)
        try:
            updated = self._api.update_task(self._task_id, priority=int(new_priority))
            self._apply_updated_task(updated)
        except Exception:
            if prev_priority is not None and self.btn_priority:
                self.btn_priority.setText(self._priority_label(prev_priority))

    def _on_budget_hours_clicked(self):
        if self._task_id is None:
            return

        current_value = (self._data or {}).get("budget_hours", None)
        if current_value is None and self.btn_budget_hours:
            current_value = (self.btn_budget_hours.text() or "").lower().replace("h", "").strip() or "0"

        dlg = PlainTextEditDialog(self, title="Edit budget hours", text=str(current_value))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_text = dlg.value().strip().lower().replace("h", "")
            if not new_text:
                return
            try:
                new_hours = float(new_text)
            except Exception:
                return
            if new_hours < 0:
                return

            prev_hours = (self._data or {}).get("budget_hours", None)
            try:
                updated = self._api.update_task(self._task_id, budget_hours=new_hours)
                self._apply_updated_task(updated)
            except Exception:
                if self.btn_budget_hours:
                    self.btn_budget_hours.setText(self._format_hours(prev_hours))

    def _on_assign_clicked(self):
        self._artist_menu.clear()

        act_clear = QAction("Unassigned", self._artist_menu)
        act_clear.setData(None)
        self._artist_menu.addAction(act_clear)
        self._artist_menu.addSeparator()

        try:
            users = self._api.get_users()
            users = sorted(users, key=lambda u: (u.get("username") or "").lower())
            for u in users:
                label = u.get("username") or f"User {u.get('id')}"
                act = QAction(label, self._artist_menu)
                act.setData(u.get("id"))
                self._artist_menu.addAction(act)
        except Exception:
            err = QAction("Failed to load users", self._artist_menu)
            err.setEnabled(False)
            self._artist_menu.addAction(err)

        self._open_menu_below(self.btn_assigned, self._artist_menu)

    def _on_artist_action(self, action: QAction):
        if self._task_id is None:
            return
        new_artist_id = action.data()
        prev_artist_id = (self._data or {}).get("artist", None)
        try:
            updated = self._api.update_task(self._task_id, artist=new_artist_id)
            self._apply_updated_task(updated)
        except Exception:
            if self.btn_assigned:
                self.btn_assigned.setText(self._artist_label(prev_artist_id))

    def update_from_data(self, task_data: dict):
        task_data = task_data or {}
        self._data = task_data

        title = task_data.get("title") or ""
        notes = str(task_data.get("notes") or "")
        status_value = task_data.get("status")
        artist_id = task_data.get("artist")
        priority_value = task_data.get("priority", 5)
        budget_hours_value = task_data.get("budget_hours", 0)

        if status_value and status_value != "done":
            self._last_non_done_status = status_value

        if hasattr(self, "btn_task_title") and self.btn_task_title:
            self.btn_task_title.setText(title)
        if hasattr(self, "edit_title_inline") and self.edit_title_inline:
            if not self._title_edit_active:
                self._set_inline_title_text(title)
                self._title_edit_original = title
            self.edit_title_inline.setToolTip(title or "Task name")
        if hasattr(self, "btn_notes") and self.btn_notes:
            if self._is_checklist_presentation():
                self.btn_notes.setText("Note")
            else:
                self.btn_notes.setText(notes or "No notes")
        if hasattr(self, "edit_notes_inline") and self.edit_notes_inline:
            self._set_inline_notes_text(notes)
            self.edit_notes_inline.setToolTip(notes or "Add note")

        if hasattr(self, "btn_hide_task") and self.btn_hide_task:
            self.btn_hide_task.setText("Unhide" if task_data.get("hidden") else "Hide")

        if self.btn_status:
            self.btn_status.setText(self._status_label(status_value))
            status_prop = status_value if status_value else "unassigned"
            self.btn_status.setProperty("status", status_prop)
            self.btn_status.style().unpolish(self.btn_status)
            self.btn_status.style().polish(self.btn_status)

        if self.btn_assigned:
            self.btn_assigned.setText(self._artist_label(artist_id))
        elif hasattr(self, "label_assigned") and self.label_assigned:
            self.label_assigned.setText(self._artist_label(artist_id))

        if self.btn_priority:
            self.btn_priority.setText(self._priority_label(priority_value))

        if self.btn_budget_hours:
            self.btn_budget_hours.setText(self._format_hours(budget_hours_value))

        if hasattr(self, "check_done_task") and self.check_done_task:
            blocker = QSignalBlocker(self.check_done_task)
            self.check_done_task.setChecked(status_value == "done")
            del blocker

        self.set_compact_mode(self._compact_mode)

    def _on_title_clicked(self):
        if self._task_id is None:
            return
        if self._is_checklist_presentation():
            self._begin_inline_title_edit()
            return

        current_title = (self._data or {}).get("title") or self.btn_task_title.text() or ""

        dlg = PlainTextEditDialog(self, title="Edit title", text=current_title)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_title = dlg.value().strip()
            if not new_title or new_title == current_title:
                return

            prev_title = current_title
            try:
                updated = self._api.update_task(self._task_id, title=new_title)
                self._apply_updated_task(updated)
            except Exception:
                self.btn_task_title.setText(prev_title)

    def _on_notes_clicked(self):
        if self._task_id is None:
            return

        current_note = (self._data or {}).get("notes") or ""

        dlg = PlainTextEditDialog(self, title="Edit notes", text=current_note)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_note = dlg.value().strip()
            if new_note == current_note:
                return

            prev_note = current_note
            try:
                updated = self._api.update_task(self._task_id, notes=new_note)
                self._apply_updated_task(updated)
            except Exception:
                if hasattr(self, "btn_notes") and self.btn_notes and not self._is_checklist_presentation():
                    self.btn_notes.setText(prev_note or "No notes")

    def _on_inline_title_edit_finished(self) -> None:
        if self._task_id is None:
            return
        if not (hasattr(self, "edit_title_inline") and self.edit_title_inline):
            return
        if self._title_edit_ignore_finish:
            self._title_edit_ignore_finish = False
            return
        if not self._title_edit_active:
            return

        current_title = str((self._data or {}).get("title") or "").strip()
        new_title = self.edit_title_inline.text().strip()
        if not new_title or new_title == current_title:
            self._end_inline_title_edit(restore_text=current_title)
            return

        self._end_inline_title_edit()
        try:
            updated = self._api.update_task(self._task_id, title=new_title)
            self._apply_updated_task(updated)
        except Exception:
            self._set_inline_title_text(current_title)

    def _on_inline_notes_edit_finished(self) -> None:
        if self._task_id is None:
            return
        if not (hasattr(self, "edit_notes_inline") and self.edit_notes_inline):
            return

        current_note = self._inline_notes_display_text((self._data or {}).get("notes") or "")
        new_note = self.edit_notes_inline.text().strip()
        if new_note == current_note:
            return

        try:
            updated = self._api.update_task(self._task_id, notes=new_note)
            self._apply_updated_task(updated)
        except Exception:
            self._set_inline_notes_text(current_note)

    def eventFilter(self, watched, event):
        if (
            watched is getattr(self, "edit_title_inline", None)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._cancel_inline_title_edit()
            return True
        return super().eventFilter(watched, event)

class ShotCard(QWidget):
    # Color code mapping - centralized for easy maintenance
    COLOR_MAP = {
        "red": "#99604C",
        "amber": "#B28659",
        "green": "#678542",
        "blue": "#4A7BA7",      # Ready to add more colors
        "purple": "#8B6FA3",
        "None": "#333333"       # Default/no color
    }
    
    def __init__(self, data=None, parent=None, task_style="card", api=None, folders=None):
        super().__init__(parent)
        data = data or {}
        self.data = data
        
        # Use Python UI setup instead of uic.loadUi
        setup_shot_card_ui(self)
    
        self.setObjectName(f"shot-{data.get('id','unknown')}")
        
        # cache
        self._thumb_sig = None
        self._thumb_orig = None           # full-res original
        self._thumb_target_width = THUMBNAIL_TARGET_WIDTH    # base width from density settings
        self._base_thumb_target_width = THUMBNAIL_TARGET_WIDTH
        self._thumb_pending_url = None
        self._current_thumbnail_url = None
        self._thumbnails_enabled = bool(THUMBNAILS_ENABLED)
        self._compact_mode = False
        self._task_style = _normalize_task_style(task_style)
        self._task_render_state = {}
        self._task_widgets_by_id = {}
        self._visible_task_ids = []

        # make sure label will not auto-stretch
        self.label_thumbnail.setScaledContents(False)
        self.label_thumbnail.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        
        # Video preview setup
        self._preview_video_path = None
        self._has_preview = False
        self._video_player = None
        self._video_widget = None
        self._audio_output = None
        self._setup_video_preview_stack()
        
        self._shot_id = data.get("id")
        self.shot_dir = data.get("base_path")
        self._api = api or http_help.DjangoAPI()
        self.filesIO = folders or filesIO.Folders()
        self._nuke_open_handler = None
        self._lock_state = "none"
        self._lock_owner_machine = None
        self._lock_script_name = None
        self._original_clip_entries = []
        self._file_state_signature = None
        self._last_nk_file = None
        self._last_nk_mtime = None
        self._last_render_file = None
        self._last_render_mtime = None
        self._last_preview_version = None

        # Color buttons are now styled via QSS (styles_v02.qss)
        # Initial color indicator will be set in update_from_data via _apply_colour_code

        self.btn_red.clicked.connect(lambda: self.on_colour_btn("red"))
        self.btn_amber.clicked.connect(lambda: self.on_colour_btn("amber"))
        self.btn_green.clicked.connect(lambda: self.on_colour_btn("green"))
        self.btn_colour_none.clicked.connect(lambda: self.on_colour_btn("none"))
        
        if data.get("hidden") == True:
            self.btn_hide_shot.setText("Unhide")
        else:
            self.btn_hide_shot.setText("Hide")
        self.btn_hide_shot.clicked.connect(self.on_hide_btn)  # Fixed: no lambda, no stale data

        if hasattr(self, "label_colourspace"):
            self.label_colourspace.setToolTip("Right-click to set colourspace")
            self.label_colourspace.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.label_colourspace.customContextMenuRequested.connect(self._on_colourspace_context_menu)

        

        # NEW: add-task click
        if hasattr(self, "bnt_addTask"):
            self.bnt_addTask.clicked.connect(self._on_add_task_clicked)

        self.btn_edit_shot_notes.clicked.connect(self._on_add_note_clicked)

        self.btn_open_nuke.clicked.connect(self._open_current_nuke_file)
        self._set_nuke_file_state(file_path=None, file_name=None, file_mtime=None)
        _set_dynamic_property(self.btn_open_nuke, "lock_state", "none")
        
        self.btn_open_assets.clicked.connect(self._open_shot_assets)
        self.btn_open_assets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_open_assets.customContextMenuRequested.connect(self._on_assets_context_menu)
        self.btn_open_precomp.clicked.connect(lambda: self.filesIO.openFileLocation(Path(self.shot_dir) / "renders" / "precomp" ))

        self._set_render_file_state()
        self._set_render_button_text()
        self.btn_latest_render.clicked.connect(self._on_push_to_dvr_clicked)  # Use new handler 

        self._set_task_layout_mode(self._task_style)
        self.set_thumbnails_enabled(self._thumbnails_enabled)

        self.update_from_data(data)

    def _set_nuke_file_state(
        self,
        *,
        file_path: str | None,
        file_name: str | None,
        file_mtime: float | None,
    ) -> None:
        self._last_nk_file = file_name
        self._last_nk_mtime = file_mtime
        self.btn_open_nuke.setProperty("file_path", file_path)
        self.refresh_lock_state_from_data()
        self._set_nuke_button_label()
        self._set_nuke_button_tooltip()

    def _set_render_file_state(
        self,
        *,
        render_path: str | None = None,
        render_relpath: str | None = None,
        render_display: str | None = None,
        render_type: str | None = None,
        render_sequence_path: str | None = None,
        render_dir: str | None = None,
        render_version: int | None = None,
        render_mtime: float | None = None,
    ) -> None:
        self._last_render_file = render_relpath or render_display
        self._last_render_mtime = render_mtime
        self.btn_latest_render.setProperty("file_path", render_path)
        self.btn_latest_render.setProperty("render_relpath", render_relpath)
        self.btn_latest_render.setProperty("render_display", render_display)
        self.btn_latest_render.setProperty("render_type", render_type)
        self.btn_latest_render.setProperty("render_sequence_path", render_sequence_path)
        self.btn_latest_render.setProperty("render_dir", render_dir)
        self.btn_latest_render.setProperty("render_version", render_version)
        self._set_render_button_text()
        self._refresh_last_conform_indicator()
        self._apply_compact_tooltips()

    def refresh_file_state_tooltips(self) -> None:
        self._set_nuke_button_tooltip()
        self._apply_compact_tooltips()

    def apply_file_state_snapshot(self, snapshot: filesIO.ShotFileStateSnapshot) -> str | None:
        if snapshot is None:
            return None

        signature = (
            snapshot.nk_path,
            snapshot.nk_mtime,
            snapshot.render_path,
            snapshot.render_relpath,
            snapshot.render_display,
            snapshot.render_type,
            snapshot.render_sequence_path,
            snapshot.render_dir,
            snapshot.render_version,
            snapshot.render_mtime,
            snapshot.preview_path,
            snapshot.preview_name,
            snapshot.preview_version,
        )
        if signature == self._file_state_signature:
            return None

        self._file_state_signature = signature
        self._set_nuke_file_state(
            file_path=snapshot.nk_path,
            file_name=snapshot.nk_name,
            file_mtime=snapshot.nk_mtime,
        )
        self._set_render_file_state(
            render_path=snapshot.render_path,
            render_relpath=snapshot.render_relpath,
            render_display=snapshot.render_display,
            render_type=snapshot.render_type,
            render_sequence_path=snapshot.render_sequence_path,
            render_dir=snapshot.render_dir,
            render_version=snapshot.render_version,
            render_mtime=snapshot.render_mtime,
        )

        preview_relative = None
        if snapshot.preview_path:
            self._preview_video_path = str(snapshot.preview_path)
            self._has_preview = True
            self._last_preview_version = snapshot.preview_version
            preview_relative = f"renders/precomp/previews/{snapshot.preview_name}"
            self.data["preview_video"] = preview_relative
            if hasattr(self, "_preview_indicator"):
                self._preview_indicator.show()
                self._position_preview_indicator()
        elif not (self.data or {}).get("preview_video"):
            self._preview_video_path = None
            self._has_preview = False
            if hasattr(self, "_preview_indicator"):
                self._preview_indicator.hide()

        self.refresh_file_state_tooltips()
        return preview_relative

    def _open_current_nuke_file(self, checked: bool = False):
        file_path = self.btn_open_nuke.property("file_path")
        if not file_path:
            return
        self._on_open_nuke_clicked(file_path)
    
    def _apply_colour_code(self, colour_code):
        """Apply color code to the shot card color indicator strip."""
        # Normalize the color code
        if colour_code in (None, "", "None", "none"):
            colour_code = "none"
        elif colour_code not in ("red", "amber", "green"):
            colour_code = "none"
        
        # Set the property on the color indicator strip
        if hasattr(self, 'color_indicator'):
            self.color_indicator.setProperty("shot_color", colour_code)
            # Force style refresh
            self.color_indicator.style().unpolish(self.color_indicator)
            self.color_indicator.style().polish(self.color_indicator)

    def _on_colourspace_context_menu(self, pos):
        if not hasattr(self, "label_colourspace"):
            return
        menu = QMenu(self)
        current = (self.data or {}).get("colourspace")
        for preset in COLOURSPACE_PRESETS:
            action = menu.addAction(preset)
            action.setCheckable(True)
            if current == preset:
                action.setChecked(True)
            action.triggered.connect(lambda checked=False, value=preset: self._set_colourspace(value))
        menu.exec(self.label_colourspace.mapToGlobal(pos))

    def _set_colourspace(self, colourspace: str) -> None:
        if self._shot_id is None:
            return
        try:
            updated = self._api.update_shot(self._shot_id, colourspace=colourspace)
            if updated:
                self.update_from_data(updated)
            else:
                self.data["colourspace"] = colourspace
                if hasattr(self, "label_colourspace"):
                    self.label_colourspace.setText(f"Colourspace: ...{colourspace[-8:-5]}...")
        except Exception:
            pass

    def _shot_title(self) -> str:
        return (self.data or {}).get("title") or Path(self.shot_dir).name

    def _normalized_shot_dir(self) -> str:
        if not self.shot_dir:
            return ""
        try:
            return self.filesIO.convert_path(self.shot_dir)
        except Exception:
            return str(self.shot_dir)

    def _shot_assets_dir(self) -> Path:
        return Path(build_shot_assets_directory(self._normalized_shot_dir()))

    def _default_matchmove_dir(self) -> str:
        return build_shot_matchmove_directory(self._normalized_shot_dir())

    def _resolved_matchmove_dir(self) -> str:
        configured_path = str((self.data or {}).get("matchmove_path") or "").strip()
        if configured_path:
            try:
                configured_path = self.filesIO.convert_path(configured_path)
            except Exception:
                pass
            try:
                if Path(configured_path).exists():
                    return configured_path
            except OSError:
                pass
        return self._default_matchmove_dir()

    def _open_shot_assets(self) -> None:
        assets_dir = self._shot_assets_dir()
        assets_dir.mkdir(parents=True, exist_ok=True)
        self.filesIO.openFileLocation(assets_dir)

    def _show_assets_matchmove_menu(self, global_pos) -> bool:
        if not self.shot_dir:
            return False

        try:
            try:
                _load_matchmove_helpers()
            except Exception as exc:
                _show_matchmove_unavailable(self, exc)
                return True

            shot_name = self._shot_title()
            matchmove_dir = self._resolved_matchmove_dir()
            latest_project = find_latest_matchmove_project(matchmove_dir, shot_name)
            shot_root = self._normalized_shot_dir()

            menu = QMenu(self)
            if latest_project is not None:
                action = menu.addAction(f"Open {latest_project.name}")
                action.triggered.connect(
                    lambda checked=False, path=str(latest_project): self._open_matchmove_project(path)
                )
            else:
                sequence_infos = list_valid_precomp_sequences(shot_root)
                if sequence_infos:
                    create_menu = menu.addMenu("Create Matchmove")
                    for sequence_info in sequence_infos:
                        label = Path(sequence_info.folder_path).name
                        action = create_menu.addAction(label)
                        action.triggered.connect(
                            lambda checked=False, info=sequence_info: self._create_matchmove_project(info)
                        )
                else:
                    precomp_dir = Path(shot_root) / "renders" / "precomp"
                    if precomp_dir.is_dir():
                        empty_action = menu.addAction("No valid EXR precomp folders found")
                    else:
                        empty_action = menu.addAction("No renders/precomp folder found")
                    empty_action.setEnabled(False)

            menu.exec(global_pos)
            return True
        except Exception as exc:
            traceback_text = traceback.format_exc(limit=10)
            QMessageBox.critical(
                self,
                "Assets Menu Error",
                "Opening the Assets matchmove menu failed.\n\n"
                f"{exc}\n\n{traceback_text}",
            )
            return True

    def _on_assets_context_menu(self, pos) -> None:
        try:
            global_pos = self.btn_open_assets.mapToGlobal(pos)
            self._show_assets_matchmove_menu(global_pos)
        except Exception as exc:
            traceback_text = traceback.format_exc(limit=10)
            QMessageBox.critical(
                self,
                "Assets Menu Error",
                "Opening the Assets matchmove menu failed.\n\n"
                f"{exc}\n\n{traceback_text}",
            )

    def _open_matchmove_project(self, project_path: str) -> None:
        try:
            open_3de_project(project_path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Open Matchmove",
                f"Could not open the 3DE project.\n\n{project_path}\n\n{exc}",
            )

    def _create_matchmove_project(self, sequence_info) -> None:
        try:
            helpers = _load_matchmove_helpers()
        except Exception as exc:
            _show_matchmove_unavailable(self, exc)
            return

        dialog = SingleCameraMatchmoveDialog(sequence_info.folder_path, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        shot_name = self._shot_title()
        matchmove_dir = self._default_matchmove_dir()
        project_dir, export_dir, project_path, version = resolve_project_path(matchmove_dir, shot_name)

        os.makedirs(matchmove_dir, exist_ok=True)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(export_dir, exist_ok=True)

        used_camera_names: set[str] = set()
        camera_name = helpers.build_unique_camera_name(
            shot_name=shot_name,
            folder_path=sequence_info.folder_path,
            slot_index=0,
            used_names=used_camera_names,
        )
        focal_length_mm = dialog.focal_length_mm

        clip_request = helpers.MatchmoveClipRequest(
            slot_index=0,
            clip_name=Path(sequence_info.folder_path).name,
            camera_name=camera_name,
            lens_name=helpers.format_focal_length_label(focal_length_mm),
            sequence_info=sequence_info,
            focal_length_mm=focal_length_mm,
            sequence_start_frame=sequence_info.first_frame,
            sequence_end_frame=sequence_info.last_frame,
        )
        request = helpers.MatchmoveProjectRequest(
            project_name=shot_name,
            shot_name=shot_name,
            clips=(clip_request,),
            camera_preset_name=dialog.camera_preset_name,
            matchmove_dir=matchmove_dir,
            export_dir=export_dir,
            fps=dialog.fps,
            project_dir=project_dir,
            project_path=project_path,
            version=version,
        )

        log_lines: list[str] = []
        result = None
        try:
            result = run_headless_3de(request, log_lines.append)
        except Exception as exc:
            detail = str(exc)
            if log_lines:
                detail = f"{detail}\n\n" + "\n".join(log_lines[-20:])
            QMessageBox.critical(self, "Create Matchmove", detail)
            return
        finally:
            if result is not None:
                cleanup_headless_artifacts(result)

        open_warning = None
        try:
            open_3de_project(project_path)
        except Exception as exc:
            open_warning = str(exc)

        patch_warning = None
        if self._shot_id is not None:
            try:
                updated = self._api.update_shot(self._shot_id, matchmove_path=matchmove_dir)
                if updated:
                    self.update_from_data(updated)
                else:
                    self.data["matchmove_path"] = matchmove_dir
            except Exception as exc:
                patch_warning = str(exc)
                self.data["matchmove_path"] = matchmove_dir
        else:
            self.data["matchmove_path"] = matchmove_dir

        message_lines = [
            f"Created {Path(project_path).name}",
            project_path,
        ]
        if open_warning:
            message_lines.append(f"Created project, but opening 3DE failed: {open_warning}")
        if patch_warning:
            message_lines.append(f"Created project, but saving matchmove_path to ShotBox failed: {patch_warning}")
        QMessageBox.information(self, "Create Matchmove", "\n\n".join(message_lines))

    def _normalize_render_label(self, value: str) -> str:
        """Strip render type suffix for comparisons."""
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
    
    def _get_nuke_base_label(self) -> str:
        """Return base Nuke button label from file_path property."""
        file_path = self.btn_open_nuke.property("file_path")
        if not file_path:
            return "No .nk file"
        try:
            return Path(str(file_path)).name
        except Exception:
            return str(file_path)
    
    def set_nuke_open_handler(self, handler):
        self._nuke_open_handler = handler

    def set_lock_state(self, lock_state: str, owner_machine: str | None = None, script_name: str | None = None):
        self._lock_state = lock_state or "none"
        self._lock_owner_machine = owner_machine
        self._lock_script_name = script_name
        _set_dynamic_property(self.btn_open_nuke, "lock_state", self._lock_state)
        self._set_nuke_button_label()
        self._set_nuke_button_tooltip()

    def refresh_lock_state_from_data(self):
        lock_info = parse_lock_info((self.data or {}).get("nuke_in_use"))
        if lock_info is None:
            self.set_lock_state("none")
            return

        script_name = self._get_nuke_base_label()
        owner = lock_info.display_owner
        if lock_info.is_stale():
            self.set_lock_state("stale", owner_machine=owner, script_name=script_name)
            return

        if lock_info.matches_system(self._api.get_system_id()):
            self.set_lock_state("mine", owner_machine=owner, script_name=script_name)
            return

        self.set_lock_state("foreign_active", owner_machine=owner, script_name=script_name)
    
    def _set_nuke_button_label(self):
        """Render the Nuke button label from the current file path."""
        base_label = self._get_nuke_base_label()
        lock_state = getattr(self, "_lock_state", "none")
        lock_owner = getattr(self, "_lock_owner_machine", None) or "unknown"
        lock_script = getattr(self, "_lock_script_name", None) or base_label

        if lock_state == "foreign_active":
            text = _compact_text(f"{lock_script} @ {lock_owner}", 20 if self._compact_mode else 48)
            self.btn_open_nuke.setText(text or "Locked")
            return
        if lock_state == "mine":
            if self._compact_mode:
                self.btn_open_nuke.setText("Locked by you")
            else:
                self.btn_open_nuke.setText(_compact_text(f"{base_label} (You)", 48))
            return

        if self._compact_mode:
            self.btn_open_nuke.setText("Nuke" if base_label != "No .nk file" else "No Nuke")
            return
        self.btn_open_nuke.setText(base_label)
    
    def _set_nuke_button_tooltip(self):
        """Render modified-time tooltip for the current .nk file."""
        lock_state = getattr(self, "_lock_state", "none")
        if lock_state == "foreign_active":
            owner = getattr(self, "_lock_owner_machine", None) or "unknown"
            script = getattr(self, "_lock_script_name", None) or self._get_nuke_base_label()
            self.btn_open_nuke.setToolTip(f"Locked by: {owner}\nScript: {script}")
            return
        if lock_state == "mine":
            script = getattr(self, "_lock_script_name", None) or self._get_nuke_base_label()
            self.btn_open_nuke.setToolTip(f"Locked by you\nScript: {script}")
            return

        last_nk_mtime = getattr(self, "_last_nk_mtime", None)
        if last_nk_mtime:
            self.btn_open_nuke.setToolTip(f"Modified {self._format_time_ago(last_nk_mtime)}")
        else:
            self.btn_open_nuke.setToolTip("No .nk file found")

    def _current_thumb_target_width(self) -> int:
        width = max(1, int(getattr(self, "_base_thumb_target_width", self._thumb_target_width)))
        if self._compact_mode:
            return max(72, int(width * 0.6))
        return width

    def _thumbnail_display_widget(self):
        if hasattr(self, "_preview_stack") and self._preview_stack is not None:
            return self._preview_stack
        return getattr(self, "label_thumbnail", None)

    def set_thumbnails_enabled(self, enabled: bool) -> None:
        self._thumbnails_enabled = bool(enabled)
        display_widget = self._thumbnail_display_widget()
        if display_widget is not None:
            display_widget.setVisible(self._thumbnails_enabled)
        if not self._thumbnails_enabled:
            self._thumb_pending_url = None
            if hasattr(self, "_video_player") and self._video_player:
                try:
                    self._video_player.stop()
                except Exception:
                    pass
            return
        if self._thumb_orig is not None:
            self._apply_thumb_scale()
            return
        if self._current_thumbnail_url:
            self.set_thumbnail(self._current_thumbnail_url)

    def _set_render_button_text(self) -> None:
        render_display = self.btn_latest_render.property("render_display")
        has_render = bool(self.btn_latest_render.property("file_path"))
        if self._compact_mode:
            self.btn_latest_render.setText("Render" if has_render else "No Render")
            return
        if render_display:
            self.btn_latest_render.setText(str(render_display))
        else:
            self.btn_latest_render.setText("No Render")

    def _refresh_last_conform_indicator(self) -> None:
        if not hasattr(self, "label_last_conform"):
            return

        last_conform = (self.data or {}).get("last_conform")
        if last_conform and last_conform not in (None, "None", ""):
            self.label_last_conform.setText(last_conform)
            current_render = (
                self.btn_latest_render.property("render_display")
                or self.btn_latest_render.text()
            )
            if current_render and self._normalize_render_label(last_conform) == self._normalize_render_label(current_render):
                self.label_last_conform.setStyleSheet("color: #4CAF50; font-weight: bold;")
            else:
                self.label_last_conform.setStyleSheet("")
            return

        self.label_last_conform.setText("None")
        self.label_last_conform.setStyleSheet("")

    def _set_hide_button_label(self) -> None:
        hidden = bool((self.data or {}).get("hidden", False))
        if self._compact_mode:
            self.btn_hide_shot.setText("U" if hidden else "H")
            self.btn_hide_shot.setToolTip("Unhide shot" if hidden else "Hide shot")
            return
        self.btn_hide_shot.setText("Unhide" if hidden else "Hide")
        self.btn_hide_shot.setToolTip("Hide/Unhide shot")

    def _apply_compact_tooltips(self) -> None:
        title = str((self.data or {}).get("title", "") or "")
        notes = str((self.data or {}).get("notes", "") or "")
        tooltip_parts = [part for part in (title, notes) if part]
        if hasattr(self, "label_colourspace"):
            colourspace = str((self.data or {}).get("colourspace", "") or "")
            if colourspace:
                tooltip_parts.append(f"Colourspace: {colourspace}")
        original_clip = str((self.data or {}).get("original_clip", "") or "")
        if original_clip:
            tooltip_parts.append(f"Clip: {Path(original_clip).name}")
        if tooltip_parts:
            tooltip_text = "\n".join(tooltip_parts)
            self.frame_7.setToolTip(tooltip_text)
            self.label_shot.setToolTip(tooltip_text)
        else:
            self.frame_7.setToolTip("")
            self.label_shot.setToolTip("")

        render_parts = []
        last_render_mtime = getattr(self, "_last_render_mtime", None)
        if last_render_mtime:
            render_parts.append(f"Rendered {self._format_time_ago(last_render_mtime)}")
        elif self.btn_latest_render.property("file_path"):
            render_display = self.btn_latest_render.property("render_display") or "Render ready"
            render_parts.append(str(render_display))
        else:
            render_parts.append("No render found")
        last_conform = (self.data or {}).get("last_conform")
        if self._compact_mode and last_conform not in (None, "None", ""):
            render_parts.append(f"Last conform: {last_conform}")
        self.btn_latest_render.setToolTip("\n".join(render_parts))

    def _apply_compact_properties(self, enabled: bool) -> None:
        compact_value = "true" if enabled else "false"
        for widget in (
            getattr(self, "frame_7", None),
            getattr(self, "content_container", None),
            getattr(self, "label_shot", None),
            getattr(self, "label_frame_range", None),
            getattr(self, "btn_hide_shot", None),
            getattr(self, "btn_open_nuke", None),
            getattr(self, "btn_latest_render", None),
            getattr(self, "bnt_addTask", None),
            getattr(self, "btn_open_assets", None),
            getattr(self, "btn_open_precomp", None),
            getattr(self, "frame_tasks", None),
        ):
            _set_dynamic_property(widget, "compact", compact_value)

    def _normalize_task_id(self, task_id):
        if task_id is None:
            return None
        try:
            return int(task_id)
        except (TypeError, ValueError):
            return task_id

    def _task_widget_presentation(self) -> str:
        return "checklist" if getattr(self, "_task_style", "card") == "checklist" else "card"

    def _display_task_ids(self) -> list:
        ordered_ids = []
        if getattr(self, "_task_style", "card") != "checklist":
            for task in self.data.get("tasks", []) or []:
                task_id = self._normalize_task_id(task.get("id"))
                if task_id is not None:
                    ordered_ids.append(task_id)
            return ordered_ids

        active_ids = []
        done_ids = []
        for task in self.data.get("tasks", []) or []:
            task_id = self._normalize_task_id(task.get("id"))
            if task_id is None:
                continue
            if task.get("status") == "done":
                done_ids.append(task_id)
            else:
                active_ids.append(task_id)
        return active_ids + done_ids

    def _ensure_task_cache_state(self) -> None:
        if not hasattr(self, "_task_render_state"):
            self._task_render_state = {}
        if not hasattr(self, "_task_widgets_by_id"):
            self._task_widgets_by_id = {}
        if not hasattr(self, "_visible_task_ids"):
            self._visible_task_ids = []
        if self._task_widgets_by_id:
            return
        frame_tasks = getattr(self, "frame_tasks", None)
        layout = frame_tasks.layout() if frame_tasks is not None else None
        if layout is None:
            return
        for index in range(layout.count()):
            item = layout.itemAt(index)
            task_widget = item.widget() if item else None
            if not isinstance(task_widget, TaskWidget):
                continue
            task_id = self._normalize_task_id(getattr(task_widget, "_data", {}).get("id"))
            if task_id is None:
                continue
            self._task_widgets_by_id[task_id] = task_widget

    def _task_data_map(self) -> dict:
        self._ensure_task_cache_state()
        tasks_by_id = {}
        for task in self.data.get("tasks", []) or []:
            task_id = self._normalize_task_id(task.get("id"))
            if task_id is None:
                continue
            tasks_by_id[task_id] = task
        return tasks_by_id

    def set_task_render_state(self, render_state: dict | None) -> None:
        self._ensure_task_cache_state()
        self._task_render_state = dict(render_state or {})

    def _notify_filter_state_changed(self) -> None:
        window = self.window()
        apply_filters = getattr(window, "_apply_filters", None)
        if not callable(apply_filters):
            return
        if hasattr(window, "_last_filter_sig"):
            delattr(window, "_last_filter_sig")
        try:
            apply_filters(force=True)
        except TypeError:
            apply_filters()

    def _remove_task_widget(self, task_id) -> None:
        self._ensure_task_cache_state()
        normalized_id = self._normalize_task_id(task_id)
        widget = self._task_widgets_by_id.pop(normalized_id, None)
        if widget is None:
            return
        layout = self.frame_tasks.layout()
        if layout is not None:
            layout.removeWidget(widget)
        widget.setParent(None)
        widget.deleteLater()

    def remove_task_by_id(self, task_id) -> None:
        self._ensure_task_cache_state()
        normalized_id = self._normalize_task_id(task_id)
        tasks = [
            task
            for task in (self.data.get("tasks", []) or [])
            if self._normalize_task_id(task.get("id")) != normalized_id
        ]
        self.data["tasks"] = tasks
        self._visible_task_ids = [
            visible_id
            for visible_id in self._visible_task_ids
            if self._normalize_task_id(visible_id) != normalized_id
        ]
        self._remove_task_widget(normalized_id)
        self._reorder_loaded_task_widgets()
        self._apply_loaded_task_visibility()
        if hasattr(self, "btn_hide_shot"):
            self.set_compact_mode(self._compact_mode)

    def _sync_task_widgets_from_data(self) -> None:
        self._ensure_task_cache_state()
        tasks_by_id = self._task_data_map()
        for task_id, widget in list(self._task_widgets_by_id.items()):
            task_data = tasks_by_id.get(task_id)
            if task_data is None:
                self._remove_task_widget(task_id)
                continue
            if getattr(widget, "_presentation", "card") != self._task_widget_presentation():
                self._rebuild_loaded_task_widgets()
                return
            widget.update_from_data(task_data)
            widget.set_compact_mode(self._compact_mode)
        self._reorder_loaded_task_widgets()
        self._apply_loaded_task_visibility()

    def _reorder_loaded_task_widgets(self) -> None:
        self._ensure_task_cache_state()
        layout = self.frame_tasks.layout()
        if layout is None:
            return
        desired_order = []
        for task_id in self._display_task_ids():
            if task_id is None or task_id not in self._task_widgets_by_id:
                continue
            desired_order.append(f"task-{task_id}")
        if desired_order:
            _ensure_order(layout, desired_order)

    def _apply_loaded_task_visibility(self) -> None:
        self._ensure_task_cache_state()
        visible_ids = {
            self._normalize_task_id(task_id)
            for task_id in (self._visible_task_ids or [])
            if self._normalize_task_id(task_id) is not None
        }
        changed = False
        for task_id, widget in self._task_widgets_by_id.items():
            should_show = task_id in visible_ids
            if widget.isVisible() != should_show:
                widget.setVisible(should_show)
                changed = True
        if changed:
            layout = self.frame_tasks.layout()
            if layout is not None:
                layout.invalidate()
            self.frame_tasks.updateGeometry()
            self.frame_tasks.update()

    def set_visible_task_ids(self, task_ids: list | tuple | set) -> list:
        self._ensure_task_cache_state()
        normalized_visible = []
        seen = set()
        tasks_by_id = self._task_data_map()
        for task_id in task_ids or []:
            normalized_id = self._normalize_task_id(task_id)
            if normalized_id is None or normalized_id in seen or normalized_id not in tasks_by_id:
                continue
            seen.add(normalized_id)
            normalized_visible.append(normalized_id)
        self._visible_task_ids = normalized_visible
        self._apply_loaded_task_visibility()
        self._reorder_loaded_task_widgets()
        return [
            task_id
            for task_id in normalized_visible
            if task_id not in self._task_widgets_by_id
        ]

    def materialize_task_ids(self, task_ids: list | tuple, max_count: int | None = None) -> list:
        self._ensure_task_cache_state()
        layout = self.frame_tasks.layout()
        if layout is None:
            return []
        tasks_by_id = self._task_data_map()
        created_ids = []
        for task_id in task_ids or []:
            if max_count is not None and len(created_ids) >= int(max_count):
                break
            normalized_id = self._normalize_task_id(task_id)
            if normalized_id is None or normalized_id in self._task_widgets_by_id:
                continue
            task_data = tasks_by_id.get(normalized_id)
            if task_data is None:
                continue
            with project_load_profiler.measure_installed_work("task_card_create"):
                task_widget = TaskWidget(task_data, presentation=self._task_widget_presentation())
            task_widget.set_compact_mode(self._compact_mode)
            layout.addWidget(task_widget)
            self._task_widgets_by_id[normalized_id] = task_widget
            created_ids.append(normalized_id)
        if created_ids:
            self._reorder_loaded_task_widgets()
            self._apply_loaded_task_visibility()
        return created_ids

    def _set_task_layout_mode(self, task_style: str) -> None:
        normalized_style = _normalize_task_style(task_style)
        existing_widgets = []
        old_layout = self.frame_tasks.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    existing_widgets.append(widget)
            QWidget().setLayout(old_layout)

        if normalized_style == "checklist":
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4 if self._compact_mode else 6)
        else:
            layout = FlowLayout(self.frame_tasks, margin=0, h_spacing=10, v_spacing=10)

        self.frame_tasks.setLayout(layout)
        for widget in existing_widgets:
            layout.addWidget(widget)

    def _rebuild_loaded_task_widgets(self) -> None:
        self._ensure_task_cache_state()
        layout = self.frame_tasks.layout()
        if layout is None:
            return
        tasks_by_id = self._task_data_map()
        current_widgets = list(self._task_widgets_by_id.items())
        self._task_widgets_by_id = {}
        for task_id, widget in current_widgets:
            task_data = tasks_by_id.get(task_id)
            if task_data is None:
                layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
                continue
            is_visible = widget.isVisible()
            layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
            replacement = TaskWidget(task_data, presentation=self._task_widget_presentation())
            replacement.setVisible(is_visible)
            replacement.set_compact_mode(self._compact_mode)
            layout.addWidget(replacement)
            self._task_widgets_by_id[task_id] = replacement
        self._reorder_loaded_task_widgets()
        self._apply_loaded_task_visibility()

    def set_task_style(self, task_style: str) -> None:
        normalized_style = _normalize_task_style(task_style)
        if normalized_style == getattr(self, "_task_style", "card"):
            return
        self._task_style = normalized_style
        self._set_task_layout_mode(self._task_style)
        self._rebuild_loaded_task_widgets()
        self.set_compact_mode(self._compact_mode)

    def set_compact_mode(self, enabled: bool) -> None:
        self._compact_mode = bool(enabled)
        self._apply_compact_properties(self._compact_mode)

        if hasattr(self, "horizontalLayout_2") and self.horizontalLayout_2:
            direction = (
                QBoxLayout.Direction.TopToBottom
                if self._compact_mode
                else QBoxLayout.Direction.LeftToRight
            )
            self.horizontalLayout_2.setDirection(direction)
            self.horizontalLayout_2.setSpacing(6 if self._compact_mode else 0)

        if hasattr(self, "verticalLayout_5") and self.verticalLayout_5:
            margins = 6 if self._compact_mode else 8
            self.verticalLayout_5.setContentsMargins(margins, margins, margins, margins)
            self.verticalLayout_5.setSpacing(4 if self._compact_mode else 6)

        if hasattr(self, "frame_tasks") and self.frame_tasks:
            self.frame_tasks.setFrameShape(
                QFrame.Shape.NoFrame if self._compact_mode else QFrame.Shape.Box
            )
            tasks_layout = self.frame_tasks.layout()
            if tasks_layout is not None:
                if getattr(self, "_task_style", "card") == "checklist":
                    tasks_layout.setSpacing(4 if self._compact_mode else 6)
                else:
                    tasks_layout.setSpacing(6 if self._compact_mode else 10)
                for i in range(tasks_layout.count()):
                    item = tasks_layout.itemAt(i)
                    child = item.widget() if item else None
                    if isinstance(child, TaskWidget):
                        child.set_compact_mode(self._compact_mode)

        if hasattr(self, "frame_metadata"):
            self.frame_metadata.setVisible(not self._compact_mode)
        if hasattr(self, "frame_2"):
            self.frame_2.setVisible(not self._compact_mode)
        if hasattr(self, "label_last_conform"):
            self.label_last_conform.setVisible(not self._compact_mode)
        if hasattr(self, "label_4"):
            self.label_4.setVisible(not self._compact_mode)

        for widget in (
            getattr(self, "label_edit_inpoint", None),
            getattr(self, "label_edit_outpoint", None),
            getattr(self, "btn_red", None),
            getattr(self, "btn_amber", None),
            getattr(self, "btn_green", None),
            getattr(self, "btn_colour_none", None),
        ):
            if widget is not None:
                widget.setVisible(not self._compact_mode)

        if hasattr(self, "horizontalSpacer_meta1"):
            self.horizontalSpacer_meta1.changeSize(
                0 if self._compact_mode else 10,
                20,
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Minimum,
            )
        if hasattr(self, "horizontalSpacer_meta2"):
            self.horizontalSpacer_meta2.changeSize(
                0 if self._compact_mode else 10,
                20,
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Minimum,
            )
        if hasattr(self, "horizontalLayout_6") and self.horizontalLayout_6:
            self.horizontalLayout_6.invalidate()

        if hasattr(self, "bnt_addTask") and self.bnt_addTask:
            self.bnt_addTask.setText("Add" if self._compact_mode else "Add task")
        if hasattr(self, "btn_open_assets") and self.btn_open_assets:
            #self.btn_open_assets.setText("Ast" if self._compact_mode else "assets folder")
            self.btn_open_assets.setToolTip("Open assets folder")
            self.btn_open_assets.setVisible(not self._compact_mode)
        if hasattr(self, "btn_open_precomp") and self.btn_open_precomp:
            #self.btn_open_precomp.setText("Pre" if self._compact_mode else "Precomp folder")
            self.btn_open_precomp.setToolTip("Open precomp folder")
            self.btn_open_precomp.setVisible(not self._compact_mode)

        self._set_hide_button_label()
        self._set_nuke_button_label()
        self._set_render_button_text()
        self._apply_compact_tooltips()
        self._apply_thumb_scale()

    def update_from_data(self, data: dict):
        # Store the latest data
        data = data or {}
        self.data = data

        self.label_shot.setText(data.get("title", "") or "")
        self.label_notes.setText(data.get("notes", "") or "")

        # Update frame range label from duration (styled same as notes)
        if hasattr(self, 'label_frame_range'):
            duration = data.get("duration")
            if duration and duration not in (None, "", 0):
                try:
                    duration_int = int(duration)
                    end_frame = 1000 + duration_int
                    self.label_frame_range.setText(f"1001-{end_frame}")
                except (ValueError, TypeError):
                    self.label_frame_range.setText("")
            else:
                self.label_frame_range.setText("")

        # Update edit in/out labels
        if hasattr(self, 'label_edit_inpoint'):
            edit_in = data.get("edit_inpoint", "—")
            self.label_edit_inpoint.setText(f"In: {edit_in if edit_in else '—'}")

        if hasattr(self, 'label_edit_outpoint'):
            edit_out = data.get("edit_outpoint", "—")
            self.label_edit_outpoint.setText(f"Out: {edit_out if edit_out else '—'}")

        # Update colourspace label
        if hasattr(self, 'label_colourspace'):
            colourspace = data.get("colourspace", "—")
            self.label_colourspace.setText(f"Colourspace.: {colourspace[8:-12] if colourspace else '—'}")

        # Update original clip label
        if hasattr(self, 'label_original_clip'):
            self._original_clip_entries = self._resolve_original_clip_entries(data)
            self.label_original_clip.setText("Clips")

            primary_clip = self._primary_original_clip_entry()
            self.label_original_clip.setProperty("clip_path", primary_clip["clip_path"] if primary_clip else None)

            if self._original_clip_entries:
                if len(self._original_clip_entries) == 1:
                    tooltip = f'{self._original_clip_entries[0]["clip_path"]}\n(Right-click for options)'
                else:
                    clip_names = "\n".join(entry["clip_name"] for entry in self._original_clip_entries[:5])
                    extra_count = max(len(self._original_clip_entries) - 5, 0)
                    if extra_count:
                        clip_names = f"{clip_names}\n+{extra_count} more"
                    tooltip = f"{len(self._original_clip_entries)} OG clips\n{clip_names}\n(Right-click for options)"
                self.label_original_clip.setToolTip(tooltip)
            else:
                self.label_original_clip.setToolTip("")

        self._apply_colour_code(data.get("colour_code"))
        self._set_hide_button_label()
        if not self.btn_latest_render.property("file_path"):
            render_hint = str(data.get("last_render") or "").strip()
            self.btn_latest_render.setProperty("render_display", render_hint or None)
            self._set_render_button_text()
        self._refresh_last_conform_indicator()

        if hasattr(self, 'btn_open_nuke'):
            self.refresh_lock_state_from_data()
            self._set_nuke_button_label()
            self._set_nuke_button_tooltip()

        thumb_path = data.get("thumbnail")
        url = f"{BASE_URL}{thumb_path}" if thumb_path else None
        self._current_thumbnail_url = url
        self.set_thumbnail(url)

        preview_video = data.get("preview_video")
        self._update_preview_from_data(preview_video)

        self._sync_task_widgets_from_data()
        self.set_compact_mode(self._compact_mode)

    def _is_valid_original_clip_path(self, value) -> bool:
        normalized_value = str(value or "").strip()
        return normalized_value not in {"", "None", "—"}

    def _resolve_original_clip_entries(self, data: dict | None = None) -> list[dict]:
        payload = data or self.data or {}
        raw_original_clips = payload.get("original_clips") or []

        clip_paths: list[str] = []
        seen_paths: set[str] = set()

        if isinstance(raw_original_clips, (list, tuple)):
            for raw_path in raw_original_clips:
                clip_path = str(raw_path or "").strip()
                if not self._is_valid_original_clip_path(clip_path) or clip_path in seen_paths:
                    continue
                clip_paths.append(clip_path)
                seen_paths.add(clip_path)

        using_original_clips = bool(clip_paths)
        if not using_original_clips:
            original_clip = str(payload.get("original_clip") or "").strip()
            if self._is_valid_original_clip_path(original_clip):
                clip_paths.append(original_clip)

        clip_entries: list[dict] = []
        for index, clip_path in enumerate(clip_paths, start=1):
            clip_entries.append(
                {
                    "clip_path": clip_path,
                    "clip_name": Path(clip_path).name,
                    "plate_index": index,
                    "plate_name": f"plate_{index:02d}",
                    "menu_label": Path(clip_path).name if using_original_clips else "OG Clip",
                }
            )

        return clip_entries

    def _primary_original_clip_entry(self) -> dict | None:
        if not self._original_clip_entries:
            return None
        return self._original_clip_entries[0]

    def _resolve_original_clip_target(
        self,
        clip_path: str | None = None,
        plate_name: str | None = None,
    ) -> tuple[str | None, str | None]:
        normalized_clip_path = str(clip_path or "").strip()
        normalized_plate_name = str(plate_name or "").strip() or None

        if normalized_clip_path:
            for clip_entry in self._original_clip_entries:
                if clip_entry["clip_path"] == normalized_clip_path:
                    return clip_entry["clip_path"], clip_entry["plate_name"]
            return normalized_clip_path, normalized_plate_name

        primary_clip = self._primary_original_clip_entry()
        if primary_clip is None:
            return None, normalized_plate_name
        return primary_clip["clip_path"], normalized_plate_name or primary_clip["plate_name"]

    def _populate_original_clip_submenu(self, menu: QMenu, clip_entry: dict) -> None:
        clip_path = clip_entry["clip_path"]
        clip_name = clip_entry["clip_name"]
        plate_name = clip_entry["plate_name"]

        open_location = menu.addAction("Open in Files")
        open_location.triggered.connect(
            lambda checked=False, path=clip_path: self.filesIO.openFileLocation(path)
        )

        copy_path = menu.addAction("Copy Path")
        copy_path.triggered.connect(
            lambda checked=False, path=clip_path: self._copy_to_clipboard(path)
        )

        copy_name = menu.addAction("Copy Filename")
        copy_name.triggered.connect(
            lambda checked=False, filename=clip_name: self._copy_to_clipboard(filename)
        )

        menu.addSeparator()

        make_preview = menu.addAction("Make Preview")
        make_preview.triggered.connect(
            lambda checked=False, path=clip_path, plate=plate_name: self._on_make_preview_clicked(
                clip_path=path,
                plate_name=plate,
            )
        )

        make_precomp_exr = menu.addAction("Make Precomp EXR (ACEScg)")
        make_precomp_exr.triggered.connect(
            lambda checked=False, path=clip_path, plate=plate_name: self._on_make_precomp_exr_clicked(
                clip_path=path,
                plate_name=plate,
            )
        )

    def _populate_original_clip_menu(self, menu: QMenu) -> None:
        if not self._original_clip_entries:
            no_clip = menu.addAction("No OG clips available")
            no_clip.setEnabled(False)
            return

        for clip_entry in self._original_clip_entries:
            clip_menu = menu.addMenu(clip_entry["menu_label"])
            self._populate_original_clip_submenu(clip_menu, clip_entry)
        
    def _setup_flow_layout(self):
        """Backwards-compatible alias for the classic task-card layout."""
        self._set_task_layout_mode("card")
    
    def _setup_nk_polling(self):
        """Poll for new .nk files every 10 seconds (with staggered start)."""
        if not hasattr(self, 'shot_dir') or not self.shot_dir:
            return
        
        shot_dir = self.filesIO.convert_path(self.shot_dir)
        scripts_dir = Path(shot_dir) / "scripts"
        
        if not scripts_dir.exists():
            # print(f"Scripts directory does not exist: {scripts_dir}")  # Debug only
            return
        
        # Store the last known file AND modification time
        self._last_nk_file = None
        self._last_nk_mtime = None
        
        latest = self.filesIO.latest_nk(self.shot_dir)
        if latest:
            self._last_nk_file = latest.name
            try:
                self._last_nk_mtime = latest.stat().st_mtime
            except:
                pass
        
        # Create timer for checking new files
        self._nk_poll_timer = QTimer(self)
        self._nk_poll_timer.timeout.connect(self._check_for_new_nk)
        
        # Create timer for updating tooltip (every 30 seconds)
        self._nk_tooltip_timer = QTimer(self)
        self._nk_tooltip_timer.timeout.connect(self._update_nk_tooltip)
        self._nk_tooltip_timer.start(30000)  # Update every 30 seconds
        
        # Start with random offset (0-9 seconds) to stagger timers across all shots
        # This prevents all 100+ shots from checking at the exact same time
        initial_delay = random.randint(0, 9000)
        
        # Safe callback that won't crash if widget is deleted
        def safe_start_nk_timer():
            try:
                if hasattr(self, '_nk_poll_timer') and self._nk_poll_timer:
                    self._nk_poll_timer.start(10000)
            except (RuntimeError, AttributeError):
                pass  # Widget was deleted, ignore
        
        QTimer.singleShot(initial_delay, safe_start_nk_timer)
        
        # Set initial tooltip
        self._update_nk_tooltip()
        
        # Debug: uncomment to see polling setup
        # print(f"✓ Polling .nk files every 10s (starting in {initial_delay/1000:.1f}s) for: {scripts_dir}")

    def _update_nk_tooltip(self):
        """Update the tooltip to show how long ago the .nk file was modified."""
        self._set_nuke_button_tooltip()
    
    def _format_time_ago(self, timestamp):
        """Format a timestamp as 'X mins/hours/days ago'."""
        import time
        now = time.time()
        diff_seconds = now - timestamp
        
        if diff_seconds < 60:
            return "just now"
        elif diff_seconds < 3600:  # Less than 1 hour
            mins = int(diff_seconds / 60)
            return f"{mins} min{'s' if mins != 1 else ''} ago"
        elif diff_seconds < 86400:  # Less than 1 day
            hours = int(diff_seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:  # 1 day or more
            days = int(diff_seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"
    
    def _on_open_nuke_clicked(self, nk_path):
        """Open the shot .nk file directly or delegate to lock-aware handler."""
        if not nk_path:
            return

        open_handler = getattr(self, "_nuke_open_handler", None)
        if callable(open_handler):
            open_handler(self, str(nk_path))
            return

        self.filesIO.open_file(str(nk_path))
    def _check_for_new_nk(self):
        """Check if a new .nk file has appeared or been modified."""
        try:
            latest_nk = self.filesIO.latest_nk(self.shot_dir)
            
            if not latest_nk:
                if self._last_nk_file is not None:
                    self._last_nk_file = None
                    self._last_nk_mtime = None
                    self.btn_open_nuke.setProperty("file_path", None)  # Clear stored path
                    self._set_nuke_button_label()
                    self._set_nuke_button_tooltip()
                return
            
            current_file = latest_nk.name
            current_mtime = None
            try:
                current_mtime = latest_nk.stat().st_mtime
            except:
                pass
            
            # Check if file changed (name OR modification time)
            file_changed = (self._last_nk_file != current_file)
            file_modified = (self._last_nk_mtime != current_mtime) if (self._last_nk_mtime and current_mtime) else False
            
            if file_changed or file_modified:
                # Debug: uncomment to see file changes
                # if file_changed:
                #     print(f"🆕 New .nk file detected: {self._last_nk_file} → {current_file}")
                # else:
                #     print(f"📝 .nk file updated: {current_file}")
                
                # Store file path on button for context menu
                self.btn_open_nuke.setProperty("file_path", str(latest_nk))
                self.refresh_lock_state_from_data()
                self._set_nuke_button_label()
                
                # Reconnect button (disconnect old connection first)
                try:
                    self.btn_open_nuke.clicked.disconnect()
                except:
                    pass
                
                # Capture latest_nk in closure to avoid stale reference
                nk_path = latest_nk
                self.btn_open_nuke.clicked.connect(
                    lambda checked=False, path=nk_path: self._on_open_nuke_clicked(path)
                )
                
                # Visual feedback (green flash)
                self._flash_button_green(self.btn_open_nuke)
                
                # Update tracking variables
                self._last_nk_file = current_file
                self._last_nk_mtime = current_mtime
                
                # Update tooltip immediately
                self._update_nk_tooltip()
                
        except Exception as e:
            pass  # Silent fail - could log to file if needed

    def _setup_render_polling(self):
        """Poll for new render files every 10 seconds (with staggered start)."""
        if not hasattr(self, 'shot_dir') or not self.shot_dir:
            return
        
        shot_dir = self.filesIO.convert_path(self.shot_dir)
        renders_dir = Path(shot_dir) / "renders" / "comp"
        
        if not renders_dir.exists():
            # print(f"Renders directory does not exist: {renders_dir}")  # Debug only
            return
        
        # Store last known render
        self._last_render_file = None
        self._last_render_mtime = None
        
        render_info = self.filesIO.latest_render_info(self.shot_dir)
        if render_info:
            self._last_render_file = render_info.get("render_relpath")
            self._last_render_mtime = render_info.get("mtime")
        
        # Create timer for checking new files
        self._render_poll_timer = QTimer(self)
        self._render_poll_timer.timeout.connect(self._check_for_new_render)
        
        # Create timer for updating tooltip (every 30 seconds)
        self._render_tooltip_timer = QTimer(self)
        self._render_tooltip_timer.timeout.connect(self._update_render_tooltip)
        self._render_tooltip_timer.start(30000)  # Update every 30 seconds
        
        # Staggered start (different offset than .nk to spread load even more)
        initial_delay = random.randint(0, 9000)
        
        # Safe callback that won't crash if widget is deleted
        def safe_start_render_timer():
            try:
                if hasattr(self, '_render_poll_timer') and self._render_poll_timer:
                    self._render_poll_timer.start(10000)
            except (RuntimeError, AttributeError):
                pass  # Widget was deleted, ignore
        
        QTimer.singleShot(initial_delay, safe_start_render_timer)
        
        # Set initial tooltip
        self._update_render_tooltip()
        
        # Debug: uncomment to see polling setup
        # print(f"✓ Polling renders every 10s (starting in {initial_delay/1000:.1f}s) for: {renders_dir}")

    def _update_render_tooltip(self):
        """Update the tooltip to show how long ago the render file was modified."""
        self._apply_compact_tooltips()

    def _check_for_new_render(self):
        """Check if a new render file has appeared."""
        try:
            render_info = self.filesIO.latest_render_info(self.shot_dir)

            if not render_info:
                if self._last_render_file is not None:
                    self._last_render_file = None
                    self._last_render_mtime = None
                    self.btn_latest_render.setProperty("file_path", None)  # Clear stored path
                    self.btn_latest_render.setProperty("render_relpath", None)
                    self.btn_latest_render.setProperty("render_display", None)
                    self.btn_latest_render.setProperty("render_type", None)
                    self.btn_latest_render.setProperty("render_sequence_path", None)
                    self.btn_latest_render.setProperty("render_dir", None)
                    self.btn_latest_render.setProperty("render_version", None)
                    self._set_render_button_text()
                    self._apply_compact_tooltips()
                return

            render_path = render_info.get("render_path")
            render_display = render_info.get("display_name", "No Render")
            render_relpath = render_info.get("render_relpath")
            render_sequence_path = render_info.get("sequence_path")
            render_dir = render_info.get("render_dir")
            render_version = render_info.get("version")
            current_mtime = render_info.get("mtime")
            render_id = render_relpath or render_display

            # Check if changed
            file_changed = (self._last_render_file != render_id)
            file_modified = (self._last_render_mtime != current_mtime) if (self._last_render_mtime and current_mtime) else False
            
            if file_changed or file_modified:
                # Debug: uncomment to see file changes
                # if file_changed:
                #     print(f"🎬 New render: {self._last_render_file} → {render_display}")
                # else:
                #     print(f"📝 Render updated: {render_display}")
                
                # Store file path on button for context menu
                self.btn_latest_render.setProperty("file_path", render_path)
                self.btn_latest_render.setProperty("render_relpath", render_relpath)
                self.btn_latest_render.setProperty("render_display", render_display)
                self.btn_latest_render.setProperty("render_type", render_info.get("type"))
                self.btn_latest_render.setProperty("render_sequence_path", render_sequence_path)
                self.btn_latest_render.setProperty("render_dir", render_dir)
                self.btn_latest_render.setProperty("render_version", render_version)
                self._set_render_button_text()
                
                # Reconnect button
                try:
                    self.btn_latest_render.clicked.disconnect()
                except:
                    pass
                
                # Capture in closure
                rpath = render_path
                self.btn_latest_render.clicked.connect(self._on_push_to_dvr_clicked)
                
                # Visual feedback
                self._flash_button_green(self.btn_latest_render)
                
                # Update tracking
                self._last_render_file = render_id
                self._last_render_mtime = current_mtime
                
                # Update tooltip immediately
                self._update_render_tooltip()
                
                # Update last_conform label color based on whether it matches new render
                if hasattr(self, 'label_last_conform'):
                    last_conform = self.data.get("last_conform") if self.data else None
                    if last_conform and self._normalize_render_label(last_conform) == self._normalize_render_label(render_display):
                        self.label_last_conform.setStyleSheet("color: #4CAF50; font-weight: bold;")
                    else:
                        self.label_last_conform.setStyleSheet("")
                
                # Update API with new render name
                if render_relpath:
                    try:
                        self._api.update_shot(self._shot_id, last_render=render_relpath)
                        # Update local data to stay in sync
                        if self.data:
                            self.data['last_render'] = render_relpath
                    except Exception as api_error:
                        pass  # Silent fail - don't break polling on API error
                
        except Exception as e:
            pass  # Silent fail - could log to file if needed

    def _flash_button_green(self, button):
        """Flash a button green for 2 seconds to show a file was detected."""
        original_style = button.styleSheet()
        button.setStyleSheet(original_style + " background-color: #4CAF50; font-weight: bold;")
        QTimer.singleShot(2000, lambda: button.setStyleSheet(original_style))

    # ==================== VIDEO PREVIEW METHODS ====================
    
    def _setup_video_preview_stack(self):
        """Set up stacked widget for thumbnail/video switching on hover."""
        if not HAS_MULTIMEDIA:
            return
            
        # Get the parent of the thumbnail label
        thumb_parent = self.label_thumbnail.parent()
        thumb_layout = thumb_parent.layout() if thumb_parent else None
        
        # Create a stacked widget to hold thumbnail and video
        self._preview_stack = QStackedWidget()
        # Don't set fixed size here - let it be set when thumbnail loads
        self._preview_stack.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        
        # Create a container for the thumbnail
        self._thumb_container = QWidget()
        self._thumb_container.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        thumb_container_layout = QVBoxLayout(self._thumb_container)
        thumb_container_layout.setContentsMargins(0, 0, 0, 0)
        thumb_container_layout.setSpacing(0)
        
        # Move the thumbnail label into the container
        if thumb_layout:
            thumb_layout.removeWidget(self.label_thumbnail)
        self.label_thumbnail.setParent(self._thumb_container)
        thumb_container_layout.addWidget(self.label_thumbnail)
        
        # Create video widget - don't set fixed size yet
        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        
        # Create media player
        self._video_player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.5)
        self._video_player.setAudioOutput(self._audio_output)
        self._video_player.setVideoOutput(self._video_widget)
        self._video_player.setLoops(QMediaPlayer.Loops.Infinite)
        
        # Add to stack (thumbnail first, video second)
        self._preview_stack.addWidget(self._thumb_container)  # Index 0
        self._preview_stack.addWidget(self._video_widget)      # Index 1
        self._preview_stack.setCurrentIndex(0)  # Show thumbnail by default
        
        # Add stack to the original layout
        if thumb_layout:
            thumb_layout.insertWidget(0, self._preview_stack)
        
        # Create preview indicator label
        self._preview_indicator = QLabel("▶ Preview")
        self._preview_indicator.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 0.7);
                color: #4CAF50;
                padding: 2px 6px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
            }
        """)
        self._preview_indicator.setParent(self._thumb_container)
        self._preview_indicator.hide()
        
        # Install event filter for hover detection
        self._preview_stack.installEventFilter(self)
    
    def _position_preview_indicator(self):
        """Position the preview indicator at bottom-left of thumbnail."""
        if hasattr(self, '_preview_indicator') and self._preview_indicator:
            self._preview_indicator.adjustSize()
            # Position at bottom-left with small margin
            y_pos = self.label_thumbnail.height() - self._preview_indicator.height() - 5
            self._preview_indicator.move(5, y_pos)
    
    def eventFilter(self, obj, event):
        """Handle hover events for video preview."""
        if not HAS_MULTIMEDIA:
            return super().eventFilter(obj, event)
            
        if hasattr(self, '_preview_stack') and obj == self._preview_stack:
            if event.type() == QEvent.Type.Enter:
                self._on_preview_hover_enter()
            elif event.type() == QEvent.Type.Leave:
                self._on_preview_hover_leave()
        return super().eventFilter(obj, event)
    
    def _on_preview_hover_enter(self):
        """Start video playback on hover."""
        if not self._has_preview or not self._preview_video_path:
            return
        if not HAS_MULTIMEDIA or not self._video_player:
            return
            
        try:
            # Set video source and play
            self._video_player.setSource(QUrl.fromLocalFile(self._preview_video_path))
            self._preview_stack.setCurrentIndex(1)  # Show video
            self._video_player.play()
        except Exception as e:
            pass  # Silent fail
    
    def _on_preview_hover_leave(self):
        """Stop video and show thumbnail on leave."""
        if not HAS_MULTIMEDIA or not self._video_player:
            return
            
        try:
            self._video_player.stop()
            self._preview_stack.setCurrentIndex(0)  # Show thumbnail
        except Exception as e:
            pass  # Silent fail
    
    def _setup_preview_polling(self):
        """Poll for preview videos and update API when new one found."""
        if not hasattr(self, 'shot_dir') or not self.shot_dir:
            #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: No shot_dir, skipping preview polling")
            return
        
        #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: Setting up preview polling for {self.shot_dir}")
        
        # Store last known preview version for change detection
        # Initialize to None - let _update_preview_from_data set it from API data
        # This way polling will detect if filesystem has a newer version than API
        self._last_preview_version = None
        
        # Create timer for checking new files
        self._preview_poll_timer = QTimer(self)
        self._preview_poll_timer.timeout.connect(self._check_for_new_preview)
        
        # Staggered start
        initial_delay = random.randint(0, 9000)
        
        def safe_start_preview_timer():
            try:
                if hasattr(self, '_preview_poll_timer') and self._preview_poll_timer:
                    self._preview_poll_timer.start(10000)
            except (RuntimeError, AttributeError):
                pass
        
        QTimer.singleShot(initial_delay, safe_start_preview_timer)
    
    def _extract_version(self, filename):
        """Extract version from either sho010_v001.mp4 or sho010_v01_preview.mp4."""
        if not filename:
            return None
        import re
        match = re.search(r'_v(\d+)(?:_preview)?\.mp4$', filename, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
    
    def _check_for_new_preview(self):
        """Check if a new preview video has appeared and update API if found."""
        try:
            preview_path, preview_name = self.filesIO.latest_preview(self.shot_dir)
            
            if not preview_path:
                #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: No preview found at {self.shot_dir}/renders/precomp/previews/")
                return
            
            current_version = self._extract_version(preview_name)
            
            #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: Checking - file={preview_name}, current_version={current_version}, last_version={self._last_preview_version}")
            
            # Check if version is higher OR if we have a different file
            should_update = False
            if self._last_preview_version is None and current_version is not None:
                should_update = True
                #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: First preview found")
            elif current_version is not None and self._last_preview_version is not None:
                if current_version > self._last_preview_version:
                    should_update = True
                    #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: Higher version found: v{self._last_preview_version} -> v{current_version}")
            
            # Also check if the actual file path changed (regardless of version parsing)
            if not should_update and preview_path and self._preview_video_path:
                if str(preview_path) != self._preview_video_path:
                    should_update = True
                    #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: Different preview file detected")
            
            if should_update:
                # Update tracking
                self._last_preview_version = current_version
                
                # Immediately update local playback path so hover works right away
                self._preview_video_path = str(preview_path)
                self._has_preview = True
                if hasattr(self, '_preview_indicator'):
                    self._preview_indicator.show()
                    self._position_preview_indicator()
                
                # Update API with preview path
                relative_preview = f"renders/precomp/previews/{preview_name}"
                #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: UPDATING API with preview_video={relative_preview}")
                
                try:
                    result = self._api.update_shot(self._shot_id, preview_video=relative_preview)
                    #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: API update SUCCESS")
                except Exception as api_error:
                    #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: API update FAILED - {api_error}")
                    pass
            else:
                #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: No version change, skipping API update")
                pass
                    
        except Exception as e:
            #print(f"[PREVIEW DEBUG] Shot {self._shot_id}: EXCEPTION in _check_for_new_preview: {e}")
            pass

    def _update_preview_from_data(self, preview_video: str | None):
        """Update preview video state from API data."""
        if not HAS_MULTIMEDIA:
            return
            
        if preview_video:
            # Build full path from base_path + relative preview path
            full_path = Path(self.filesIO.convert_path(self.shot_dir)) / preview_video
            
            if full_path.exists():
                self._preview_video_path = str(full_path)
                self._has_preview = True
                
                # Sync version tracking from API data to prevent re-detection
                preview_name = Path(preview_video).name
                api_version = self._extract_version(preview_name)
                if api_version is not None:
                    # Only update if API has a higher or equal version
                    if self._last_preview_version is None or api_version >= self._last_preview_version:
                        self._last_preview_version = api_version
                
                # Show indicator
                if hasattr(self, '_preview_indicator'):
                    self._preview_indicator.show()
                    self._position_preview_indicator()
            else:
                self._preview_video_path = None
                self._has_preview = False
                if hasattr(self, '_preview_indicator'):
                    self._preview_indicator.hide()
        else:
            self._preview_video_path = None
            self._has_preview = False
            if hasattr(self, '_preview_indicator'):
                self._preview_indicator.hide()
    
    def _update_preview_stack_size(self):
        """Update video widget and stack size when thumbnail size changes."""
        if not HAS_MULTIMEDIA:
            return
        if hasattr(self, '_preview_stack') and self._preview_stack:
            size = self.label_thumbnail.size()
            self._preview_stack.setFixedSize(size)
            if hasattr(self, '_thumb_container') and self._thumb_container:
                self._thumb_container.setFixedSize(size)
            if hasattr(self, '_video_widget') and self._video_widget:
                self._video_widget.setFixedSize(size)
            self._position_preview_indicator()

    # ==================== END VIDEO PREVIEW METHODS ====================
        
    def closeEvent(self, event):
        """Stop polling timers when widget is closed."""
        if hasattr(self, '_nk_poll_timer'):
            self._nk_poll_timer.stop()
        if hasattr(self, '_nk_tooltip_timer'):
            self._nk_tooltip_timer.stop()
        if hasattr(self, '_render_poll_timer'):
            self._render_poll_timer.stop()
        if hasattr(self, '_render_tooltip_timer'):
            self._render_tooltip_timer.stop()
        if hasattr(self, '_preview_poll_timer'):
            self._preview_poll_timer.stop()
        # Clean up video player
        if hasattr(self, '_video_player') and self._video_player:
            self._video_player.stop()
        super().closeEvent(event)
        
    
    def on_hide_btn(self):
        """Toggle hide/unhide status of the shot."""
        # Use current data stored on the instance, not stale parameter
        current_hidden = self.data.get("hidden", False)
        
        # Toggle the hidden state
        new_hidden = not current_hidden
        
        # Update in database
        try:
            updated_data = self._api.update_shot(shot_id=self._shot_id, hidden=new_hidden)
            # If API returns updated data, use it
            if updated_data:
                self.update_from_data(updated_data)
            else:
                # Otherwise update local data and UI manually
                self.data["hidden"] = new_hidden
                self._set_hide_button_label()
        except Exception as e:
            pass  # Silent fail - could log to file if needed  


    # NEW: minimal slot to create and append a task
    def on_colour_btn(self, colour):
        """Handle color button clicks - applies color immediately and updates database."""
        # Apply color immediately using centralized color map
        self._apply_colour_code(colour)
        
        # Update database (will be reflected when API data comes back)
        self._api.update_shot(shot_id=self._shot_id, colour_code=colour)
    
    def _on_make_preview_clicked(
        self,
        checked: bool = False,
        clip_path: str | None = None,
        plate_name: str | None = None,
    ):
        """Generate a preview video from the original clip using Nuke headless (always v001)."""
        self._make_preview_from_source(
            source_type="original_clip",
            clip_path=clip_path,
            plate_name=plate_name,
        )

    def _on_make_precomp_exr_clicked(
        self,
        checked: bool = False,
        clip_path: str | None = None,
        plate_name: str | None = None,
    ):
        """Generate a precomp EXR sequence from the original clip (v01, ACEScg)."""
        self._make_precomp_exr_from_original(
            clip_path=clip_path,
            plate_name=plate_name,
        )
    
    def _on_make_preview_from_render_clicked(self):
        """Generate a preview video from the latest render using Nuke headless (matches render version)."""
        self._make_preview_from_source(source_type="render")

    def _resolve_preview_project_name(self) -> str:
        job_name = str((self.data or {}).get("job_name", "") or "").strip()
        if job_name:
            return job_name

        job_data = (self.data or {}).get("job")
        if isinstance(job_data, dict):
            for key in ("title", "name"):
                value = str(job_data.get(key, "") or "").strip()
                if value:
                    return value

        shot_dir = str(getattr(self, "shot_dir", "") or (self.data or {}).get("base_path", "") or "").strip()
        if shot_dir:
            resolved = Path(self.filesIO.convert_path(shot_dir))
            parts = list(resolved.parts)
            for marker in ("VFX", "Nuke"):
                marker_lower = marker.lower()
                for index, part in enumerate(parts):
                    if part.lower() == marker_lower and index > 0:
                        candidate = str(parts[index - 1]).strip()
                        if candidate:
                            return candidate

        return "Project"
    
    def _make_preview_from_source(
        self,
        source_type: str = "original_clip",
        clip_path: str | None = None,
        plate_name: str | None = None,
    ):
        """
        Generate a preview video from either original clip or render.
        
        Args:
            source_type: "original_clip" for v001, "render" to match render version
        """
        from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QApplication
        from PyQt6.QtCore import Qt
        
        # Import the preview generator from nuke_headless_tasks
        try:
            from nuke_headless_tasks import (
                PreviewGenerator,
                build_preview_output_path,
                extract_version_from_path,
                resolve_preview_input_colourspace,
            )
            from settings import get_settings_manager
        except ImportError:
            QMessageBox.warning(
                self, "Make Preview",
                "nuke_headless_tasks module not found.\n\nEnsure it's in the same directory as widgets.py"
            )
            return
        
        target_plate_name = None
        render_type = None

        # Get source path and version based on source type
        if source_type == "render":
            # Get render path from button property
            render_path = self.btn_latest_render.property("file_path")
            render_type = self.btn_latest_render.property("render_type")
            render_sequence_path = self.btn_latest_render.property("render_sequence_path")
            render_version = self.btn_latest_render.property("render_version")
            if not render_path:
                QMessageBox.warning(self, "Make Preview", "No render file available.")
                return
            sequence_path = render_sequence_path if render_type == "exr" and render_sequence_path else render_path
            input_path = self.filesIO.convert_path(sequence_path)
            # Extract version from render filename or use stored version
            version = render_version if render_version is not None else extract_version_from_path(sequence_path)
            if version is None:
                version = 1  # Fallback to v001 if no version found
        else:
            resolved_clip_path, resolved_plate_name = self._resolve_original_clip_target(
                clip_path=clip_path,
                plate_name=plate_name,
            )
            if not resolved_clip_path:
                QMessageBox.warning(self, "Make Preview", "No original clip path available.")
                return
            input_path = self.filesIO.convert_path(resolved_clip_path)
            version = 1  # Original clip always creates v001
            target_plate_name = resolved_plate_name or "plate_01"
        
        # Get shot metadata
        shot_dir = self.filesIO.convert_path(self.shot_dir)
        shot_name = self.data.get("title", "shot")
        shot_colourspace = self.data.get("colourspace", "sRGB")
        fps = self.data.get("fps", 25)
        preview_colourspace = resolve_preview_input_colourspace(
            source_type=source_type,
            media_type=render_type if source_type == "render" else None,
            input_path=input_path,
            requested_colourspace=shot_colourspace,
        )
        
        # Get job name for the project field on slate
        job_name = self._resolve_preview_project_name()
        
        settings = get_settings_manager()
        preview_quality = settings.get("preview_quality", "medium")
        overwrite_enabled = bool(settings.get("preview_overwrite", False))
        nuke_exe_path = settings.get("nuke_exe_path", "")

        # Create the preview generator
        generator = PreviewGenerator(nuke_path=nuke_exe_path or None)
        
        # Validate Nuke is available
        if not generator.nuke_available:
            QMessageBox.warning(
                self, "Make Preview",
                "Nuke executable not found.\n\nPlease ensure Nuke is installed and available on this machine."
            )
            return
        
        # Validate input file
        valid, error = generator.validate_input(input_path)
        if not valid:
            QMessageBox.warning(self, "Make Preview", error)
            return
        
        # Pre-check for existing preview if overwrite is disabled
        output_path, _, file_exists = build_preview_output_path(
            shot_dir=shot_dir,
            shot_name=shot_name,
            version=version,
            plate_name=target_plate_name,
        )
        if file_exists and not overwrite_enabled:
            QMessageBox.information(
                self, "Make Preview",
                f"Preview already exists:\n{output_path.name}\n\nEnable overwrite in Settings to replace it."
            )
            return

        # Common parameters for preview generation
        preview_params = dict(
            input_path=input_path,
            shot_dir=shot_dir,
            shot_name=shot_name,
            project=job_name,
            artist="ShotBox",
            colourspace=preview_colourspace,
            fps=fps,
            quality=preview_quality,
            version=version,
            plate_name=target_plate_name,
        )

        preview_target_label = (
            f"{shot_name} {target_plate_name} v{version:03d}"
            if target_plate_name
            else f"{shot_name} v{version:03d}"
        )
        
        # Show progress dialog for the actual render
        progress = QProgressDialog(
            f"Generating preview for {preview_target_label}...\n\nThis may take a few minutes.",
            "Cancel", 0, 0, self
        )
        progress.setWindowTitle("Make Preview")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        # Output callback for debug logging
        def on_output(line):
            print(line)
            QApplication.processEvents()
        
        # Cancellation check callback
        def check_cancelled():
            QApplication.processEvents()
            return progress.wasCanceled()
        
        # Generate the preview (with overwrite if needed)
        result = generator.generate_preview_with_overwrite(
            **preview_params,
            on_output=on_output,
            check_cancelled=check_cancelled,
        )
        
        progress.close()
        
        if result.success:
            # Update the shot's preview_video field in the database
            try:
                self._api.update_shot(self._shot_id, preview_video=result.relative_path)
            except Exception as e:
                print(f"Warning: Could not update preview_video in database: {e}")
            
            QMessageBox.information(
                self, "Make Preview",
                f"Preview created successfully!\n\n{result.output_path}"
            )
        elif "cancelled" in result.error.lower():
            QMessageBox.information(self, "Make Preview", "Preview generation cancelled.")
        else:
            error_text = "\n".join(result.output_lines[-20:]) if result.output_lines else result.error
            QMessageBox.warning(
                self, "Make Preview",
                f"Preview generation failed.\n\n{error_text}"
            )

    def _make_precomp_exr_from_original(
        self,
        clip_path: str | None = None,
        plate_name: str | None = None,
    ):
        """Generate a precomp EXR sequence from the original clip using Nuke headless."""
        from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QApplication
        from PyQt6.QtCore import Qt

        try:
            from nuke_headless_tasks import PreviewGenerator, build_precomp_exr_output_path
            from settings import get_settings_manager
        except ImportError:
            QMessageBox.warning(
                self, "Make Precomp EXR",
                "nuke_headless_tasks module not found.\n\nEnsure it's in the same directory as widgets.py"
            )
            return

        resolved_clip_path, resolved_plate_name = self._resolve_original_clip_target(
            clip_path=clip_path,
            plate_name=plate_name,
        )
        if not resolved_clip_path:
            QMessageBox.warning(self, "Make Precomp EXR", "No original clip path available.")
            return

        input_path = self.filesIO.convert_path(resolved_clip_path)
        shot_dir = self.filesIO.convert_path(self.shot_dir)
        shot_name = self.data.get("title", "shot")
        colourspace = self.data.get("colourspace", "sRGB")
        fps = self.data.get("fps", 25)
        version = 1
        target_plate_name = resolved_plate_name or "plate_01"

        settings = get_settings_manager()
        overwrite_enabled = bool(settings.get("preview_overwrite", False))
        nuke_exe_path = settings.get("nuke_exe_path", "")

        generator = PreviewGenerator(nuke_path=nuke_exe_path or None)

        if not generator.nuke_available:
            QMessageBox.warning(
                self, "Make Precomp EXR",
                "Nuke executable not found.\n\nPlease ensure Nuke is installed and available on this machine."
            )
            return

        valid, error = generator.validate_input(input_path)
        if not valid:
            QMessageBox.warning(self, "Make Precomp EXR", error)
            return

        sequence_path, _, file_exists = build_precomp_exr_output_path(
            shot_dir=shot_dir,
            shot_name=shot_name,
            version=version,
            plate_name=target_plate_name,
        )
        if file_exists and not overwrite_enabled:
            QMessageBox.information(
                self, "Make Precomp EXR",
                f"Precomp EXR already exists:\n{sequence_path.parent}\n\nEnable overwrite in Settings to replace it."
            )
            return

        progress = QProgressDialog(
            f"Generating precomp EXR for {shot_name} {target_plate_name} v{version:02d}...\n\nThis may take a few minutes.",
            "Cancel", 0, 0, self
        )
        progress.setWindowTitle("Make Precomp EXR")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def on_output(line):
            print(line)
            QApplication.processEvents()

        def check_cancelled():
            QApplication.processEvents()
            return progress.wasCanceled()

        result = generator.generate_precomp_exr_with_overwrite(
            input_path=input_path,
            shot_dir=shot_dir,
            shot_name=shot_name,
            colourspace=colourspace or "sRGB",
            fps=fps,
            version=version,
            plate_name=target_plate_name,
            on_output=on_output,
            check_cancelled=check_cancelled,
        )

        progress.close()

        if result.success:
            QMessageBox.information(
                self, "Make Precomp EXR",
                f"Precomp EXR created successfully!\n\n{result.output_dir}"
            )
        elif "cancelled" in result.error.lower():
            QMessageBox.information(self, "Make Precomp EXR", "Precomp EXR generation cancelled.")
        else:
            error_text = "\n".join(result.output_lines[-20:]) if result.output_lines else result.error
            QMessageBox.warning(
                self, "Make Precomp EXR",
                f"Precomp EXR generation failed.\n\n{error_text}"
            )
        
    def _on_add_task_clicked(self):
        if not self._shot_id:
            return
        dlg = TaskCreateDialog(self, api=self._api)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        values = dlg.get_values()
        try:
            new_task = self._api.create_task(shot_id=self._shot_id, title=values.get("title", "New Task"))
            update_payload = {}
            if values.get("notes"):
                update_payload["notes"] = values["notes"]
            if values.get("status") is not None:
                update_payload["status"] = values["status"]
            if values.get("priority") is not None:
                update_payload["priority"] = values["priority"]
            if values.get("budget_hours") is not None:
                update_payload["budget_hours"] = values["budget_hours"]
            if values.get("artist") is not None:
                update_payload["artist"] = values["artist"]
            if update_payload:
                new_task = self._api.update_task(new_task.get("id"), **update_payload)
            tasks = list(self.data.get("tasks", []) or [])
            tasks.append(new_task)
            self.data["tasks"] = tasks
            self._sync_task_widgets_from_data()
            self._notify_filter_state_changed()
        except Exception as e:
            print(f"[ShotCard] Add task failed: {e}")

    def _on_add_note_clicked(self):
        if not self._shot_id:
            return
        # start with current notes text
        current = self.label_notes.text() or ""
        text, ok = QInputDialog.getMultiLineText(self, "Add note", "Notes:", current)
        if not ok:
            return
        new_note = (text or "").strip()
        try:
            updated = self._api.update_shot(self._shot_id, notes=new_note)
            self.update_from_data(updated)
        except Exception as e:
            pass  # Silent fail
        self.update_from_data(self.data)   

    def _on_push_to_dvr_clicked(self):
        """Push latest render to DaVinci Resolve and update last_conform field."""
        # Get the current render path from button property
        render_path = self.btn_latest_render.property("file_path")
        render_type = self.btn_latest_render.property("render_type")
        render_sequence_path = self.btn_latest_render.property("render_sequence_path")
        render_dir = self.btn_latest_render.property("render_dir")
        
        if render_type == "exr":
            dvr_path = render_dir or render_sequence_path or render_path
        else:
            dvr_path = render_path

        if not dvr_path or dvr_path == "none":
            return
        
        render_display = self.btn_latest_render.property("render_display")
        if not render_display:
            render_display = self.btn_latest_render.text()
        if not render_display:
            render_display = Path(render_path).name if render_path else None
        
        # Push to DaVinci Resolve - only update database if successful
        success = self.filesIO.push2dvr(dvr_path)
        
        if success and self._shot_id and render_display:
            try:
                updated = self._api.update_shot(self._shot_id, last_conform=render_display)
                # Update the label immediately
                if hasattr(self, 'label_last_conform'):
                    self.label_last_conform.setText(render_display)
                    # Set green style since we just conformed the latest render
                    self.label_last_conform.setStyleSheet("color: #4CAF50; font-weight: bold;")
                # Update local data
                self.data['last_conform'] = render_display
                self._apply_compact_tooltips()
            except Exception as e:
                pass  # Silent fail - DVR push already happened


    def contextMenuEvent(self, event):
        """Handle right-click context menu events."""
        widget = self.childAt(event.pos())
        
        # Check if right-click was on btn_open_nuke, btn_latest_render, or btn_open_assets
        if widget is None:
            return
            
        # Assets button handles its own context menu directly.
        if widget == self.btn_open_assets:
            return

        if widget == self.btn_open_nuke or widget == self.btn_latest_render:
            menu = QMenu(self)
            
            if widget == self.btn_open_nuke:
                # Get the nuke file path from button property
                nk_path = self.btn_open_nuke.property("file_path")
                if nk_path and nk_path != "none":
                    open_location = menu.addAction("Open File Location")
                    open_location.triggered.connect(lambda: self.filesIO.openFileLocation(str(nk_path)))
                    
                    copy_path = menu.addAction("Copy File Path")
                    copy_path.triggered.connect(lambda: self._copy_to_clipboard(str(nk_path)))
                    
                    copy_name = menu.addAction("Copy File Name")
                    # Extract filename from path
                    filename = Path(nk_path).name if nk_path else "unknown"
                    copy_name.triggered.connect(lambda: self._copy_to_clipboard(filename))
                else:
                    no_file = menu.addAction("No Nuke file found")
                    no_file.setEnabled(False)
            
            elif widget == self.btn_latest_render:
                # Get the render file path from button property
                render_path = self.btn_latest_render.property("file_path")
                if render_path and render_path != "none":
                    open_location = menu.addAction("Open File Location")
                    open_location.triggered.connect(lambda: self.filesIO.openFileLocation(render_path))
                    
                    copy_path = menu.addAction("Copy File Path")
                    copy_path.triggered.connect(lambda: self._copy_to_clipboard(render_path))
                    
                    copy_name = menu.addAction("Copy File Name")
                    # Extract filename from path
                    filename = Path(render_path).name if render_path else "unknown"
                    copy_name.triggered.connect(lambda: self._copy_to_clipboard(filename))
                    
                    open_file = menu.addAction("Open in Default Player")
                    open_file.triggered.connect(lambda: self.filesIO.open_file(render_path))
                    
                    menu.addSeparator()
                    
                    make_preview = menu.addAction("Make Preview")
                    make_preview.triggered.connect(self._on_make_preview_from_render_clicked)
                else:
                    no_file = menu.addAction("No render found")
                    no_file.setEnabled(False)
            
            menu.exec(event.globalPos())
        
        # Check if right-click was on label_frame_range
        elif hasattr(self, 'label_frame_range') and widget == self.label_frame_range:
            menu = QMenu(self)
            edit_duration = menu.addAction("Edit Duration")
            edit_duration.triggered.connect(self._on_edit_duration_clicked)
            menu.exec(event.globalPos())
        
        # Check if right-click was on label_original_clip
        elif hasattr(self, 'label_original_clip') and widget == self.label_original_clip:
            menu = QMenu(self)
            self._populate_original_clip_menu(menu)
            menu.exec(event.globalPos())
    
    def _copy_to_clipboard(self, text):
        """Copy text to system clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(str(text))
        # Optional: uncomment for debugging
        # print(f"Copied to clipboard: {text}")

    def _on_edit_duration_clicked(self):
        """Handle editing the shot duration via right-click on frame range label."""
        current_duration = self.data.get("duration", 0)
        try:
            current_duration = int(current_duration) if current_duration else 0
        except (ValueError, TypeError):
            current_duration = 0
        
        new_duration, ok = QInputDialog.getInt(
            self,
            "Edit Duration",
            "Enter shot duration (frames):",
            value=current_duration,
            min=1,
            max=99999
        )
        
        if ok and new_duration != current_duration:
            try:
                self._api.update_shot(self._shot_id, duration=new_duration)
                # Update local data and label immediately
                self.data['duration'] = new_duration
                end_frame = 1000 + new_duration
                self.label_frame_range.setText(f"1001-{end_frame}")
            except Exception as e:
                pass  # Silent fail


    def _apply_thumb_scale(self):
        if not self._thumb_orig:
            return
        target_width = self._current_thumb_target_width()
        pixel_map = self._thumb_orig.scaledToWidth(
            int(target_width),
            Qt.TransformationMode.SmoothTransformation
        )
        self.label_thumbnail.setPixmap(pixel_map)
        self.label_thumbnail.setFixedSize(pixel_map.size())
        # Update video preview stack size to match
        self._update_preview_stack_size()

    def set_thumbnail_width(self, width: int):
        self._thumb_target_width = max(1, int(width))
        self._base_thumb_target_width = self._thumb_target_width
        if self._thumbnails_enabled:
            self._apply_thumb_scale()

    def set_thumbnail(self, url: str | None):
        self._current_thumbnail_url = url
        if not url:
            return
        if not self._thumbnails_enabled:
            self._thumb_pending_url = None
            return
        if url == self._thumb_sig and self._thumb_orig is not None:
            # same image, just reapply scale in case width changed
            self._apply_thumb_scale()
            return
        self._thumb_pending_url = url
        thumb_token = project_load_profiler.start_installed_async_work("thumbnail_load")

        def _on_loaded(pixmap: QPixmap | None) -> None:
            project_load_profiler.finish_installed_async_work(thumb_token)
            try:
                if self._thumb_pending_url != url:
                    return
                if pixmap is None:
                    return
                self._thumb_orig = pixmap
                self._thumb_sig = url
                self._apply_thumb_scale()
            except RuntimeError:
                return

        ImageLoader.instance().load(url, _on_loaded)

class TimelineFrame(QWidget):
    def __init__(
        self,
        timeline=None,
        show_hidden=False,
        layout_mode="list",
        card_spacing=8,
        compact_mode=False,
        task_style="card",
        nuke_open_handler=None,
        api=None,
        folders=None,
        desired_order: list[str] | None = None,
    ):
        super().__init__()
        timeline = timeline or {}
        self.show_hidden = show_hidden
        self._last_timeline = timeline  # keep a copy for refreshes
        self._layout_mode = _normalize_layout_mode(layout_mode)
        self._compact_mode = bool(compact_mode)
        self._task_style = _normalize_task_style(task_style)
        self._nuke_open_handler = nuke_open_handler
        self._api = api
        self._folders = folders
        self.setObjectName(f"timeline-{timeline.get('id')}")

        self.frame = QFrame()
        self.frame.setFrameShape(QFrame.Shape.StyledPanel)

        frame_layout = QVBoxLayout()
        self.shots_layout = _create_shots_layout(self._layout_mode, card_spacing)
        frame_layout.addLayout(self.shots_layout)
        self.frame.setLayout(frame_layout)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.frame)
        self.setLayout(main_layout)
        
        self.update_from_data(timeline, desired_order=desired_order)
    
    def set_show_hidden(self, flag: bool):
        """Update show_hidden flag. Visibility is handled by main.py _apply_filters."""
        self.show_hidden = bool(flag)

    def set_nuke_open_handler(self, handler):
        self._nuke_open_handler = handler
        for i in range(self.shots_layout.count()):
            item = self.shots_layout.itemAt(i)
            card = item.widget() if item else None
            if isinstance(card, ShotCard):
                card.set_nuke_open_handler(handler)

    def update_from_data(self, timeline: dict, *, desired_order: list[str] | None = None):
        """Update shot cards from timeline data. Does NOT manage visibility - that's handled by _apply_filters."""
        self._last_timeline = timeline or self._last_timeline

        # BUGFIX: Build existing dict and detect/remove duplicates in the same pass
        existing = {}
        duplicates_to_remove = []
        
        for i in range(self.shots_layout.count()):
            item = self.shots_layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is not None and w.objectName():
                name = w.objectName()
                if name in existing:
                    # Duplicate found - mark for removal
                    duplicates_to_remove.append(w)
                else:
                    existing[name] = w
        
        # Remove any duplicates found
        for dup_widget in duplicates_to_remove:
            self.shots_layout.removeWidget(dup_widget)
            dup_widget.deleteLater()
        
        api_order = []

        for shot in timeline.get("shots", []):
            sid = shot.get("id")
            if sid is None:
                continue
            name = f"shot-{sid}"
            api_order.append(name)
            
            if name in existing:
                # Update existing widget data
                existing[name].update_from_data(shot)
                existing[name].set_task_style(self._task_style)
                if self._nuke_open_handler is not None:
                    existing[name].set_nuke_open_handler(self._nuke_open_handler)
            else:
                # Create new widget
                card = _create_shot_card(
                    shot,
                    parent=self,
                    task_style=self._task_style,
                    api=self._api,
                    folders=self._folders,
                )
                card.setObjectName(name)
                if self._nuke_open_handler is not None:
                    card.set_nuke_open_handler(self._nuke_open_handler)
                card.set_compact_mode(self._compact_mode)
                self.shots_layout.addWidget(card)

        # Remove any existing spacers (list mode adds one at the end)
        for i in reversed(range(self.shots_layout.count())):
            item = self.shots_layout.itemAt(i)
            if isinstance(item, QSpacerItem):
                self.shots_layout.removeItem(item)

        # Add vertical spacer at the end for list mode only
        if self._layout_mode == "list":
            spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self.shots_layout.addItem(spacer)

        # Remove stale widgets
        for name, widget in list(existing.items()):
            if name not in api_order:
                self.shots_layout.removeWidget(widget)
                widget.deleteLater()

        # Maintain order
        resolved_order = desired_order or api_order
        _ensure_order(self.shots_layout, resolved_order)
        self.set_compact_mode(self._compact_mode)

    def set_layout_mode(self, layout_mode: str, card_spacing: int | None = None):
        """Switch the shots layout between list and grid, preserving shot cards."""
        new_mode = _normalize_layout_mode(layout_mode)
        if new_mode == self._layout_mode:
            if card_spacing is not None:
                self.shots_layout.setSpacing(max(0, int(card_spacing)))
            return

        existing_widgets = []
        for i in range(self.shots_layout.count()):
            item = self.shots_layout.itemAt(i)
            if item and item.widget():
                existing_widgets.append(item.widget())

        while self.shots_layout.count():
            item = self.shots_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(self)

        frame_layout = self.frame.layout()
        if frame_layout:
            frame_layout.removeItem(self.shots_layout)

        QWidget().setLayout(self.shots_layout)
        self.shots_layout = _create_shots_layout(new_mode, card_spacing)
        if frame_layout:
            frame_layout.addLayout(self.shots_layout)

        for widget in existing_widgets:
            self.shots_layout.addWidget(widget)

        if new_mode == "list":
            spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self.shots_layout.addItem(spacer)

        self._layout_mode = new_mode

    def set_task_style(self, task_style: str) -> None:
        self._task_style = _normalize_task_style(task_style)
        for i in range(self.shots_layout.count()):
            item = self.shots_layout.itemAt(i)
            if not item:
                continue
            card = item.widget()
            if isinstance(card, ShotCard):
                card.set_task_style(self._task_style)

    def set_compact_mode(self, enabled: bool) -> None:
        self._compact_mode = bool(enabled)
        for i in range(self.shots_layout.count()):
            item = self.shots_layout.itemAt(i)
            if not item:
                continue
            card = item.widget()
            if isinstance(card, ShotCard):
                card.set_compact_mode(self._compact_mode)

class JobBtn(QPushButton):
    def __init__(self, title="Title", data=None, frame_layout=None, on_activate=None):
        super().__init__(title)
        self.title = title
        self.data = data or {}
        self.frame_layout = frame_layout
        self.on_activate = on_activate
        self.clicked.connect(self.on_click)

    def on_click(self):
        if self.on_activate:
            self.on_activate(self.data)
