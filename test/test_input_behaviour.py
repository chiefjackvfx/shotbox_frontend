from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDateTimeEdit, QDial, QDialog, QDoubleSpinBox,
    QScrollArea, QSlider, QSpinBox, QTabBar, QTextEdit, QVBoxLayout, QWidget,
)

from input_behaviour import install_input_wheel_guard


@pytest.fixture(scope='session')
def app():
    app = QApplication.instance() or QApplication([])
    previous = getattr(app, '_input_wheel_guard', None)
    guard = install_input_wheel_guard(app)
    yield app
    if previous is None:
        app.removeEventFilter(guard)
        del app._input_wheel_guard
        guard.deleteLater()
    app.processEvents()


def wheel(widget, *, pixels=False):
    local = QPointF(widget.rect().center())
    event = QWheelEvent(
        local, QPointF(widget.mapToGlobal(local.toPoint())),
        QPoint(0, -30) if pixels else QPoint(), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate if pixels else Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize('focused', [False, True])
@pytest.mark.parametrize('kind', ['combo', 'editable_combo', 'spin', 'double', 'date', 'slider', 'dial', 'tabs'])
def test_wheel_scrolls_dialog_without_editing_inputs(app, kind, focused):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    scroll = QScrollArea()
    layout.addWidget(scroll)
    content = QWidget()
    content.setMinimumSize(400, 1200)
    scroll.setWidget(content)
    if kind in ('combo', 'editable_combo'):
        field = QComboBox(content)
        field.addItems(['one', 'two', 'three'])
        field.setCurrentIndex(1)
        field.setEditable(kind == 'editable_combo')
        read = field.currentText
    elif kind == 'date':
        field = QDateTimeEdit(content)
        read = field.dateTime
    elif kind == 'tabs':
        field = QTabBar(content)
        for label in ('one', 'two', 'three'):
            field.addTab(label)
        field.setCurrentIndex(1)
        read = field.currentIndex
    else:
        field = {'spin': QSpinBox, 'double': QDoubleSpinBox,
                 'slider': QSlider, 'dial': QDial}[kind](content)
        field.setValue(50)
        read = field.value
    field.move(10, 400)
    dialog.show()
    app.processEvents()
    scroll.verticalScrollBar().setValue(350)
    field.setFocus() if focused else field.clearFocus()
    before = read()
    targets = [field]
    if kind in ('spin', 'double', 'date', 'editable_combo'):
        targets.append(field.lineEdit())
    try:
        for target in targets:
            for pixels in (False, True):
                offset = scroll.verticalScrollBar().value()
                wheel(target, pixels=pixels)
                assert read() == before
                assert scroll.verticalScrollBar().value() > offset
    finally:
        dialog.close()


def test_text_editors_and_scrollbars_still_scroll(app):
    editor = QTextEdit()
    editor.setPlainText('\n'.join(str(i) for i in range(200)))
    editor.show()
    app.processEvents()
    try:
        wheel(editor.viewport())
        assert editor.verticalScrollBar().value() > 0
        before = editor.verticalScrollBar().value()
        wheel(editor.verticalScrollBar())
        assert editor.verticalScrollBar().value() > before
        assert editor.toPlainText().startswith('0\n1\n')
    finally:
        editor.close()


def test_combo_popup_scrolls_and_keyboard_selection_still_works(app):
    combo = QComboBox()
    combo.addItems([str(i) for i in range(100)])
    combo.setMaxVisibleItems(5)
    combo.show()
    combo.showPopup()
    app.processEvents()
    try:
        view = combo.view()
        view.setFixedHeight(100)
        app.processEvents()
        wheel(view.viewport())
        assert view.verticalScrollBar().value() > 0
        assert combo.currentIndex() == 0
        combo.hidePopup()
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() == 1
    finally:
        combo.close()


def test_guard_installation_is_shared_across_windows(app):
    assert install_input_wheel_guard(app) is app._input_wheel_guard
