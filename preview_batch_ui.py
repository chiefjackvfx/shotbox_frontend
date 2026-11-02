"""Clip selection and destination for batch MP4 previews."""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout)
from deadline_ui import DeadlineOptions
from input_behaviour import install_input_wheel_guard


def choose_preview_batch(parent, tasks, *, overwrite=False):
    from filesIO import Folders
    from nuke_headless_tasks import list_existing_preview_paths
    files_io = Folders()
    install_input_wheel_guard(QApplication.instance())
    dialog = QDialog(parent)
    dialog.setWindowTitle("Batch MP4s — Select clips")
    dialog.resize(640, 600)
    layout = QVBoxLayout(dialog)
    instruction = QLabel("Clips missing matching MP4 previews are selected automatically.\nChange the ticks to choose which clips to include.", dialog)
    instruction.setWordWrap(True)
    layout.addWidget(instruction)
    clips = QListWidget(dialog)
    clips.setAccessibleName("Clips to render as MP4")
    for task in tasks:
        shot = task['shot'].get('title') or 'Untitled shot'
        plate = task.get('plate_name') or task['source_type'].replace('_', ' ')
        item = QListWidgetItem(f"{shot} · {plate} · v{task['version']:03d} · {Path(task['source_path']).name}", clips)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        existing = False
        try:
            base = task['shot'].get('base_path')
            if base:
                shot_dir = files_io.convert_path(base)
                matches = list_existing_preview_paths(
                    shot_dir, shot, task['version'], plate_name=task.get('plate_name'),
                    include_plate_previews=bool(task.get('plate_name')))
                if task['source_type'] == 'original_clip' and not task['shot'].get('original_clips'):
                    # Legacy single-source previews did not have a plate suffix.
                    matches += list_existing_preview_paths(
                        shot_dir, shot, task['version'], include_plate_previews=False)
                existing = any(path.is_file() for path in matches)
            status = "Preview exists" if existing else "Missing preview"
        except OSError:
            status = "Could not check preview; selected for review"
        item.setCheckState(Qt.CheckState.Unchecked if existing else Qt.CheckState.Checked)
        item.setToolTip(f"{task['source_path']}\n{status}")
    layout.addWidget(clips)
    overwrite_box = QCheckBox("Overwrite existing MP4s", dialog)
    overwrite_box.setChecked(overwrite)
    overwrite_box.setToolTip("Replace existing files for checked clips, locally or on Deadline.")
    layout.addWidget(overwrite_box)
    controls = QHBoxLayout()
    def set_all(state):
        for i in range(clips.count()):
            clips.item(i).setCheckState(state)
    for text, state in (("Select all", Qt.CheckState.Checked), ("Select none", Qt.CheckState.Unchecked)):
        button = QPushButton(text, dialog)
        button.clicked.connect(lambda checked=False, state=state: set_all(state))
        controls.addWidget(button)
    controls.addStretch()
    layout.addLayout(controls)
    summary = QLabel(dialog)
    layout.addWidget(summary)
    destination = QComboBox(dialog)
    destination.addItems(["Render locally", "Add to Deadline"])
    destination.setAccessibleName("MP4 render destination")
    layout.addWidget(destination)
    options = DeadlineOptions(dialog)
    # A movie must be written by a single task, never split across workers.
    options.chunked.setChecked(False)
    options.chunked.hide()
    options.chunk_size.hide()
    options.layout().labelForField(options.chunk_size).hide()
    options.status.setText("Prepare MP4 scenes locally and queue complete movies in Deadline.\nManage submitted jobs in Deadline Monitor.")
    layout.addWidget(options)
    options.hide()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
    start = buttons.button(QDialogButtonBox.StandardButton.Ok)
    layout.addWidget(buttons)
    def selected():
        return [task for i, task in enumerate(tasks) if clips.item(i).checkState() == Qt.CheckState.Checked]
    def update():
        count = len(selected())
        summary.setText(f"Selected: {count} of {len(tasks)} clips")
        start.setEnabled(count > 0)
        start.setText("Add to Deadline" if destination.currentIndex() else "Render locally")
    clips.itemChanged.connect(update)
    destination.currentIndexChanged.connect(lambda index: options.setVisible(index == 1))
    destination.currentIndexChanged.connect(update)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    update()
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted or not selected():
            return None
        return {'tasks': selected(), 'deadline': options.options(persist_chunking=False) if destination.currentIndex() else None,
                'overwrite': overwrite_box.isChecked()}
    finally:
        options.stop()
        dialog.deleteLater()
