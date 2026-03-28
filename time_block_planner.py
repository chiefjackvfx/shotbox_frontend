"""
Time Block Planner - Daily task scheduling interface
v3 - Design focused: better visuals, completed states, context menus
"""

import sys
from datetime import datetime, date, timedelta
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QSizePolicy, QSplitter,
    QLineEdit, QDoubleSpinBox, QDialog, QDialogButtonBox, QFormLayout,
    QComboBox, QMenu, QCheckBox
)
from PyQt6.QtCore import Qt, QMimeData, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QDrag, QPixmap, QPainter, QColor, QAction, QCursor
import json

# =============================================================================
# COLORS & STYLING
# =============================================================================

PRIORITY_COLORS = {
    1: "#e85555",  # Red - urgent
    2: "#e89955",  # Orange  
    3: "#e8e855",  # Yellow
    4: "#7bc96f",  # Green
    5: "#666666",  # Grey - low
}

COLORS = {
    "bg_dark": "#121212",
    "bg_panel": "#1a1a1a", 
    "bg_card": "#242424",
    "bg_card_hover": "#2a2a2a",
    "bg_slot": "#1e1e1e",
    "bg_slot_hover": "#2a2a2a",
    "bg_drop_target": "#2d3a2d",
    "border": "#333333",
    "border_light": "#444444",
    "text": "#e0e0e0",
    "text_muted": "#888888",
    "text_dim": "#555555",
    "accent": "#5a9a5a",
    "completed_bg": "#1a2a1a",
    "completed_border": "#2a4a2a",
}

def get_priority_color(priority: int) -> str:
    return PRIORITY_COLORS.get(priority, PRIORITY_COLORS[5])

# =============================================================================
# DUMMY DATA
# =============================================================================

DUMMY_TASKS = [
    {"id": 1, "shot": "VFX_010", "task": "Roto", "status": "in_progress", "priority": 1, "budget_hours": 2.0, "deadline": "2025-01-05"},
    {"id": 2, "shot": "VFX_020", "task": "Comp", "status": "pending", "priority": 1, "budget_hours": 1.5, "deadline": "2025-01-07"},
    {"id": 3, "shot": "VFX_030", "task": "Paint", "status": "pending", "priority": 3, "budget_hours": 1.0, "deadline": None},
    {"id": 4, "shot": "VFX_040", "task": "Tracking", "status": "in_progress", "priority": 2, "budget_hours": 2.5, "deadline": "2025-01-06"},
    {"id": 5, "shot": "VFX_050", "task": "Roto", "status": "pending", "priority": 4, "budget_hours": 0.5, "deadline": None},
    {"id": 6, "shot": "VFX_060", "task": "Comp", "status": "review", "priority": 1, "budget_hours": 1.0, "deadline": "2025-01-04"},
    {"id": 7, "shot": "VFX_070", "task": "CG", "status": "pending", "priority": 2, "budget_hours": 3.0, "deadline": "2025-01-10"},
]

_temp_task_counter = 1000

def get_next_temp_id():
    global _temp_task_counter
    _temp_task_counter += 1
    return _temp_task_counter

def format_time_12h(hour: int, minute: int = 0) -> str:
    period = "AM" if hour < 12 else "PM"
    display_hour = hour if hour <= 12 else hour - 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{minute:02d} {period}" if minute else f"{display_hour} {period}"

def slot_index_to_time(index: int, start_hour: int = 9) -> tuple[int, int]:
    total_minutes = start_hour * 60 + index * 30
    return total_minutes // 60, total_minutes % 60

def get_deadline_status(deadline_str: str) -> tuple[str, str]:
    if not deadline_str:
        return None, None
    try:
        deadline = datetime.strptime(deadline_str, "%Y-%m-%d").date()
        diff = (deadline - date.today()).days
        if diff < 0:
            return "OVERDUE", "#e85555"
        elif diff == 0:
            return "TODAY", "#e89955"
        elif diff == 1:
            return "Tomorrow", "#e8e855"
        elif diff <= 3:
            return f"{diff}d", "#7bc96f"
        return None, None
    except:
        return None, None


# =============================================================================
# TEMP TASK DIALOG
# =============================================================================

