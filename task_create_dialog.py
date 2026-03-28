"""
Task creation dialog with quick-fill presets.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QComboBox,
    QFrame,
    QWidget,
)
from PyQt6.QtCore import Qt

import http_help
from flow_layout import FlowLayout


TITLE_PRESETS = [
    "Match move",
    "Camera match",
    "Split plate",
    "Set up comp",
    "Rod paint",
    "Paint",
    "Roto",
    "Comp",
    "Track",
]

NOTES_PRESETS = [
    "idk",

]

BUDGET_PRESETS = [0.1, 0.25, 0.5, 1, 2.5, 3, 5, 10]
PRIORITY_PRESETS = [1, 5, 8, 10]

PRESET_PALETTES = {
    "title": ["#315B7A", "#2F6B5B", "#6B5B2F", "#6B3F2F", "#2F4F6B", "#4F6B2F"],
    "notes": ["#2F3B4A", "#3B2F4A", "#4A3B2F", "#2F4A3B", "#4A2F3B", "#3B4A2F"],
    "budget": ["#2F3542", "#36414A", "#3B4550", "#31414E"],
    "priority": ["#4A3B4F", "#4F3B3B", "#3B4F3B", "#3B3B4F"],
    "status": ["#3A4F6A", "#4F6A3A", "#6A4F3A", "#6A3A4F"],
}

STATUS_COLORS = {
    "unassigned": "#4a4a4a",
    "assigned": "#3d5a80",
    "not_started": "#5a4a6a",
    "in_progress": "#4a6a8a",
    "waiting_for_approval": "#7a6a3a",
    "approved": "#3a6a4a",
    "done": "#2a5a3a",
    "rejected": "#6a3a3a",
}


class TaskCreateDialog(QDialog):
    def __init__(
        self,
        parent=None,
        api: Optional[http_help.DjangoAPI] = None,
        default_artist_id: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("task_create_dialog")
        self.setWindowTitle("Create Task")

        self._api = api or http_help.DjangoAPI()
        self._selected_artist_id = default_artist_id

        self._build_ui()
        self._populate_artists()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        grid_container = QWidget()
        grid_layout = QGridLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(10)
        grid_layout.setVerticalSpacing(8)

        # Title
        self.title_edit = QLineEdit()
        title_group = self._build_field_group(
            "Title",
            self.title_edit,
            TITLE_PRESETS,
            self._set_title,
            "title",
        )
        grid_layout.addLayout(title_group, 0, 0)

        # Notes
        self.notes_edit = QLineEdit()
        notes_group = self._build_field_group(
            "Notes",
            self.notes_edit,
            NOTES_PRESETS,
            self._set_notes,
            "notes",
        )
        grid_layout.addLayout(notes_group, 0, 1)

        # Budget
        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setRange(0, 1000)
        self.budget_spin.setDecimals(2)
        self.budget_spin.setSingleStep(0.25)
        self.budget_spin.setSuffix(" h")
        budget_group = self._build_field_group(
            "Budget Hours",
            self.budget_spin,
            [f"{v:g}" for v in BUDGET_PRESETS],
            self._set_budget_from_text,
            "budget",
        )
        grid_layout.addLayout(budget_group, 1, 0)

        # Priority
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, 10)
        self.priority_spin.setValue(5)
        priority_group = self._build_field_group(
            "Priority",
            self.priority_spin,
            [str(v) for v in PRIORITY_PRESETS],
            self._set_priority_from_text,
            "priority",
        )
        grid_layout.addLayout(priority_group, 1, 1)

        layout.addWidget(grid_container)

        divider = QFrame()
        divider.setObjectName("task_dialog_divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        # Status
        self.status_combo = QComboBox()
        for value, label in http_help.TASK_STATUS_CHOICES:
            self.status_combo.addItem(label, value)
        self._add_field(
            layout,
            "Status",
            self.status_combo,
            [label for _, label in http_help.TASK_STATUS_CHOICES],
            self._set_status_from_label,
            "status",
            add_divider=False,
        )

        # Artist
        artist_row = QVBoxLayout()
        artist_row.setSpacing(4)
        artist_label = QLabel("Artist")
        artist_label.setObjectName("task_field_label")
        artist_row.addWidget(artist_label)

        self.artist_value_label = QLabel("Unassigned")
        self.artist_value_label.setObjectName("task_artist_value")
        artist_row.addWidget(self.artist_value_label)

        self.artist_button_container = QWidget()
        self.artist_flow = FlowLayout(self.artist_button_container, margin=0, h_spacing=6, v_spacing=6)
        artist_row.addWidget(self.artist_button_container)
        layout.addLayout(artist_row)

        # Buttons
        buttons_row = QHBoxLayout()
        buttons_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons_row.addWidget(cancel_btn)
        create_btn = QPushButton("Create")
        create_btn.clicked.connect(self.accept)
        buttons_row.addWidget(create_btn)
        layout.addLayout(buttons_row)

    def _add_field(
        self,
        parent_layout: QVBoxLayout,
        label: str,
        widget: QWidget,
        presets: Iterable[str],
        handler,
        kind: str,
        add_divider: bool = True,
    ) -> None:
        group = self._build_field_group(label, widget, presets, handler, kind)
        parent_layout.addLayout(group)

        if add_divider:
            divider = QFrame()
            divider.setObjectName("task_dialog_divider")
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setFrameShadow(QFrame.Shadow.Sunken)
            parent_layout.addWidget(divider)

    def _build_field_group(
        self,
        label: str,
        widget: QWidget,
        presets: Iterable[str],
        handler,
        kind: str,
    ) -> QVBoxLayout:
        group = QVBoxLayout()
        group.setSpacing(4)
        title = QLabel(label)
        title.setObjectName("task_field_label")
        group.addWidget(title)
        group.addWidget(widget)

        if presets:
            preset_container = QWidget()
            preset_flow = FlowLayout(preset_container, margin=0, h_spacing=4, v_spacing=4)
            for index, preset in enumerate(presets):
                btn = QPushButton(preset)
                btn.setObjectName("task_preset_button")
                btn.setStyleSheet(self._preset_style(kind, preset, index))
                btn.clicked.connect(lambda checked=False, p=preset: handler(p))
                preset_flow.addWidget(btn)
            group.addWidget(preset_container)

        return group

    def _preset_style(self, kind: str, preset: str, index: int) -> str:
        if kind == "status":
            status_value = None
            for value, label in http_help.TASK_STATUS_CHOICES:
                if label.lower() == preset.lower():
                    status_value = value
                    break
            if status_value and status_value in STATUS_COLORS:
                color = STATUS_COLORS[status_value]
            else:
                palette = PRESET_PALETTES.get(kind, PRESET_PALETTES["title"])
                color = palette[index % len(palette)]
        else:
            palette = PRESET_PALETTES.get(kind, PRESET_PALETTES["title"])
            color = palette[index % len(palette)]

        return (
            "QPushButton#task_preset_button {"
            f"background-color: {color};"
            "}"
            "QPushButton#task_preset_button:hover {"
            "border-color: #FF6B35;"
            "color: #FFFFFF;"
            "}"
        )

    def _populate_artists(self) -> None:
        for i in reversed(range(self.artist_flow.count())):
            item = self.artist_flow.takeAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        colors = ["#4a7ba7", "#8B6FA3", "#678542", "#B28659", "#99604C", "#6FA8DC"]

        unassigned_btn = QPushButton("Unassigned")
        unassigned_btn.setObjectName("task_artist_button")
        unassigned_btn.clicked.connect(lambda: self._set_artist(None, "Unassigned"))
        unassigned_btn.setStyleSheet("background-color: #2F3542; color: #D4D4D4;")
        self.artist_flow.addWidget(unassigned_btn)

        try:
            users = self._api.get_users()
        except Exception:
            users = []

        for idx, user in enumerate(users):
            user_id = user.get("id")
            username = user.get("first_name") or user.get("username") or f"User {user_id}"
            color = colors[idx % len(colors)]
            btn = QPushButton(username)
            btn.setObjectName("task_artist_button")
            btn.clicked.connect(lambda checked=False, uid=user_id, name=username: self._set_artist(uid, name))
            btn.setStyleSheet(f"background-color: {color}; color: #FFFFFF;")
            self.artist_flow.addWidget(btn)

        if self._selected_artist_id:
            for user in users:
                if user.get("id") == self._selected_artist_id:
                    name = user.get("first_name") or user.get("username") or f"User {self._selected_artist_id}"
                    self.artist_value_label.setText(name)
                    break

    def _set_title(self, preset: str) -> None:
        self.title_edit.setText(preset)

    def _set_notes(self, preset: str) -> None:
        self.notes_edit.setText(preset)

    def _set_budget_from_text(self, preset: str) -> None:
        try:
            self.budget_spin.setValue(float(preset))
        except Exception:
            pass

    def _set_priority_from_text(self, preset: str) -> None:
        try:
            self.priority_spin.setValue(int(preset))
        except Exception:
            pass

    def _set_status_from_label(self, label: str) -> None:
        for index in range(self.status_combo.count()):
            if self.status_combo.itemText(index).lower() == label.lower():
                self.status_combo.setCurrentIndex(index)
                break

    def _set_artist(self, artist_id: Optional[int], label: str) -> None:
        self._selected_artist_id = artist_id
        self.artist_value_label.setText(label)

    def get_values(self) -> dict:
        title = self.title_edit.text().strip() or "New Task"
        notes = self.notes_edit.text().strip()
        status = self.status_combo.currentData()
        priority = int(self.priority_spin.value())
        budget_hours = float(self.budget_spin.value())
        return {
            "title": title,
            "notes": notes,
            "status": status,
            "priority": priority,
            "budget_hours": budget_hours,
            "artist": self._selected_artist_id,
        }
