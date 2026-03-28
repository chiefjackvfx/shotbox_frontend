from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt


def setup_task_widget_ui(widget):
    """
    Set up the TaskWidget UI directly on the given widget.
    This mimics uic.loadUi() behavior: all widgets become attributes of 'widget'.
    """
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
