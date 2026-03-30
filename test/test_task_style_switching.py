from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

import widgets


def make_task(task_id: int, *, title: str, status: str, artist=None) -> dict:
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "artist": artist,
        "hidden": False,
        "notes": "",
        "priority": 5,
        "budget_hours": 0,
    }


class FakeTaskApi:
    def __init__(self, task_data: dict):
        self.task_data = dict(task_data)

    def update_task(self, task_id: int, **fields):
        updated = dict(self.task_data)
        updated.update(fields)
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

    def test_checklist_style_rebuilds_loaded_widgets_and_moves_done_last(self):
        card = MinimalStyleShotCard(
            {
                "id": 999,
                "title": "Shot 999",
                "tasks": [
                    make_task(11, title="Done first", status="done"),
                    make_task(12, title="Active second", status="assigned"),
                ],
            }
        )
        try:
            card.set_visible_task_ids([11, 12])
            card.materialize_task_ids([11, 12])

            before = []
            layout = card.frame_tasks.layout()
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget() if item else None
                if isinstance(widget, widgets.TaskWidget):
                    before.append((widget._data.get("id"), widget._presentation))

            self.assertEqual(before, [(11, "card"), (12, "card")])

            card.set_task_style("checklist")

            after = []
            layout = card.frame_tasks.layout()
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget() if item else None
                if isinstance(widget, widgets.TaskWidget):
                    after.append((widget._data.get("id"), widget._presentation))

            self.assertEqual(after, [(12, "checklist"), (11, "checklist")])
        finally:
            card.close()
            card.deleteLater()
            self.app.processEvents()

    def test_checklist_done_toggle_restores_previous_non_done_status(self):
        task = make_task(21, title="Toggle me", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            widget.check_done_task.setChecked(True)
            self.app.processEvents()
            self.assertEqual(widget._data.get("status"), "done")

            widget.check_done_task.setChecked(False)
            self.app.processEvents()
            self.assertEqual(widget._data.get("status"), "assigned")
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_done_state_keeps_title_readable_without_strikeout(self):
        task = make_task(22, title="Readable done task", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
            widget.check_done_task.setChecked(True)
            self.app.processEvents()
            self.assertFalse(widget.btn_task_title.font().strikeOut())

            widget.check_done_task.setChecked(False)
            self.app.processEvents()
            self.assertFalse(widget.btn_task_title.font().strikeOut())
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()

    def test_checklist_inline_notes_editor_updates_task_notes(self):
        task = make_task(23, title="Inline notes", status="assigned")
        widget = widgets.TaskWidget(task, presentation="checklist")
        widget._api = FakeTaskApi(task)
        try:
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


if __name__ == "__main__":
    unittest.main()
