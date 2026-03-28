#!/usr/bin/env python3
from __future__ import annotations

"""
ShotBox Assignment Board - PyQt6
Kanban-style drag-and-drop interface for assigning tasks to artists.
"""

from typing import Optional
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import gc
import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QScrollArea, QPushButton, QComboBox, QProgressBar, QDialog, QCheckBox
)
from PyQt6.QtCore import (
    Qt, QMimeData, QTimer, QSize, QEvent, pyqtSignal
)
from PyQt6.QtGui import QDrag, QPixmap, QPainter, QColor

import http_help
import requests
import widgets as widgets_module
from task_create_dialog import TaskCreateDialog

_THUMB_CACHE = OrderedDict()
_THUMB_CACHE_LOCK = threading.Lock()
_THUMB_CACHE_MAX = 100
_THUMB_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# ============================================================================
# TASK CARD WIDGET
# ============================================================================

class DraggableTaskWidget(widgets_module.TaskWidget):
    """Task widget with drag support for reassignment."""

    def __init__(self, task_data: dict, parent=None, column=None):
        self._column = column
        self._task_ref = task_data if isinstance(task_data, dict) else None
        super().__init__(task_data)
        if parent is not None:
            self.setParent(parent)
        self._drag_start_pos = None
        self._drag_allowed = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        if self.layout():
            self.layout().setContentsMargins(0, 0, 0, 0)

        frame_layout = getattr(self, "task_frame", None)
        if frame_layout and frame_layout.layout():
            frame_layout.layout().setContentsMargins(4, 4, 4, 4)
            frame_layout.layout().setSpacing(3)

        if hasattr(self, "drag_handle"):
            self.drag_handle.setVisible(True)

    def update_from_data(self, task_data: dict):
        if self._column and isinstance(task_data, dict):
            new_artist = task_data.get("artist")
            target_id = new_artist if new_artist is not None else "unassigned"
            board = getattr(self._column, "board", None)
            if board and hasattr(board, "columns") and target_id not in board.columns:
                target_id = self._column.artist_data.get("id")
            if self._column.artist_data.get("id") != target_id:
                if self._column.board and hasattr(self._column.board, "relocate_task_ui"):
                    self._column.board.relocate_task_ui(task_data, self._column, target_id)
                return

        super().update_from_data(task_data)
        if isinstance(self._task_ref, dict) and isinstance(task_data, dict):
            self._task_ref.update(task_data)
        if self._column:
            if self._column.board and hasattr(self._column.board, "_should_show_task"):
                task_ref = self._task_ref if isinstance(self._task_ref, dict) else task_data
                if not self._column.board._should_show_task(task_ref):
                    task_id = getattr(self, "_task_id", None)
                    if task_id is not None:
                        self._column.remove_task(task_id)
                    return
            self._column._update_stats()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_drag_handle_hit(event.position().toPoint()):
                self._drag_allowed = True
                self._drag_start_pos = event.pos()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                self._drag_allowed = False
                self._drag_start_pos = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_allowed:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_allowed = False
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if not self._drag_allowed:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < 6:
            return
        task_id = getattr(self, "_task_id", None)
        if task_id is None:
            return

        if self._column and self._column.board:
            self._column.board.notify_drag_state(True)

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(str(task_id))
        drag.setMimeData(mime_data)

        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(0.8)
        self.render(painter)
        painter.end()

        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        drag.exec(Qt.DropAction.MoveAction)

        if self._column and self._column.board:
            self._column.board.notify_drag_state(False)

    def _is_drag_handle_hit(self, pos) -> bool:
        handle = getattr(self, "drag_handle", None)
        if not handle or not handle.isVisible():
            return True
        return handle.geometry().contains(pos)


# ============================================================================
# SHOT GROUP WIDGET
# ============================================================================

