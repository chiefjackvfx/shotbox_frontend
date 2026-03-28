#!/usr/bin/env python3
"""
ShotBox Assignment Board - PyQt6 Prototype
A Kanban-style drag-and-drop interface for assigning tasks to artists.
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QScrollArea, QPushButton, QGraphicsDropShadowEffect,
    QSizePolicy, QComboBox
)
from PyQt6.QtCore import (
    Qt, QMimeData, QPoint, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QSize
)
from PyQt6.QtGui import QDrag, QPixmap, QPainter, QColor, QFont, QFontDatabase

# ============================================================================
# DEMO DATA
# ============================================================================

DEMO_ARTISTS = [
    {"id": "unassigned", "name": "Unassigned", "initials": "+", "role": "Drag to assign →", "color": "#444444",
     "status": None, "capacity": 0},
    {"id": "jack", "name": "Jack H.", "initials": "JH", "role": "Comp Lead", "color": "#4a7ba7", "status": "online",
     "capacity": 6},
    {"id": "emma", "name": "Emma M.", "initials": "EM", "role": "Roto / Paint", "color": "#8B6FA3", "status": "online",
     "capacity": 8},
    {"id": "tom", "name": "Tom K.", "initials": "TK", "role": "Junior Comp", "color": "#678542", "status": "away",
     "capacity": 6},
    {"id": "sarah", "name": "Sarah L.", "initials": "SL", "role": "Comp Artist", "color": "#B28659",
     "status": "offline", "capacity": 8},
]

DEMO_TASKS = [
    # Unassigned
    {"id": 1, "shot": "SHO_052", "type": "Comp", "desc": "Hero shot - full CG replacement with complex edge work",
     "due": "Overdue", "due_status": "overdue", "hours": 6, "priority": "high", "status": "pending",
     "artist": "unassigned"},
    {"id": 2, "shot": "SHO_067", "type": "Roto", "desc": "Actor extraction - hair detail required", "due": "Today",
     "due_status": "soon", "hours": 3, "priority": "high", "status": "pending", "artist": "unassigned"},
    {"id": 3, "shot": "SHO_089", "type": "Comp", "desc": "Set extension - add background buildings", "due": "Tomorrow",
     "due_status": "normal", "hours": 4, "priority": "medium", "status": "pending", "artist": "unassigned"},
    {"id": 4, "shot": "SHO_103", "type": "Paint", "desc": "Remove tracking markers from wall", "due": "Fri",
     "due_status": "normal", "hours": 1, "priority": "low", "status": "pending", "artist": "unassigned"},
    {"id": 5, "shot": "SHO_118", "type": "Roto", "desc": "Vehicle extraction for BG replacement", "due": "Fri",
     "due_status": "normal", "hours": 2, "priority": "medium", "status": "pending", "artist": "unassigned"},
    {"id": 6, "shot": "SHO_125", "type": "Cleanup", "desc": "Despill green edges on talent", "due": "Mon",
     "due_status": "normal", "hours": 0.75, "priority": "low", "status": "pending", "artist": "unassigned"},

    # Jack's tasks
    {"id": 7, "shot": "SHO_020", "type": "Comp", "desc": "Fix edge bleed on left side, adjust grade", "due": "Today",
     "due_status": "soon", "hours": 2, "priority": "high", "status": "in-progress", "artist": "jack"},
    {"id": 8, "shot": "SHO_035", "type": "Comp", "desc": "Integrate CG element, match lighting", "due": "Tomorrow",
     "due_status": "normal", "hours": 4, "priority": "medium", "status": "pending", "artist": "jack"},
    {"id": 9, "shot": "SHO_041", "type": "Comp", "desc": "Sky replacement, colour match", "due": "Fri",
     "due_status": "normal", "hours": 3, "priority": "medium", "status": "pending", "artist": "jack"},
    {"id": 10, "shot": "SHO_055", "type": "Comp", "desc": "Simple cleanup and grade pass", "due": "Mon",
     "due_status": "normal", "hours": 1.5, "priority": "low", "status": "pending", "artist": "jack"},

    # Emma's tasks
    {"id": 11, "shot": "SHO_015", "type": "Roto", "desc": "Hero talent extraction with hair detail", "due": "Today",
     "due_status": "soon", "hours": 2, "priority": "high", "status": "review", "artist": "emma"},
    {"id": 12, "shot": "SHO_033", "type": "Paint", "desc": "Remove rig and tracking markers", "due": "Tomorrow",
     "due_status": "normal", "hours": 1.5, "priority": "medium", "status": "pending", "artist": "emma"},
    {"id": 13, "shot": "SHO_048", "type": "Roto", "desc": "Simple garbage matte for BG", "due": "Fri",
     "due_status": "normal", "hours": 1, "priority": "low", "status": "pending", "artist": "emma"},

    # Tom's tasks (overloaded)
    {"id": 14, "shot": "SHO_008", "type": "Comp", "desc": "Revision - adjust colour per client notes", "due": "Overdue",
     "due_status": "overdue", "hours": 1, "priority": "high", "status": "revision", "artist": "tom"},
    {"id": 15, "shot": "SHO_022", "type": "Cleanup", "desc": "Degrain and denoise pass", "due": "Today",
     "due_status": "soon", "hours": 2, "priority": "medium", "status": "in-progress", "artist": "tom"},
    {"id": 16, "shot": "SHO_029", "type": "Comp", "desc": "Simple screen insert", "due": "Tomorrow",
     "due_status": "normal", "hours": 3, "priority": "medium", "status": "pending", "artist": "tom"},
    {"id": 17, "shot": "SHO_044", "type": "Comp", "desc": "Dust and scratch cleanup", "due": "Fri",
     "due_status": "normal", "hours": 2, "priority": "low", "status": "pending", "artist": "tom"},
    {"id": 18, "shot": "SHO_061", "type": "Cleanup", "desc": "Wire removal from stunt rig", "due": "Mon",
     "due_status": "normal", "hours": 4, "priority": "low", "status": "pending", "artist": "tom"},

    # Sarah's tasks (light load)
    {"id": 19, "shot": "SHO_078", "type": "Comp", "desc": "Final grade and delivery prep", "due": "Tomorrow",
     "due_status": "normal", "hours": 2, "priority": "medium", "status": "in-progress", "artist": "sarah"},
]

# ============================================================================
# STYLE SHEET
# ============================================================================

STYLESHEET = """
/* Main Window */
QMainWindow {
    background: #1a1a1a;
}

