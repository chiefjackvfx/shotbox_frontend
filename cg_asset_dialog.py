"""Create a CG asset without blocking the desktop during scene creation."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import uuid

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import app_cache
import cg_asset_helpers as helpers
from settings import get_settings_manager
from input_behaviour import install_input_wheel_guard


class CgAssetDialog(QDialog):
    def __init__(self, cg_root: Path, sources: list[Path], configured: str,
                 work_dir: Path, parent=None, *, context_title="", shot_duration=None):
        super().__init__(None, Qt.WindowType.Window)
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        install_input_wheel_guard(QApplication.instance())
        self.cg_root = Path(cg_root)
        self.sources = sources
        self._selection_order = []
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
        self._create_after_inspection = False
        self._inspection_timer = QTimer(self)
        self._inspection_timer.setSingleShot(True)
        self._inspection_timer.setInterval(350)
        self._inspection_timer.timeout.connect(self._inspect)
        self.setWindowTitle(f"Create CG Asset — {context_title}" if context_title else "Create CG Asset")
        self.resize(780, 650)
        layout = QVBoxLayout(self)
        self.inputs = QWidget(self)
        fields = QVBoxLayout(self.inputs)
        fields.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.application = QComboBox()
        self.application.addItems(["Houdini", "Blender"])
        form.addRow("Application:", self.application)
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
        instructions = QLabel("Check a camera export to inspect it and calculate its duration automatically. "
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
        duration_row = QHBoxLayout()
        duration_row.addWidget(self.duration_spin, 1)
        try:
            frames = int(str(shot_duration).strip())
        except ValueError:
            frames = 0
        if 0 < frames <= self.duration_spin.maximum():
            use_shot = QPushButton(f"Use shot duration ({frames} frames)")
            use_shot.clicked.connect(lambda: self._use_shot_duration(frames))
            duration_row.addWidget(use_shot)
        duration_form.addRow("Duration (frames):", duration_row)
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
        self.application.currentTextChanged.connect(self._application_changed)
        self._application_changed()

    def _application_changed(self):
        blender = self.application.currentText() == "Blender"
        self._selection_order.clear()
        self.camera_list.clear()
        for path in self.sources:
            if path.name.lower().endswith("_blender.py") == blender:
                self._add_source(path)
        self.override_check.setChecked(False)
        self._invalidate()

    def selected_sources(self):
        items = (self.camera_list.item(i) for i in range(self.camera_list.count()))
        checked = [Path(item.data(Qt.ItemDataRole.UserRole)) for item in items
                   if item.checkState() == Qt.CheckState.Checked]

        if self.application.currentText() == "Blender":
            self._selection_order = [path for path in self._selection_order if path in checked]
            self._selection_order.extend(path for path in checked if path not in self._selection_order)
            return list(self._selection_order)
        return checked

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
        paths, _ = QFileDialog.getOpenFileNames(self, f"Select 3DE {self.application.currentText()} Exports",
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
        self._inspection_timer.stop()
        if self.selected_sources():
            self._inspection_timer.start()
        self.camera_summary.clear()
        if not self.override_check.isChecked():
            self.duration_spin.setValue(0)
        self._update()

    def _use_shot_duration(self, frames):
        self.override_check.setChecked(True)
        self.duration_spin.setValue(frames)

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
        needs_inspection = bool(sources) and self._prepared is None
        can_prepare = not self._busy or self._mode == "prepare"
        self.create_button.setEnabled(can_prepare and not self._create_after_inspection and not error
                                      and (duration > 0 or (needs_inspection and not self.override_check.isChecked())))
        if not self._busy:
            self.status_label.setText(error)

    def _inspect(self):
        self._inspection_timer.stop()
        if self._busy or not self.selected_sources():
            return
        self._prepared = None
        try:
            self._pending_signature = helpers.source_signature(self.selected_sources())
            self._start("prepare", sources=[str(path) for path in self.selected_sources()])
        except Exception as exc:
            self._create_after_inspection = False
            QMessageBox.warning(self, "Inspect Cameras", str(exc))
            self._update()

    def _create(self):
        if self._busy:
            if self._mode == "prepare":
                self._create_after_inspection = True
                self._update()
            return
        try:
            name = helpers.validate_asset_name(self.name_edit.text())
            destination = helpers.asset_destination(self.cg_root, name)
            helpers.validate_new_destination(destination)
            sources = self.selected_sources()
            if sources and (not self._prepared or helpers.source_signature(sources) != self._signature):
                self._invalidate()
                self._create_after_inspection = True
                self._inspect()
                return
            if self.duration_spin.value() <= 0:
                raise ValueError("Enter a positive duration in frames.")
            self._publish_name = name
            self._start("save", prepared=str(self._prepared) if sources else None,
                        destination=destination.as_posix(), duration=self.duration_spin.value())
        except Exception as exc:
            QMessageBox.warning(self, "Create CG Asset", str(exc))

    def _start(self, mode, **values):
        application = self.application.currentText()
        blender = application == "Blender"
        if blender:
            configured = get_settings_manager().get("blender_exe_path", "") or ""
            program = helpers.resolve_blender(configured)
            arguments = ["--background", "--factory-startup", "--python-exit-code", "1",
                         "--python", str(Path(__file__).with_name("blender_cg_worker.py")), "--"]
            environment = dict(os.environ, TMPDIR=str(self.work_dir), TEMP=str(self.work_dir),
                               TMP=str(self.work_dir), BLENDER_USER_RESOURCES=str(self.work_dir / "user"),
                               XDG_CACHE_HOME=str(self.work_dir / "cache"))
        else:
            _, program = helpers.resolve_houdini(self.configured)
            arguments = [str(Path(__file__).with_name("houdini_cg_worker.py"))]
            environment = helpers.worker_environment(program, self.work_dir)
        token = uuid.uuid4().hex
        request_path = self.work_dir / f"{token}.json"
        self._result_path = self.work_dir / f"{token}.result.json"
        self._output_path = self.work_dir / f"{token}.{'blend' if blender else 'hip'}"
        request = dict(values, mode=mode, fps=self.fps_spin.value(), output=str(self._output_path))
        request_path.write_text(json.dumps(request), encoding="utf-8")
        env = QProcessEnvironment()
        for key, value in environment.items():
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
        self.status_label.setText(f"Starting {application}…")
        self._update()
        self.process.start(str(program), [*arguments, str(request_path), str(self._result_path)])

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
        create_after_inspection = self._create_after_inspection
        self._create_after_inspection = False
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
                if self.application.currentText() == "Houdini" and "license" in detail.casefold() and "all in use" in detail.casefold():
                    detail = (
                        "All the Houdini licences are busy. I'd magic up another, "
                        "but I'm just a dialog box.\n\n"
                        "Check with Jack, or try again shortly."
                    )
                raise RuntimeError(detail)
            result = json.loads(self._result_path.read_text(encoding="utf-8"))
            if result.get("output") != str(self._output_path) or not self._output_path.is_file():
                raise RuntimeError(f"{self.application.currentText()} did not return the expected saved scene.")
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
                if create_after_inspection:
                    self._create()
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
            self.status_label.setText(f"Cancelling {self.application.currentText()}…")
            self.process.kill()
        else:
            super().reject()

    def reject(self):
        if self._busy:
            self._close_after_cancel = True
        self._cancel_or_close()

    def stop_worker(self):
        """Finish all writes before releasing the protected temporary directory."""
        self._inspection_timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._cancelled = True
            self.process.kill()
            self.process.waitForFinished(-1)


_open_windows = set()


@app_cache.temporary_cache_task
def show_create_dialog(cg_root, sources, configured="", parent=None, *, context_title="",
                       shot_duration=None, on_created=None):
    resources = ExitStack()
    try:
        resources.enter_context(app_cache.temporary_cache_lease())
        directory = resources.enter_context(tempfile.TemporaryDirectory(
            prefix="cg-asset-", dir=app_cache.cache_directory("temp")))
        dialog = CgAssetDialog(cg_root, sources, configured, Path(directory), parent,
                               context_title=context_title, shot_duration=shot_duration)
        app = QApplication.instance()

        def finish(result):
            if dialog not in _open_windows:
                return
            _open_windows.remove(dialog)
            app.aboutToQuit.disconnect(shutdown)
            try:
                dialog.stop_worker()
            finally:
                resources.close()
                dialog.deleteLater()
            if result == QDialog.DialogCode.Accepted and on_created:
                on_created(dialog.project_path)

        def shutdown():
            finish(QDialog.DialogCode.Rejected)

        dialog.finished.connect(finish)
        app.aboutToQuit.connect(shutdown)
        _open_windows.add(dialog)
        dialog.show()
        return dialog
    except Exception:
        resources.close()
        raise
