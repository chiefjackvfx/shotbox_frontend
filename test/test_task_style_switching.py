from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

import widgets


def make_task(task_id: int, *, title: str, status: str, artist=None, progress=None) -> dict:
    if progress is None:
        progress = 100 if status in {"waiting_for_approval", "approved", "done"} else 0
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "progress": progress,
        "artist": artist,
        "hidden": False,
        "notes": "",
        "priority": 5,
        "budget_hours": 0,
    }


class FakeTaskApi:
    def __init__(self, task_data: dict):
        self.task_data = dict(task_data)
        self.calls = []

    def update_task(self, task_id: int, **fields):
        self.calls.append({"task_id": task_id, "fields": dict(fields)})
        updated = dict(self.task_data)
        updated.update(fields)
        if updated.get("status") in {"waiting_for_approval", "approved", "done"}:
            updated["progress"] = 100
        else:
            try:
                updated["progress"] = max(0, min(100, int(updated.get("progress", 0))))
            except Exception:
                updated["progress"] = 0
        self.task_data = dict(updated)
        return updated

    def username_from_id(self, user_id):
        if user_id in (None, "", 0):
            return "Unassigned"
        return f"Artist {user_id}"

    def get_users(self):
        return []


class MinimalStyleShotCard(widgets.ShotCard):
    def __init__(self, data: dict, parent=None):
        QWidget.__init__(self, parent)
        self.data = data
        self._compact_mode = False
        self._task_style = "card"
        self._task_render_state = {}
        self._task_widgets_by_id = {}
        self._visible_task_ids = []
        self._set_hide_button_label = lambda: None
        self._set_nuke_button_label = lambda: None
        self._set_render_button_text = lambda: None
        self._apply_compact_tooltips = lambda: None
        self._apply_thumb_scale = lambda: None

        outer_layout = QVBoxLayout(self)
        self.frame_tasks = QFrame(self)
        self.frame_tasks.setLayout(QVBoxLayout())
        outer_layout.addWidget(self.frame_tasks)


class TaskStyleSwitchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _show_widget(self, widget: QWidget) -> None:
        widget.show()
        widget.raise_()
        widget.activateWindow()
        self.app.processEvents()

    def _wait_for_single_click(self) -> None:
        QTest.qWait(self.app.doubleClickInterval() + 30)
        self.app.processEvents()

    def test_checklist_style_rebuilds_loaded_widgets_and_moves_completed_last(self):
        card = MinimalStyleShotCard(
            {
                "id": 999,
                "title": "Shot 999",
                "tasks": [
                    make_task(11, title="Done first", status="done"),
                    make_task(12, title="Active second", status="assigned"),
                    make_task(13, title="Waiting third", status="waiting_for_approval"),
                    make_task(14, title="Approved fourth", status="approved"),
                ],
            }
        )
        try:
            card.set_visible_task_ids([11, 12, 13, 14])
            card.materialize_task_ids([11, 12, 13, 14])

            before = []
            layout = card.frame_tasks.layout()
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget() if item else None
                if isinstance(widget, widgets.TaskWidget):
                    before.append((widget._data.get("id"), widget._presentation))

            self.assertEqual(
                before,
                [(11, "card"), (12, "card"), (13, "card"), (14, "card")],
            )

            card.set_task_style("checklist")

            after = []
            layout = card.frame_tasks.layout()
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget() if item else None
                if isinstance(widget, widgets.TaskWidget):
                    after.append((widget._data.get("id"), widget._presentation))

            self.assertEqual(
                after,
                [(12, "checklist"), (13, "checklist"), (11, "checklist"), (14, "checklist")],
            )
        finally:
            card.close()
            card.deleteLater()
            self.app.processEvents()

    def test_checklist_progress_left_clicks_move_to_waiting_then_approved(self):
        task = make_task(21, title="Toggle me", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            self.assertEqual(widget.check_done_task.text(), "○")

            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            self._wait_for_single_click()
            self.assertEqual(widget._data.get("progress"), 25)
            self.assertEqual(widget._data.get("status"), "assigned")
            self.assertEqual(widget.check_done_task.text(), "◔")

            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            self._wait_for_single_click()
            self.assertEqual(widget._data.get("progress"), 50)
            self.assertEqual(widget.check_done_task.text(), "◑")

            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            self._wait_for_single_click()
            self.assertEqual(widget._data.get("progress"), 75)
            self.assertEqual(widget.check_done_task.text(), "◕")

            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            self._wait_for_single_click()
            self.assertEqual(widget._data.get("progress"), 100)
            self.assertEqual(widget._data.get("status"), "waiting_for_approval")
            self.assertEqual(widget.check_done_task.text(), "●")

            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            self._wait_for_single_click()
            self.assertEqual(widget._data.get("progress"), 100)
            self.assertEqual(widget._data.get("status"), "approved")
            self.assertEqual(widget._api.calls[-1]["fields"], {"status": "approved", "progress": 100})
            self.assertFalse(widget.btn_task_title.font().strikeOut())
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_right_click_reduces_progress_and_uncompletes_terminal_status(self):
        task = make_task(22, title="Readable done task", status="approved")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.RightButton)
            self.app.processEvents()
            self.assertEqual(widget._data.get("progress"), 75)
            self.assertEqual(widget._data.get("status"), "in_progress")
            self.assertEqual(widget.check_done_task.text(), "◕")
            self.assertFalse(widget.btn_task_title.font().strikeOut())
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_double_click_jumps_to_waiting_without_extra_increment(self):
        task = make_task(23, title="Jump me", status="assigned", progress=25)
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            QTest.mouseDClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            QTest.qWait(self.app.doubleClickInterval() + 30)
            self.app.processEvents()

            self.assertEqual(widget._data.get("progress"), 100)
            self.assertEqual(widget._data.get("status"), "waiting_for_approval")
            self.assertEqual(widget.check_done_task.text(), "●")
            self.assertEqual(len(widget._api.calls), 1)
            self.assertEqual(
                widget._api.calls[0]["fields"],
                {"progress": 100, "status": "waiting_for_approval"},
            )
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_left_click_on_approved_is_noop(self):
        task = make_task(24, title="Already approved", status="approved")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.LeftButton)
            self._wait_for_single_click()

            self.assertEqual(widget._data.get("progress"), 100)
            self.assertEqual(widget._data.get("status"), "approved")
            self.assertEqual(widget._api.calls, [])
            self.assertFalse(widget.btn_task_title.font().strikeOut())
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_right_click_on_zero_progress_is_noop(self):
        task = make_task(25, title="Zero progress", status="assigned", progress=0)
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            QTest.mouseClick(widget.check_done_task, Qt.MouseButton.RightButton)
            self.app.processEvents()

            self.assertEqual(widget._data.get("progress"), 0)
            self.assertEqual(widget._data.get("status"), "assigned")
            self.assertEqual(widget._api.calls, [])
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_notes_editor_updates_task_notes(self):
        task = make_task(26, title="Inline notes", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            self.assertTrue(hasattr(widget, "edit_notes_inline"))
            self.assertEqual(widget.edit_notes_inline.text(), "")

            widget.edit_notes_inline.setText("Needs lens flare cleanup")
            widget.edit_notes_inline.editingFinished.emit()
            self.app.processEvents()

            self.assertEqual(widget._data.get("notes"), "Needs lens flare cleanup")
            self.assertEqual(widget.edit_notes_inline.text(), "Needs lens flare cleanup")
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_title_click_enters_inline_edit_mode(self):
        task = make_task(27, title="Track roto", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            self.assertFalse(widget._title_edit_active)
            self.assertIs(widget.title_stack.currentWidget(), widget.btn_task_title)

            widget.btn_task_title.click()
            self.app.processEvents()

            self.assertTrue(widget._title_edit_active)
            self.assertIs(widget.title_stack.currentWidget(), widget.edit_title_inline)
            self.assertEqual(widget.edit_title_inline.text(), "Track roto")
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_title_editor_updates_task_title_on_enter(self):
        task = make_task(28, title="Prep comp", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            widget.btn_task_title.click()
            self.app.processEvents()

            widget.edit_title_inline.setText("Prep final comp")
            QTest.keyClick(widget.edit_title_inline, Qt.Key.Key_Return)
            self.app.processEvents()

            self.assertEqual(widget._data.get("title"), "Prep final comp")
            self.assertFalse(widget._title_edit_active)
            self.assertIs(widget.title_stack.currentWidget(), widget.btn_task_title)
            self.assertEqual(widget._api.calls[-1]["fields"], {"title": "Prep final comp"})
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_title_editor_saves_on_focus_loss(self):
        task = make_task(29, title="Integrate pass", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            widget.btn_task_title.click()
            self.app.processEvents()

            widget.edit_title_inline.setText("Integrate final pass")
            widget.edit_notes_inline.setFocus(Qt.FocusReason.OtherFocusReason)
            self.app.processEvents()

            self.assertEqual(widget._data.get("title"), "Integrate final pass")
            self.assertFalse(widget._title_edit_active)
            self.assertEqual(widget._api.calls[-1]["fields"], {"title": "Integrate final pass"})
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_title_editor_escape_cancels(self):
        task = make_task(27, title="Cleanup matte", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            widget.btn_task_title.click()
            self.app.processEvents()

            widget.edit_title_inline.setText("Cleanup matte v2")
            QTest.keyClick(widget.edit_title_inline, Qt.Key.Key_Escape)
            self.app.processEvents()

            self.assertEqual(widget._data.get("title"), "Cleanup matte")
            self.assertFalse(widget._title_edit_active)
            self.assertIs(widget.title_stack.currentWidget(), widget.btn_task_title)
            self.assertEqual(widget._api.calls, [])
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_title_editor_unchanged_title_skips_api_update(self):
        task = make_task(28, title="Plate prep", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            widget.btn_task_title.click()
            self.app.processEvents()

            widget.edit_title_inline.setText("Plate prep")
            widget.edit_title_inline.editingFinished.emit()
            self.app.processEvents()

            self.assertEqual(widget._data.get("title"), "Plate prep")
            self.assertFalse(widget._title_edit_active)
            self.assertEqual(widget._api.calls, [])
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_title_editor_empty_title_reverts(self):
        task = make_task(29, title="Keying pass", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            self._show_widget(widget)
            widget.btn_task_title.click()
            self.app.processEvents()

            widget.edit_title_inline.setText("   ")
            widget.edit_title_inline.editingFinished.emit()
            self.app.processEvents()

            self.assertEqual(widget._data.get("title"), "Keying pass")
            self.assertFalse(widget._title_edit_active)
            self.assertEqual(widget._api.calls, [])
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_card_title_click_still_uses_dialog_editor(self):
        task = make_task(30, title="Card title", status="assigned")
        widget = widgets.TaskWidget(task, presentation="card")
        widget._api = FakeTaskApi(task)

        class FakeDialog:
            def __init__(self, *args, **kwargs):
                return

            def exec(self):
                return widgets.QDialog.DialogCode.Accepted

            def value(self):
                return "Card title updated"

        try:
            self._show_widget(widget)
            self.assertFalse(hasattr(widget, "edit_title_inline"))

            with mock.patch.object(widgets, "PlainTextEditDialog", FakeDialog):
                widget.btn_task_title.click()
                self.app.processEvents()

            self.assertEqual(widget._data.get("title"), "Card title updated")
            self.assertEqual(widget._api.calls[-1]["fields"], {"title": "Card title updated"})
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
