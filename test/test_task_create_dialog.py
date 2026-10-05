from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from task_create_dialog import TaskCreateDialog


class FakeApi:
    def get_users(self):
        return [{"id": 7, "first_name": "Artist"}]


class TaskCreateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def show_dialog(self):
        dialog = TaskCreateDialog(api=FakeApi())
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.activateWindow()
        self.app.processEvents()
        return dialog

    def test_enter_accepts_after_clicking_every_preset_and_artist(self):
        sample = self.show_dialog()
        buttons = [
            (button.objectName(), button.text())
            for button in sample.findChildren(QPushButton)
            if button.objectName() in {"task_preset_button", "task_artist_button"}
        ]
        sample.close()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            for name, text in buttons:
                with self.subTest(key=key, preset=text, name=name):
                    dialog = self.show_dialog()
                    button = next(b for b in dialog.findChildren(QPushButton, name) if b.text() == text)
                    accepted = []
                    dialog.accepted.connect(lambda: accepted.append(dialog.get_values()))
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    self.app.processEvents()
                    self.assertTrue(dialog.isVisible())
                    values = dialog.get_values()
                    focus = self.app.focusWidget()
                    self.assertIsNotNone(focus)
                    QTest.keyClick(focus, key)
                    self.app.processEvents()
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                    self.assertEqual(accepted, [values])
                    dialog.close()

    def test_enter_commits_pending_budget_and_priority_edits(self):
        for field, text, expected in (("budget_spin", "2.75", 2.75), ("priority_spin", "8", 8)):
            with self.subTest(field=field):
                dialog = self.show_dialog()
                spin = getattr(dialog, field)
                spin.setKeyboardTracking(False)
                spin.setFocus()
                spin.selectAll()
                QTest.keyClicks(spin, text)
                QTest.keyClick(spin, Qt.Key.Key_Return)
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                value = "budget_hours" if field == "budget_spin" else "priority"
                self.assertEqual(dialog.get_values()[value], expected)
                dialog.close()

    def test_enter_selects_status_in_open_dropdown_without_creating_task(self):
        dialog = self.show_dialog()
        dialog.status_combo.showPopup()
        self.app.processEvents()
        view = dialog.status_combo.view()
        view.setCurrentIndex(dialog.status_combo.model().index(1, 0))
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.status_combo.currentIndex(), 1)

    def test_escape_and_cancel_still_reject_dialog_after_preset_click(self):
        for action in ("escape", "cancel"):
            with self.subTest(action=action):
                dialog = self.show_dialog()
                preset = dialog.findChild(QPushButton, "task_preset_button")
                QTest.mouseClick(preset, Qt.MouseButton.LeftButton)
                if action == "escape":
                    QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Escape)
                else:
                    cancel = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Cancel")
                    QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
                self.assertFalse(dialog.isVisible())
                self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)


if __name__ == "__main__":
    unittest.main()
