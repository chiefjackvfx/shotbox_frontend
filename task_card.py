from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QCheckBox, QLineEdit
)
from PyQt6.QtCore import Qt


def _setup_task_card_ui(widget):
    # Set size constraints for compact design
    widget.setMinimumWidth(160)
    widget.setMaximumWidth(240)

    # Outer layout with no margins (frame will have the visual styling)
    outer_layout = QVBoxLayout(widget)
    widget.outer_layout = outer_layout
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_layout.setSpacing(0)

    # Main frame that gets the border/hover styling
    widget.task_frame = QFrame(widget)
    widget.task_frame.setObjectName("task_frame")
    widget.task_frame.setFrameShape(QFrame.Shape.StyledPanel)
    widget.task_frame.setFrameShadow(QFrame.Shadow.Raised)

    outer_layout.addWidget(widget.task_frame)

    # Main layout inside the frame
    layout = QVBoxLayout(widget.task_frame)
    widget.task_layout = layout
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(4)

    # === DRAG HANDLE (optional; shown in assignment board) ===
    widget.drag_handle = QFrame(widget.task_frame)
    widget.drag_handle.setObjectName("task_drag_handle")
    widget.drag_handle.setMinimumHeight(14)

    drag_layout = QHBoxLayout(widget.drag_handle)
    drag_layout.setContentsMargins(4, 2, 4, 2)
    drag_layout.setSpacing(4)

    widget.drag_label = QLabel("drag", widget.drag_handle)
    widget.drag_label.setObjectName("task_drag_label")
    drag_layout.addWidget(widget.drag_label)
    drag_layout.addStretch()

    # Let parent handle mouse events for dragging
    widget.drag_handle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    widget.drag_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    layout.addWidget(widget.drag_handle)

    # === TOP ROW: Title + Hide + Delete ===
    top_row = QHBoxLayout()
    widget.top_row = top_row
    top_row.setSpacing(2)

    # Task title button (editable)
    widget.btn_task_title = QPushButton("Task Name", widget.task_frame)
    widget.btn_task_title.setObjectName("btn_task_title")
    widget.btn_task_title.setFlat(True)
    widget.btn_task_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    top_row.addWidget(widget.btn_task_title, 1)

    # Hide task button
    widget.btn_hide_task = QPushButton("◉", widget.task_frame)
    widget.btn_hide_task.setObjectName("btn_hide_task")
    widget.btn_hide_task.setFixedSize(30, 20)
    widget.btn_hide_task.setToolTip("Hide/Unhide task")
    top_row.addWidget(widget.btn_hide_task)

    # Delete button
    widget.btn_delete_task = QPushButton("×", widget.task_frame)
    widget.btn_delete_task.setObjectName("btn_delete_task")
    widget.btn_delete_task.setFixedSize(20, 20)
    widget.btn_delete_task.setToolTip("Delete task")
    top_row.addWidget(widget.btn_delete_task)

    layout.addLayout(top_row)

    # === MIDDLE ROW: Status + Assigned ===
    middle_row = QHBoxLayout()
    widget.middle_row = middle_row
    middle_row.setSpacing(4)

    widget.btn_status = QPushButton("Status", widget.task_frame)
    widget.btn_status.setObjectName("btn_status")
    widget.btn_status.setMinimumWidth(70)
    widget.btn_status.setProperty("status", "unassigned")  # used by QSS
    middle_row.addWidget(widget.btn_status)

    widget.btn_assigned = QPushButton("Unassigned", widget.task_frame)
    widget.btn_assigned.setObjectName("btn_assigned")
    widget.btn_assigned.setMinimumWidth(70)
    middle_row.addWidget(widget.btn_assigned)

    middle_row.addStretch()
    layout.addLayout(middle_row)

    # === PLANNING ROW: Priority + Budget (hours) ===
    planning_row = QHBoxLayout()
    widget.planning_row = planning_row
    planning_row.setSpacing(4)

    widget.btn_priority = QPushButton("P5", widget.task_frame)
    widget.btn_priority.setObjectName("btn_priority")

    widget.btn_priority.setToolTip("Priority (1–10)")
    planning_row.addWidget(widget.btn_priority)

    widget.btn_budget_hours = QPushButton("0.0h", widget.task_frame)
    widget.btn_budget_hours.setObjectName("btn_budget_hours")
    widget.btn_budget_hours.setToolTip("Budget (hours)")
    planning_row.addWidget(widget.btn_budget_hours)


    planning_row.addStretch()
    layout.addLayout(planning_row)

    # === BOTTOM ROW: Notes ===
    notes_row = QHBoxLayout()
    widget.notes_row = notes_row
    notes_row.setSpacing(2)

    widget.btn_notes = QPushButton("No notes", widget.task_frame)
    widget.btn_notes.setObjectName("btn_notes")
    widget.btn_notes.setFlat(True)
    widget.btn_notes.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    notes_row.addWidget(widget.btn_notes, 1)

    layout.addLayout(notes_row)