class TempTaskDialog(QDialog):
    def __init__(self, default_time: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Block")
        self.setFixedWidth(280)
        self.setStyleSheet(f"""
            QDialog {{ background: {COLORS['bg_panel']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}
            QLabel {{ color: {COLORS['text']}; }}
            QLineEdit, QDoubleSpinBox {{ 
                background: {COLORS['bg_dark']}; border: 1px solid {COLORS['border']}; 
                border-radius: 4px; padding: 8px; color: {COLORS['text']}; 
            }}
            QLineEdit:focus, QDoubleSpinBox:focus {{ border-color: {COLORS['accent']}; }}
            QPushButton {{
                background: {COLORS['bg_card']}; border: 1px solid {COLORS['border']};
                border-radius: 4px; padding: 8px 16px; color: {COLORS['text']};
            }}
            QPushButton:hover {{ background: {COLORS['bg_card_hover']}; }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Time label
        if default_time:
            time_lbl = QLabel(default_time)
            time_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
            layout.addWidget(time_lbl)
        
        # Task name
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("What are you working on?")
        layout.addWidget(self.txt_name)
        
        # Duration row
        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Duration:"))
        self.spn_hours = QDoubleSpinBox()
        self.spn_hours.setRange(0.5, 8.0)
        self.spn_hours.setSingleStep(0.5)
        self.spn_hours.setValue(1.0)
        self.spn_hours.setSuffix("h")
        self.spn_hours.setFixedWidth(80)
        dur_row.addWidget(self.spn_hours)
        dur_row.addStretch()
        layout.addLayout(dur_row)
        
        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        
        btn_add = QPushButton("Add")
        btn_add.setStyleSheet(f"background: {COLORS['accent']}; border-color: {COLORS['accent']};")
        btn_add.clicked.connect(self.accept)
        btn_row.addWidget(btn_add)
        
        layout.addLayout(btn_row)
        self.txt_name.setFocus()
    
    def get_task_data(self) -> dict:
        return {
            "id": get_next_temp_id(), "shot": "", "task": self.txt_name.text() or "Block",
            "status": "temp", "priority": 5, "budget_hours": self.spn_hours.value(),
            "is_temp": True
        }


# =============================================================================
# DRAGGABLE TASK CARD
# =============================================================================

class DraggableTaskCard(QFrame):
    def __init__(self, task_data: dict, parent=None):
        super().__init__(parent)
        self.task_data = task_data
        self.drag_start_pos = None
        self._setup_ui()
    
    def _setup_ui(self):
        priority = self.task_data.get("priority", 5)
        priority_color = get_priority_color(priority)
        
        self.setObjectName("task_card")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(f"""
            QFrame#task_card {{
                background: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-left: 3px solid {priority_color};
                border-radius: 6px;
                padding: 0px;
            }}
            QFrame#task_card:hover {{
                background: {COLORS['bg_card_hover']};
                border-color: {COLORS['border_light']};
                border-left: 3px solid {priority_color};
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        
        # Header: Shot + Priority
        header = QHBoxLayout()
        header.setSpacing(8)
        
        shot_label = QLabel(self.task_data.get("shot", "SHOT"))
        shot_label.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {COLORS['text']};")
        header.addWidget(shot_label)
        
        task_label = QLabel(self.task_data.get("task", ""))
        task_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        header.addWidget(task_label)
        
        header.addStretch()
        
        # Priority badge
        pri_label = QLabel(f"P{priority}")
        pri_label.setStyleSheet(f"""
            background: {priority_color}22; 
            color: {priority_color}; 
            padding: 2px 6px; 
            border-radius: 3px; 
            font-size: 10px; 
            font-weight: 600;
        """)
        header.addWidget(pri_label)
        
        layout.addLayout(header)
        
        # Footer: Hours + Deadline
        footer = QHBoxLayout()
        footer.setSpacing(12)
        
        hours = self.task_data.get("budget_hours", 1.0)
        hours_label = QLabel(f"{hours}h")
        hours_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        footer.addWidget(hours_label)
        
        deadline_text, deadline_color = get_deadline_status(self.task_data.get("deadline"))
        if deadline_text:
            dl_label = QLabel(deadline_text)
            dl_label.setStyleSheet(f"color: {deadline_color}; font-size: 11px; font-weight: 600;")
            footer.addWidget(dl_label)
        
        footer.addStretch()
        layout.addLayout(footer)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)
    
    def mouseMoveEvent(self, event):
        if not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return
        
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-task", json.dumps(self.task_data).encode())
        drag.setMimeData(mime)
        
        # Create drag preview
        pixmap = QPixmap(self.size())
        self.render(pixmap)
        drag.setPixmap(pixmap.scaled(pixmap.width(), pixmap.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        drag.setHotSpot(event.pos())
        
        drag.exec(Qt.DropAction.MoveAction)
        self.drag_start_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)


# =============================================================================
# TIME SLOT WIDGET
# =============================================================================

class TimeSlotWidget(QFrame):
    task_dropped = pyqtSignal(int, dict)
    slot_double_clicked = pyqtSignal(int)
    SLOT_HEIGHT = 44
    
    def __init__(self, slot_index: int, start_hour: int = 9, parent=None):
        super().__init__(parent)
        self.slot_index = slot_index
        self.start_hour = start_hour
        self.hour, self.minute = slot_index_to_time(slot_index, start_hour)
        self.task_data = None
        self.is_blocked = False
        self.is_continuation = False
        
        self.setAcceptDrops(True)
        self.setFixedHeight(self.SLOT_HEIGHT)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Time gutter
        self.time_gutter = QLabel()
        self.time_gutter.setFixedWidth(70)
        self.time_gutter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        
        if self.minute == 0:
            self.time_gutter.setText(format_time_12h(self.hour))
            self.time_gutter.setStyleSheet(f"""
                color: {COLORS['text_muted']}; 
                font-size: 11px; 
                font-weight: 500;
                padding-right: 12px;
                padding-top: 4px;
            """)
        else:
            self.time_gutter.setStyleSheet("padding-right: 12px;")
        
        layout.addWidget(self.time_gutter)
        
        # Drop zone
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("drop_zone")
        
        drop_layout = QHBoxLayout(self.drop_zone)
        drop_layout.setContentsMargins(0, 0, 0, 0)
        
        self.content_label = QLabel("")
        self.content_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.content_label)
        
        layout.addWidget(self.drop_zone, 1)
        
        # Now safe to style
        self._set_default_style()
    
    def _set_default_style(self):
        border_style = "border-top: 1px solid #2a2a2a;" if self.minute == 0 else "border-top: 1px dotted #222222;"
        self.drop_zone.setStyleSheet(f"""
            QFrame#drop_zone {{
                background: {COLORS['bg_slot']};
                {border_style}
                border-radius: 0px;
                margin-right: 12px;
            }}
            QFrame#drop_zone:hover {{
                background: {COLORS['bg_slot_hover']};
            }}
        """)
        self.content_label.setStyleSheet(f"color: {COLORS['text_dim']};")
    
    def _set_drag_hover_style(self):
        self.drop_zone.setStyleSheet(f"""
            QFrame#drop_zone {{
                background: {COLORS['bg_drop_target']};
                border: 2px dashed {COLORS['accent']};
                border-radius: 4px;
                margin-right: 12px;
            }}
        """)
    
    def set_blocked(self, label: str = ""):
        self.is_blocked = True
        self.content_label.setText(label)
        self.content_label.setStyleSheet(f"color: {COLORS['text_dim']};")
    
    def set_continuation(self):
        self.is_continuation = True
        self.drop_zone.hide()
        self.setAcceptDrops(False)
    
    def clear_slot(self):
        self.task_data = None
        self.is_continuation = False
        self.is_blocked = False
        self.drop_zone.show()
        self.content_label.setText("")
        self._set_default_style()
        self.setAcceptDrops(True)
    
    def is_available(self) -> bool:
        return not self.is_continuation and not self.task_data and not self.is_blocked
    
    def dragEnterEvent(self, event):
        if self.is_continuation or self.is_blocked:
            event.ignore()
            return
        if event.mimeData().hasFormat("application/x-task") or event.mimeData().hasFormat("application/x-lunch"):
            event.acceptProposedAction()
            self._set_drag_hover_style()
    
    def dragLeaveEvent(self, event):
        self._set_default_style()
    
    def dropEvent(self, event):
        self._set_default_style()
        if event.mimeData().hasFormat("application/x-lunch"):
            event.acceptProposedAction()
            self.task_dropped.emit(self.slot_index, {"_type": "lunch"})
            return
        if event.mimeData().hasFormat("application/x-task"):
            task_data = json.loads(event.mimeData().data("application/x-task").data().decode())
            event.acceptProposedAction()
            self.task_dropped.emit(self.slot_index, task_data)
    
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_available():
            self.slot_double_clicked.emit(self.slot_index)
        super().mouseDoubleClickEvent(event)