QWidget {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: #e0e0e0;
}

/* Header */
QFrame#header {
    background: #252525;
    border-bottom: 1px solid #333;
}

QLabel#headerTitle {
    font-size: 16px;
    font-weight: bold;
    color: #ffffff;
}

QComboBox {
    background: #333;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 6px 12px;
    color: #e0e0e0;
    min-width: 150px;
}

QComboBox:hover {
    border-color: #555;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid #888;
    margin-right: 8px;
}

/* Board Container */
QScrollArea#boardScroll {
    background: #1a1a1a;
    border: none;
}

QScrollArea#boardScroll > QWidget > QWidget {
    background: #1a1a1a;
}

/* Columns */
QFrame#column {
    background: #252525;
    border-radius: 10px;
    min-width: 280px;
    max-width: 280px;
}

QFrame#unassignedColumn {
    background: #2a2a2a;
    border: 2px dashed #444;
    border-radius: 10px;
    min-width: 300px;
    max-width: 300px;
}

/* Column Header */
QFrame#columnHeader {
    background: transparent;
    border-bottom: 1px solid #333;
}

QLabel#columnTitle {
    font-size: 14px;
    font-weight: bold;
    color: #ffffff;
}

QLabel#columnSubtitle {
    font-size: 11px;
    color: #888;
}

QLabel#taskCount {
    background: #333;
    color: #aaa;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 12px;
    font-weight: bold;
}

QLabel#taskCountUrgent {
    background: #99604C;
    color: #fff;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 12px;
    font-weight: bold;
}

/* Avatar */
QLabel#avatar {
    border-radius: 16px;
    font-weight: bold;
    font-size: 12px;
    color: white;
}

QLabel#statusDot {
    border-radius: 5px;
    border: 2px solid #252525;
}

/* Workload Bar */
QFrame#workloadBar {
    background: #333;
    border-radius: 2px;
    max-height: 4px;
    min-height: 4px;
}

QFrame#workloadFill {
    border-radius: 2px;
    max-height: 4px;
    min-height: 4px;
}