class ShotGroupCard(QFrame):
    """A small shot card that contains its tasks."""

    def __init__(self, shot_data: dict, parent=None, column=None):
        super().__init__(parent)
        self.shot_data = shot_data
        self.column = column
        self.setObjectName("shotGroupCard")
        self._task_cards = []
        self._thumb_orig = None
        self._thumb_sig = None
        self._api = None
        if self.column and getattr(self.column, "board", None):
            self._api = self.column.board._api
        else:
            self._api = http_help.DjangoAPI()

        self._destroyed = False
        self.destroyed.connect(self._mark_destroyed)

        self._setup_ui()
        self._apply_thumbnail()

    def _mark_destroyed(self, *_args):
        self._destroyed = True

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header = QFrame(self)
        header.setObjectName("shotHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.thumbnail_label = QLabel("No Thumbnail", header)
        self.thumbnail_label.setObjectName("shotThumbnail")
        self.thumbnail_label.setFixedSize(QSize(120, 68))
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.thumbnail_label)

        title = self.shot_data.get("shot", "") or "Shot"
        self.title_label = QLabel(title, header)
        self.title_label.setObjectName("shotTitle")
        self.title_label.setWordWrap(True)
        header_layout.addWidget(self.title_label, 1)

        self.add_task_btn = QPushButton("Add Task", header)
        self.add_task_btn.setObjectName("shotAddTaskButton")
        self.add_task_btn.clicked.connect(self._on_add_task_clicked)
        header_layout.addWidget(self.add_task_btn)

        layout.addWidget(header)

        self.tasks_container = QFrame(self)
        self.tasks_container.setObjectName("shotTasks")
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setContentsMargins(12, 0, 0, 0)
        self.tasks_layout.setSpacing(4)
        layout.addWidget(self.tasks_container)

    def _apply_thumbnail(self):
        url = self.shot_data.get("thumbnail_url")
        if not url:
            return
        if url == self._thumb_sig and self._thumb_orig is not None:
            return
        with _THUMB_CACHE_LOCK:
            cached = _THUMB_CACHE.get(url)
            if cached is not None:
                _THUMB_CACHE.move_to_end(url)
        if cached:
            self._set_thumbnail_from_bytes(url, cached)
            return

        def worker():
            try:
                response = requests.get(url, timeout=5)
            except Exception:
                return
            if not response.ok:
                return
            data = response.content
            if not data:
                return
            with _THUMB_CACHE_LOCK:
                _THUMB_CACHE[url] = data
                _THUMB_CACHE.move_to_end(url)
                while len(_THUMB_CACHE) > _THUMB_CACHE_MAX:
                    _THUMB_CACHE.popitem(last=False)
            QTimer.singleShot(0, lambda: self._set_thumbnail_from_bytes(url, data))

        _THUMB_EXECUTOR.submit(worker)

    def _set_thumbnail_from_bytes(self, url: str, data: bytes) -> None:
        if self._destroyed or not data:
            return
        if url != self.shot_data.get("thumbnail_url"):
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._thumb_orig = pixmap
        self._thumb_sig = url
        scaled = pixmap.scaled(
            self.thumbnail_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.thumbnail_label.setPixmap(scaled)
        self.thumbnail_label.setText("")

    def add_task(self, task_data: dict, column=None) -> DraggableTaskWidget:
        card = DraggableTaskWidget(task_data, parent=self.tasks_container, column=column)
        card.setAcceptDrops(True)
        if column:
            card.installEventFilter(column)
        self.tasks_layout.addWidget(card)
        self._task_cards.append(card)
        return card

    def remove_task_card(self, card: DraggableTaskWidget) -> None:
        if card in self._task_cards:
            self._task_cards.remove(card)
        self.tasks_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    def task_count(self) -> int:
        return len(self._task_cards)

    def _on_add_task_clicked(self) -> None:
        shot_id = self.shot_data.get("shot_id")
        if not shot_id:
            return
        default_artist_id = None
        if self.column and not self.column.is_unassigned:
            default_artist_id = self.column.artist_data.get("id")
        dlg = TaskCreateDialog(self, api=self._api, default_artist_id=default_artist_id)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        values = dlg.get_values()
        try:
            new_task = self._api.create_task(shot_id=shot_id, title=values.get("title", "New Task"))
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
        except Exception as exc:
            print(f"[Assignment Board] Add task failed: {exc}")
            return

        task_data = {
            "id": new_task.get("id"),
            "shot": self.shot_data.get("shot", ""),
            "shot_id": shot_id,
            "thumbnail_url": self.shot_data.get("thumbnail_url"),
            "title": new_task.get("title", values.get("title", "Task")),
            "notes": new_task.get("notes") or values.get("notes") or "",
            "status": new_task.get("status") or values.get("status"),
            "artist": new_task.get("artist"),
            "priority": new_task.get("priority", values.get("priority", 5)),
            "budget_hours": new_task.get("budget_hours", values.get("budget_hours", 0)),
            "hidden": new_task.get("hidden", False),
            "hours": float(new_task.get("budget_hours") or 0),
        }

        target_artist_id = task_data.get("artist") or "unassigned"
        target_column = None
        if self.column and getattr(self.column, "board", None):
            target_column = self.column.board.columns.get(target_artist_id)
            if not target_column:
                target_column = self.column.board.columns.get("unassigned")
        if target_column:
            target_column.add_task(task_data)


# ============================================================================
# ARTIST COLUMN WIDGET
# ============================================================================

class ArtistColumn(QFrame):
    """A column representing an artist's task queue."""

    def __init__(self, artist_data: dict, board, parent=None):
        super().__init__(parent)
        self.artist_data = artist_data
        self.board = board
        self.is_unassigned = artist_data["id"] == "unassigned"
        self.tasks = []
        self.task_cards = {}
        self.shot_groups = {}

        if self.is_unassigned:
            self.setObjectName("unassignedColumn")
        else:
            self.setObjectName("column")

        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._create_header(layout)

        if not self.is_unassigned:
            self._create_workload_bar(layout)

        self._create_card_list(layout)
        self._create_footer(layout)

    def _create_header(self, parent_layout):
        header = QFrame()
        header.setObjectName("columnHeader")
        header.setFixedHeight(60)
        header.setAcceptDrops(True)
        header.installEventFilter(self)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 12, 14, 12)

        left = QHBoxLayout()
        left.setSpacing(10)

        avatar_container = QWidget()
        avatar_container.setFixedSize(36, 36)
        avatar_layout = QVBoxLayout(avatar_container)
        avatar_layout.setContentsMargins(0, 0, 0, 0)

        avatar = QLabel(self.artist_data["initials"])
        avatar.setObjectName("avatar")
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                stop:0 {self.artist_data['color']}, 
                stop:1 {self._darken_color(self.artist_data['color'])});
            border-radius: 16px;
            font-weight: bold;
            font-size: 12px;
            color: white;
        """)
        avatar_layout.addWidget(avatar)

        if self.artist_data.get("status"):
            status_colors = {"online": "#678542", "away": "#B28659", "offline": "#555"}
            status_dot = QLabel()
            status_dot.setObjectName("statusDot")
            status_dot.setFixedSize(10, 10)
            status_dot.setStyleSheet(f"""
                background: {status_colors.get(self.artist_data['status'], '#555')};
                border-radius: 5px;
                border: 2px solid #252525;
            """)
            status_dot.setParent(avatar_container)
            status_dot.move(24, 24)

        left.addWidget(avatar_container)

        name_container = QVBoxLayout()
        name_container.setSpacing(2)

        name = QLabel(self.artist_data["name"])
        name.setObjectName("columnTitle")
        name_container.addWidget(name)

        role = QLabel(self.artist_data["role"])
        role.setObjectName("columnSubtitle")
        name_container.addWidget(role)

        left.addLayout(name_container)
        left.addStretch()

        layout.addLayout(left)

        self.task_count_label = QLabel("0")
        self.task_count_label.setObjectName("taskCountUrgent" if self.is_unassigned else "taskCount")
        layout.addWidget(self.task_count_label)

        parent_layout.addWidget(header)

    def _create_workload_bar(self, parent_layout):
        container = QWidget()
        container.setFixedHeight(40)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 4, 14, 8)
        layout.setSpacing(4)

        self.progress_in_progress = QProgressBar()
        self.progress_in_progress.setObjectName("progress_in_progress")
        self.progress_in_progress.setTextVisible(True)
        self.progress_in_progress.setFixedHeight(12)
        layout.addWidget(self.progress_in_progress)

        self.progress_done = QProgressBar()
        self.progress_done.setObjectName("progress_done")
        self.progress_done.setTextVisible(True)
        self.progress_done.setFixedHeight(12)
        layout.addWidget(self.progress_done)

        parent_layout.addWidget(container)

        self.capacity_warning = QFrame()
        self.capacity_warning.setObjectName("capacityWarning")
        self.capacity_warning.setFixedHeight(32)
        self.capacity_warning.hide()

        #warning_layout = QHBoxLayout(self.capacity_warning)
        #warning_layout.setContentsMargins(12, 6, 12, 6)

        #warning_text = QLabel("⚠ Over capacity - consider reassigning")
        #warning_text.setObjectName("capacityWarningText")
        #warning_layout.addWidget(warning_text)

        parent_layout.addWidget(self.capacity_warning)

    def _create_card_list(self, parent_layout):
        scroll = QScrollArea()
        scroll.setObjectName("cardList")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAcceptDrops(False)
        scroll.viewport().setAcceptDrops(True)
        scroll.viewport().installEventFilter(self)

        self.card_container = QWidget()
        self.card_container.setObjectName("cardListContainer")
        self.card_container.setAcceptDrops(True)
        self.card_container.installEventFilter(self)

        self.card_layout = QVBoxLayout(self.card_container)
        self.card_layout.setContentsMargins(10, 8, 10, 8)
        self.card_layout.setSpacing(8)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.drop_indicator = QFrame()
        self.drop_indicator.setObjectName("dropIndicator")
        self.drop_indicator.setFixedHeight(60)
        self.drop_indicator.hide()
        self.drop_indicator.setAcceptDrops(False)

        drop_layout = QVBoxLayout(self.drop_indicator)
        drop_label = QLabel(f"Drop here to assign to {self.artist_data['name']}")
        drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_label.setStyleSheet("color: #4a7ba7; font-size: 12px;")
        drop_layout.addWidget(drop_label)

        self.card_layout.addWidget(self.drop_indicator)
        self.card_layout.addStretch()

        scroll.setWidget(self.card_container)
        parent_layout.addWidget(scroll, 1)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.DragEnter:
            self.dragEnterEvent(event)
            return True
        if event.type() == QEvent.Type.DragMove:
            self.dragMoveEvent(event)
            return True
        if event.type() == QEvent.Type.DragLeave:
            self.dragLeaveEvent(event)
            return True
        if event.type() == QEvent.Type.Drop:
            self.dropEvent(event)
            return True
        return super().eventFilter(obj, event)

    def _create_footer(self, parent_layout):
        footer = QFrame()
        footer.setObjectName("columnFooter")
        footer.setFixedHeight(20)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(14, 4, 14, 4)

        self.footer_left = QLabel("0 tasks")
        self.footer_left.setObjectName("footerText")
        layout.addWidget(self.footer_left)

        layout.addStretch()

        parent_layout.addWidget(footer)

    def _darken_color(self, hex_color: str) -> str:
        c = QColor(hex_color)
        return QColor.fromHslF(c.hueF(), c.saturationF(), max(0, c.lightnessF() - 0.2)).name()

    def add_task(self, task_data: dict):
        if self.board and hasattr(self.board, "_should_show_task"):
            if not self.board._should_show_task(task_data):
                return
        shot_id = task_data.get("shot_id")
        if shot_id is None:
            shot_id = task_data.get("shot")
        task_data["shot_id"] = shot_id

        group = self.shot_groups.get(shot_id)
        if not group:
            group = ShotGroupCard({
                "shot": task_data.get("shot", ""),
                "thumbnail_url": task_data.get("thumbnail_url"),
                "shot_id": shot_id,
            }, parent=self.card_container, column=self)
            group.setAcceptDrops(True)
            group.installEventFilter(self)
            self.shot_groups[shot_id] = group
            insert_index = max(0, self.card_layout.count() - 1)
            self.card_layout.insertWidget(insert_index, group)

        card = group.add_task(task_data, column=self)
        self.task_cards[task_data.get("id")] = (task_data, card, group)
        self.tasks.append(task_data)

        self._update_stats()

    def remove_task(self, task_id: int) -> Optional[dict]:
        entry = self.task_cards.pop(task_id, None)
        if entry:
            task, card, group = entry
            for idx, existing in enumerate(self.tasks):
                if existing.get("id") == task_id:
                    self.tasks.pop(idx)
                    break
            group.remove_task_card(card)
            if group.task_count() == 0:
                shot_id = group.shot_data.get("shot_id")
                if shot_id in self.shot_groups:
                    self.shot_groups.pop(shot_id, None)
                self.card_layout.removeWidget(group)
                group.setParent(None)
                group.deleteLater()
            self._update_stats()
            return task
        return None

    def _update_stats(self):
        count = len(self.tasks)
        self.task_count_label.setText(str(count))

        in_progress_count = sum(
            1 for t in self.tasks if t.get("status") == "in_progress"
        )
        done_waiting_count = sum(
            1 for t in self.tasks if t.get("status") in ("done", "waiting_for_approval")
        )

        capacity = float(self.artist_data.get("capacity", 0))
        total_count = count
        max_count = max(1, total_count)

        if hasattr(self, "progress_in_progress") and hasattr(self, "progress_done"):
            self.progress_in_progress.setRange(0, max_count)
            self.progress_in_progress.setValue(min(in_progress_count, max_count))
            self.progress_in_progress.setFormat(
                f"{in_progress_count}/{total_count} tasks in progress"
            )

            self.progress_done.setRange(0, max_count)
            self.progress_done.setValue(min(done_waiting_count, max_count))
            self.progress_done.setFormat(
                f"{done_waiting_count}/{total_count} tasks done or waiting"
            )

        if not self.is_unassigned and capacity > 0 and hasattr(self, "capacity_warning"):
            self.capacity_warning.setVisible(count > capacity)
        elif hasattr(self, "capacity_warning"):
            self.capacity_warning.setVisible(False)

        if hasattr(self, "footer_left"):
            self.footer_left.setText(f"{count} tasks")

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self.drop_indicator.show()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_indicator.hide()

    def dropEvent(self, event):
        self.drop_indicator.hide()

        if not event.mimeData().hasText():
            event.ignore()
            return

        task_id = int(event.mimeData().text())
        if self.board:
            self.board.move_task(task_id, self.artist_data["id"])

        event.acceptProposedAction()


# ============================================================================
# TOAST NOTIFICATION
# ============================================================================

class ToastNotification(QFrame):
    """An animated toast notification."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setFixedSize(280, 50)
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        check = QLabel("✓")
        check.setStyleSheet("color: #678542; font-size: 18px; font-weight: bold;")
        layout.addWidget(check)

        self.message = QLabel("Task assigned")
        self.message.setObjectName("toastText")
        layout.addWidget(self.message)

        layout.addStretch()

    def show_message(self, text: str, duration: int = 2500):
        self.message.setText(text)

        if self.parent():
            parent_rect = self.parent().rect()
            x = parent_rect.width() - self.width() - 24
            y = parent_rect.height() - self.height() - 24
            self.move(x, y)

        self.show()
        self.raise_()
        QTimer.singleShot(duration, self.hide)


