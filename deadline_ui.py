"""Denoise destination controls; repository queries never block the Qt thread."""
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QFormLayout, QLabel, QPushButton, QSpinBox, QWidget

from deadline_client import discover_client
from input_behaviour import install_input_wheel_guard
from settings import get_settings_manager


class DeadlineOptions(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        install_input_wheel_guard(QApplication.instance())
        settings = get_settings_manager()
        layout = QFormLayout(self)
        self.pool, self.group = QComboBox(), QComboBox()
        for combo, key, default in ((self.pool, "deadline_pool", "linux"), (self.group, "deadline_group", "none")):
            combo.setEditable(True)
            value = settings.get(key, default)
            combo.addItem(value if isinstance(value, str) else default)
            layout.addRow("Pool:" if combo is self.pool else "Group:", combo)
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        priority = settings.get("deadline_priority", 50)
        self.priority.setValue(priority if isinstance(priority, int) else 50)
        layout.addRow("Priority:", self.priority)
        self.chunked = QCheckBox("Chunked rendering")
        self.chunked.setChecked(settings.get("deadline_chunked", True) is not False)
        self.chunk_size = QSpinBox()
        self.chunk_size.setRange(1, 1000000)
        chunk_size = settings.get("deadline_chunk_size", 10)
        self.chunk_size.setValue(chunk_size if isinstance(chunk_size, int) else 10)
        self.chunk_size.setEnabled(self.chunked.isChecked())
        self.chunked.toggled.connect(self.chunk_size.setEnabled)
        layout.addRow(self.chunked)
        layout.addRow("Frames per task:", self.chunk_size)
        self.concurrent_tasks = QSpinBox()
        self.concurrent_tasks.setRange(1, 16)
        concurrent = settings.get("deadline_concurrent_tasks", 1)
        self.concurrent_tasks.setValue(concurrent if isinstance(concurrent, int) else 1)
        layout.addRow("Concurrent tasks per worker:", self.concurrent_tasks)
        self.allow_local = QCheckBox("Allow local projects (this Linux computer only)")
        self.allow_local.setChecked(sys.platform == "linux")
        self.allow_local.setEnabled(sys.platform == "linux")
        layout.addRow(self.allow_local)
        self.refresh = QPushButton("Refresh Deadline queues")
        self.refresh.clicked.connect(self.load_queues)
        layout.addRow(self.refresh)
        self.status = QLabel("Profiles are prepared locally. Linux workers render new versions.\n"
                             "Manage submitted jobs in Deadline Monitor.")
        self.status.setWordWrap(True)
        layout.addRow(self.status)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.finished.connect(self.loaded)
        self.process.errorOccurred.connect(lambda _: self.status.setText("Could not run Deadline. Check its executable in Settings."))
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.stop)

    def load_queues(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            executable = discover_client(get_settings_manager().get("deadline_client", ""))
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("Reading Deadline queues…")
        self.process.start(executable, ["-ExecuteScript", str(Path(__file__).with_name("deadline_probe.py"))])
        self.timeout.start(60000)

    def loaded(self, code, _status):
        self.timeout.stop()
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        try:
            if code:
                raise ValueError(text)
            line = next(line for line in text.splitlines() if line.startswith("SHOTBOX_DEADLINE "))
            data = json.loads(line.removeprefix("SHOTBOX_DEADLINE "))
            for combo, key in ((self.pool, "pools"), (self.group, "groups")):
                selected = combo.currentText()
                combo.clear()
                combo.addItems(sorted(data[key]))
                combo.setCurrentText(selected)
            self.priority.setMaximum(data["max_priority"])
            self.status.setText("Repository: " + data["repository"])
        except (ValueError, KeyError, StopIteration) as exc:
            self.status.setText("Deadline unavailable: " + (str(exc) or text)[-1000:])

    def stop(self):
        self.timeout.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)

    def options(self):
        settings = get_settings_manager()
        values = {"pool": self.pool.currentText().strip(), "group": self.group.currentText().strip(),
                  "priority": self.priority.value(), "client": settings.get("deadline_client", ""),
                  "shared_roots": settings.get("deadline_shared_roots", []),
                  "allow_local": self.allow_local.isChecked(),
                  "chunked": self.chunked.isChecked(), "chunk_size": self.chunk_size.value(),
                  "concurrent_tasks": self.concurrent_tasks.value()}
        for key in ("pool", "group", "priority", "chunked", "chunk_size", "concurrent_tasks"):
            settings.set("deadline_" + key, values[key])
        return values