/* Capacity Warning */
QFrame#capacityWarning {
    background: rgba(153, 96, 76, 0.2);
    border-radius: 6px;
}

QLabel#capacityWarningText {
    color: #e57373;
    font-size: 11px;
}

/* Card List */
QScrollArea#cardList {
    background: transparent;
    border: none;
}

QWidget#cardListContainer {
    background: transparent;
}

/* Task Card */
QFrame#taskCard {
    background: #1e1e1e;
    border-radius: 8px;
    border-left: 4px solid #444;
}

QFrame#taskCard:hover {
    background: #262626;
}

QFrame#taskCard[priority="high"] {
    border-left-color: #99604C;
}

QFrame#taskCard[priority="medium"] {
    border-left-color: #B28659;
}

QFrame#taskCard[priority="low"] {
    border-left-color: #678542;
}

QLabel#shotName {
    font-size: 14px;
    font-weight: bold;
    color: #ffffff;
}

QLabel#taskType {
    font-size: 10px;
    padding: 3px 8px;
    border-radius: 4px;
    font-weight: bold;
}

QLabel#taskType[tasktype="Comp"] {
    background: #3d5a80;
    color: #a8c5e2;
}

QLabel#taskType[tasktype="Roto"] {
    background: #5c4d7d;
    color: #c4b5e0;
}

QLabel#taskType[tasktype="Paint"] {
    background: #6b5b4f;
    color: #d4c4b5;
}

QLabel#taskType[tasktype="Cleanup"] {
    background: #4a5d4a;
    color: #b5d4b5;
}

QLabel#taskDesc {
    font-size: 11px;
    color: #999;
}

QLabel#dueDate {
    font-size: 11px;
    color: #666;
}

QLabel#dueDate[duestatus="overdue"] {
    color: #e57373;
    font-weight: bold;
}

QLabel#dueDate[duestatus="soon"] {
    color: #ffb74d;
}

QLabel#hoursLabel {
    font-size: 11px;
    color: #666;
}

QLabel#statusBadge {
    font-size: 9px;
    padding: 2px 6px;
    border-radius: 3px;
    font-weight: bold;
}

QLabel#statusBadge[taskstatus="pending"] {
    background: #444;
    color: #aaa;
}

QLabel#statusBadge[taskstatus="in-progress"] {
    background: #3d5a80;
    color: #a8c5e2;
}

QLabel#statusBadge[taskstatus="review"] {
    background: #5c4d7d;
    color: #c4b5e0;
}

QLabel#statusBadge[taskstatus="revision"] {
    background: #99604C;
    color: #ffcdd2;
}

/* Column Footer */
QFrame#columnFooter {
    background: transparent;
    border-top: 1px solid #333;
}

QLabel#footerText {
    font-size: 11px;
    color: #888;
}

QLabel#footerHours {
    font-size: 11px;
    color: #aaa;
    font-weight: bold;
}

/* Empty State */
QLabel#emptyState {
    color: #555;
    font-size: 12px;
}

/* Drop Indicator */
QFrame#dropIndicator {
    background: rgba(74, 123, 167, 0.15);
    border: 2px dashed #4a7ba7;
    border-radius: 8px;
}

/* Toast */
QFrame#toast {
    background: #333;
    border-radius: 8px;
    border-left: 4px solid #678542;
}

QLabel#toastText {
    color: #fff;
    font-size: 13px;
}

/* Quick Filters */
QPushButton#quickFilter {
    background: #333;
    border: none;
    border-radius: 12px;
    padding: 4px 12px;
    color: #888;
    font-size: 11px;
}

QPushButton#quickFilter:hover {
    background: #444;
    color: #fff;
}

