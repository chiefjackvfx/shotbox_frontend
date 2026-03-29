from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import matchmove_helpers as shared_matchmove


@dataclass(frozen=True)
class TimelineMatchmoveCandidate:
    shot_id: int | None
    shot_title: str
    shot_root: str
    sequence_info: shared_matchmove.SequenceInfo


class TimelineMatchmoveCandidateRow(QFrame):
    def __init__(self, candidate: TimelineMatchmoveCandidate, parent=None):
        super().__init__(parent)
        self.candidate = candidate

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        self.check_box = QCheckBox(self)
        layout.addWidget(self.check_box)

        self.label_shot = QLabel(candidate.shot_title, self)
        self.label_shot.setMinimumWidth(120)
        layout.addWidget(self.label_shot)

        self.label_folder = QLabel(Path(candidate.sequence_info.folder_path).name, self)
        self.label_folder.setMinimumWidth(120)
        layout.addWidget(self.label_folder)

        summary = (
            f"{candidate.sequence_info.display_pattern} | "
            f"{candidate.sequence_info.first_frame}-{candidate.sequence_info.last_frame} | "
            f"{candidate.sequence_info.width}x{candidate.sequence_info.height}"
        )
        self.label_summary = QLabel(summary, self)
        self.label_summary.setWordWrap(True)
        layout.addWidget(self.label_summary, 1)

        self.label_focal = QLabel("Focal (mm)", self)
        layout.addWidget(self.label_focal)

        self.focal_spin_box = QDoubleSpinBox(self)
        self.focal_spin_box.setRange(1.0, 999.0)
        self.focal_spin_box.setDecimals(2)
        self.focal_spin_box.setValue(shared_matchmove.DEFAULT_FOCAL_LENGTH_MM)
        self.focal_spin_box.setEnabled(False)
        layout.addWidget(self.focal_spin_box)

        self.check_box.toggled.connect(self._on_checked_changed)

    def _on_checked_changed(self, checked: bool) -> None:
        self.focal_spin_box.setEnabled(bool(checked))

    def is_selected(self) -> bool:
        return self.check_box.isChecked()

    def focal_length_mm(self) -> float:
        return float(self.focal_spin_box.value())


