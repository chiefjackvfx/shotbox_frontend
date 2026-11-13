# Houdini 22.0 Shelf Tool
# Longhouse Version Browser
#
# Browse, open and navigate HIP file versions in the current HIP directory.

import math
import os
import re
import hou

from PySide6 import QtCore, QtGui, QtWidgets


VERSION_PATTERN = re.compile(
    r"^(?P<base>.+)_(?P<marker>[vV])(?P<version>\d+)$"
)


class VersionBrowserMascot(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(VersionBrowserMascot, self).__init__(parent)
        self.setFixedSize(150, 130)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.phase = 0.0
        self.age = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        self.phase += 0.075
        self.update()

    def set_age(self, age):
        self.age = max(-1.0, min(1.0, float(age)))
        self.update()

    def showEvent(self, event):
        super(VersionBrowserMascot, self).showEvent(event)
        if not self.timer.isActive():
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super(VersionBrowserMascot, self).hideEvent(event)

    @staticmethod
    def _clamp(value):
        return max(0.0, min(1.0, value))

    def _draw_living_character(self, painter, age, walk, bob):
        dark = QtGui.QColor("#39333D")
        white = QtGui.QColor("#FFFFFF")
        orange = QtGui.QColor("#FF6600")
        aged_orange = QtGui.QColor("#B96D43")
        age_mix = self._clamp(age / 0.85)
        body_color = QtGui.QColor(
            round(orange.red() * (1.0 - age_mix) + aged_orange.red() * age_mix),
            round(orange.green() * (1.0 - age_mix) + aged_orange.green() * age_mix),
            round(orange.blue() * (1.0 - age_mix) + aged_orange.blue() * age_mix),
        )
        young = self._clamp(-age)
        stride = walk * (7.0 + young * 2.0 - age_mix * 3.0)
        body_top = 79.0 + bob - young
        left_foot_x = 42.0 + stride
        right_foot_x = 82.0 - stride
        shoulder_left = QtCore.QPointF(43, body_top + 8)
        shoulder_right = QtCore.QPointF(82, body_top + 8)
        hand_left = QtCore.QPointF(29 - stride * 0.45, body_top + 22 + stride * 0.25)
        hand_right = QtCore.QPointF(98 + stride * 0.45, body_top + 22 - stride * 0.25)

        leg_pen = QtGui.QPen(dark, 4.0)
        leg_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        leg_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(leg_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawLine(QtCore.QPointF(51, body_top + 37), QtCore.QPointF(47 + stride, 134))
        painter.drawLine(QtCore.QPointF(47 + stride, 134), QtCore.QPointF(left_foot_x, 145))
        painter.drawLine(QtCore.QPointF(75, body_top + 37), QtCore.QPointF(78 - stride, 134))
        painter.drawLine(QtCore.QPointF(78 - stride, 134), QtCore.QPointF(right_foot_x, 145))
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(body_color)
        painter.drawRoundedRect(QtCore.QRectF(left_foot_x - 7, 141, 16, 8), 3, 3)
        painter.drawRoundedRect(QtCore.QRectF(right_foot_x - 7, 141, 16, 8), 3, 3)

        cane_opacity = self._clamp((age - 0.52) / 0.3)
        if cane_opacity > 0.0:
            painter.save()
            painter.setOpacity(cane_opacity)
            cane_x = 106.0
            cane_pen = QtGui.QPen(QtGui.QColor("#775E3D"), 2.8)
            cane_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(cane_pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            cane = QtGui.QPainterPath()
            cane.moveTo(cane_x, hand_right.y())
            cane.lineTo(cane_x, 145)
            cane.moveTo(cane_x, hand_right.y() + 3)
            cane.cubicTo(cane_x, hand_right.y() - 5, cane_x - 9, hand_right.y() - 5, cane_x - 9, hand_right.y() + 3)
            painter.drawPath(cane)
            painter.restore()

        arm_pen = QtGui.QPen(dark, 4.0)
        arm_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        arm_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(arm_pen)
        left_arm = QtGui.QPainterPath()
        left_arm.moveTo(shoulder_left)
        left_arm.cubicTo(34, body_top + 11, hand_left.x() - 3, hand_left.y() - 4, hand_left.x(), hand_left.y())
        painter.drawPath(left_arm)
        right_arm = QtGui.QPainterPath()
        right_arm.moveTo(shoulder_right)
        right_arm.cubicTo(92, body_top + 10, hand_right.x() + 3, hand_right.y() - 4, hand_right.x(), hand_right.y())
        painter.drawPath(right_arm)

        painter.setPen(QtGui.QPen(dark, 1.5))
        painter.setBrush(body_color)
        coat = QtGui.QPainterPath()
        coat.moveTo(44, body_top)
        coat.lineTo(82, body_top)
        coat.lineTo(89, body_top + 39)
        coat.lineTo(36, body_top + 39)
        coat.closeSubpath()
        painter.drawPath(coat)

        spiral = QtGui.QPainterPath()
        spiral.moveTo(50, body_top + 16)
        spiral.cubicTo(56, body_top + 8, 70, body_top + 9, 75, body_top + 16)
        spiral.cubicTo(80, body_top + 23, 74, body_top + 31, 67, body_top + 31)
        spiral.cubicTo(59, body_top + 31, 56, body_top + 25, 60, body_top + 21)
        spiral.cubicTo(64, body_top + 18, 70, body_top + 22, 66, body_top + 25)
        spiral_pen = QtGui.QPen(white, 2.7)
        spiral_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        spiral_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(spiral_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(spiral)

        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(white)
        painter.drawEllipse(QtCore.QRectF(hand_left.x() - 4, hand_left.y() - 4, 8, 8))
        painter.drawEllipse(QtCore.QRectF(hand_right.x() - 4, hand_right.y() - 4, 8, 8))
        if cane_opacity > 0.0:
            painter.setBrush(white)
            painter.drawEllipse(QtCore.QRectF(102, hand_right.y() - 4, 8, 8))

    def _draw_skeleton_character(self, painter, walk, bob):
        dark = QtGui.QColor("#39333D")
        bone = QtGui.QColor("#E3D8C6")
        stride = walk * 4.0
        body_top = 79.0 + bob

        bone_pen = QtGui.QPen(bone, 3.0)
        bone_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        bone_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(bone_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawLine(QtCore.QPointF(63, body_top), QtCore.QPointF(63, body_top + 32))
        painter.drawLine(QtCore.QPointF(49, body_top + 4), QtCore.QPointF(77, body_top + 4))

        for rib_y in (body_top + 8, body_top + 15, body_top + 22, body_top + 29):
            rib = QtGui.QPainterPath()
            rib.moveTo(63, rib_y - 2)
            rib.cubicTo(54, rib_y - 4, 48, rib_y - 1, 47, rib_y + 3)
            rib.moveTo(63, rib_y - 2)
            rib.cubicTo(72, rib_y - 4, 78, rib_y - 1, 79, rib_y + 3)
            painter.drawPath(rib)

        left_shoulder = QtCore.QPointF(47, body_top + 5)
        right_shoulder = QtCore.QPointF(79, body_top + 5)
        left_elbow = QtCore.QPointF(36, body_top + 18 + stride)
        right_elbow = QtCore.QPointF(91, body_top + 18 - stride)
        left_hand = QtCore.QPointF(29, body_top + 30 + stride * 0.4)
        right_hand = QtCore.QPointF(98, body_top + 30 - stride * 0.4)
        painter.drawLine(left_shoulder, left_elbow)
        painter.drawLine(left_elbow, left_hand)
        painter.drawLine(right_shoulder, right_elbow)
        painter.drawLine(right_elbow, right_hand)

        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(QtGui.QColor("#FFFFFF"))
        painter.drawEllipse(QtCore.QRectF(left_hand.x() - 4, left_hand.y() - 4, 8, 8))
        painter.drawEllipse(QtCore.QRectF(right_hand.x() - 4, right_hand.y() - 4, 8, 8))

        joints = (left_shoulder, right_shoulder, left_elbow, right_elbow)
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(bone)
        for joint in joints:
            painter.drawEllipse(QtCore.QRectF(joint.x() - 3, joint.y() - 3, 6, 6))

        left_hip = QtCore.QPointF(53, body_top + 34)
        right_hip = QtCore.QPointF(73, body_top + 34)
        left_knee = QtCore.QPointF(49 + stride, 132)
        right_knee = QtCore.QPointF(77 - stride, 132)
        left_foot = QtCore.QPointF(42 + stride, 145)
        right_foot = QtCore.QPointF(84 - stride, 145)
        painter.setPen(bone_pen)
        painter.drawLine(left_hip, left_knee)
        painter.drawLine(left_knee, left_foot)
        painter.drawLine(right_hip, right_knee)
        painter.drawLine(right_knee, right_foot)
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(bone)
        for joint in (left_hip, right_hip, left_knee, right_knee):
            painter.drawEllipse(QtCore.QRectF(joint.x() - 3, joint.y() - 3, 6, 6))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.translate(24, 5)
        painter.scale(0.8, 0.8)

        walk = math.sin(self.phase * 2.8)
        bob = abs(walk) * 2.0
        old = max(0.0, self.age)
        skeleton_opacity = self._clamp((old - 0.88) / 0.12)

        painter.setPen(QtGui.QPen(QtGui.QColor("#777C84"), 1.0))
        painter.drawLine(QtCore.QPointF(10, 151), QtCore.QPointF(125, 151))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 28))
        painter.drawEllipse(QtCore.QRectF(44, 148, 42, 4))

        if skeleton_opacity < 1.0:
            painter.save()
            painter.setOpacity(1.0 - skeleton_opacity)
            self._draw_living_character(painter, self.age, walk, bob)
            painter.restore()
        if skeleton_opacity > 0.0:
            painter.save()
            painter.setOpacity(skeleton_opacity)
            self._draw_skeleton_character(painter, walk, bob)
            painter.restore()

        painter.end()


class VersionBrowser(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(VersionBrowser, self).__init__(parent)

        self.setWindowTitle("Longhouse | Version Browser")
        self.setMinimumSize(680, 430)
        self.resize(780, 500)

        self.current_path = hou.hipFile.path()
        self.current_directory = ""
        self.current_base = ""
        self.current_marker = "v"
        self.current_extension = ".hip"
        self.current_version = None

        self.versions = []

        self._read_current_file()
        self._build_ui()
        self.refresh_versions()

    # ------------------------------------------------------------------
    # Current HIP information
    # ------------------------------------------------------------------

    def _read_current_file(self):

        if not self.current_path:
            return

        self.current_path = os.path.abspath(self.current_path)

        self.current_directory = os.path.dirname(self.current_path)

        filename = os.path.basename(self.current_path)

        name, self.current_extension = os.path.splitext(filename)

        match = VERSION_PATTERN.match(name)

        if not match:
            return

        self.current_base = match.group("base")
        self.current_marker = match.group("marker")
        self.current_version = int(match.group("version"))

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):

        self.setStyleSheet("""
            QDialog {
                background: #30343A;
                color: #F2F2F2;
            }

            QLabel {
                color: #E6E6E6;
            }

            QLabel#Heading {
                font-size: 17px;
                font-weight: 600;
            }

            QLabel#Current {
                color: #FFB36B;
                font-size: 12px;
                font-weight: 600;
            }

            QLabel#Subheading {
                color: #AEB5BF;
                font-size: 11px;
            }

            QLineEdit {
                background: #25292F;
                color: #F2F2F2;
                border: 1px solid #515965;
                border-radius: 4px;
                padding: 6px;
            }

            QTableWidget {
                background: #25292F;
                alternate-background-color: #2B3037;
                color: #E8E8E8;
                border: 1px solid #515965;
                gridline-color: #3B414A;
                selection-background-color: #66503F;
                selection-color: white;
            }

            QHeaderView::section {
                background: #414750;
                color: #F2F2F2;
                padding: 7px;
                border: none;
                border-right: 1px solid #515965;
            }

            QPushButton {
                background: #484E57;
                color: #F2F2F2;
                border: 1px solid #626A75;
                border-radius: 5px;
                padding: 7px 13px;
                min-width: 75px;
            }

            QPushButton:hover {
                background: #59636F;
                border-color: #FF8A36;
            }

            QPushButton:disabled {
                color: #777D85;
                background: #363B42;
                border-color: #454B54;
            }

            QPushButton#Open {
                background: #B95B20;
                border-color: #E57A35;
                font-weight: 600;
            }

            QPushButton#Open:hover {
                background: #D16B29;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        heading = QtWidgets.QLabel("Version Browser")
        heading.setObjectName("Heading")
        header.addWidget(heading, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.mascot = VersionBrowserMascot(self)
        header.addWidget(self.mascot, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)

        self.current_label = QtWidgets.QLabel()
        self.current_label.setObjectName("Current")
        layout.addWidget(self.current_label)

        self.directory_label = QtWidgets.QLabel()
        self.directory_label.setObjectName("Subheading")
        self.directory_label.setWordWrap(True)
        layout.addWidget(self.directory_label)

        # Search
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter versions...")
        self.search.textChanged.connect(self._filter_versions)
        layout.addWidget(self.search)

        # Version table
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([
            "Version",
            "Filename",
            "Last Modified"
        ])

        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )

        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(
            self._update_navigation_buttons
        )
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)

        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )

        self.table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        self.table.doubleClicked.connect(self.open_selected)

        layout.addWidget(self.table, 1)

        # Information
        self.info_label = QtWidgets.QLabel()
        self.info_label.setObjectName("Subheading")
        layout.addWidget(self.info_label)

        # Navigation buttons
        navigation = QtWidgets.QHBoxLayout()

        self.previous_button = QtWidgets.QPushButton("Open Previous")
        self.previous_button.clicked.connect(self.open_previous)
        navigation.addWidget(self.previous_button)

        self.next_button = QtWidgets.QPushButton("Open Next")
        self.next_button.clicked.connect(self.open_next)
        navigation.addWidget(self.next_button)

        navigation.addStretch(1)

        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_versions)
        navigation.addWidget(self.refresh_button)

        layout.addLayout(navigation)

        # Bottom buttons
        buttons = QtWidgets.QHBoxLayout()

        buttons.addStretch(1)

        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)

        self.open_button = QtWidgets.QPushButton("Open")
        self.open_button.setObjectName("Open")
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)

        layout.addLayout(buttons)

        if self.current_version is not None:
            self.current_label.setText(
                "Current HIP: {}_{}{}".format(
                    self.current_base,
                    self.current_marker,
                    str(self.current_version).zfill(3)
                )
            )
        else:
            self.current_label.setText(
                "Current HIP: {}".format(
                    os.path.basename(self.current_path)
                    if self.current_path else "Unsaved scene"
                )
            )

        self.directory_label.setText(
            "Directory: {}".format(
                self.current_directory or "No directory"
            )
        )

    # ------------------------------------------------------------------
    # Version scanning
    # ------------------------------------------------------------------

    def refresh_versions(self):

        self.versions = []

        if not self.current_directory or not self.current_base:
            self.table.setRowCount(0)

            self.info_label.setText(
                "The current HIP filename must end in _v001 or _V001."
            )

            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.open_button.setEnabled(False)

            return

        try:
            filenames = os.listdir(self.current_directory)
        except Exception as error:
            self.table.setRowCount(0)
            self.info_label.setText("Unable to read HIP directory.")
            self._update_navigation_buttons()
            hou.ui.displayMessage(
                "Unable to read HIP directory:\n{}".format(error)
            )
            return

        for filename in filenames:

            name, extension = os.path.splitext(filename)

            if extension.lower() != self.current_extension.lower():
                continue

            match = VERSION_PATTERN.match(name)

            if not match:
                continue

            if match.group("base") != self.current_base:
                continue

            if match.group("marker") != self.current_marker:
                continue

            version = int(match.group("version"))

            full_path = os.path.join(
                self.current_directory,
                filename
            )

            if not os.path.isfile(full_path):
                continue

            try:
                modified = os.path.getmtime(full_path)
            except Exception:
                modified = 0

            self.versions.append({
                "version": version,
                "filename": filename,
                "path": full_path,
                "modified": modified
            })

        self.versions.sort(
            key=lambda item: item["version"],
            reverse=True
        )

        self._populate_table()

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _populate_table(self):

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        search_text = self.search.text().lower()

        visible_versions = [
            item for item in self.versions
            if search_text in item["filename"].lower()
        ]

        for item in visible_versions:

            row = self.table.rowCount()
            self.table.insertRow(row)

            version_text = "{}{}".format(
                self.current_marker,
                str(item["version"]).zfill(3)
            )

            version_item = QtWidgets.QTableWidgetItem(
                version_text
            )

            filename_item = QtWidgets.QTableWidgetItem(
                item["filename"]
            )

            modified_text = "Unknown"

            if item["modified"]:
                from datetime import datetime

                modified_text = datetime.fromtimestamp(
                    item["modified"]
                ).strftime("%Y-%m-%d %H:%M:%S")

            modified_item = QtWidgets.QTableWidgetItem(
                modified_text
            )

            for table_item in (
                version_item,
                filename_item,
                modified_item
            ):
                table_item.setData(
                    QtCore.Qt.ItemDataRole.UserRole,
                    item["path"]
                )

            if item["version"] == self.current_version:

                for table_item in (
                    version_item,
                    filename_item,
                    modified_item
                ):
                    table_item.setForeground(
                        QtGui.QColor("#FFB36B")
                    )

                filename_item.setText(
                    item["filename"] + "  (CURRENT)"
                )

            self.table.setItem(row, 0, version_item)
            self.table.setItem(row, 1, filename_item)
            self.table.setItem(row, 2, modified_item)

        self.info_label.setText(
            "{} version(s) found".format(
                len(visible_versions)
            )
        )

        self._update_navigation_buttons()

    def _filter_versions(self, text):

        self._populate_table()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def selected_version(self):

        row = self.table.currentRow()

        if row < 0:
            return None

        item = self.table.item(row, 0)

        if item is None:
            return None

        path = item.data(
            QtCore.Qt.ItemDataRole.UserRole
        )

        for version in self.versions:
            if version["path"] == path:
                return version

        return None

    def _update_navigation_buttons(self):

        selected = self.selected_version()
        age = 0.0
        if self.current_version is not None and selected is not None:
            if selected["version"] > self.current_version:
                age = -1.0
            elif selected["version"] < self.current_version:
                older_versions = sorted({
                    item["version"] for item in self.versions
                    if item["version"] < self.current_version
                }, reverse=True)
                rank = older_versions.index(selected["version"]) + 1
                age = rank / max(8, len(older_versions))
        self.mascot.set_age(age)

        self.open_button.setEnabled(selected is not None)

        if self.current_version is None:
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return

        previous = any(
            item["version"] < self.current_version
            for item in self.versions
        )

        next_version = any(
            item["version"] > self.current_version
            for item in self.versions
        )

        self.previous_button.setEnabled(previous)
        self.next_button.setEnabled(next_version)

    # ------------------------------------------------------------------
    # Open
    # ------------------------------------------------------------------

    def open_path(self, path):

        if not path or not os.path.isfile(path):
            hou.ui.displayMessage(
                "The selected HIP file no longer exists."
            )
            return

        if os.path.abspath(path) == os.path.abspath(
            hou.hipFile.path()
        ):
            self.close()
            return

        result = hou.ui.displayMessage(
            "Open this HIP file?\n\n{}\n\n"
            "Any unsaved changes in the current scene will be lost.".format(
                os.path.basename(path)
            ),
            buttons=("Open", "Cancel"),
            default_choice=1,
            close_choice=1,
            severity=hou.severityType.Warning
        )

        if result != 0:
            return

        try:
            hou.hipFile.load(
                path,
                suppress_save_prompt=True
            )
            self.close()

        except Exception as error:
            hou.ui.displayMessage(
                "Unable to open HIP file:\n{}".format(error),
                title="Version Browser"
            )

    def open_selected(self):

        selected = self.selected_version()

        if not selected:
            return

        self.open_path(selected["path"])

    # ------------------------------------------------------------------
    # Previous / Next
    # ------------------------------------------------------------------

    def open_previous(self):

        if self.current_version is None:
            return

        candidates = [
            item for item in self.versions
            if item["version"] < self.current_version
        ]

        if not candidates:
            return

        target = max(
            candidates,
            key=lambda item: item["version"]
        )

        self.open_path(target["path"])

    def open_next(self):

        if self.current_version is None:
            return

        candidates = [
            item for item in self.versions
            if item["version"] > self.current_version
        ]

        if not candidates:
            return

        target = min(
            candidates,
            key=lambda item: item["version"]
        )

        self.open_path(target["path"])


# ----------------------------------------------------------------------
# Launch
# ----------------------------------------------------------------------

def show_version_browser():

    parent = hou.qt.mainWindow()

    dialog = VersionBrowser(parent)

    dialog.setAttribute(
        QtCore.Qt.WidgetAttribute.WA_DeleteOnClose,
        True
    )

    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


if __name__ == "__main__":
    show_version_browser()
