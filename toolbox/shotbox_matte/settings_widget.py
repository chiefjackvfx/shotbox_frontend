"""Shotbox-managed configuration, setup and Nuke registration."""
import os

from PyQt6.QtCore import QProcess, QProcessEnvironment
from PyQt6.QtWidgets import (QWidget, QFormLayout, QHBoxLayout, QLineEdit, QPushButton,
                            QLabel, QFileDialog, QPlainTextEdit)
from .configuration import APPLICATION_ROOT, load_configuration, save_configuration


class MatteSettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._lease = None
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fields = {}
        labels = (("interpreter", "Matte worker Python"), ("model_root", "External model folder"),
                  ("model_config", "Optional model configuration"), ("cache_root", "Nuke persistent cache override"))
        for key, title in labels:
            field = QLineEdit()
            self.fields[key] = field
            row = QHBoxLayout()
            row.addWidget(field)
            browse = QPushButton("Browse")
            browse.clicked.connect(lambda _checked=False, name=key: self._browse(name))
            row.addWidget(browse)
            layout.addRow(title + ":", row)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save Matte Configuration")
        self.setup_button = QPushButton("Prepare Matte Runtime")
        self.install_button = QPushButton("Install / Update Nuke Integration")
        for button in (self.save_button, self.setup_button, self.install_button):
            actions.addWidget(button)
        layout.addRow(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addRow(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMaximumHeight(160)
        self.log.hide()
        layout.addRow(self.log)
        self.save_button.clicked.connect(self.save)
        self.setup_button.clicked.connect(self.prepare_runtime)
        self.install_button.clicked.connect(self.install)
        self.reload()

    def reload(self):
        import plugins_install
        try:
            values = load_configuration()
            for key, field in self.fields.items():
                field.setText(values[key])
            self.status.setText(plugins_install.nuke_plugin_status())
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def _browse(self, key):
        field = self.fields[key]
        if key in ("model_root", "cache_root"):
            value = QFileDialog.getExistingDirectory(self, "Choose " + key.replace("_", " "), field.text())
        else:
            value, _ = QFileDialog.getOpenFileName(self, "Choose " + key.replace("_", " "), field.text())
        if value:
            field.setText(value)

    def values(self):
        return {key: field.text().strip() for key, field in self.fields.items()}

    def save(self):
        try:
            save_configuration(self.values())
            self.status.setText("Matte configuration saved. Reopen Matte to apply changes; Nuke reads it for the next job.")
            return True
        except (OSError, ValueError) as exc:
            self.status.setText(f"Configuration could not be saved: {exc}")
            return False

    def install(self):
        import plugins_install
        try:
            result = plugins_install.install_nuke_plugin(configuration=self.values())
            if result.failures:
                self.status.setText("Installation incomplete: " + "; ".join(f"{key}: {value}" for key, value in result.failures.items()))
            else:
                self.status.setText("Nuke integration installed. Restart Nuke. "
                                    f"{len(result.installed)} installed, {len(result.updated)} updated, "
                                    f"{len(result.unchanged)} unchanged.")
        except (OSError, ValueError) as exc:
            self.status.setText(f"Nuke installation failed: {exc}")

    def is_busy(self):
        return self._process is not None

    def prepare_runtime(self):
        if self.is_busy():
            return
        if os.name != "nt" and __import__("sys").platform != "linux":
            self.status.setText("Matte GPU runtime setup supports Linux and Windows.")
            return
        from app_cache import cache_directory, temporary_cache_lease
        self._lease = temporary_cache_lease()
        self._lease.__enter__()
        process = QProcess(self)
        self._process = process
        process.setWorkingDirectory(str(APPLICATION_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            environment.remove(key)
        for key in ("TMPDIR", "TEMP", "TMP"):
            environment.insert(key, str(cache_directory("temp")))
        environment.insert("UV_CACHE_DIR", str(cache_directory("temp") / "matte-downloads"))
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(lambda: self.log.appendPlainText(bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")))
        process.finished.connect(self._setup_finished)
        process.errorOccurred.connect(self._setup_error)
        self.setup_button.setEnabled(False)
        self.status.setText("Preparing the separate GPU runtime. First setup downloads Python and dependencies.")
        self.log.clear()
        self.log.show()
        if os.name == "nt":
            process.start("powershell.exe", ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                            "-File", str(APPLICATION_ROOT / "launch_windows.ps1"), "--setup-only"])
        else:
            process.start("sh", [str(APPLICATION_ROOT / "launch.sh"), "--setup-only"])

    def _setup_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._setup_finished(-1, QProcess.ExitStatus.CrashExit)

    def _setup_finished(self, code, status):
        process, self._process = self._process, None
        if process is None:
            return
        self.setup_button.setEnabled(True)
        if self._lease is not None:
            self._lease.__exit__(None, None, None)
            self._lease = None
        if code == 0 and status == QProcess.ExitStatus.NormalExit:
            from .configuration import default_interpreter
            self.fields["interpreter"].setText(str(default_interpreter()))
            if self.save():
                self.status.setText("Matte GPU runtime is ready. Choose an external model folder, then save configuration.")
        else:
            self.status.setText("Runtime preparation failed. See the setup log, fix the cause, then retry.")
        process.deleteLater()