# =============================================================================
# SPANNING TASK WIDGET (Scheduled block with completion + split on drop)
# =============================================================================

class SpanningTaskWidget(QFrame):
    drag_started = pyqtSignal(int, dict)
    completed_changed = pyqtSignal(int, bool)
    remove_requested = pyqtSignal(int)
    split_requested = pyqtSignal(int, dict, str)  # slot_index, new_task, side ('left' or 'right')
    
    def __init__(self, task_data: dict, slot_index: int, span_slots: int, slot_height: int, parent=None):
        super().__init__(parent)
        self.task_data = task_data
        self.slot_index = slot_index
        self.span_slots = span_slots
        self.slot_height = slot_height
        self.is_completed = False
        self.drag_start_pos = None
        
        self.setAcceptDrops(True)
        self.setFixedHeight(span_slots * slot_height - 6)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._setup_ui()
        
        self._drop_side = None  # Track which side is being hovered
    
    def _setup_ui(self):
        is_temp = self.task_data.get("is_temp", False)
        priority = self.task_data.get("priority", 5)
        priority_color = get_priority_color(priority)
        
        self._apply_style(priority_color, is_temp)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        
        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setFixedSize(18, 18)
        self.checkbox.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 2px solid {COLORS['border_light']};
                border-radius: 4px;
                background: transparent;
            }}
            QCheckBox::indicator:checked {{
                background: {COLORS['accent']};
                border-color: {COLORS['accent']};
            }}
            QCheckBox::indicator:hover {{
                border-color: {COLORS['accent']};
            }}
        """)
        self.checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.checkbox)
        
        # Content
        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        
        if is_temp:
            self.title_label = QLabel(self.task_data.get('task', 'Block'))
            self.title_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text']}; font-size: 13px;")
        else:
            self.title_label = QLabel(f"{self.task_data.get('shot', '')}  •  {self.task_data.get('task', '')}")
            self.title_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text']}; font-size: 13px;")
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        
        if not is_temp:
            pri_label = QLabel(f"P{priority}")
            pri_label.setStyleSheet(f"color: {priority_color}; font-size: 10px; font-weight: 600;")
            title_row.addWidget(pri_label)
        
        content_layout.addLayout(title_row)
        
        # Duration label (we'll update this dynamically)
        self.dur_label = QLabel(f"{self.task_data.get('budget_hours', 1.0)}h")
        self.dur_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        content_layout.addWidget(self.dur_label)
        
        content_layout.addStretch()
        layout.addLayout(content_layout, 1)
    
    def update_duration(self, new_hours: float):
        """Update displayed duration after a split."""
        self.task_data['budget_hours'] = new_hours
        self.dur_label.setText(f"{new_hours}h")
    
    def _apply_style(self, priority_color: str, is_temp: bool, highlight_side: str = None):
        # Base styles
        if self.is_completed:
            base_bg = COLORS['completed_bg']
            base_border = COLORS['completed_border']
            left_border_color = COLORS['accent']
        elif is_temp:
            base_bg = "#2a2a35"
            base_border = "#3a3a45"
            left_border_color = "#6666aa"
        else:
            base_bg = COLORS['bg_card']
            base_border = COLORS['border']
            left_border_color = priority_color
        
        # Add split highlight gradient if hovering
        if highlight_side == 'right':
            self.setStyleSheet(f"""
                SpanningTaskWidget {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                        stop:0 {base_bg}, stop:0.5 {base_bg}, stop:0.5 {COLORS['bg_drop_target']}, stop:1 {COLORS['bg_drop_target']});
                    border: 1px solid {base_border};
                    border-left: 3px solid {left_border_color};
                    border-radius: 6px;
                }}
            """)
        elif highlight_side == 'left':
            self.setStyleSheet(f"""
                SpanningTaskWidget {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                        stop:0 {COLORS['bg_drop_target']}, stop:0.5 {COLORS['bg_drop_target']}, stop:0.5 {base_bg}, stop:1 {base_bg});
                    border: 1px solid {base_border};
                    border-left: 3px solid {left_border_color};
                    border-radius: 6px;
                }}
            """)
        else:
            hover_bg = COLORS['bg_card_hover'] if not self.is_completed else base_bg
            self.setStyleSheet(f"""
                SpanningTaskWidget {{
                    background: {base_bg};
                    border: 1px solid {base_border};
                    border-left: 3px solid {left_border_color};
                    border-radius: 6px;
                }}
                SpanningTaskWidget:hover {{
                    background: {hover_bg};
                }}
            """)
    
    def _on_checkbox_changed(self, state):
        self.is_completed = state == Qt.CheckState.Checked.value
        priority_color = get_priority_color(self.task_data.get("priority", 5))
        self._apply_style(priority_color, self.task_data.get("is_temp", False))
        
        if self.is_completed:
            self.title_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text_muted']}; font-size: 13px; text-decoration: line-through;")
        else:
            self.title_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text']}; font-size: 13px;")
        
        self.completed_changed.emit(self.slot_index, self.is_completed)
    
    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {COLORS['bg_panel']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 8px 16px;
                border-radius: 4px;
                color: {COLORS['text']};
            }}
            QMenu::item:selected {{
                background: {COLORS['bg_card_hover']};
            }}
        """)
        
        complete_action = menu.addAction("✓ Mark Complete" if not self.is_completed else "↩ Mark Incomplete")
        complete_action.triggered.connect(lambda: self.checkbox.setChecked(not self.is_completed))
        
        menu.addSeparator()
        
        remove_action = menu.addAction("✕ Remove")
        remove_action.triggered.connect(lambda: self.remove_requested.emit(self.slot_index))
        
        menu.exec(self.mapToGlobal(pos))
    
    # Drag & Drop handling for split
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-task"):
            # Only allow split if we have more than 1 slot
            if self.span_slots >= 2:
                event.acceptProposedAction()
                self._update_drop_highlight(event.position().x())
            else:
                event.ignore()
        else:
            event.ignore()
    
    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-task"):
            self._update_drop_highlight(event.position().x())
            event.acceptProposedAction()
    
    def dragLeaveEvent(self, event):
        self._drop_side = None
        priority_color = get_priority_color(self.task_data.get("priority", 5))
        self._apply_style(priority_color, self.task_data.get("is_temp", False))
    
    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-task"):
            task_data = json.loads(event.mimeData().data("application/x-task").data().decode())
            side = 'right' if event.position().x() > self.width() / 2 else 'left'
            
            # Reset style
            priority_color = get_priority_color(self.task_data.get("priority", 5))
            self._apply_style(priority_color, self.task_data.get("is_temp", False))
            
            # Emit split request
            self.split_requested.emit(self.slot_index, task_data, side)
            event.acceptProposedAction()
    
    def _update_drop_highlight(self, x_pos):
        side = 'right' if x_pos > self.width() / 2 else 'left'
        if side != self._drop_side:
            self._drop_side = side
            priority_color = get_priority_color(self.task_data.get("priority", 5))
            self._apply_style(priority_color, self.task_data.get("is_temp", False), highlight_side=side)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Don't start drag if clicking checkbox area
            if event.pos().x() > 40:
                self.drag_start_pos = event.pos()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)
    
    def mouseMoveEvent(self, event):
        if not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return
        
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-task", json.dumps(self.task_data).encode())
        drag.setMimeData(mime)
        
        pixmap = QPixmap(self.size())
        self.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        
        self.drag_started.emit(self.slot_index, self.task_data)
        drag.exec(Qt.DropAction.MoveAction)
        self.drag_start_pos = None


