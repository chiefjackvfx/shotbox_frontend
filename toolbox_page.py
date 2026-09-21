"""Toolbox page for project setup and reusable utilities."""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import copy_speed_ramps_up


def _connect_to_resolve():
    """Return ShotBox's Resolve connection and an optional error message."""
    try:
        import DaVinciResolveScript as dvr_script
    except Exception as error:
        return None, f"Could not load the DaVinci Resolve scripting API: {error}"

    try:
        resolve = dvr_script.scriptapp("Resolve")
    except Exception as error:
        return None, f"Could not connect to DaVinci Resolve: {error}"

    if not resolve:
        return None, "Could not connect to DaVinci Resolve. Is Resolve open?"
    return resolve, None


class ToolboxPage(QWidget):
    """Landing page for project setup workflows and frequently used tools."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Toolbox")
        title.setObjectName("toolbox_title")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        description = QLabel(
            "Project setup, utility tools, and frequently used scripts will live here."
        )
        description.setObjectName("toolbox_description")
        description.setWordWrap(True)
        layout.addWidget(description)

        speed_ramps_group = QGroupBox("Copy Speed Ramps Up")
        speed_ramps_group.setObjectName("copy_speed_ramps_group")
        speed_ramps_layout = QVBoxLayout(speed_ramps_group)
        speed_ramps_layout.setSpacing(8)

        self.copy_speed_ramps_button = QPushButton("Copy Speed Ramps Up")
        self.copy_speed_ramps_button.setObjectName("copy_speed_ramps_button")
        self.copy_speed_ramps_button.clicked.connect(self._copy_speed_ramps_up)
        speed_ramps_layout.addWidget(self.copy_speed_ramps_button)

        guide = QLabel(
            "How to use:\n"
            "1. Put each clip that already has a speed ramp on the lower video layer.\n"
            "2. Put the clip that should receive the ramp directly above it.\n"
            "3. Align the visible starts of both clips exactly, then click the button.\n\n"
            "All matching pairs on the current timeline are processed. ShotBox creates "
            "and opens a new timeline; the original timeline is left unchanged."
        )
        guide.setObjectName("copy_speed_ramps_guide")
        guide.setWordWrap(True)
        speed_ramps_layout.addWidget(guide)

        self.copy_speed_ramps_status = QLabel("Ready.")
        self.copy_speed_ramps_status.setObjectName("copy_speed_ramps_status")
        self.copy_speed_ramps_status.setWordWrap(True)
        speed_ramps_layout.addWidget(self.copy_speed_ramps_status)

        layout.addWidget(speed_ramps_group)

        layout.addStretch()

    def _copy_speed_ramps_up(self) -> None:
        title = "Copy Speed Ramps Up"
        self.copy_speed_ramps_button.setEnabled(False)
        self.copy_speed_ramps_button.setText("Running...")
        self.copy_speed_ramps_status.setText("Connecting to DaVinci Resolve...")
        QApplication.processEvents()

        try:
            resolve, connection_error = _connect_to_resolve()
            if connection_error:
                result = {"success": False, "message": connection_error, "warnings": []}
            else:
                self.copy_speed_ramps_status.setText("Copying speed ramps...")
                QApplication.processEvents()
                result = copy_speed_ramps_up.run(resolve)
        except Exception as error:
            result = {
                "success": False,
                "message": f"Copy Speed Ramps Up failed: {error}",
                "warnings": [],
            }
        finally:
            self.copy_speed_ramps_button.setText("Copy Speed Ramps Up")
            self.copy_speed_ramps_button.setEnabled(True)

        if not isinstance(result, dict):
            result = {
                "success": False,
                "message": "The tool returned an invalid result.",
                "warnings": [],
            }

        message = str(result.get("message") or "The tool returned no status message.")
        warnings = [str(warning) for warning in (result.get("warnings") or [])]

        if result.get("success"):
            dialog_message = message
            if warnings:
                dialog_message += "\n\nWarnings:\n- " + "\n- ".join(warnings)
            self.copy_speed_ramps_status.setText(dialog_message)
            QMessageBox.information(self, title, dialog_message)
        else:
            self.copy_speed_ramps_status.setText(message)
            QMessageBox.critical(self, title, message)
