"""GUI-only naming dialog using Nuke 17.1v2's bundled Qt binding."""
from PySide6 import QtCore, QtGui, QtWidgets

from .naming import destination, sanitize_live
from .precomp_paths import normalize_name


def _parent_window():
    app = QtWidgets.QApplication.instance()
    for widget in app.topLevelWidgets():
        if widget.metaObject().className() == "Foundry::UI::DockMainWindow":
            return widget
    return app.activeWindow()


class NamingDialog(QtWidgets.QDialog):
    def __init__(self, node, parent=None, initial_name=None):
        super().__init__(parent if parent is not None else _parent_window())
        self.node = node
        self.precomp_name = ""
        self._valid = False
        self.setWindowTitle("Name precomp")
        self.setObjectName("shotboxNamingDialog")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.resize(620, 330)
        self.setStyleSheet("""
            QLineEdit#precompName {
                border: 1px solid #606060; border-radius: 5px;
                padding: 9px 10px; background-color: #242424;
            }
            QLineEdit#precompName:focus { border: 1px solid #cf955e; }
            QLabel#versionBadge, QLabel#extensionBadge {
                border: 1px solid #555555; border-radius: 4px;
                padding: 7px 9px; color: #d4d4d4; background-color: #363636;
            }
            QPushButton#generateButton {
                background-color: #ad713e; color: #ffffff;
                border: 1px solid #ca915e; border-radius: 4px;
                padding: 7px 19px; font-weight: 600;
            }
            QPushButton#generateButton:hover { background-color: #bd804a; }
            QPushButton#generateButton:disabled {
                background-color: #383838; color: #808080; border-color: #505050;
            }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("Alpha precomp")
        font = QtGui.QFont(title.font())
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 2)
        else:
            font.setPixelSize(max(14, font.pixelSize() + 2))
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel("Alpha only · Read back into this graph")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #aaa;")
        layout.addWidget(subtitle)
        layout.addSpacing(4)
        label = QtWidgets.QLabel("Precomp name")
        layout.addWidget(label)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setObjectName("precompName")
        self.name_edit.setPlaceholderText("Type Precomp Name…")
        self.name_edit.setToolTip("Letters, numbers and underscores. Spaces and punctuation become underscores as you type.")
        name_font = QtGui.QFont(self.name_edit.font())
        name_font.setBold(True)
        self.name_edit.setFont(name_font)
        self.name_edit.setMinimumHeight(36)
        self.name_edit.installEventFilter(self)
        label.setBuddy(self.name_edit)
        row.addWidget(self.name_edit, 1)
        self.version_badge = QtWidgets.QLabel("_v001")
        self.version_badge.setObjectName("versionBadge")
        self.version_badge.setToolTip("The next available version. Existing renders are kept.")
        row.addWidget(self.version_badge)
        extension = QtWidgets.QLabel(".exr")
        extension.setObjectName("extensionBadge")
        row.addWidget(extension)
        layout.addLayout(row)
        hint = QtWidgets.QLabel("Space → _     •     Each render creates a new version")
        hint.setStyleSheet("color: #999;")
        layout.addWidget(hint)
        layout.addSpacing(6)
        self.filename_preview = QtWidgets.QLabel()
        self.filename_preview.setObjectName("filenamePreview")
        self.path_preview = QtWidgets.QLabel()
        self.path_preview.setObjectName("pathPreview")
        self.path_preview.setStyleSheet("color: #999;")
        for preview in (self.filename_preview, self.path_preview):
            preview.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            preview.setWordWrap(True)
            preview.setMinimumWidth(0)
            preview.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            preview.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(preview)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setObjectName("nameError")
        self.error_label.setStyleSheet("color: #e4a56d;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        layout.addStretch(1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("cancelButton")
        cancel.setMinimumHeight(32)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.generate_button = QtWidgets.QPushButton("Generate")
        self.generate_button.setObjectName("generateButton")
        self.generate_button.setDefault(True)
        self.generate_button.clicked.connect(self._try_accept)
        buttons.addWidget(self.generate_button)
        layout.addLayout(buttons)
        self.name_edit.textChanged.connect(self._on_name_changed)
        self.name_edit.returnPressed.connect(self._try_accept)
        previous = node["sb_precomp_name"].value() if initial_name is None else initial_name
        self.name_edit.setText(sanitize_live(previous))
        self._on_name_changed()

    def eventFilter(self, obj, event):
        if obj is self.name_edit and event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Space:
                self.name_edit.insert("_")
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _on_name_changed(self):
        raw = self.name_edit.text()
        clean = sanitize_live(raw)
        if clean != raw:
            cursor = self.name_edit.cursorPosition()
            blocker = QtCore.QSignalBlocker(self.name_edit)
            self.name_edit.setText(clean)
            self.name_edit.setCursorPosition(min(cursor, len(clean)))
            del blocker
        self._valid = False
        if not clean.strip("_"):
            self.filename_preview.setText("Your precomp filename appears here")
            self.path_preview.clear()
            self.error_label.setText("Enter a precomp name." if clean else "")
            self.generate_button.setEnabled(False)
            return
        import nuke
        try:
            info = destination(self.node, clean, nuke.root().name())
        except (ValueError, OSError) as exc:
            self.filename_preview.clear()
            self.path_preview.clear()
            self.error_label.setText(str(exc))
            self.generate_button.setEnabled(False)
            return
        self.version_badge.setText("_v%03d" % info["version"] if info["version"] is not None else "new version")
        self.filename_preview.setText(info["filename"])
        self.path_preview.setText(info["pattern"])
        self.error_label.clear()
        self.generate_button.setEnabled(True)
        self._valid = True

    def _try_accept(self):
        if self.result() == QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._on_name_changed()
        if not self._valid:
            if not self.name_edit.text().strip("_"):
                self.error_label.setText("Please enter a precomp name.")
            self.name_edit.setFocus()
            return
        self.precomp_name = normalize_name(self.name_edit.text())
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
