"""Keep wheel scrolling from editing form values throughout the application."""

from PyQt6.QtCore import QEvent, QObject, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QComboBox,
    QScrollBar, QTabBar, QWidget,
)


class InputWheelGuard(QObject):
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        control = watched
        while control is not None:
            # Lists, editors and scroll bars retain their scrolling behavior,
            # including the list in an open combo box popup.
            if isinstance(control, (QAbstractScrollArea, QScrollBar)):
                return False
            if isinstance(control, (QComboBox, QAbstractSpinBox, QAbstractSlider, QTabBar)):
                break
            if control.isWindow():
                return False
            control = control.parentWidget()
        if control is None:
            return False

        # Forward to the surrounding page so hovering an input doesn't leave
        # a dead patch in a scrollable form. Preserve trackpad deltas as well.
        parent = control.parentWidget() if not control.isWindow() else None
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                viewport = parent.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                    device=event.pointingDevice(),
                )
                forwarded.ignore()
                self.parent().sendEvent(viewport, forwarded)
                if forwarded.isAccepted():
                    event.accept()
                    return True
            if parent.isWindow():
                break
            parent = parent.parentWidget()
        event.ignore()
        return True


def install_input_wheel_guard(app):
    """Install once, retaining the filter for all current and future dialogs."""
    if not hasattr(app, '_input_wheel_guard'):
        app._input_wheel_guard = InputWheelGuard(app)
        app.installEventFilter(app._input_wheel_guard)
    return app._input_wheel_guard