def _setup_task_checklist_ui(widget):
    widget.setMinimumWidth(0)
    widget.setMaximumWidth(16777215)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    outer_layout = QVBoxLayout(widget)
    widget.outer_layout = outer_layout
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_layout.setSpacing(0)

    widget.task_frame = QFrame(widget)
    widget.task_frame.setObjectName("task_frame")
    widget.task_frame.setFrameShape(QFrame.Shape.NoFrame)
    widget.task_frame.setFrameShadow(QFrame.Shadow.Plain)
    outer_layout.addWidget(widget.task_frame)

    layout = QHBoxLayout(widget.task_frame)
    widget.task_layout = layout
    layout.setContentsMargins(8, 5, 8, 5)
    layout.setSpacing(6)

    widget.check_done_task = QCheckBox("", widget.task_frame)
    widget.check_done_task.setObjectName("task_done_toggle")
    widget.check_done_task.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.check_done_task.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    layout.addWidget(widget.check_done_task)

    widget.btn_task_title = QPushButton("Task Name", widget.task_frame)
    widget.btn_task_title.setObjectName("btn_task_title")
    widget.btn_task_title.setFlat(True)
    widget.btn_task_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout.addWidget(widget.btn_task_title, 1)

    widget.edit_notes_inline = QLineEdit(widget.task_frame)
    widget.edit_notes_inline.setObjectName("edit_task_notes_inline")
    widget.edit_notes_inline.setPlaceholderText("Add note...")
    widget.edit_notes_inline.setClearButtonEnabled(True)
    widget.edit_notes_inline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout.addWidget(widget.edit_notes_inline, 2)

    widget.btn_status = QPushButton("Status", widget.task_frame)
    widget.btn_status.setObjectName("btn_status")
    widget.btn_status.setProperty("status", "unassigned")
    widget.btn_status.setMinimumWidth(64)
    layout.addWidget(widget.btn_status)

    widget.btn_assigned = QPushButton("Unassigned", widget.task_frame)
    widget.btn_assigned.setObjectName("btn_assigned")
    widget.btn_assigned.setMinimumWidth(70)
    layout.addWidget(widget.btn_assigned)

    widget.btn_priority = QPushButton("P5", widget.task_frame)
    widget.btn_priority.setObjectName("btn_priority")
    widget.btn_priority.setToolTip("Priority (1–10)")
    layout.addWidget(widget.btn_priority)

    widget.btn_budget_hours = QPushButton("0.0h", widget.task_frame)
    widget.btn_budget_hours.setObjectName("btn_budget_hours")
    widget.btn_budget_hours.setToolTip("Budget (hours)")
    layout.addWidget(widget.btn_budget_hours)

    widget.btn_notes = None

    widget.btn_hide_task = QPushButton("◉", widget.task_frame)
    widget.btn_hide_task.setObjectName("btn_hide_task")
    widget.btn_hide_task.setFixedSize(18, 18)
    widget.btn_hide_task.setToolTip("Hide/Unhide task")
    layout.addWidget(widget.btn_hide_task)

    widget.btn_delete_task = QPushButton("×", widget.task_frame)
    widget.btn_delete_task.setObjectName("btn_delete_task")
    widget.btn_delete_task.setFixedSize(18, 18)
    widget.btn_delete_task.setToolTip("Delete task")
    layout.addWidget(widget.btn_delete_task)

    widget.drag_handle = None
    widget.drag_label = None
    widget.top_row = None
    widget.middle_row = None
    widget.planning_row = None
    widget.notes_row = None


def setup_task_widget_ui(widget, presentation="card"):
    """
    Set up the TaskWidget UI directly on the given widget.
    This mimics uic.loadUi() behavior: all widgets become attributes of 'widget'.
    """
    if str(presentation).lower() == "checklist":
        _setup_task_checklist_ui(widget)
        return
    _setup_task_card_ui(widget)
