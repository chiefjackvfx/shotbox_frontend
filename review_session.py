"""Shared-screen session controls for Review."""

from PyQt6.QtCore import Qt, pyqtSignal
from readable_combo_box import ReadableComboBox
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class ReviewSessionBar(QWidget):
    shot_selected = pyqtSignal(int)
    previous_shot = pyqtSignal()
    next_shot = pyqtSignal()
    present_toggled = pyqtSignal(bool)
    tools_toggled = pyqtSignal(bool)
    play_all_toggled = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("review_session_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(5)
        shots = QHBoxLayout()
        shots.setSpacing(6)
        shots.addWidget(QLabel("Review:"))
        self.combo_shot = ReadableComboBox()
        self.combo_shot.setMinimumWidth(220)
        self.combo_shot.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_shot.setMinimumContentsLength(22)
        self.combo_shot.currentIndexChanged.connect(self.shot_selected.emit)
        shots.addWidget(self.combo_shot, 1)
        self.button_previous = self._button("Previous", "Previous shot (Page Up)", self.previous_shot.emit)
        self.button_next = self._button("Next", "Next shot (Page Down)", self.next_shot.emit)
        shots.addWidget(self.button_previous)
        shots.addWidget(self.button_next)
        self.button_play_all = QPushButton("Play Timeline")
        self.button_play_all.setCheckable(True)
        self.button_play_all.setToolTip("Play from this shot through the timeline; stop at a shot with missing media")
        self.button_play_all.toggled.connect(self.play_all_toggled.emit)
        shots.addWidget(self.button_play_all)
        self.button_tools = QPushButton("Drawing")
        self.button_tools.setCheckable(True)
        self.button_tools.setChecked(True)
        self.button_tools.setToolTip("Show drawing tools; drawing pauses playback")
        self.button_tools.toggled.connect(self.tools_toggled.emit)
        shots.addWidget(self.button_tools)
        self.button_present = QPushButton("Present")
        self.button_present.setCheckable(True)
        self.button_present.setToolTip("Full-screen group review (F11); Escape returns to Shotbox")
        self.button_present.toggled.connect(self.present_toggled.emit)
        shots.addWidget(self.button_present)
        layout.addLayout(shots)

        self.set_shots([], 0)

    @staticmethod
    def _button(text, tooltip, callback):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setFixedHeight(28)
        button.clicked.connect(callback)
        return button

    def set_shots(self, shots, current_index):
        self.combo_shot.blockSignals(True)
        self.combo_shot.clear()
        for index, shot in enumerate(shots):
            self.combo_shot.addItem(f"{index + 1}/{len(shots)}  {shot.get('title') or 'Shot'}", index)
        self.combo_shot.setCurrentIndex(current_index)
        self.combo_shot.blockSignals(False)
        self.combo_shot.setEnabled(bool(shots))
        self.button_previous.setEnabled(current_index > 0)
        self.button_next.setEnabled(current_index + 1 < len(shots))
        self.button_play_all.setEnabled(bool(shots))


class ReviewPresentationWindow(QDialog):
    closing = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("review_page")
        self.setWindowTitle("Shotbox — Review")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def reject(self):
        self.close()

    def closeEvent(self, event):
        self.closing.emit()
        super().closeEvent(event)
