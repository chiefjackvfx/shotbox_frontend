"""Toolbox page for project setup and reusable utilities."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
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
        self.setObjectName("toolbox_page")
        self.setStyleSheet("""
            QWidget#toolbox_page QLabel { background: transparent; }
            QLabel#toolbox_title { font-size: 28px; font-weight: bold; color: #F3F4F6; }
            QLabel#toolbox_description { color: #9CA3AF; font-size: 13px; }
            QLabel#toolbox_section { color: #FF8659; font-size: 11px; font-weight: bold; }
            QGroupBox#copy_speed_ramps_group {
                background: #22262E; border: 1px solid #363D49;
                border-radius: 10px; margin-top: 14px; padding-top: 16px;
                font-size: 18px; font-weight: bold; color: #F3F4F6;
            }
            QGroupBox#copy_speed_ramps_group::title {
                subcontrol-origin: margin; subcontrol-position: top left;
                left: 22px; padding: 0 8px;
            }
            QLabel#toolbox_tool_description { color: #B7BFCC; font-size: 13px; }
            QLabel#copy_speed_ramps_guide { color: #D4D9E2; font-size: 13px; }
            QLabel#toolbox_requirement { color: #909BAB; font-size: 11px; }
            QLabel#copy_speed_ramps_status {
                color: #AAB6C7; font-size: 12px; padding-top: 4px;
            }
            QFrame#toolbox_divider { background: #363D49; border: none; max-height: 1px; }
            QPushButton#copy_speed_ramps_button {
                background: #FF6B35; color: #171A20; border: 1px solid #FF6B35;
                border-radius: 6px; padding: 11px 18px; font-size: 12px;
                font-weight: bold; text-transform: none;
            }
            QPushButton#copy_speed_ramps_button:hover { background: #FF8659; border-color: #FF8659; }
            QPushButton#copy_speed_ramps_button:pressed { background: #E45B29; }
            QPushButton#copy_speed_ramps_button:focus { border: 1px solid #FFE0D2; }
            QPushButton#copy_speed_ramps_button:disabled {
                background: #343B47; border-color: #343B47; color: #919BAC;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll)

        canvas = QWidget()
        canvas_layout = QHBoxLayout(canvas)
        canvas_layout.setContentsMargins(32, 32, 32, 32)
        content = QWidget()
        content.setMaximumWidth(720)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        canvas_layout.addWidget(content, 1, Qt.AlignmentFlag.AlignTop)
        canvas_layout.addStretch(0)
        scroll.setWidget(canvas)

        title = QLabel("Toolbox")
        title.setObjectName("toolbox_title")
        content_layout.addWidget(title)

        description = QLabel("Project setup and everyday tools for your workflow.")
        description.setObjectName("toolbox_description")
        description.setWordWrap(True)
        content_layout.addWidget(description)
        content_layout.addSpacing(20)

        section = QLabel("DAVINCI RESOLVE  /  TIMELINE TOOLS")
        section.setObjectName("toolbox_section")
        section.setWordWrap(True)
        content_layout.addWidget(section)

        speed_ramps_group = QGroupBox("Copy Speed Ramps Up")
        speed_ramps_group.setObjectName("copy_speed_ramps_group")
        speed_ramps_layout = QVBoxLayout(speed_ramps_group)
        speed_ramps_layout.setContentsMargins(24, 28, 24, 24)
        speed_ramps_layout.setSpacing(18)

        tool_description = QLabel("Transfer speed ramps to the clips on the video layer above.")
        tool_description.setObjectName("toolbox_tool_description")
        tool_description.setWordWrap(True)
        speed_ramps_layout.addWidget(tool_description)

        guide = QLabel(
            '<p style="margin-top: 0;"><b>How to use</b></p>'
            '<p>1. Put each clip that already has a speed ramp on the lower video layer.</p>'
            '<p>2. Put the clip that should receive the ramp directly above it.</p>'
            '<p>3. Align the visible starts of both clips exactly, then click the button.</p>'
            '<p style="color: #9CA3AF; margin-bottom: 0;">All matching pairs on the current '
            'timeline are processed. ShotBox creates and opens a new timeline; '
            'the original timeline is left unchanged.</p>'
        )
        guide.setObjectName("copy_speed_ramps_guide")
        guide.setWordWrap(True)
        speed_ramps_layout.addWidget(guide)

        divider = QFrame()
        divider.setObjectName("toolbox_divider")
        divider.setFixedHeight(1)
        speed_ramps_layout.addWidget(divider)

        requirement = QLabel("Open a timeline in DaVinci Resolve to use this tool.")
        requirement.setObjectName("toolbox_requirement")
        requirement.setWordWrap(True)
        speed_ramps_layout.addWidget(requirement)

        self.copy_speed_ramps_button = QPushButton("Copy Speed Ramps Up")
        self.copy_speed_ramps_button.setObjectName("copy_speed_ramps_button")
        self.copy_speed_ramps_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_speed_ramps_button.clicked.connect(self._copy_speed_ramps_up)
        speed_ramps_layout.addWidget(self.copy_speed_ramps_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.copy_speed_ramps_status = QLabel("Ready.")
        self.copy_speed_ramps_status.setObjectName("copy_speed_ramps_status")
        self.copy_speed_ramps_status.setWordWrap(True)
        self.copy_speed_ramps_status.setTextFormat(Qt.TextFormat.PlainText)
        speed_ramps_layout.addWidget(self.copy_speed_ramps_status)
        content_layout.addWidget(speed_ramps_group)

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
