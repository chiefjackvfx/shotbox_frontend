from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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
import settings
import widgets


class FakeSettingsManager:
    def __init__(self, initial: dict | None = None):
        self._settings = copy.deepcopy(settings.DEFAULT_SETTINGS)
        if initial:
            self._settings.update(initial)

    def get(self, key: str, default=None):
        keys = key.split(".")
        value = self._settings
        try:
            for part in keys:
                value = value[part]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value, save: bool = True):
        keys = key.split(".")
        target = self._settings
        for part in keys[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[keys[-1]] = value

    def save(self):
        return True


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
        self._shot_id = data.get("id")
        self._compact_mode = False

        outer_layout = QVBoxLayout(self)
        self.frame_tasks = QFrame(self)
        self.frame_tasks.setLayout(QVBoxLayout())
        outer_layout.addWidget(self.frame_tasks)

        for task in data.get("tasks", []) or []:
            self.frame_tasks.layout().addWidget(widgets.TaskWidget(task))


class FakeTimelineWidget(QWidget):
    def __init__(self, shots: list[dict], shot_cards: list[FakeShotCard], parent=None):
        super().__init__(parent)
        self._last_timeline = {"shots": list(shots)}
        outer_layout = QVBoxLayout(self)
        self.shots_layout = QVBoxLayout()
        outer_layout.addLayout(self.shots_layout)
        for shot_card in shot_cards:
            self.shots_layout.addWidget(shot_card)


class FilterHarness(QMainWindow):
    _set_task_loading_text = page_nukedash.page_nukedash._set_task_loading_text
    _prepare_task_loading_progress = page_nukedash.page_nukedash._prepare_task_loading_progress
    _timeline_task_loading_key = page_nukedash.page_nukedash._timeline_task_loading_key
    _start_task_loading_timing = page_nukedash.page_nukedash._start_task_loading_timing
    _finish_task_loading_timing = page_nukedash.page_nukedash._finish_task_loading_timing
    _sync_task_loading_status = page_nukedash.page_nukedash._sync_task_loading_status
    _default_saved_filter_state = page_nukedash.page_nukedash._default_saved_filter_state
    _normalize_saved_filter_state = page_nukedash.page_nukedash._normalize_saved_filter_state
    _current_structured_filter_state = page_nukedash.page_nukedash._current_structured_filter_state
    _persist_filter_state = page_nukedash.page_nukedash._persist_filter_state
    _filters_enabled = page_nukedash.page_nukedash._filters_enabled
    _set_filter_controls_enabled = page_nukedash.page_nukedash._set_filter_controls_enabled
    _set_all_task_widgets_visible = page_nukedash.page_nukedash._set_all_task_widgets_visible
    _restore_timeline_api_order = page_nukedash.page_nukedash._restore_timeline_api_order
    _on_enable_filters_changed = page_nukedash.page_nukedash._on_enable_filters_changed
    _current_task_render_state = page_nukedash.page_nukedash._current_task_render_state
    _task_matches_filters = page_nukedash.page_nukedash._task_matches_filters
    _visible_task_ids_for_shot = page_nukedash.page_nukedash._visible_task_ids_for_shot
    _shot_matches_search = page_nukedash.page_nukedash._shot_matches_search
    _reset_task_materialize_queue = page_nukedash.page_nukedash._reset_task_materialize_queue
    _enqueue_task_materialization = page_nukedash.page_nukedash._enqueue_task_materialization
    _process_task_materialize_queue = page_nukedash.page_nukedash._process_task_materialize_queue
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

        self.checkBox_enable_filters = QCheckBox("Enable Filters", central)
        self.checkBox_show_to_conform = QCheckBox("To Conform", central)
        self.checkBox_hidden_shot = QCheckBox("Hidden Shots", central)
        self.checkBox_hidden_tasks = QCheckBox("Hidden Tasks", central)
        self.comboBox_sort = QComboBox(central)
        self.Search_bar = QLineEdit(central)
        self.comboBox_sort_artist = QComboBox(central)
        self.comboBox_sort_status = QComboBox(central)
        self.Label_results = QLabel("", central)
        self.label_task_loading = QLabel("", central)
        self.timelines_tabs = QTabWidget(central)

        layout.addWidget(self.checkBox_enable_filters)
        layout.addWidget(self.checkBox_show_to_conform)
        layout.addWidget(self.checkBox_hidden_shot)
        layout.addWidget(self.checkBox_hidden_tasks)
        layout.addWidget(self.comboBox_sort)
        layout.addWidget(self.Search_bar)
        layout.addWidget(self.comboBox_sort_artist)
        layout.addWidget(self.comboBox_sort_status)
        layout.addWidget(self.Label_results)
        layout.addWidget(self.label_task_loading)
        layout.addWidget(self.timelines_tabs)

        self._settings_manager = FakeSettingsManager()
        self._saved_filter_state = self._default_saved_filter_state()
        self._restoring_filter_controls = False
        self.show_hidden_shots = False
        self.show_hidden_tasks = False
        self.show_to_conform = False
        self._is_scrolling = False
        self._pending_filter_apply = False
        self._search_timer = QTimer(self)
        self._toggle_hidden_timer = QTimer(self)
        self._task_materialize_batch_size = 20
        self._task_materialize_queue = []
        self._task_materialize_timer = QTimer(self)
        self._task_materialize_timer.setSingleShot(True)
        self._task_materialize_timer.timeout.connect(self._process_task_materialize_queue)
        self._set_task_loading_text("Tasks loaded")

        self.comboBox_sort.addItem("A-Z", "title_asc")
        self.comboBox_sort.addItem("Z-A", "title_desc")
        self.comboBox_sort.addItem("Highest Tasks", "task_count_desc")
        self.comboBox_sort.addItem("Lowest Tasks", "task_count_asc")

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
        self.checkBox_enable_filters.setChecked(False)
        self.checkBox_enable_filters.stateChanged.connect(self._on_enable_filters_changed)
        self._set_filter_controls_enabled(self._filters_enabled())

        for index, shots in enumerate(shot_groups):
            shot_cards = [FakeShotCard(shot, self.timelines_tabs) for shot in shots]
            timeline_widget = FakeTimelineWidget(shots, shot_cards, self.timelines_tabs)
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

    def _set_filters_enabled(self, enabled: bool) -> None:
        self.harness.checkBox_enable_filters.setChecked(bool(enabled))
        self.app.processEvents()

    def test_filters_start_disabled_and_controls_are_greyed_out(self):
        self.assertFalse(self.harness.checkBox_enable_filters.isChecked())
        self.assertFalse(self.harness.checkBox_show_to_conform.isEnabled())
        self.assertFalse(self.harness.checkBox_hidden_shot.isEnabled())
        self.assertFalse(self.harness.checkBox_hidden_tasks.isEnabled())
        self.assertFalse(self.harness.comboBox_sort.isEnabled())
        self.assertFalse(self.harness.comboBox_sort_artist.isEnabled())
        self.assertFalse(self.harness.comboBox_sort_status.isEnabled())
        self.assertFalse(self.harness.Search_bar.isEnabled())

    def test_enabling_filters_reenables_filter_controls(self):
        self._set_filters_enabled(True)

        self.assertTrue(self.harness.checkBox_show_to_conform.isEnabled())
        self.assertTrue(self.harness.checkBox_hidden_shot.isEnabled())
        self.assertTrue(self.harness.checkBox_hidden_tasks.isEnabled())
        self.assertTrue(self.harness.comboBox_sort.isEnabled())
        self.assertTrue(self.harness.comboBox_sort_artist.isEnabled())
        self.assertTrue(self.harness.comboBox_sort_status.isEnabled())
        self.assertTrue(self.harness.Search_bar.isEnabled())

    def test_filters_off_shows_all_shots_and_tasks(self):
        self.assertEqual(self._visible_task_ids(101), [1, 2, 3, 4])
        self.assertEqual(self._visible_task_ids(102), [5])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertTrue(self._shot_card(101).isVisible())
        self.assertTrue(self._shot_card(102).isVisible())
        self.assertTrue(self._shot_card(103).isVisible())
        self.assertEqual(self.harness.Label_results.text(), "3 results")

    def test_filters_off_skips_task_visibility_and_sort_logic(self):
        def fail_task_visibility(*args, **kwargs):
            raise AssertionError("_apply_task_visibility should not run when filters are disabled")

        def fail_sort(*args, **kwargs):
            raise AssertionError("_apply_sort_to_timeline should not run when filters are disabled")

        self.harness._apply_task_visibility = fail_task_visibility
        self.harness._apply_sort_to_timeline = fail_sort

        if hasattr(self.harness, "_last_filter_sig"):
            del self.harness._last_filter_sig
        self.harness._apply_filters(force=True)
        self.app.processEvents()

        self.assertEqual(self.harness.Label_results.text(), "3 results")

    def test_turning_filters_off_restores_all_task_widgets_visible(self):
        self._set_filters_enabled(True)
        self._select_status("done")

        self.assertEqual(self._visible_task_ids(101), [1])
        self.assertFalse(self._shot_card(102).isVisible())

        self._set_filters_enabled(False)

        self.assertEqual(self._visible_task_ids(101), [1, 2, 3, 4])
        self.assertEqual(self._visible_task_ids(102), [5])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertTrue(self._shot_card(102).isVisible())
        self.assertEqual(self.harness.Label_results.text(), "3 results")

    def test_done_filter_hides_non_matching_tasks_and_shots(self):
        self._set_filters_enabled(True)
        self._select_status("done")

        self.assertEqual(self.harness._status_filter_values, {"done"})
        self.assertEqual(self._visible_task_ids(101), [1])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertFalse(self._shot_card(102).isVisible())
        self.assertEqual(self.harness.Label_results.text(), "2 results")

    def test_multiple_statuses_use_or_semantics_per_task(self):
        self._set_filters_enabled(True)
        self._select_status("done")
        self._select_status("approved")

        self.assertEqual(self.harness._status_filter_values, {"done", "approved"})
        self.assertEqual(self._visible_task_ids(101), [1, 3])
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertFalse(self._shot_card(102).isVisible())

    def test_artist_and_status_filters_intersect_on_same_task(self):
        self._set_filters_enabled(True)
        self._select_status("done")
        self._set_artist_filter(2)

        self.assertFalse(self._shot_card(101).isVisible())
        self.assertFalse(self._shot_card(102).isVisible())
        self.assertEqual(self._visible_task_ids(103), [6])
        self.assertEqual(self.harness.Label_results.text(), "1 results")

    def test_clearing_filters_restores_all_non_hidden_tasks(self):
        self._set_filters_enabled(True)
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
        self._set_filters_enabled(True)
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
