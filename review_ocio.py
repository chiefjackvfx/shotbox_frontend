"""DJV-style OCIO viewing controls backed by a real display processor."""

import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from readable_combo_box import ReadableComboBox
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

try:
    import PyOpenColorIO as ocio
except ImportError:
    ocio = None


class OCIOPanel(QFrame):
    processor_changed = pyqtSignal(object)
    status_changed = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("review_ocio_panel")
        self._settings = None
        self._loading = False
        self.config = None
        self.processor = None
        self._source_kind = "video"
        self._input_hint = "sRGB - Display"
        self._input_preferences = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        form = QFormLayout()
        form.setVerticalSpacing(4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        self.combo_configuration = ReadableComboBox()
        for label, value in (("Off", "off"), ("File", "file"),
                             ("Environment", "environment"), ("Built-in ACES", "builtin")):
            self.combo_configuration.addItem(label, value)
        form.addRow("Configuration:", self.combo_configuration)

        file_row = QWidget()
        file_row.setObjectName("review_ocio_file_row")
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(4)
        self.edit_filename = QLineEdit()
        self.edit_filename.setPlaceholderText("Choose an OCIO configuration file")
        self.button_browse = QPushButton("Browse")
        file_layout.addWidget(self.edit_filename, 1)
        file_layout.addWidget(self.button_browse)
        form.addRow("File name:", file_row)
        self.label_name = QLabel("None")
        self.label_name.setTextFormat(Qt.TextFormat.PlainText)
        self.label_name.setWordWrap(True)
        form.addRow("Name:", self.label_name)
        self.combo_input = ReadableComboBox()
        self.combo_display = ReadableComboBox()
        self.combo_view = ReadableComboBox()
        self.combo_look = ReadableComboBox()
        for name, combo in (("Input:", self.combo_input), ("Display:", self.combo_display),
                            ("View:", self.combo_view), ("Look:", self.combo_look)):
            combo.setMinimumWidth(200)
            form.addRow(name, combo)
        self.label_status = QLabel()
        self.label_status.setObjectName("review_ocio_status")
        self.label_status.setWordWrap(True)
        self.label_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label_status)

        self.combo_configuration.currentIndexChanged.connect(self._reload_config)
        self.edit_filename.editingFinished.connect(self._reload_config)
        self.button_browse.clicked.connect(self._browse)
        self.combo_input.currentIndexChanged.connect(self._input_changed)
        self.combo_display.currentIndexChanged.connect(self._display_changed)
        self.combo_view.currentIndexChanged.connect(self._rebuild_processor)
        self.combo_look.currentIndexChanged.connect(self._rebuild_processor)
        self._reload_config()

    def set_settings_manager(self, settings):
        self._settings = settings
        state = settings.get("review_ocio", {}) or {}
        self._loading = True
        self._input_preferences = dict(state.get("inputs", {}))
        self.edit_filename.setText(state.get("file", ""))
        mode = state.get("mode", "environment" if os.environ.get("OCIO") else "off")
        self.combo_configuration.setCurrentIndex(max(0, self.combo_configuration.findData(mode)))
        self._loading = False
        # Restore controls before writing back any normalised preferences.
        self._settings = None
        self._reload_config()
        if self.config:
            self._loading = True
            self._select(self.combo_display, state.get("display", ""))
            self._populate_views(state.get("view", ""))
            self._select(self.combo_look, state.get("look", "None"))
            self._loading = False
            self._rebuild_processor()
        self._settings = settings

    def _select(self, combo, name):
        index = combo.findText(name)
        if index >= 0:
            combo.setCurrentIndex(index)

    def set_source(self, is_exr=False, colourspace=None):
        kind = "exr" if is_exr else "video"
        hint = colourspace or ("ACEScg" if is_exr else "sRGB - Display")
        changed = kind != self._source_kind or hint != self._input_hint
        self._input_hint = hint
        self._source_kind = kind
        if self.config and changed:
            preferred = self._input_preferences.get(kind) or self._input_hint
            if self.combo_input.findText(preferred) < 0:
                space = self.config.getColorSpace(ocio.ROLE_SCENE_LINEAR) if is_exr else None
                preferred = space.getName() if space else ""
            if preferred:
                self._loading = True
                self._select(self.combo_input, preferred)
                self._loading = False
                self._rebuild_processor()

    def _browse(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose OCIO Configuration", self.edit_filename.text(),
            "OCIO configurations (*.ocio);;All files (*)")
        if filename:
            self.edit_filename.setText(filename)
            if self.combo_configuration.currentData() == "file":
                self._reload_config()
            else:
                self.combo_configuration.setCurrentIndex(self.combo_configuration.findData("file"))

    def _reload_config(self, *_):
        if self._loading:
            return
        mode = self.combo_configuration.currentData()
        self.edit_filename.setEnabled(mode == "file")
        self.button_browse.setEnabled(ocio is not None)
        self.config = None
        self.processor = None
        error = ""
        try:
            if mode != "off":
                if ocio is None:
                    raise RuntimeError("OpenColorIO is unavailable. Install the updated Shotbox requirements.")
                if mode == "file":
                    filename = os.path.expandvars(os.path.expanduser(self.edit_filename.text().strip()))
                    if not filename:
                        raise ValueError("Choose an OCIO configuration file.")
                    config = ocio.Config.CreateFromFile(filename)
                elif mode == "environment":
                    if not os.environ.get("OCIO"):
                        raise ValueError("The OCIO environment variable is not set.")
                    config = ocio.Config.CreateFromEnv()
                else:
                    config = ocio.Config.CreateFromBuiltinConfig("studio-config-latest")
                config.validate()
                self.config = config
        except Exception as exc:
            error = str(exc)
        self._loading = True
        for combo in (self.combo_input, self.combo_display, self.combo_view, self.combo_look):
            combo.clear()
            combo.setEnabled(self.config is not None)
        if self.config:
            self.label_name.setText(self.config.getName() or Path(self.edit_filename.text()).stem)
            self.combo_input.addItems(list(self.config.getColorSpaceNames()))
            preferred = self._input_preferences.get(self._source_kind) or self._input_hint
            if self.combo_input.findText(preferred) < 0:
                space = self.config.getColorSpace(ocio.ROLE_SCENE_LINEAR)
                preferred = space.getName() if space else ""
            self._select(self.combo_input, preferred)
            self.combo_display.addItems(list(self.config.getDisplays()))
            self._select(self.combo_display, self.config.getDefaultDisplay())
            self._populate_views()
            self.combo_look.addItem("None")
            self.combo_look.addItems(list(self.config.getLookNames()))
        else:
            self.label_name.setText("None")
        self._loading = False
        if error:
            self._status(error, True)
            self.processor_changed.emit(None)
            self._persist()
        else:
            self._rebuild_processor()

    def _populate_views(self, preferred=""):
        self.combo_view.clear()
        if self.config and self.combo_display.currentText():
            display = self.combo_display.currentText()
            self.combo_view.addItems(list(self.config.getViews(display)))
            self._select(self.combo_view, preferred or self.config.getDefaultView(display))

    def _display_changed(self, *_):
        if self._loading:
            return
        preferred = self.combo_view.currentText()
        self._loading = True
        self._populate_views(preferred)
        self._loading = False
        self._rebuild_processor()

    def _input_changed(self, *_):
        if self._loading:
            return
        self._input_preferences[self._source_kind] = self.combo_input.currentText()
        self._rebuild_processor()

    def _rebuild_processor(self, *_):
        if self._loading:
            return
        self.processor = None
        try:
            if self.config:
                transform = ocio.DisplayViewTransform(
                    src=self.combo_input.currentText(), display=self.combo_display.currentText(),
                    view=self.combo_view.currentText())
                pipeline = ocio.LegacyViewingPipeline()
                pipeline.setDisplayViewTransform(transform)
                pipeline.setLooksOverrideEnabled(True)
                look = self.combo_look.currentText()
                pipeline.setLooksOverride("" if look == "None" else look)
                self.processor = pipeline.getProcessor(self.config).getDefaultCPUProcessor()
                self._status("OCIO display transform active.")
            else:
                self._status("OCIO off — pixels shown without a display transform.")
        except Exception as exc:
            self._status(f"OCIO transform failed: {exc}", True)
        self.processor_changed.emit(self.processor)
        self._persist()

    def _status(self, text, error=False):
        self.label_status.setText(text)
        self.label_status.setProperty("error", "true" if error else "false")
        self.label_status.style().unpolish(self.label_status)
        self.label_status.style().polish(self.label_status)
        self.status_changed.emit(text, error)

    def _persist(self):
        if self._settings is not None and not self._loading:
            state = {
                "mode": self.combo_configuration.currentData(), "file": self.edit_filename.text(),
                "inputs": dict(self._input_preferences), "display": self.combo_display.currentText(),
                "view": self.combo_view.currentText(), "look": self.combo_look.currentText(),
            }
            if self._settings.get("review_ocio") != state:
                self._settings.set("review_ocio", state)