class TimelineMatchmoveDialog(QDialog):
    def __init__(
        self,
        timeline_title: str,
        candidates: list[TimelineMatchmoveCandidate],
        matchmove_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._timeline_title = str(timeline_title or "").strip() or "Timeline"
        self._matchmove_dir = shared_matchmove.normalize_user_path(matchmove_dir)
        self._candidates = sorted(
            list(candidates or []),
            key=lambda candidate: (
                candidate.shot_title.lower(),
                Path(candidate.sequence_info.folder_path).name.lower(),
            ),
        )
        self._candidate_rows: list[TimelineMatchmoveCandidateRow] = []
        self._group_labels: list[QLabel] = []
        self._built_request: shared_matchmove.MatchmoveProjectRequest | None = None

        self.setWindowTitle("Create Timeline Matchmove")
        self.resize(1040, 780)
        self._build_ui()
        self._update_create_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "Select one or more EXR precomp sequences from the active timeline and build a multi-clip 3DE project."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        details = QLabel(
            f"Output folder: {self._matchmove_dir}\n"
            "Each selected sequence becomes its own 3DE camera. Camera preset, FPS, and frame override are shared."
        )
        details.setWordWrap(True)
        details.setStyleSheet("color: #9aa;")
        layout.addWidget(details)

        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("Project name:"))
        self.project_name_edit = QLineEdit(self._timeline_title, self)
        self.project_name_edit.textChanged.connect(self._update_create_state)
        project_row.addWidget(self.project_name_edit)
        layout.addLayout(project_row)

        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel("Camera preset:"))
        self.camera_combo_box = QComboBox(self)
        self.camera_combo_box.addItems(shared_matchmove.CAMERA_PRESETS.keys())
        self.camera_combo_box.setCurrentText("Alexa 35")
        camera_row.addWidget(self.camera_combo_box)

        camera_row.addWidget(QLabel("FPS:"))
        self.fps_spin_box = QDoubleSpinBox(self)
        self.fps_spin_box.setRange(1.0, 120.0)
        self.fps_spin_box.setDecimals(3)
        self.fps_spin_box.setValue(shared_matchmove.DEFAULT_FPS)
        camera_row.addWidget(self.fps_spin_box)
        camera_row.addStretch(1)
        layout.addLayout(camera_row)

        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel("Frame override start:"))
        self.frame_start_edit = QLineEdit(self)
        self.frame_start_edit.setValidator(QIntValidator())
        self.frame_start_edit.setPlaceholderText("Auto")
        frame_row.addWidget(self.frame_start_edit)

        frame_row.addWidget(QLabel("end:"))
        self.frame_end_edit = QLineEdit(self)
        self.frame_end_edit.setValidator(QIntValidator())
        self.frame_end_edit.setPlaceholderText("Auto")
        frame_row.addWidget(self.frame_end_edit)
        frame_row.addStretch(1)
        layout.addLayout(frame_row)

        self.validation_label = QLabel(self)
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.StyledPanel)
        layout.addWidget(scroll_area, 1)

        scroll_contents = QWidget(scroll_area)
        scroll_layout = QVBoxLayout(scroll_contents)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(8)

        current_shot_title = None
        for candidate in self._candidates:
            if candidate.shot_title != current_shot_title:
                current_shot_title = candidate.shot_title
                shot_label = QLabel(current_shot_title, scroll_contents)
                shot_label.setStyleSheet("font-weight: bold; color: #ddd; margin-top: 6px;")
                self._group_labels.append(shot_label)
                scroll_layout.addWidget(shot_label)

            row = TimelineMatchmoveCandidateRow(candidate, scroll_contents)
            row.check_box.toggled.connect(self._update_create_state)
            self._candidate_rows.append(row)
            scroll_layout.addWidget(row)

        scroll_layout.addStretch(1)
        scroll_area.setWidget(scroll_contents)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.create_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.create_button.setText("Create 3DE Project")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_rows(self) -> list[TimelineMatchmoveCandidateRow]:
        return [row for row in self._candidate_rows if row.is_selected()]

    def _validated_project_name(self) -> str:
        return shared_matchmove.validate_shot_name(self.project_name_edit.text())

    def _update_create_state(self) -> None:
        selected_count = len(self._selected_rows())
        try:
            self._validated_project_name()
        except Exception as exc:
            self.validation_label.setText(str(exc))
            self.validation_label.setStyleSheet("color: #f99;")
            self.create_button.setEnabled(False)
            return

        if selected_count <= 0:
            self.validation_label.setText("Select at least one EXR sequence.")
            self.validation_label.setStyleSheet("color: #f99;")
            self.create_button.setEnabled(False)
            return

        self.validation_label.setText(f"{selected_count} clip(s) selected.")
        self.validation_label.setStyleSheet("color: #9aa;")
        self.create_button.setEnabled(True)

    def build_request(self) -> shared_matchmove.MatchmoveProjectRequest:
        project_name = self._validated_project_name()
        selected_rows = self._selected_rows()
        if not selected_rows:
            raise ValueError("Select at least one EXR sequence.")

        project_dir, export_dir, project_path, version = shared_matchmove.resolve_project_path(
            self._matchmove_dir,
            project_name,
        )

        clip_requests: list[shared_matchmove.MatchmoveClipRequest] = []
        used_camera_names: set[str] = set()
        for slot_index, row in enumerate(selected_rows):
            sequence_info = row.candidate.sequence_info
            sequence_start_frame, sequence_end_frame = shared_matchmove.resolve_requested_frame_range(
                sequence_info,
                self.frame_start_edit.text(),
                self.frame_end_edit.text(),
            )
            focal_length_mm = row.focal_length_mm()
            clip_requests.append(
                shared_matchmove.MatchmoveClipRequest(
                    slot_index=slot_index,
                    clip_name=Path(sequence_info.folder_path).name,
                    camera_name=shared_matchmove.build_unique_camera_name(
                        project_name,
                        sequence_info.folder_path,
                        slot_index,
                        used_camera_names,
                    ),
                    lens_name=shared_matchmove.format_focal_length_label(focal_length_mm),
                    sequence_info=sequence_info,
                    focal_length_mm=focal_length_mm,
                    sequence_start_frame=sequence_start_frame,
                    sequence_end_frame=sequence_end_frame,
                )
            )

        return shared_matchmove.MatchmoveProjectRequest(
            project_name=project_name,
            shot_name=project_name,
            clips=tuple(clip_requests),
            camera_preset_name=self.camera_combo_box.currentText().strip(),
            matchmove_dir=self._matchmove_dir,
            export_dir=export_dir,
            fps=float(self.fps_spin_box.value()),
            project_dir=project_dir,
            project_path=project_path,
            version=version,
        )

    @property
    def built_request(self) -> shared_matchmove.MatchmoveProjectRequest | None:
        return self._built_request

    def accept(self) -> None:
        try:
            self._built_request = self.build_request()
        except Exception as exc:
            QMessageBox.warning(self, "Create Matchmove", str(exc))
            return
        super().accept()
