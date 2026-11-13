# Houdini 22.0 shelf tool
# Opens a floating clone of the current Scene Viewer and asks where the
# native Houdini playbar should live. The Longhouse character from the user's
# startup 123.py is shown in the choice dialog and points at the hovered option.
# Closing the floating viewer always returns the playbar to the original viewer.

import hou
import math
from PySide6 import QtCore, QtGui, QtWidgets

WINDOW_TITLE = "Floating Scene Viewer"
WINDOW_SIZE = (1400, 900)
SESSION_KEY = "_floating_scene_viewer_playbar_choice_mascot"
OLD_SESSION_KEYS = (
    "_floating_scene_viewer_playbar_choice",
    "_floating_scene_viewer_native_playbar",
    "_floating_scene_viewer_timeline_swap_manager",
    "_floating_scene_viewer_timeline_swap_manager_v2",
    "_floating_scene_viewer_timeline_swap_manager_v3",
)


# -----------------------------------------------------------------------------
# Animated Longhouse character for the choice popup
# -----------------------------------------------------------------------------

class TimelineChoiceCharacter(QtWidgets.QWidget):
    """Small animated version of the 123.py character, pointing at a choice."""

    def __init__(self, parent=None):
        super(TimelineChoiceCharacter, self).__init__(parent)
        self.setFixedSize(170, 140)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.phase = 0.0
        self.target_y = 42.0
        self.current_y = 42.0

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def point_to(self, option_index):
        # The two option buttons sit vertically beside this character.
        if option_index == 0:
            self.target_y = 42.0
        elif option_index == 1:
            self.target_y = 98.0
        else:
            self.target_y = 73.0
        self.update()

    def _tick(self):
        self.phase += 0.055
        self.current_y += (self.target_y - self.current_y) * 0.22
        self.update()

    def showEvent(self, event):
        super(TimelineChoiceCharacter, self).showEvent(event)
        if not self.timer.isActive():
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super(TimelineChoiceCharacter, self).hideEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        orange = QtGui.QColor("#FF6600")
        dark = QtGui.QColor("#39333D")
        white = QtGui.QColor("#FFFFFF")
        bob = math.sin(self.phase * 2.0) * 0.8
        body_top = 49.0 + bob
        body_bottom = body_top + 43.0
        shoulder_x, shoulder_y = 57.0, body_top + 16.0
        hand_x, hand_y = 139.0, self.current_y

        limb_pen = QtGui.QPen(dark, 3.5)
        limb_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        limb_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)

        # Legs and little shoes, matching the original 123.py mascot.
        painter.setPen(limb_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawLine(QtCore.QPointF(31, body_bottom - 4), QtCore.QPointF(25, 111))
        painter.drawLine(QtCore.QPointF(25, 111), QtCore.QPointF(39, 115))
        painter.drawLine(QtCore.QPointF(51, body_bottom - 4), QtCore.QPointF(61, 109))
        painter.drawLine(QtCore.QPointF(61, 109), QtCore.QPointF(74, 113))

        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(orange)
        painter.drawRoundedRect(QtCore.QRectF(32, 111 + bob, 14, 7), 3, 3)
        painter.drawRoundedRect(QtCore.QRectF(69, 109 - bob, 14, 7), 3, 3)

        # Left arm remains relaxed.
        painter.setPen(limb_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        left_arm = QtGui.QPainterPath()
        left_arm.moveTo(26, body_top + 18)
        left_arm.cubicTo(19, body_top + 20, 13, body_top + 27, 14, body_top + 33)
        painter.drawPath(left_arm)

        # Right arm reaches towards the option currently under the mouse.
        right_arm = QtGui.QPainterPath()
        right_arm.moveTo(57, shoulder_y)
        right_arm.cubicTo(76, shoulder_y - 7, 91, hand_y, hand_x, hand_y)
        painter.drawPath(right_arm)

        # Orange body.
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(orange)
        painter.drawRoundedRect(
            QtCore.QRectF(20, body_top, 45, 43), 2, 2
        )

        # White spiral mark from the original character.
        dy = body_top - 28.0
        spiral = QtGui.QPainterPath()
        spiral.moveTo(26, 38 + dy)
        spiral.cubicTo(34, 31 + dy, 47, 32 + dy, 55, 39 + dy)
        spiral.cubicTo(62, 46 + dy, 59, 56 + dy, 52, 62 + dy)
        spiral.cubicTo(45, 67 + dy, 36, 63 + dy, 31, 58 + dy)
        spiral.cubicTo(27, 54 + dy, 30, 48 + dy, 35, 45 + dy)
        spiral.cubicTo(40, 41 + dy, 46, 43 + dy, 48, 47 + dy)
        spiral.cubicTo(50, 51 + dy, 46, 55 + dy, 42, 53 + dy)
        spiral.cubicTo(39, 52 + dy, 40, 49 + dy, 43, 48 + dy)

        spiral_pen = QtGui.QPen(white, 3.6)
        spiral_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        spiral_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(spiral_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(spiral)

        # Hands: left hand and a pointing hand on the end of the right arm.
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(white)
        painter.drawEllipse(QtCore.QRectF(10, body_top + 29, 8, 8))
        painter.drawEllipse(QtCore.QRectF(hand_x - 4, hand_y - 4, 8, 8))

        # Pointing index finger extending toward the hovered option.
        finger_pen = QtGui.QPen(white, 4.0)
        finger_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(finger_pen)
        painter.drawLine(
            QtCore.QPointF(hand_x + 1, hand_y - 1),
            QtCore.QPointF(158, hand_y - 4)
        )
        painter.end()


class _ChoiceButton(QtWidgets.QPushButton):
    hovered = QtCore.Signal(bool)

    def enterEvent(self, event):
        self.hovered.emit(True)
        super(_ChoiceButton, self).enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(False)
        super(_ChoiceButton, self).leaveEvent(event)

    def focusInEvent(self, event):
        self.hovered.emit(True)
        super(_ChoiceButton, self).focusInEvent(event)

    def focusOutEvent(self, event):
        self.hovered.emit(False)
        super(_ChoiceButton, self).focusOutEvent(event)


class TimelineLocationDialog(QtWidgets.QDialog):
    """Choice dialog with the animated character pointing at the hovered option."""

    def __init__(self, parent=None):
        super(TimelineLocationDialog, self).__init__(parent)
        self.choice = "original"  # Escape, window close and dismissal default here.
        self.setWindowTitle("Choose Timeline Location")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Dialog
            | QtCore.Qt.WindowType.WindowTitleHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )

        self.setStyleSheet("""
            QDialog { background: #30343A; color: #F2F2F2; }
            QLabel#Heading { color: #F2F2F2; font-size: 16px; font-weight: 600; }
            QLabel#Subheading { color: #B8BEC7; font-size: 11px; }
            QPushButton {
                color: #F2F2F2; background: #484E57;
                border: 1px solid #626A75; border-radius: 6px;
                padding: 11px 14px; text-align: left; font-size: 12px;
            }
            QPushButton:hover, QPushButton:focus {
                background: #59636F; border: 1px solid #FF8A36;
            }
            QPushButton:default { border: 1px solid #FF8A36; }
        """)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(9)

        heading = QtWidgets.QLabel("Where would you like Houdini's timeline?")
        heading.setObjectName("Heading")
        outer.addWidget(heading)

        subheading = QtWidgets.QLabel(
            "Choose which Scene Viewer should contain the native playbar. "
            "Closing the floating viewer returns it to the original viewer."
        )
        subheading.setObjectName("Subheading")
        subheading.setWordWrap(True)
        outer.addWidget(subheading)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 5, 0, 0)
        row.setSpacing(14)

        self.character = TimelineChoiceCharacter(self)
        self.character.point_to(0)
        row.addWidget(self.character, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        choices = QtWidgets.QVBoxLayout()
        choices.setSpacing(10)

        self.original_button = _ChoiceButton("Original Scene Viewer")
        self.original_button.setMinimumHeight(46)
        self.original_button.setDefault(True)
        self.original_button.setAutoDefault(True)
        self.original_button.hovered.connect(
            lambda active: self.character.point_to(0 if active else None)
        )
        self.original_button.clicked.connect(
            lambda: self._choose("original")
        )

        self.floating_button = _ChoiceButton("Floating Scene Viewer")
        self.floating_button.setMinimumHeight(46)
        self.floating_button.setAutoDefault(True)
        self.floating_button.hovered.connect(
            lambda active: self.character.point_to(1 if active else None)
        )
        self.floating_button.clicked.connect(
            lambda: self._choose("floating")
        )

        choices.addStretch(1)
        choices.addWidget(self.original_button)
        choices.addWidget(self.floating_button)
        choices.addStretch(1)
        row.addLayout(choices, 1)
        outer.addLayout(row)

        self.resize(540, 220)

    def _choose(self, choice):
        self.choice = choice
        self.accept()

    def selected_location(self):
        self.exec()
        return self.choice


# -----------------------------------------------------------------------------
# Native playbar / floating Scene Viewer management
# -----------------------------------------------------------------------------

class FloatingViewerPlaybarManager(QtCore.QObject):
    def __init__(self, source_viewer):
        super(FloatingViewerPlaybarManager, self).__init__(hou.qt.mainWindow())
        self.source_viewer = source_viewer
        self.floating_viewer = None
        self.floating_panel = None
        self.floating_window = None
        self.playbar_in_floating = False
        self._closing = False

    def _move_playbar_to_source(self):
        """Put the native playbar at the bottom of the original Scene Viewer pane."""
        try:
            if self.source_viewer is not None:
                source_pane = self.source_viewer.pane()
                if source_pane is not None:
                    hou.playbar.moveToPane(source_pane)
                    self.playbar_in_floating = False
                    return True
        except Exception as exc:
            print("Could not move playbar to original Scene Viewer pane: {}".format(exc))

        try:
            hou.playbar.moveToBottom()
            self.playbar_in_floating = False
            return True
        except Exception as exc:
            print("Could not restore playbar to main desktop: {}".format(exc))
            return False

    def _ask_playbar_location(self):
        dialog = TimelineLocationDialog(hou.qt.mainWindow())
        return dialog.selected_location()

    def open(self):
        self.floating_viewer = self.source_viewer.clone()
        if self.floating_viewer is None:
            raise RuntimeError("Houdini did not create a cloned Scene Viewer.")

        try:
            self.floating_viewer.setName(WINDOW_TITLE)
        except Exception:
            pass

        self.floating_panel = self.floating_viewer.floatingPanel()
        if self.floating_panel is None:
            raise RuntimeError(
                "The cloned Scene Viewer is not in a hou.FloatingPanel; "
                "cannot place the native playbar in it."
            )

        self.floating_window = self.floating_panel.qtParentWindow()
        if self.floating_window is None:
            raise RuntimeError("Could not get the floating viewer window.")

        try:
            self.floating_window.setWindowTitle(WINDOW_TITLE)
            self.floating_window.resize(*WINDOW_SIZE)
        except Exception:
            pass

        self.floating_window.show()
        self.floating_window.raise_()
        self.floating_window.activateWindow()

        choice = self._ask_playbar_location()
        if choice == "floating":
            self.floating_panel.setContainsPlaybar(True)
            self.playbar_in_floating = True
        else:
            self._move_playbar_to_source()

        self.floating_window.installEventFilter(self)
        self.floating_window.raise_()
        self.floating_window.activateWindow()
        return self.floating_viewer

    def eventFilter(self, watched, event):
        if (
            watched is self.floating_window
            and event.type() == QtCore.QEvent.Type.Close
        ):
            self.restore_playbar()
        return False

    def restore_playbar(self):
        if self._closing:
            return
        self._closing = True
        self._move_playbar_to_source()

        try:
            if self.floating_window is not None:
                self.floating_window.removeEventFilter(self)
        except Exception:
            pass

        if getattr(hou.session, SESSION_KEY, None) is self:
            try:
                delattr(hou.session, SESSION_KEY)
            except Exception:
                setattr(hou.session, SESSION_KEY, None)

    def cleanup(self):
        self.restore_playbar()
        try:
            if self.floating_panel is not None:
                self.floating_panel.close()
            elif self.floating_window is not None:
                self.floating_window.close()
        except Exception:
            pass


def _close_previous_tool_windows():
    """Clean up floating windows made by previous versions of this tool."""
    for key in OLD_SESSION_KEYS:
        old_manager = getattr(hou.session, key, None)
        if old_manager is None:
            continue

        try:
            old_window = getattr(old_manager, "floating_window", None)
            cleanup = getattr(old_manager, "cleanup", None)
            if callable(cleanup):
                try:
                    cleanup(restore_playbar=True)
                except TypeError:
                    cleanup()
            if old_window is not None:
                old_window.close()
        except Exception as exc:
            print("Previous Scene Viewer tool cleanup warning: {}".format(exc))

        try:
            delattr(hou.session, key)
        except Exception:
            pass


def _find_main_scene_viewer():
    # Prefer the docked Scene Viewer directly under the cursor.
    try:
        viewer = hou.ui.paneTabUnderCursor()
        if (
            viewer is not None
            and viewer.type() == hou.paneTabType.SceneViewer
            and not viewer.isFloating()
        ):
            return viewer
    except Exception:
        pass

    # Otherwise use a Scene Viewer from the current desktop layout.
    try:
        desktop = hou.ui.curDesktop()
        for viewer in desktop.sceneViewers():
            try:
                if not viewer.isFloating():
                    return viewer
            except Exception:
                continue
    except Exception:
        pass

    try:
        viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
        if viewer is not None and not viewer.isFloating():
            return viewer
    except Exception:
        pass

    return None


def open_floating_scene_viewer():
    existing = getattr(hou.session, SESSION_KEY, None)
    if existing is not None:
        try:
            if (
                existing.floating_window is not None
                and existing.floating_window.isVisible()
            ):
                existing.floating_window.raise_()
                existing.floating_window.activateWindow()
                return existing.floating_viewer
        except Exception:
            pass

    _close_previous_tool_windows()

    source_viewer = _find_main_scene_viewer()
    if source_viewer is None:
        hou.ui.displayMessage(
            "No docked Scene Viewer was found in the current Houdini desktop.\n\n"
            "Click inside your normal Scene Viewer and run this shelf tool again.",
            title=WINDOW_TITLE,
        )
        return None

    manager = FloatingViewerPlaybarManager(source_viewer)
    try:
        viewer = manager.open()
        setattr(hou.session, SESSION_KEY, manager)
        print("Floating Scene Viewer opened.")
        print(
            "The native playbar is in the selected viewer; closing the floating "
            "window returns it to the original Scene Viewer."
        )
        return viewer
    except Exception as exc:
        try:
            manager.restore_playbar()
        except Exception:
            pass
        try:
            if manager.floating_panel is not None:
                manager.floating_panel.close()
            elif manager.floating_window is not None:
                manager.floating_window.close()
        except Exception:
            pass

        message = "Could not open Floating Scene Viewer:\n\n{}: {}".format(
            type(exc).__name__, exc
        )
        print(message)
        hou.ui.displayMessage(message, title=WINDOW_TITLE)
        return None


if __name__ == "__main__":
    open_floating_scene_viewer()
