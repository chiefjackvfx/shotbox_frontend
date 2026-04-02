from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtWidgets import QApplication, QWidget

from flow_layout import FlowLayout
import widgets


class FixedSizeWidget(QWidget):
    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)
        self._size = QSize(width, height)

    def sizeHint(self) -> QSize:
        return QSize(self._size)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._size)


class FlowLayoutHiddenWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hidden_widget_does_not_leave_gap_in_flow_layout(self):
        container = QWidget()
        layout = FlowLayout(container, margin=0, h_spacing=10, v_spacing=10)
        container.setLayout(layout)

        first = FixedSizeWidget(50, 20, container)
        second = FixedSizeWidget(50, 20, container)
        third = FixedSizeWidget(50, 20, container)

        layout.addWidget(first)
        layout.addWidget(second)
        layout.addWidget(third)

        second.hide()
        container.resize(120, 100)
        layout.setGeometry(QRect(0, 0, 120, 100))
        self.app.processEvents()

        self.assertEqual(first.geometry().y(), third.geometry().y())
        self.assertGreater(third.geometry().x(), first.geometry().x())

    def test_ensure_order_reorders_flow_layout_without_rebuilding_widgets(self):
        container = QWidget()
        layout = FlowLayout(container, margin=0, h_spacing=10, v_spacing=10)
        container.setLayout(layout)

        first = FixedSizeWidget(50, 20, container)
        first.setObjectName("shot-1")
        second = FixedSizeWidget(50, 20, container)
        second.setObjectName("shot-2")
        third = FixedSizeWidget(50, 20, container)
        third.setObjectName("shot-3")

        layout.addWidget(first)
        layout.addWidget(second)
        layout.addWidget(third)

        widgets._ensure_order(layout, ["shot-3", "shot-1", "shot-2"])

        ordered_widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        self.assertEqual(
            [widget.objectName() for widget in ordered_widgets],
            ["shot-3", "shot-1", "shot-2"],
        )
        self.assertIs(ordered_widgets[0], third)
        self.assertIs(ordered_widgets[1], first)
        self.assertIs(ordered_widgets[2], second)


if __name__ == "__main__":
    unittest.main()