# =============================================================================
# LUNCH BLOCK
# =============================================================================

class LunchBlockWidget(QFrame):
    drag_started = pyqtSignal(int)
    
    def __init__(self, slot_index: int, span_slots: int, slot_height: int, parent=None):
        super().__init__(parent)
        self.slot_index = slot_index
        self.drag_start_pos = None
        
        self.setFixedHeight(span_slots * slot_height - 6)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(f"""
            LunchBlockWidget {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #2a2a22, stop:1 #252520);
                border: 1px solid #3a3a30;
                border-left: 3px solid #888855;
                border-radius: 6px;
            }}
            LunchBlockWidget:hover {{
                background: #2f2f25;
            }}
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        
        icon = QLabel("☕")
        icon.setStyleSheet("font-size: 16px;")
        layout.addWidget(icon)
        
        title = QLabel("Lunch")
        title.setStyleSheet(f"color: {COLORS['text']}; font-weight: 500;")
        layout.addWidget(title)
        
        layout.addStretch()
        
        dur = QLabel("1h")
        dur.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        layout.addWidget(dur)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_start_pos = None
        super().mouseReleaseEvent(event)
    
    def mouseMoveEvent(self, event):
        if not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return
        
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-lunch", b"lunch")
        drag.setMimeData(mime)
        
        pixmap = QPixmap(self.size())
        self.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        
        self.drag_started.emit(self.slot_index)
        drag.exec(Qt.DropAction.MoveAction)
        self.drag_start_pos = None


# =============================================================================
# CURRENT TIME INDICATOR
# =============================================================================

class CurrentTimeIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Time badge
        self.time_label = QLabel()
        self.time_label.setFixedWidth(70)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_label.setStyleSheet(f"""
            color: {COLORS['text']};
            background: #cc4444;
            border-radius: 3px;
            padding: 2px 6px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
        """)
        layout.addWidget(self.time_label)
        
        # Line
        line = QFrame()
        line.setFixedHeight(2)
        line.setStyleSheet("background: #cc4444;")
        layout.addWidget(line, 1)
    
    def set_time(self, hour: int, minute: int):
        self.time_label.setText(format_time_12h(hour, minute))


# =============================================================================
# TASK LIST PANEL
# =============================================================================

class TaskListPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.task_cards = {}
        self.all_tasks = []
        self.current_sort = "priority"
        self._setup_ui()
    
    def _setup_ui(self):
        self.setStyleSheet(f"background: {COLORS['bg_panel']}; border: none;")
        self.setMinimumWidth(280)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = QLabel("Tasks")
        header.setStyleSheet(f"color: {COLORS['text']}; font-size: 18px; font-weight: 600;")
        layout.addWidget(header)
        
        # Summary
        self.summary_label = QLabel("0 tasks • 0h")
        self.summary_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        layout.addWidget(self.summary_label)
        
        # Sort
        sort_row = QHBoxLayout()
        sort_row.setSpacing(8)
        
        sort_label = QLabel("Sort:")
        sort_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        sort_row.addWidget(sort_label)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Priority", "Hours", "Deadline", "Name"])
        self.sort_combo.setFixedHeight(28)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                background: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                padding: 4px 8px;
                color: {COLORS['text']};
                font-size: 11px;
            }}
            QComboBox:hover {{ border-color: {COLORS['border_light']}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background: {COLORS['bg_panel']};
                border: 1px solid {COLORS['border']};
                selection-background-color: {COLORS['bg_card_hover']};
            }}
        """)
        self.sort_combo.currentTextChanged.connect(self._on_sort_changed)
        sort_row.addWidget(self.sort_combo)
        sort_row.addStretch()
        
        layout.addLayout(sort_row)
        
        # Task list scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: {COLORS['bg_panel']};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border']};
                border-radius: 3px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {COLORS['border_light']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        
        self.task_container = QWidget()
        self.task_container.setStyleSheet("background: transparent;")
        self.task_layout = QVBoxLayout(self.task_container)
        self.task_layout.setContentsMargins(0, 0, 0, 0)
        self.task_layout.setSpacing(8)
        self.task_layout.addStretch()
        
        scroll.setWidget(self.task_container)
        layout.addWidget(scroll, 1)
    
    def _on_sort_changed(self, text):
        self.current_sort = text.lower()
        self._refresh_display()
    
    def _refresh_display(self):
        for card in list(self.task_cards.values()):
            self.task_layout.removeWidget(card)
            card.deleteLater()
        self.task_cards.clear()
        
        tasks = list(self.all_tasks)
        
        if self.current_sort == "priority":
            tasks.sort(key=lambda t: t.get("priority", 5))
        elif self.current_sort == "hours":
            tasks.sort(key=lambda t: t.get("budget_hours", 0), reverse=True)
        elif self.current_sort == "deadline":
            tasks.sort(key=lambda t: t.get("deadline") or "9999")
        elif self.current_sort == "name":
            tasks.sort(key=lambda t: t.get("shot", ""))
        
        for task in tasks:
            card = DraggableTaskCard(task)
            self.task_cards[task["id"]] = card
            self.task_layout.insertWidget(self.task_layout.count() - 1, card)
        
        self._update_summary()
    
    def _update_summary(self):
        count = len(self.task_cards)
        hours = sum(t.get("budget_hours", 0) for t in self.all_tasks if t["id"] in self.task_cards)
        self.summary_label.setText(f"{count} tasks • {hours:.1f}h")
    
    def add_task(self, task_data: dict):
        if any(t["id"] == task_data["id"] for t in self.all_tasks):
            return
        self.all_tasks.append(task_data)
        self._refresh_display()
    
    def remove_task(self, task_id: int):
        self.all_tasks = [t for t in self.all_tasks if t["id"] != task_id]
        if task_id in self.task_cards:
            card = self.task_cards.pop(task_id)
            self.task_layout.removeWidget(card)
            card.deleteLater()
        self._update_summary()
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-task"):
            event.acceptProposedAction()
            self.setStyleSheet(f"background: {COLORS['bg_drop_target']}; border: none;")
    
    def dragLeaveEvent(self, event):
        self.setStyleSheet(f"background: {COLORS['bg_panel']}; border: none;")
    
    def dropEvent(self, event):
        self.setStyleSheet(f"background: {COLORS['bg_panel']}; border: none;")
        if event.mimeData().hasFormat("application/x-task"):
            task_data = json.loads(event.mimeData().data("application/x-task").data().decode())
            if not task_data.get("is_temp"):
                self.add_task(task_data)
            event.acceptProposedAction()


# =============================================================================
# TIME BLOCK PANEL
# =============================================================================

class TimeBlockPanel(QFrame):
    schedule_changed = pyqtSignal()
    task_unscheduled = pyqtSignal(dict)
    
    SLOT_HEIGHT = 44
    LUNCH_SLOTS = 2
    
    def __init__(self, start_hour: int = 9, end_hour: int = 18, parent=None):
        super().__init__(parent)
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.num_slots = (end_hour - start_hour) * 2
        self.time_slots = {}
        self.spanning_tasks = {}
        self.scheduled_tasks = {}
        self.lunch_slot = None
        self.lunch_widget = None
        self.current_date = date.today()
        
        self._setup_ui()
        self._setup_lunch()
        self._setup_time_indicator()
    
    def _setup_ui(self):
        self.setStyleSheet(f"background: {COLORS['bg_dark']}; border: none;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        
        # Header with date nav
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        
        nav_btn_style = f"""
            QPushButton {{
                background: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_muted']};
                font-size: 14px;
                padding: 4px;
            }}
            QPushButton:hover {{
                background: {COLORS['bg_card_hover']};
                color: {COLORS['text']};
            }}
        """
        
        self.btn_prev = QPushButton("‹")
        self.btn_prev.setFixedSize(32, 32)
        self.btn_prev.setStyleSheet(nav_btn_style)
        self.btn_prev.clicked.connect(self._prev_day)
        header_row.addWidget(self.btn_prev)
        
        self.btn_today = QPushButton("Today")
        self.btn_today.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['accent']};
                border: none;
                border-radius: 6px;
                color: white;
                font-size: 12px;
                font-weight: 500;
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background: #6aaa6a; }}
        """)
        self.btn_today.clicked.connect(self._go_today)
        header_row.addWidget(self.btn_today)
        
        self.btn_next = QPushButton("›")
        self.btn_next.setFixedSize(32, 32)
        self.btn_next.setStyleSheet(nav_btn_style)
        self.btn_next.clicked.connect(self._next_day)
        header_row.addWidget(self.btn_next)
        
        header_row.addSpacing(16)
        
        self.date_label = QLabel()
        self.date_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 20px; font-weight: 600;")
        header_row.addWidget(self.date_label)
        
        header_row.addStretch()
        
        # Extend day button
        self.btn_extend = QPushButton("+ Add Hour")
        self.btn_extend.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_muted']};
                font-size: 11px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background: {COLORS['bg_card_hover']};
                color: {COLORS['text']};
                border-color: {COLORS['border_light']};
            }}
        """)
        self.btn_extend.clicked.connect(self._extend_day)
        header_row.addWidget(self.btn_extend)
        
        # Hours label
        self.hours_label = QLabel()
        self.hours_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        self._update_hours_label()
        header_row.addWidget(self.hours_label)
        
        layout.addLayout(header_row)
        
        self._update_date_label()
        
        # Schedule container
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: {COLORS['bg_dark']};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border']};
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        
        self.slots_container = QWidget()
        self.slots_container.setStyleSheet("background: transparent;")
        self.slots_layout = QVBoxLayout(self.slots_container)
        self.slots_layout.setContentsMargins(0, 0, 0, 0)
        self.slots_layout.setSpacing(0)
        
        for i in range(self.num_slots):
            slot = TimeSlotWidget(i, self.start_hour)
            slot.task_dropped.connect(self._on_task_dropped)
            slot.slot_double_clicked.connect(self._on_slot_double_clicked)
            self.time_slots[i] = slot
            self.slots_layout.addWidget(slot)
        
        self.slots_layout.addStretch()
        self.scroll.setWidget(self.slots_container)
        layout.addWidget(self.scroll, 1)
    
    def _update_date_label(self):
        today = date.today()
        if self.current_date == today:
            prefix = "Today"
        elif self.current_date == today + timedelta(days=1):
            prefix = "Tomorrow"
        elif self.current_date == today - timedelta(days=1):
            prefix = "Yesterday"
        else:
            prefix = self.current_date.strftime("%A")
        
        self.date_label.setText(f"{prefix}, {self.current_date.strftime('%b %d')}")
    
    def _update_hours_label(self):
        self.hours_label.setText(f"{format_time_12h(self.start_hour)} – {format_time_12h(self.end_hour)}")
    
    def _extend_day(self):
        """Add one hour (2 slots) to the end of the day."""
        if self.end_hour >= 23:
            return
        
        self.end_hour += 1
        old_num_slots = self.num_slots
        self.num_slots = (self.end_hour - self.start_hour) * 2
        
        # Add new slots
        for i in range(old_num_slots, self.num_slots):
            slot = TimeSlotWidget(i, self.start_hour)
            slot.task_dropped.connect(self._on_task_dropped)
            slot.slot_double_clicked.connect(self._on_slot_double_clicked)
            self.time_slots[i] = slot
            self.slots_layout.insertWidget(self.slots_layout.count() - 1, slot)
        
        self._update_hours_label()
        
        # Scroll to show new slots
        QTimer.singleShot(100, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        ))
    
    def _prev_day(self):
        self.current_date -= timedelta(days=1)
        self._update_date_label()
    
    def _next_day(self):
        self.current_date += timedelta(days=1)
        self._update_date_label()
    
    def _go_today(self):
        self.current_date = date.today()
        self._update_date_label()
    
    def _setup_lunch(self):
        self.lunch_slot = (12 - self.start_hour) * 2
        self._create_lunch_widget()
    
    def _create_lunch_widget(self):
        if self.lunch_widget:
            self.lunch_widget.deleteLater()
        
        for i in range(self.lunch_slot, self.lunch_slot + self.LUNCH_SLOTS):
            if i in self.time_slots:
                self.time_slots[i].set_blocked("" if i > self.lunch_slot else "")
                if i > self.lunch_slot:
                    self.time_slots[i].set_continuation()
        
        self.lunch_widget = LunchBlockWidget(self.lunch_slot, self.LUNCH_SLOTS, self.SLOT_HEIGHT, self.slots_container)
        self.lunch_widget.drag_started.connect(self._on_lunch_drag)
        self._position_widget(self.lunch_widget, self.lunch_slot)
        self.lunch_widget.show()
    
    def _on_lunch_drag(self, old_slot):
        for i in range(old_slot, old_slot + self.LUNCH_SLOTS):
            if i in self.time_slots:
                self.time_slots[i].clear_slot()
    
    def _setup_time_indicator(self):
        self.time_indicator = CurrentTimeIndicator(self.slots_container)
        self.time_indicator.hide()
        
        self.time_timer = QTimer(self)
        self.time_timer.timeout.connect(self._update_time_indicator)
        self.time_timer.start(60000)
        self._update_time_indicator()
    
    def _update_time_indicator(self):
        if self.current_date != date.today():
            self.time_indicator.hide()
            return
        
        now = datetime.now()
        if now.hour < self.start_hour or now.hour >= self.end_hour:
            self.time_indicator.hide()
            return
        
        mins = (now.hour - self.start_hour) * 60 + now.minute
        y = int((mins / 30) * self.SLOT_HEIGHT) - 10
        
        self.time_indicator.set_time(now.hour, now.minute)
        self.time_indicator.setGeometry(0, y, self.slots_container.width(), 20)
        self.time_indicator.show()
        self.time_indicator.raise_()
    
    def _position_widget(self, widget, slot_index):
        y = slot_index * self.SLOT_HEIGHT + 3
        widget.setGeometry(70, y, self.slots_container.width() - 82, widget.height())
    
    def _on_slot_double_clicked(self, slot_index):
        hour, minute = slot_index_to_time(slot_index, self.start_hour)
        dialog = TempTaskDialog(format_time_12h(hour, minute), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._place_task(slot_index, dialog.get_task_data())
    
    def _on_task_dropped(self, slot_index, task_data):
        if task_data.get("_type") == "lunch":
            self._move_lunch(slot_index)
            return
        self._place_task(slot_index, task_data)
    
    def _move_lunch(self, new_slot):
        for i in range(new_slot, min(new_slot + self.LUNCH_SLOTS, self.num_slots)):
            if not self.time_slots[i].is_available():
                self._create_lunch_widget()
                return
        self.lunch_slot = new_slot
        self._create_lunch_widget()
        self._raise_time_indicator()
    
    def _place_task(self, slot_index, task_data):
        span = max(1, round(task_data.get("budget_hours", 1.0) * 2))
        
        for i in range(slot_index, min(slot_index + span, self.num_slots)):
            if not self.time_slots.get(i) or not self.time_slots[i].is_available():
                return
        
        for i in range(slot_index, min(slot_index + span, self.num_slots)):
            self.time_slots[i].task_data = task_data
            if i > slot_index:
                self.time_slots[i].set_continuation()
        
        self.scheduled_tasks[slot_index] = task_data
        self._create_spanning_widget(slot_index, task_data, span)
        self._raise_time_indicator()
        self.schedule_changed.emit()
    
    def _create_spanning_widget(self, slot_index, task_data, span):
        if slot_index in self.spanning_tasks:
            self.spanning_tasks[slot_index].deleteLater()
        
        widget = SpanningTaskWidget(task_data, slot_index, span, self.SLOT_HEIGHT, self.slots_container)
        widget.drag_started.connect(self._on_spanning_drag)
        widget.remove_requested.connect(self._remove_task)
        widget.split_requested.connect(self._on_split_requested)
        
        self._position_widget(widget, slot_index)
        widget.show()
        self.spanning_tasks[slot_index] = widget
    
    def _on_split_requested(self, slot_index: int, new_task: dict, side: str):
        """Handle adding a parallel task (multitasking) - visual side-by-side, not time split."""
        if slot_index not in self.scheduled_tasks:
            return
        
        existing_task = self.scheduled_tasks[slot_index]
        existing_widget = self.spanning_tasks.get(slot_index)
        if not existing_widget:
            return
        
        # Store multitask info - which tasks share this slot
        if not hasattr(self, 'multitask_slots'):
            self.multitask_slots = {}  # slot_index -> list of task widgets
        
        # Get the span of the existing task
        existing_span = existing_widget.span_slots
        
        # Create new task with its own duration (don't modify hours)
        new_span = max(1, round(new_task.get("budget_hours", 1.0) * 2))
        # Limit new task span to not exceed existing task's span for clean visuals
        new_span = min(new_span, existing_span)
        
        # Track that this slot now has multiple tasks
        if slot_index not in self.multitask_slots:
            self.multitask_slots[slot_index] = [existing_widget]
        
        # Create new widget for the parallel task
        new_widget = SpanningTaskWidget(new_task, slot_index, new_span, self.SLOT_HEIGHT, self.slots_container)
        new_widget.drag_started.connect(lambda idx, data: self._on_multitask_drag(slot_index, new_widget, data))
        new_widget.remove_requested.connect(lambda idx: self._remove_multitask(slot_index, new_widget))
        new_widget.split_requested.connect(self._on_split_requested)
        
        self.multitask_slots[slot_index].append(new_widget)
        
        # Reposition all widgets in this slot to be side by side
        self._position_multitask_widgets(slot_index)
        
        new_widget.show()
        self._raise_time_indicator()
        self.schedule_changed.emit()
    
    def _position_multitask_widgets(self, slot_index: int):
        """Position multiple task widgets side by side in the same time slot."""
        if slot_index not in self.multitask_slots:
            return
        
        widgets = self.multitask_slots[slot_index]
        if not widgets:
            return
        
        num_widgets = len(widgets)
        total_width = self.slots_container.width() - 82  # Same as normal positioning
        widget_width = (total_width - (num_widgets - 1) * 4) // num_widgets  # 4px gap between
        
        for i, widget in enumerate(widgets):
            y = slot_index * self.SLOT_HEIGHT + 3
            x = 70 + i * (widget_width + 4)
            widget.setGeometry(x, y, widget_width, widget.height())
    
    def _on_multitask_drag(self, slot_index: int, widget: SpanningTaskWidget, task_data: dict):
        """Handle dragging a multitask widget out."""
        self._remove_multitask(slot_index, widget, emit_unscheduled=True, task_data=task_data)
    
    def _remove_multitask(self, slot_index: int, widget: SpanningTaskWidget, emit_unscheduled: bool = False, task_data: dict = None):
        """Remove a widget from a multitask slot."""
        if slot_index not in self.multitask_slots:
            return
        
        widgets = self.multitask_slots[slot_index]
        if widget in widgets:
            widgets.remove(widget)
            widget.deleteLater()
            
            # If only one widget left, remove from multitask tracking
            if len(widgets) == 1:
                # Reposition the remaining widget to full width
                remaining = widgets[0]
                self._position_widget(remaining, slot_index)
                del self.multitask_slots[slot_index]
            elif len(widgets) > 1:
                # Reposition remaining widgets
                self._position_multitask_widgets(slot_index)
            else:
                # No widgets left
                del self.multitask_slots[slot_index]
        
        if emit_unscheduled and task_data and not task_data.get('is_temp'):
            self.task_unscheduled.emit(task_data)
        
        self.schedule_changed.emit()
    
    def _on_spanning_drag(self, slot_index, task_data):
        self._remove_task(slot_index, emit_unscheduled=True)
    
    def _remove_task(self, slot_index, emit_unscheduled=True):
        if slot_index not in self.scheduled_tasks:
            return
        
        task_data = self.scheduled_tasks.pop(slot_index)
        span = max(1, round(task_data.get("budget_hours", 1.0) * 2))
        
        for i in range(slot_index, min(slot_index + span, self.num_slots)):
            if i in self.time_slots:
                self.time_slots[i].clear_slot()
        
        if slot_index in self.spanning_tasks:
            self.spanning_tasks[slot_index].deleteLater()
            del self.spanning_tasks[slot_index]
        
        if emit_unscheduled and not task_data.get("is_temp"):
            self.task_unscheduled.emit(task_data)
        
        self.schedule_changed.emit()
    
    def _raise_time_indicator(self):
        self.time_indicator.raise_()
    
    def get_schedule(self):
        return dict(self.scheduled_tasks)
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Position single-task widgets
        for idx, widget in self.spanning_tasks.items():
            # Skip if this slot has multitask widgets
            if hasattr(self, 'multitask_slots') and idx in self.multitask_slots:
                continue
            self._position_widget(widget, idx)
        
        # Position multitask widgets
        if hasattr(self, 'multitask_slots'):
            for slot_index in self.multitask_slots:
                self._position_multitask_widgets(slot_index)
        
        if self.lunch_widget:
            self._position_widget(self.lunch_widget, self.lunch_slot)
        self._update_time_indicator()


# =============================================================================
# MAIN WINDOW
# =============================================================================

class TimeBlockPlanner(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Time Block Planner")
        self.setMinimumSize(950, 700)
        self.resize(1150, 800)
        
        self._setup_ui()
        self._load_data()
        self._connect_signals()
    
    def _setup_ui(self):
        self.setStyleSheet(f"background: {COLORS['bg_dark']};")
        
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Main content
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background: {COLORS['border']};
                width: 1px;
            }}
        """)
        
        self.task_list = TaskListPanel()
        splitter.addWidget(self.task_list)
        
        self.time_blocks = TimeBlockPanel()
        splitter.addWidget(self.time_blocks)
        
        splitter.setSizes([300, 850])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        
        layout.addWidget(splitter)
        
        # Status bar
        status = QFrame()
        status.setFixedHeight(40)
        status.setStyleSheet(f"""
            background: {COLORS['bg_panel']};
            border-top: 1px solid {COLORS['border']};
        """)
        
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(20, 0, 20, 0)
        
        self.status_label = QLabel("Drag tasks to schedule")
        self.status_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        status_layout.addWidget(self.status_label)
        
        status_layout.addStretch()
        
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
        status_layout.addWidget(self.stats_label)
        
        layout.addWidget(status)
    
    def _load_data(self):
        for task in DUMMY_TASKS:
            self.task_list.add_task(task)
        self._update_stats()
    
    def _connect_signals(self):
        self.time_blocks.schedule_changed.connect(self._on_schedule_changed)
        self.time_blocks.task_unscheduled.connect(self._on_task_unscheduled)
    
    def _on_schedule_changed(self):
        scheduled = self.time_blocks.get_schedule()
        scheduled_ids = {t['id'] for t in scheduled.values() if not t.get('is_temp')}
        
        for task_id in list(self.task_list.task_cards.keys()):
            if task_id in scheduled_ids:
                self.task_list.remove_task(task_id)
        
        self._update_stats()
    
    def _on_task_unscheduled(self, task_data):
        if not task_data.get('is_temp'):
            self.task_list.add_task(task_data)
        self._update_stats()
    
    def _update_stats(self):
        scheduled = self.time_blocks.get_schedule()
        hours = sum(t.get('budget_hours', 1) for t in scheduled.values())
        tasks = len([t for t in scheduled.values() if not t.get('is_temp')])
        blocks = len([t for t in scheduled.values() if t.get('is_temp')])
        completed = len([idx for idx, t in scheduled.items() 
                        if idx in self.time_blocks.spanning_tasks 
                        and self.time_blocks.spanning_tasks[idx].is_completed])
        
        parts = []
        if tasks:
            parts.append(f"{tasks} tasks")
        if blocks:
            parts.append(f"{blocks} blocks")
        parts.append(f"{hours:.1f}h scheduled")
        if completed:
            parts.append(f"{completed} done")
        
        remaining = 8.0 - hours
        if remaining > 0:
            parts.append(f"{remaining:.1f}h free")
        
        self.stats_label.setText("  •  ".join(parts))


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set application-wide font
    font = app.font()
    font.setFamily("Segoe UI, SF Pro Display, -apple-system, sans-serif")
    app.setFont(font)
    
    window = TimeBlockPlanner()
    window.show()
    sys.exit(app.exec())