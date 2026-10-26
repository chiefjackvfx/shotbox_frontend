"""Shared model configuration and Nuke registration."""
from PyQt6.QtWidgets import (QWidget, QFormLayout, QHBoxLayout, QLineEdit, QPushButton,
                            QLabel, QFileDialog)
from .configuration import load_configuration, save_configuration


class MatteSettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fields = {}
        labels = (("model_root", "External model folder"),
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
        self.install_button = QPushButton("Install / Update Nuke Integration")
        for button in (self.save_button, self.install_button):
            actions.addWidget(button)
        layout.addRow(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addRow(self.status)
        runtime_note = QLabel("Matte uses Shotbox's Python and installed dependencies. "
                             "Use the Shotbox installer or updater to repair them.")
        runtime_note.setWordWrap(True)
        layout.addRow(runtime_note)
        self.save_button.clicked.connect(self.save)
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