QPushButton#quickFilter:checked {
    background: #4a7ba7;
    color: #fff;
}
"""


# ============================================================================
# TASK CARD WIDGET
# ============================================================================

class TaskCard(QFrame):
    """A draggable task card widget."""

    def __init__(self, task_data: dict, parent=None):
        super().__init__(parent)
        self.task_data = task_data
        self.setObjectName("taskCard")
        self.setProperty("priority", task_data.get("priority", "medium"))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedHeight(100)
        self.setAcceptDrops(False)

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Header row: shot name + task type
        header = QHBoxLayout()
        header.setSpacing(8)

        shot_name = QLabel(self.task_data["shot"])
        shot_name.setObjectName("shotName")
        header.addWidget(shot_name)

        header.addStretch()

        task_type = QLabel(self.task_data["type"])
        task_type.setObjectName("taskType")
        task_type.setProperty("tasktype", self.task_data["type"])
        header.addWidget(task_type)

        layout.addLayout(header)

        # Description
        desc = QLabel(self.task_data["desc"])
        desc.setObjectName("taskDesc")
        desc.setWordWrap(True)
        desc.setMaximumHeight(32)
        layout.addWidget(desc)

        # Meta row: due date, hours, status
        meta = QHBoxLayout()
        meta.setSpacing(12)

        due = QLabel(f"⏱ {self.task_data['due']}")
        due.setObjectName("dueDate")
        due.setProperty("duestatus", self.task_data.get("due_status", "normal"))
        meta.addWidget(due)

        hours = self.task_data["hours"]
        hours_text = f"{hours}h" if hours >= 1 else f"{int(hours * 60)}m"
        hours_label = QLabel(f"~{hours_text}")
        hours_label.setObjectName("hoursLabel")
        meta.addWidget(hours_label)

        status = QLabel(self.task_data["status"].replace("-", " ").title())
        status.setObjectName("statusBadge")
        status.setProperty("taskstatus", self.task_data["status"])
        meta.addWidget(status)

        meta.addStretch()
        layout.addLayout(meta)

    def _apply_style(self):
        self.style().unpolish(self)
        self.style().polish(self)

        # Apply to children too
        for child in self.findChildren(QWidget):
            child.style().unpolish(child)
            child.style().polish(child)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(str(self.task_data["id"]))
            drag.setMimeData(mime_data)

            # Create a pixmap of the card for the drag preview
            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setOpacity(0.8)
            self.render(painter)
            painter.end()

            drag.setPixmap(pixmap)
            drag.setHotSpot(event.pos())

            # Execute drag
            self.setStyleSheet("opacity: 0.5;")
            drag.exec(Qt.DropAction.MoveAction)
            self.setStyleSheet("")


# ============================================================================
# ARTIST COLUMN WIDGET
# ============================================================================

class ArtistColumn(QFrame):
    """A column representing an artist's task queue."""

    def __init__(self, artist_data: dict, parent=None):
        super().__init__(parent)
        self.artist_data = artist_data
        self.is_unassigned = artist_data["id"] == "unassigned"
        self.tasks = []
        self.card_widgets = []

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

        # Header
        self._create_header(layout)

        # Workload bar (not for unassigned)
        if not self.is_unassigned:
            self._create_workload_bar(layout)

        # Quick filters (only for unassigned)
        if self.is_unassigned:
            self._create_quick_filters(layout)

        # Card list (scrollable)
        self._create_card_list(layout)

        # Footer
        self._create_footer(layout)

    def _create_header(self, parent_layout):
        header = QFrame()
        header.setObjectName("columnHeader")
        header.setFixedHeight(60)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 12, 14, 12)

        # Left side: avatar + name
        left = QHBoxLayout()
        left.setSpacing(10)

        # Avatar
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

        # Status dot (positioned manually)
        if self.artist_data["status"]:
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

        # Name and role
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

        # Right side: task count
        self.task_count_label = QLabel("0")
        self.task_count_label.setObjectName("taskCountUrgent" if self.is_unassigned else "taskCount")
        layout.addWidget(self.task_count_label)

        parent_layout.addWidget(header)

    def _create_workload_bar(self, parent_layout):
        container = QWidget()
        container.setFixedHeight(16)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(14, 4, 14, 8)

        bar_bg = QFrame()
        bar_bg.setObjectName("workloadBar")
        bar_bg.setFixedHeight(4)

        self.workload_fill = QFrame(bar_bg)
        self.workload_fill.setObjectName("workloadFill")
        self.workload_fill.setFixedHeight(4)
        self.workload_fill.move(0, 0)

        layout.addWidget(bar_bg)
        parent_layout.addWidget(container)

        # Capacity warning (hidden by default)
        self.capacity_warning = QFrame()
        self.capacity_warning.setObjectName("capacityWarning")
        self.capacity_warning.setFixedHeight(32)
        self.capacity_warning.hide()

        warning_layout = QHBoxLayout(self.capacity_warning)
        warning_layout.setContentsMargins(12, 6, 12, 6)

        warning_text = QLabel("⚠ Over capacity - consider reassigning")
        warning_text.setObjectName("capacityWarningText")
        warning_layout.addWidget(warning_text)

        parent_layout.addWidget(self.capacity_warning)

    def _create_quick_filters(self, parent_layout):
        container = QFrame()
        container.setStyleSheet("border-bottom: 1px solid #333;")
        container.setFixedHeight(40)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)

        filters = ["All", "Comp", "Roto", "Paint", "Urgent"]
        for i, f in enumerate(filters):
            btn = QPushButton(f)
            btn.setObjectName("quickFilter")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            layout.addWidget(btn)

        layout.addStretch()
        parent_layout.addWidget(container)

    def _create_card_list(self, parent_layout):
        scroll = QScrollArea()
        scroll.setObjectName("cardList")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAcceptDrops(False)  # Let the column handle drops

        self.card_container = QWidget()
        self.card_container.setObjectName("cardListContainer")
        self.card_container.setAcceptDrops(False)  # Let the column handle drops

        self.card_layout = QVBoxLayout(self.card_container)
        self.card_layout.setContentsMargins(10, 8, 10, 8)
        self.card_layout.setSpacing(8)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Drop indicator (hidden by default)
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

        # Spacer to push cards to top
        self.card_layout.addStretch()

        scroll.setWidget(self.card_container)
        parent_layout.addWidget(scroll, 1)

    def _create_footer(self, parent_layout):
        footer = QFrame()
        footer.setObjectName("columnFooter")
        footer.setFixedHeight(40)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(14, 10, 14, 10)

        self.footer_left = QLabel("0 tasks")
        self.footer_left.setObjectName("footerText")
        layout.addWidget(self.footer_left)

        layout.addStretch()

        self.footer_right = QLabel("0h")
        self.footer_right.setObjectName("footerHours")
        layout.addWidget(self.footer_right)

        parent_layout.addWidget(footer)

    def _darken_color(self, hex_color: str) -> str:
        """Darken a hex color for gradient effect."""
        c = QColor(hex_color)
        return QColor.fromHslF(c.hueF(), c.saturationF(), max(0, c.lightnessF() - 0.2)).name()

    def add_task(self, task_data: dict):
        """Add a task card to this column."""
        card = TaskCard(task_data)

        # Insert before the drop indicator and stretch
        # Find the position of drop_indicator
        insert_pos = 0
        for i in range(self.card_layout.count()):
            item = self.card_layout.itemAt(i)
            if item and item.widget() == self.drop_indicator:
                insert_pos = i
                break

        self.card_layout.insertWidget(insert_pos, card)
        self.card_widgets.append(card)
        self.tasks.append(task_data)

        self._update_stats()

    def remove_task(self, task_id: int) -> dict:
        """Remove and return a task by ID."""
        for i, task in enumerate(self.tasks):
            if task["id"] == task_id:
                self.tasks.pop(i)
                widget = self.card_widgets.pop(i)
                widget.setParent(None)
                widget.deleteLater()
                self._update_stats()
                return task
        return None

    def _update_stats(self):
        """Update task count and hours."""
        count = len(self.tasks)
        total_hours = sum(t["hours"] for t in self.tasks)

        self.task_count_label.setText(str(count))

        if self.is_unassigned:
            self.footer_left.setText(f"{count} tasks waiting")
            self.footer_right.setText(f"~{total_hours:.0f}h total")
        else:
            self.footer_left.setText(f"~{total_hours:.1f}h assigned")
            self.footer_right.setText(f"{self.artist_data['capacity']}h available")

            # Update workload bar
            capacity = self.artist_data["capacity"]
            if capacity > 0:
                fill_pct = min(100, (total_hours / capacity) * 100)
                bar_width = int(fill_pct * 2.5)  # ~250px max width

                if fill_pct < 60:
                    color = "#678542"  # Green
                elif fill_pct < 85:
                    color = "#B28659"  # Amber
                else:
                    color = "#99604C"  # Red

                self.workload_fill.setFixedWidth(bar_width)
                self.workload_fill.setStyleSheet(f"background: {color}; border-radius: 2px;")

                # Show/hide capacity warning
                self.capacity_warning.setVisible(fill_pct > 90)

    # Drag and drop handlers
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

        # Find the main window (AssignmentBoard) by walking up the widget tree
        widget = self
        board = None
        while widget is not None:
            if isinstance(widget, QMainWindow):
                board = widget
                break
            widget = widget.parent()

        if board and hasattr(board, 'move_task'):
            board.move_task(task_id, self.artist_data["id"])

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

        # Checkmark
        check = QLabel("✓")
        check.setStyleSheet("color: #678542; font-size: 18px; font-weight: bold;")
        layout.addWidget(check)

        # Message
        self.message = QLabel("Task assigned")
        self.message.setObjectName("toastText")
        layout.addWidget(self.message)

        layout.addStretch()

        # Setup animation
        self._opacity = 1.0

    def show_message(self, text: str, duration: int = 2500):
        """Show the toast with a message."""
        self.message.setText(text)

        # Position at bottom right of parent
        if self.parent():
            parent_rect = self.parent().rect()
            x = parent_rect.width() - self.width() - 24
            y = parent_rect.height() - self.height() - 24
            self.move(x, y)

        self.show()
        self.raise_()

        # Auto-hide after duration
        QTimer.singleShot(duration, self.hide)


