from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import page_nukedash
import widgets


def make_task(
    task_id: int,
    *,
    title: str,
    status: str,
    artist: int | None,
    hidden: bool = False,
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "artist": artist,
        "hidden": hidden,
        "notes": "",
        "priority": 5,
        "budget_hours": 0,
    }


class FakeShotCard(widgets.ShotCard):
    def __init__(self, data: dict, parent=None):
        QWidget.__init__(self, parent)
        self.data = data

        outer_layout = QVBoxLayout(self)
        self.frame_tasks = QFrame(self)
        self.frame_tasks.setLayout(QVBoxLayout())
        outer_layout.addWidget(self.frame_tasks)

        for task in data.get("tasks", []) or []:
            self.frame_tasks.layout().addWidget(widgets.TaskWidget(task))


class FakeTimelineWidget(QWidget):
    def __init__(self, shot_cards: list[FakeShotCard], parent=None):
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        self.shots_layout = QVBoxLayout()
        outer_layout.addLayout(self.shots_layout)
        for shot_card in shot_cards:
            self.shots_layout.addWidget(shot_card)


class FilterHarness(QMainWindow):
    _apply_filters = page_nukedash.page_nukedash._apply_filters
    _apply_task_visibility = page_nukedash.page_nukedash._apply_task_visibility
    _get_searchable_text = page_nukedash.page_nukedash._get_searchable_text
    _on_status_combo_activated = page_nukedash.page_nukedash._on_status_combo_activated
    _on_status_item_changed = page_nukedash.page_nukedash._on_status_item_changed
    _on_status_popup_pressed = page_nukedash.page_nukedash._on_status_popup_pressed
    _set_all_status_filters = page_nukedash.page_nukedash._set_all_status_filters
    _setup_status_filter_dropdown = page_nukedash.page_nukedash._setup_status_filter_dropdown
    _update_status_filter_label = page_nukedash.page_nukedash._update_status_filter_label

    def __init__(self, shot_groups: list[list[dict]]):
        super().__init__()

        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.Search_bar = QLineEdit(central)
        self.comboBox_sort_artist = QComboBox(central)
        self.comboBox_sort_status = QComboBox(central)
        self.Label_results = QLabel("", central)
        self.timelines_tabs = QTabWidget(central)

        layout.addWidget(self.Search_bar)
        layout.addWidget(self.comboBox_sort_artist)
        layout.addWidget(self.comboBox_sort_status)
        layout.addWidget(self.Label_results)
        layout.addWidget(self.timelines_tabs)

        self.show_hidden_shots = False
        self.show_hidden_tasks = False
        self.show_to_conform = False
        self._is_scrolling = False
        self._pending_filter_apply = False

        self.comboBox_sort_artist.addItem("All Artists", None)
        artist_ids = sorted(
            {
                task.get("artist")
                for shots in shot_groups
                for shot in shots
                for task in shot.get("tasks", []) or []
                if task.get("artist") is not None
            }
        )
        for artist_id in artist_ids:
            self.comboBox_sort_artist.addItem(f"Artist {artist_id}", artist_id)

        self._setup_status_filter_dropdown()

        for index, shots in enumerate(shot_groups):
            shot_cards = [FakeShotCard(shot, self.timelines_tabs) for shot in shots]
            timeline_widget = FakeTimelineWidget(shot_cards, self.timelines_tabs)
            self.timelines_tabs.addTab(timeline_widget, f"Timeline {index + 1}")

        self.show()
        QApplication.processEvents()

    def _apply_sort_to_timeline(self, timeline_widget, hide_hidden_tasks):
        return

    def _current_sort_mode(self) -> str:
        return "title_asc"

    def _shot_needs_conform(self, shot_card, shot_data: dict) -> bool:
        return False


class FakeApi:
    def __init__(self, updated_task: dict):
        self.updated_task = updated_task

    def update_task(self, task_id: int, **fields):
        updated = dict(self.updated_task)
        updated.update(fields)
        return updated


class NukeDashStatusFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.harness = FilterHarness(
            [
                [
                    {
                        "id": 101,
                        "title": "Shot 101",
                        "notes": "",
                        "hidden": False,
                        "tasks": [
                            make_task(1, title="Comp done", status="done", artist=1),
                            make_task(2, title="Paint assigned", status="assigned", artist=2),
                            make_task(3, title="Prep approved", status="approved", artist=1),
                            make_task(4, title="Hidden done", status="done", artist=1, hidden=True),
                        ],
                    },
                    {
                        "id": 102,
                        "title": "Shot 102",
                        "notes": "",
                        "hidden": False,
                        "tasks": [
                            make_task(5, title="Roto assigned", status="assigned", artist=2),
                        ],
                    },
                    {
                        "id": 103,
                        "title": "Shot 103",
                        "notes": "",
                        "hidden": False,
                        "tasks": [
                            make_task(6, title="Track done", status="done", artist=2),
                        ],
                    },
                ]
            ]
        )
        self.harness._apply_filters(force=True)
        self.app.processEvents()

    def tearDown(self):
        self.harness.close()
        self.harness.deleteLater()
        self.app.processEvents()

    def _timeline_widget(self):
        return self.harness.timelines_tabs.widget(0)

    def _shot_cards(self) -> list[FakeShotCard]:
        cards = []
        timeline_widget = self._timeline_widget()
        for index in range(timeline_widget.shots_layout.count()):
            item = timeline_widget.shots_layout.itemAt(index)
            widget = item.widget() if item else None
            if isinstance(widget, FakeShotCard):
                cards.append(widget)
        return cards

    def _shot_card(self, shot_id: int) -> FakeShotCard:
        for shot_card in self._shot_cards():
            if shot_card.data.get("id") == shot_id:
                return shot_card
        raise AssertionError(f"Shot {shot_id} not found")

    def _visible_task_ids(self, shot_id: int) -> list[int]:
        shot_card = self._shot_card(shot_id)
        visible_ids = []
        layout = shot_card.frame_tasks.layout()
        for index in range(layout.count()):
            item = layout.itemAt(index)
            task_widget = item.widget() if item else None
            if isinstance(task_widget, widgets.TaskWidget) and task_widget.isVisible():
                visible_ids.append(task_widget._data.get("id"))
        return visible_ids

    def _task_widget(self, task_id: int) -> widgets.TaskWidget:
        for shot_card in self._shot_cards():
            layout = shot_card.frame_tasks.layout()
            for index in range(layout.count()):
                item = layout.itemAt(index)
                task_widget = item.widget() if item else None
                if isinstance(task_widget, widgets.TaskWidget) and task_widget._data.get("id") == task_id:
                    return task_widget
        raise AssertionError(f"Task {task_id} not found")

    def _set_artist_filter(self, artist_id: int | None) -> None:
        combo = self.harness.comboBox_sort_artist
        for index in range(combo.count()):
            if combo.itemData(index) == artist_id:
                combo.setCurrentIndex(index)
                break
        else:
            raise AssertionError(f"Artist filter {artist_id} not found")
        self.harness._apply_filters(force=True)
        self.app.processEvents()

    def _select_status(self, value: str) -> None:
        model = self.harness.comboBox_sort_status.model()
        item = self.harness._status_filter_items[value]
        self.harness._on_status_popup_pressed(model.indexFromItem(item))
        self.app.processEvents()

    def _clear_status_filter(self) -> None:
        self.harness._on_status_combo_activated(1)
        self.app.processEvents()

    def test_done_filter_hides_non_matching_tasks_and_shots(self):
        self._select_status("done")

        self.assertEqual(self.harness._status_filter_values, {"done"})
        self.assertEqual(self._visible_task_ids(101), [1])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertFalse(self._shot_card(102).isVisible())
        self.assertEqual(self.harness.Label_results.text(), "2 results")

    def test_multiple_statuses_use_or_semantics_per_task(self):
        self._select_status("done")
        self._select_status("approved")

        self.assertEqual(self.harness._status_filter_values, {"done", "approved"})
        self.assertEqual(self._visible_task_ids(101), [1, 3])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertFalse(self._shot_card(102).isVisible())

    def test_artist_and_status_filters_intersect_on_same_task(self):
        self._select_status("done")
        self._set_artist_filter(2)

        self.assertFalse(self._shot_card(101).isVisible())
        self.assertFalse(self._shot_card(102).isVisible())
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertEqual(self.harness.Label_results.text(), "1 results")

    def test_clearing_filters_restores_all_non_hidden_tasks(self):
        self._select_status("done")
        self._set_artist_filter(2)

        self._clear_status_filter()
        self._set_artist_filter(None)

        self.assertEqual(self._visible_task_ids(101), [1, 2, 3])
        self.assertEqual(self._visible_task_ids(102), [5])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertTrue(self._shot_card(101).isVisible())
        self.assertTrue(self._shot_card(102).isVisible())
        self.assertTrue(self._shot_card(103).isVisible())

    def test_status_edit_reapplies_filters_immediately(self):
        self._select_status("done")

        task_widget = self._task_widget(6)
        updated_task = dict(task_widget._data)
        updated_task["status"] = "assigned"
        task_widget._api = FakeApi(updated_task)

        action = QAction("Assigned", task_widget)
        action.setData("assigned")
        task_widget._on_status_action(action)
        self.app.processEvents()

        self.assertEqual(task_widget._data.get("status"), "assigned")
        self.assertEqual(self._shot_card(103).data["tasks"][0]["status"], "assigned")
        self.assertFalse(self._shot_card(103).isVisible())
        self.assertEqual(self.harness.Label_results.text(), "1 results")


if __name__ == "__main__":
    unittest.main()