# ============================================================================
# MAIN ASSIGNMENT BOARD PAGE
# ============================================================================

class AssignmentBoardPage(QWidget):
    """Kanban-style assignment board embedded as a tab."""

    drag_state_changed = pyqtSignal(bool)
    auto_refresh_pause_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._api = http_help.DjangoAPI()
        self._jobs_by_id = {}
        self._active_job_id = None
        self._active_timeline_id = None
        self._users = []
        self.columns = {}
        self._drag_active = False
        self._pending_jobs_data = None
        self._deferred_jobs_data = None
        self._hide_done_approved = True
        self._show_hidden_tasks = False
        self._pause_auto_refresh = False
        self._build_token = 0
        self._pending_tasks = []
        self._pending_task_index = 0
        self._build_chunk_size = 25
        self._build_chunk_delay_ms = 10

        self._setup_ui()
        self._load_users()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(60)

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        title = QLabel("🎬 ShotBox — Assignment Board")
        title.setObjectName("headerTitle")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self.job_combo = QComboBox()
        self.job_combo.currentIndexChanged.connect(self._on_job_selected)
        header_layout.addWidget(self.job_combo)

        self.timeline_combo = QComboBox()
        self.timeline_combo.currentIndexChanged.connect(self._on_timeline_selected)
        header_layout.addWidget(self.timeline_combo)

        self.hide_done_checkbox = QCheckBox("Hide done/approved")
        self.hide_done_checkbox.setChecked(self._hide_done_approved)
        self.hide_done_checkbox.toggled.connect(self._on_hide_done_toggled)
        header_layout.addWidget(self.hide_done_checkbox)

        self.show_hidden_checkbox = QCheckBox("Show hidden")
        self.show_hidden_checkbox.setChecked(self._show_hidden_tasks)
        self.show_hidden_checkbox.toggled.connect(self._on_show_hidden_toggled)
        header_layout.addWidget(self.show_hidden_checkbox)

        self.pause_auto_refresh_checkbox = QCheckBox("Stop auto refresh")
        self.pause_auto_refresh_checkbox.setChecked(self._pause_auto_refresh)
        self.pause_auto_refresh_checkbox.toggled.connect(
            self._on_pause_auto_refresh_toggled
        )
        header_layout.addWidget(self.pause_auto_refresh_checkbox)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_from_api)
        header_layout.addWidget(self.refresh_btn)

        main_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setObjectName("boardScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.board_container = QWidget()
        self.board_layout = QHBoxLayout(self.board_container)
        self.board_layout.setContentsMargins(20, 20, 20, 20)
        self.board_layout.setSpacing(16)
        self.board_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        scroll.setWidget(self.board_container)
        main_layout.addWidget(scroll, 1)

        self.toast = ToastNotification(self)

    def _load_users(self):
        try:
            self._users = self._api.get_users()
        except Exception:
            self._users = []

    def _refresh_from_api(self):
        try:
            jobs = self._api.getAPI()
        except Exception:
            jobs = []
        self.set_jobs_data(jobs, force=True)

    def set_jobs_data(self, jobs: list, force: bool = False):
        if not self.isVisible():
            self._deferred_jobs_data = jobs
            return
        if self._drag_active:
            self._pending_jobs_data = jobs
            return
        self._jobs_by_id = {job.get("id"): job for job in jobs if job.get("id") is not None}
        self._populate_job_combo()
        if self.job_combo.count() == 0:
            self._clear_board()
            return
        if self._active_job_id is not None:
            self.set_active_job_id(self._active_job_id)
            return
        if self.job_combo.currentIndex() < 0:
            self.job_combo.blockSignals(True)
            self.job_combo.setCurrentIndex(0)
            self.job_combo.blockSignals(False)
        if self.job_combo.currentIndex() >= 0:
            self._on_job_selected(self.job_combo.currentIndex())

    def set_active_job_id(self, job_id: int | None):
        if job_id is None:
            return
        self._active_job_id = job_id
        for i in range(self.job_combo.count()):
            if self.job_combo.itemData(i) == job_id:
                if self.job_combo.currentIndex() == i:
                    if not self.columns:
                        self._populate_timeline_combo()
                        self._rebuild_board()
                    return
                self.job_combo.setCurrentIndex(i)
                return

    def notify_drag_state(self, active: bool) -> None:
        if self._drag_active == active:
            return
        self._drag_active = active
        self.drag_state_changed.emit(active)
        if not active and self._pending_jobs_data is not None:
            pending = self._pending_jobs_data
            self._pending_jobs_data = None
            self.set_jobs_data(pending, force=True)

    def set_active_timeline_id(self, timeline_id: int | None):
        if self.timeline_combo.count() == 0:
            return
        if timeline_id is None:
            if self.timeline_combo.currentIndex() == 0:
                self._active_timeline_id = None
                self._rebuild_board()
                return
            self.timeline_combo.setCurrentIndex(0)
            return
        for i in range(self.timeline_combo.count()):
            if self.timeline_combo.itemData(i) == timeline_id:
                if self.timeline_combo.currentIndex() == i:
                    self._active_timeline_id = timeline_id
                    if not self.columns:
                        self._rebuild_board()
                    return
                self.timeline_combo.setCurrentIndex(i)
                return

    def showEvent(self, event):
        super().showEvent(event)
        if self._deferred_jobs_data is not None:
            jobs = self._deferred_jobs_data
            self._deferred_jobs_data = None
            self.set_jobs_data(jobs, force=True)
            return
        if self._jobs_by_id and not self.columns:
            if self._active_job_id is not None:
                self._populate_timeline_combo()
                self._rebuild_board()
            elif self.job_combo.count() > 0:
                self._on_job_selected(max(0, self.job_combo.currentIndex()))

    def hideEvent(self, event):
        super().hideEvent(event)
        self._build_token += 1
        if self.columns:
            self._clear_board()
        QTimer.singleShot(0, self._finalize_cleanup)

    def _populate_job_combo(self):
        self.job_combo.blockSignals(True)
        self.job_combo.clear()

        for job_id, job in self._jobs_by_id.items():
            title = job.get("title", f"Job {job_id}")
            self.job_combo.addItem(title, job_id)

        self.job_combo.blockSignals(False)

        if self._active_job_id is None and self.job_combo.count() > 0:
            self.job_combo.setCurrentIndex(0)
        elif self._active_job_id is not None:
            self.set_active_job_id(self._active_job_id)

    def _on_job_selected(self, index):
        if index < 0:
            return
        job_id = self.job_combo.itemData(index)
        self._active_job_id = job_id
        self._populate_timeline_combo()
        self._rebuild_board()

    def _populate_timeline_combo(self):
        self.timeline_combo.blockSignals(True)
        self.timeline_combo.clear()
        self.timeline_combo.addItem("All Timelines", None)

        job = self._jobs_by_id.get(self._active_job_id)
        if job:
            for timeline in job.get("timelines", []):
                timeline_id = timeline.get("id")
                title = timeline.get("title", f"Timeline {timeline_id}")
                self.timeline_combo.addItem(title, timeline_id)

        self.timeline_combo.blockSignals(False)
        self.timeline_combo.setCurrentIndex(0)
        self._active_timeline_id = None

    def _on_timeline_selected(self, index):
        if index < 0:
            return
        self._active_timeline_id = self.timeline_combo.itemData(index)
        self._rebuild_board()

    def _on_hide_done_toggled(self, checked: bool) -> None:
        self._hide_done_approved = checked
        self._rebuild_board()

    def _on_show_hidden_toggled(self, checked: bool) -> None:
        self._show_hidden_tasks = checked
        self._rebuild_board()

    def _on_pause_auto_refresh_toggled(self, checked: bool) -> None:
        self._pause_auto_refresh = checked
        self.auto_refresh_pause_changed.emit(checked)

    def _clear_board(self):
        while self.board_layout.count():
            item = self.board_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.columns = {}
        self._pending_tasks = []
        self._pending_task_index = 0

    def _rebuild_board(self):
        self._build_token += 1
        build_token = self._build_token

        self._clear_board()

        artists = self._build_artist_list()
        for artist in artists:
            column = ArtistColumn(artist, board=self)
            self.columns[artist["id"]] = column
            self.board_layout.addWidget(column)

        self._pending_tasks = self._collect_tasks()
        self._pending_task_index = 0

        QTimer.singleShot(0, lambda: self._continue_build_tasks(build_token))

    def _continue_build_tasks(self, build_token: int) -> None:
        if build_token != self._build_token:
            return
        if not self.isVisible():
            return
        if not self._pending_tasks:
            return

        start = self._pending_task_index
        end = min(start + self._build_chunk_size, len(self._pending_tasks))
        for i in range(start, end):
            task = self._pending_tasks[i]
            artist_id = task.get("artist", "unassigned")
            if artist_id not in self.columns:
                artist_id = "unassigned"
            column = self.columns.get(artist_id)
            if column:
                column.add_task(task)
        self._pending_task_index = end

        if self._pending_task_index < len(self._pending_tasks):
            QTimer.singleShot(self._build_chunk_delay_ms, lambda: self._continue_build_tasks(build_token))
        else:
            self._pending_tasks = []
            self._pending_task_index = 0

    def _finalize_cleanup(self) -> None:
        with _THUMB_CACHE_LOCK:
            _THUMB_CACHE.clear()
        gc.collect()

    def _build_artist_list(self) -> list[dict]:
        colors = ["#4a7ba7", "#8B6FA3", "#678542", "#B28659", "#99604C", "#6FA8DC"]
        artists = [{
            "id": "unassigned",
            "name": "Unassigned",
            "initials": "+",
            "role": "Drag to assign →",
            "color": "#444444",
            "status": None,
            "capacity": 0,
        }]

        for idx, user in enumerate(self._users):
            user_id = user.get("id")
            username = user.get("username") or f"User {user_id}"
            first_name = user.get("first_name") or ""
            last_name = user.get("last_name") or ""
            display_name = first_name or username
            initials = "".join([part[0] for part in display_name.split() if part][:2]).upper()
            color = colors[idx % len(colors)]
            artists.append({
                "id": user_id,
                "name": display_name,
                "initials": initials or "U",
                "role": "Artist",
                "color": color,
                "status": None,
                "capacity": 8,
            })

        return artists

    def _collect_tasks(self) -> list[dict]:
        job = self._jobs_by_id.get(self._active_job_id)
        if not job:
            return []

        tasks = []
        for timeline in job.get("timelines", []):
            if self._active_timeline_id is not None and timeline.get("id") != self._active_timeline_id:
                continue
            for shot in timeline.get("shots", []):
                shot_title = shot.get("title", "")
                shot_id = shot.get("id")
                thumbnail_url = self._build_thumbnail_url(shot.get("thumbnail"))
                for task in shot.get("tasks", []):
                    if not self._should_show_task(task):
                        continue
                    status = task.get("status", "unassigned")
                    tasks.append({
                        "id": task.get("id"),
                        "shot": shot_title,
                        "shot_id": shot_id,
                        "thumbnail_url": thumbnail_url,
                        "title": task.get("title", "Task"),
                        "notes": task.get("notes") or "",
                        "status": status,
                        "artist": task.get("artist"),
                        "priority": task.get("priority", 5),
                        "budget_hours": task.get("budget_hours", 0),
                        "hidden": task.get("hidden", False),
                        "hours": float(task.get("budget_hours") or 0),
                    })
        return tasks

    def _should_show_task(self, task_data: dict) -> bool:
        if task_data.get("hidden") and not self._show_hidden_tasks:
            return False
        if not self._hide_done_approved:
            return True
        status = task_data.get("status")
        if isinstance(status, str) and status.lower() in ("done", "approved"):
            return False
        return True

    def _build_thumbnail_url(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base_url = getattr(widgets_module, "BASE_URL", "")
        if not base_url:
            return path
        return f"{base_url.rstrip('/')}{path}"

    def move_task(self, task_id: int, target_artist_id):
        source_column = None
        task_data = None

        for column in self.columns.values():
            for task in column.tasks:
                if task.get("id") == task_id:
                    source_column = column
                    break
            if source_column:
                break

        if not source_column:
            return

        if source_column.artist_data["id"] == target_artist_id:
            return

        target_artist = None if target_artist_id == "unassigned" else target_artist_id

        task_data = source_column.remove_task(task_id)
        if not task_data:
            return

        task_data["artist"] = target_artist

        target_column = self.columns.get(target_artist_id)
        if not target_column:
            target_column = self.columns.get("unassigned")
        if not target_column:
            self.toast.show_message("Assignment failed")
            return

        target_column.add_task(task_data)

        def on_success():
            if target_artist_id == "unassigned":
                self.toast.show_message("Task moved to Unassigned")
            else:
                artist_name = target_column.artist_data["name"]
                self.toast.show_message(f"Task assigned to {artist_name}")

        def on_error(_exc: Exception):
            target_column.remove_task(task_id)
            task_data["artist"] = source_column.artist_data.get("id")
            source_column.add_task(task_data)
            self.toast.show_message("Assignment failed")

        self._update_task_artist_async(task_id, target_artist, on_success, on_error)

    def relocate_task_ui(self, task_data: dict, source_column: ArtistColumn, target_artist_id):
        if not source_column:
            return
        if target_artist_id is None:
            target_artist_id = "unassigned"
        if source_column.artist_data.get("id") == target_artist_id:
            return
        task_id = task_data.get("id")
        if task_id is None:
            return

        source_column.remove_task(task_id)

        task_data["artist"] = None if target_artist_id == "unassigned" else target_artist_id
        target_column = self.columns.get(target_artist_id) or self.columns.get("unassigned")
        if target_column:
            target_column.add_task(task_data)

    def _update_task_artist_async(
        self,
        task_id: int,
        target_artist,
        on_success,
        on_error,
    ) -> None:
        def worker():
            try:
                self._api.update_task(task_id, artist=target_artist)
            except Exception as exc:
                QTimer.singleShot(0, lambda: on_error(exc))
                return
            QTimer.singleShot(0, on_success)

        threading.Thread(target=worker, daemon=True).start()