# ============================================================================
# MAIN ASSIGNMENT BOARD
# ============================================================================

class AssignmentBoard(QMainWindow):
    """The main Kanban-style assignment board."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShotBox — Assignment Board")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 800)

        self.columns = {}
        self._setup_ui()
        self._load_demo_data()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        self._create_header(main_layout)

        # Board (horizontal scroll area with columns)
        self._create_board(main_layout)

        # Toast notification
        self.toast = ToastNotification(central)

    def _create_header(self, parent_layout):
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(60)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)

        # Title
        title = QLabel("🎬 ShotBox — Assignment Board")
        title.setObjectName("headerTitle")
        layout.addWidget(title)

        layout.addStretch()

        # Timeline selector
        timeline_combo = QComboBox()
        timeline_combo.addItems([
            "Reel 1 - Main Timeline",
            "Reel 2 - VFX Heavy",
            "Reel 3 - Cleanup"
        ])
        layout.addWidget(timeline_combo)

        parent_layout.addWidget(header)

    def _create_board(self, parent_layout):
        scroll = QScrollArea()
        scroll.setObjectName("boardScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        board_container = QWidget()
        self.board_layout = QHBoxLayout(board_container)
        self.board_layout.setContentsMargins(20, 20, 20, 20)
        self.board_layout.setSpacing(16)
        self.board_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Create columns for each artist
        for artist in DEMO_ARTISTS:
            column = ArtistColumn(artist)
            self.columns[artist["id"]] = column
            self.board_layout.addWidget(column)

        scroll.setWidget(board_container)
        parent_layout.addWidget(scroll, 1)

    def _load_demo_data(self):
        """Load demo tasks into columns."""
        for task in DEMO_TASKS:
            artist_id = task["artist"]
            if artist_id in self.columns:
                self.columns[artist_id].add_task(task)

    def move_task(self, task_id: int, target_artist_id: str):
        """Move a task from one column to another."""
        # Find source column
        source_column = None
        task_data = None

        for col_id, column in self.columns.items():
            for task in column.tasks:
                if task["id"] == task_id:
                    source_column = column
                    break
            if source_column:
                break

        if not source_column:
            return

        # Don't move if dropping on same column
        if source_column.artist_data["id"] == target_artist_id:
            return

        # Remove from source
        task_data = source_column.remove_task(task_id)

        if task_data:
            # Update artist assignment
            task_data["artist"] = target_artist_id

            # Add to target
            target_column = self.columns[target_artist_id]
            target_column.add_task(task_data)

            # Show toast
            if target_artist_id == "unassigned":
                self.toast.show_message("Task moved to Unassigned")
            else:
                artist_name = target_column.artist_data["name"]
                self.toast.show_message(f"Task assigned to {artist_name}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # Set dark palette for better integration
    palette = app.palette()
    palette.setColor(palette.ColorRole.Window, QColor("#1a1a1a"))
    palette.setColor(palette.ColorRole.WindowText, QColor("#e0e0e0"))
    palette.setColor(palette.ColorRole.Base, QColor("#252525"))
    palette.setColor(palette.ColorRole.Text, QColor("#e0e0e0"))
    app.setPalette(palette)

    window = AssignmentBoard()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()