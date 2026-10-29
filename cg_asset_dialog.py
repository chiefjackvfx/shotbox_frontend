"""Create a CG asset without blocking the desktop during Houdini work."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import uuid

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import app_cache
import cg_asset_helpers as helpers
from input_behaviour import install_input_wheel_guard


class CgAssetDialog(QDialog):
    def __init__(self, cg_root: Path, sources: list[Path], configured: str,
                 work_dir: Path, parent=None, *, context_title=""):
        super().__init__(parent)
        install_input_wheel_guard(QApplication.instance())
        self.cg_root = Path(cg_root)
        self.configured = configured
        self.work_dir = Path(work_dir)
        self.project_path = None
        self._prepared = None
        self._signature = None
        self._busy = False
        self._cancelled = False
        self._close_after_cancel = False
        self._logs = ""
        self._mode = None
        self.setWindowTitle(f"Create CG Asset — {context_title}" if context_title else "Create CG Asset")
        self.resize(780, 650)
        layout = QVBoxLayout(self)
        self.inputs = QWidget(self)
        fields = QVBoxLayout(self.inputs)
        fields.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Asset name:", self.name_edit)
        self.destination_label = QLabel()
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Destination:", self.destination_label)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setValue(25)
        form.addRow("FPS:", self.fps_spin)
        fields.addLayout(form)
        instructions = QLabel("Select 3DE Houdini Python exports, then Inspect Cameras to calculate duration. "
                              "You can also create an empty scene by entering its duration.")
        instructions.setWordWrap(True)
        fields.addWidget(instructions)
        self.camera_list = QListWidget()
        fields.addWidget(self.camera_list, 1)
        actions = QHBoxLayout()
        self.browse_button = QPushButton("Browse…")
        self.remove_button = QPushButton("Remove Selected")
        self.inspect_button = QPushButton("Inspect Cameras")
        for button in (self.browse_button, self.remove_button, self.inspect_button):
            actions.addWidget(button)
        fields.addLayout(actions)
        self.camera_summary = QLabel()
        self.camera_summary.setWordWrap(True)
        fields.addWidget(self.camera_summary)
        self.override_check = QCheckBox("Override camera duration")
        fields.addWidget(self.override_check)
        duration_form = QFormLayout()
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 10000000)
        self.duration_spin.setSpecialValueText("Enter duration")
        duration_form.addRow("Duration (frames):", self.duration_spin)
        self.range_label = QLabel()
        duration_form.addRow("Frame range:", self.range_label)
        fields.addLayout(duration_form)
        layout.addWidget(self.inputs, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = self.buttons.addButton("Create and Open", QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(self.buttons)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.name_edit.textChanged.connect(self._update)
        self.fps_spin.valueChanged.connect(self._invalidate)
        self.camera_list.itemChanged.connect(self._invalidate)
        self.duration_spin.valueChanged.connect(self._update)
        self.override_check.toggled.connect(self._override_changed)
        self.browse_button.clicked.connect(self._browse)
        self.remove_button.clicked.connect(self._remove)
        self.inspect_button.clicked.connect(self._inspect)
        self.buttons.accepted.connect(self._create)
        self.buttons.rejected.connect(self._cancel_or_close)
        for path in sources:
            self._add_source(path)
        self._update()

    def selected_sources(self):
        return [Path(self.camera_list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.camera_list.count())
                if self.camera_list.item(i).checkState() == Qt.CheckState.Checked]

    def _add_source(self, path, checked=False):
        path = Path(path).resolve()
        for index in range(self.camera_list.count()):
            item = self.camera_list.item(index)
            if Path(item.data(Qt.ItemDataRole.UserRole)) == path:
                if checked:
                    item.setCheckState(Qt.CheckState.Checked)
                return
        item = QListWidgetItem(f"{path.name}\n{path.parent}")
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.camera_list.addItem(item)

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select 3DE Houdini Exports",
                                               str(self.cg_root.parent), "Python Exports (*.py)")
        for path in paths:
            self._add_source(path, checked=True)
        if paths:
            self._invalidate()

    def _remove(self):
        for item in self.camera_list.selectedItems():
            self.camera_list.takeItem(self.camera_list.row(item))
        self._invalidate()

    def _invalidate(self, *args):
        self._prepared = None
        self._signature = None
        self._auto_duration = None
        self.camera_summary.clear()
        if not self.override_check.isChecked():
            self.duration_spin.setValue(0)
        self._update()

    def _override_changed(self, checked):
        if not checked:
            self.duration_spin.setValue(getattr(self, "_auto_duration", None) or 0)
        self._update()

    def _update(self, *args):
        sources = self.selected_sources()
        error = ""
        try:
            destination = helpers.asset_destination(self.cg_root, self.name_edit.text())
            self.destination_label.setText(str(destination))
            helpers.validate_new_destination(destination)
        except (ValueError, OSError) as exc:
            error = str(exc)
            self.destination_label.setText(str(self.cg_root / "<asset_name>"))
        duration = self.duration_spin.value()
        self.range_label.setText(f"1001 – {1001 + duration - 1}" if duration else "1001 – …")
        self.duration_spin.setEnabled(not sources or self.override_check.isChecked())
        self.override_check.setEnabled(bool(sources))
        self.inspect_button.setEnabled(bool(sources) and not self._busy)
        ready = not sources or self._prepared is not None
        self.create_button.setEnabled(not self._busy and not error and duration > 0 and ready)
        if not self._busy:
            self.status_label.setText(error or ("Inspect the selected cameras before creating." if not ready else ""))

    def _inspect(self):
        self._prepared = None
        try:
            self._pending_signature = helpers.source_signature(self.selected_sources())
            self._start("prepare", sources=[str(path) for path in self.selected_sources()])
        except Exception as exc:
            QMessageBox.warning(self, "Inspect Cameras", str(exc))
            self._update()

    def _create(self):
        try:
            name = helpers.validate_asset_name(self.name_edit.text())
            destination = helpers.asset_destination(self.cg_root, name)
            helpers.validate_new_destination(destination)
            sources = self.selected_sources()
            if sources and (not self._prepared or helpers.source_signature(sources) != self._signature):
                self._invalidate()
                raise ValueError("The camera selection or source files changed. Inspect the cameras again.")
            if self.duration_spin.value() <= 0:
                raise ValueError("Enter a positive duration in frames.")
            self._publish_name = name
            self._start("save", prepared=str(self._prepared) if sources else None,
                        destination=destination.as_posix(), duration=self.duration_spin.value())
        except Exception as exc:
            QMessageBox.warning(self, "Create CG Asset", str(exc))

    def _start(self, mode, **values):
        _, hython = helpers.resolve_houdini(self.configured)
        token = uuid.uuid4().hex
        request_path = self.work_dir / f"{token}.json"
        self._result_path = self.work_dir / f"{token}.result.json"
        self._output_path = self.work_dir / f"{token}.hip"
        request = dict(values, mode=mode, fps=self.fps_spin.value(), output=str(self._output_path))
        request_path.write_text(json.dumps(request), encoding="utf-8")
        env = QProcessEnvironment()
        for key, value in helpers.worker_environment(hython, self.work_dir).items():
            env.insert(key, value)
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.work_dir))
        self._mode = mode
        self._busy = True
        self._cancelled = False
        self._logs = ""
        self.inputs.setEnabled(False)
        self.create_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel Operation")
        self.progress.show()
        self.status_label.setText("Starting Houdini…")
        self.process.start(str(hython), [str(Path(__file__).with_name("houdini_cg_worker.py")),
                                         str(request_path), str(self._result_path)])

    def _read_output(self):
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._logs = (self._logs + output)[-24000:]
        for line in self._logs.splitlines():
            if line.startswith("SHOTBOX_PROGRESS "):
                self.status_label.setText(line.removeprefix("SHOTBOX_PROGRESS "))

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(-1, QProcess.ExitStatus.CrashExit)

    def _finished(self, exit_code, exit_status):
        if not self._busy:
            return
        self._read_output()
        self._busy = False
        self.inputs.setEnabled(True)
        self.progress.hide()
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        if self._close_after_cancel:
            super().reject()
            return
        if self._cancelled:
            self._update()
            self.status_label.setText("Operation cancelled. No asset was created.")
            return
        try:
            if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
                detail = self._logs[-6000:] or self.process.errorString()
                if "license" in detail.casefold() and "all in use" in detail.casefold():
                    detail = (
                        "All the Houdini licences are busy. I'd magic up another, "
                        "but I'm just a dialog box.\n\n"
                        "Check with Jack, or try again shortly."
                    )
                raise RuntimeError(detail)
            result = json.loads(self._result_path.read_text(encoding="utf-8"))
            if result.get("output") != str(self._output_path) or not self._output_path.is_file():
                raise RuntimeError("Houdini did not return the expected saved scene.")
            if self._mode == "prepare":
                if helpers.source_signature(self.selected_sources()) != self._pending_signature:
                    raise ValueError("A camera export changed during inspection. Inspect again.")
                self._prepared = self._output_path
                self._signature = self._pending_signature
                summaries = result["cameras"]
                self._auto_duration = helpers.longest_duration(summaries)
                self.camera_summary.setText("\n".join(
                    f"{Path(summary['source']).name}: " +
                    (f"{summary['duration']} frames" if summary.get("duration") is not None
                     else "duration unavailable; enter a manual override") for summary in summaries))
                if not self.override_check.isChecked():
                    self.duration_spin.setValue(self._auto_duration or 0)
            else:
                self.project_path = helpers.publish_scene(self._output_path, self.cg_root, self._publish_name)
                super().accept()
                return
        except Exception as exc:
            QMessageBox.warning(self, "Create CG Asset", str(exc))
        self._update()

    def _cancel_or_close(self):
        if self._busy:
            self._cancelled = True
            self.status_label.setText("Cancelling Houdini…")
            self.process.kill()
        else:
            super().reject()

    def reject(self):
        if self._busy:
            self._close_after_cancel = True
        self._cancel_or_close()

    def stop_worker(self):
        """Finish all writes before releasing the protected temporary directory."""
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._cancelled = True
            self.process.kill()
            self.process.waitForFinished(-1)


@app_cache.temporary_cache_task
def show_create_dialog(cg_root, sources, configured="", parent=None, *, context_title=""):
    with app_cache.temporary_cache_lease(), tempfile.TemporaryDirectory(
        prefix="houdini-cg-", dir=app_cache.cache_directory("temp")
    ) as directory:
        dialog = CgAssetDialog(cg_root, sources, configured, Path(directory), parent, context_title=context_title)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.project_path
            return None
        finally:
            dialog.stop_worker()
            dialog.deleteLater()
