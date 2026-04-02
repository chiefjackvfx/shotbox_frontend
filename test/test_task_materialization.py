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


class LazyFakeShotCard(widgets.ShotCard):
    def __init__(self, data: dict, parent=None):
        QWidget.__init__(self, parent)
        self.data = copy.deepcopy(data)
        self._shot_id = data.get("id")
        self._compact_mode = False
        self._task_render_state = {}
        self._task_widgets_by_id = {}
        self._visible_task_ids = []

        outer_layout = QVBoxLayout(self)
        self.frame_tasks = QFrame(self)
        self.frame_tasks.setLayout(QVBoxLayout())
        outer_layout.addWidget(self.frame_tasks)


class FakeTimelineWidget(QWidget):
    def __init__(self, shots: list[dict], shot_cards: list[LazyFakeShotCard], parent=None):
        super().__init__(parent)
        self._last_timeline = {"shots": copy.deepcopy(shots)}
        outer_layout = QVBoxLayout(self)
        self.shots_layout = QVBoxLayout()
        outer_layout.addLayout(self.shots_layout)
        for shot_card in shot_cards:
            self.shots_layout.addWidget(shot_card)


class MaterializationHarness(QMainWindow):
    _set_task_loading_text = page_nukedash.page_nukedash._set_task_loading_text
    _prepare_task_loading_progress = page_nukedash.page_nukedash._prepare_task_loading_progress
    _timeline_task_loading_key = page_nukedash.page_nukedash._timeline_task_loading_key
    _start_task_loading_timing = page_nukedash.page_nukedash._start_task_loading_timing
    _finish_task_loading_timing = page_nukedash.page_nukedash._finish_task_loading_timing
    _sync_task_loading_status = page_nukedash.page_nukedash._sync_task_loading_status
    _default_saved_filter_state = page_nukedash.page_nukedash._default_saved_filter_state
    _normalize_saved_filter_state = page_nukedash.page_nukedash._normalize_saved_filter_state
    _load_saved_filter_state = page_nukedash.page_nukedash._load_saved_filter_state
    _current_structured_filter_state = page_nukedash.page_nukedash._current_structured_filter_state
    _persist_filter_state = page_nukedash.page_nukedash._persist_filter_state
    _apply_saved_artist_filter = page_nukedash.page_nukedash._apply_saved_artist_filter
    _apply_saved_status_filter_values = page_nukedash.page_nukedash._apply_saved_status_filter_values
    _apply_saved_filter_state_to_controls = page_nukedash.page_nukedash._apply_saved_filter_state_to_controls
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
    _on_status_combo_activated = page_nukedash.page_nukedash._on_status_combo_activated
    _on_status_item_changed = page_nukedash.page_nukedash._on_status_item_changed
    _on_status_popup_pressed = page_nukedash.page_nukedash._on_status_popup_pressed
    _set_all_status_filters = page_nukedash.page_nukedash._set_all_status_filters
    _setup_status_filter_dropdown = page_nukedash.page_nukedash._setup_status_filter_dropdown
    _update_status_filter_label = page_nukedash.page_nukedash._update_status_filter_label

    def __init__(self, shot_groups: list[list[dict]], settings_initial: dict | None = None):
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

        self._settings_manager = FakeSettingsManager(settings_initial)
        self._saved_filter_state = self._load_saved_filter_state()
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
        self._apply_saved_filter_state_to_controls()
        self.checkBox_enable_filters.stateChanged.connect(self._on_enable_filters_changed)
        self._set_filter_controls_enabled(self._filters_enabled())

        for index, shots in enumerate(shot_groups):
            shot_cards = [LazyFakeShotCard(shot, self.timelines_tabs) for shot in shots]
            timeline_widget = FakeTimelineWidget(shots, shot_cards, self.timelines_tabs)
            self.timelines_tabs.addTab(timeline_widget, f"Timeline {index + 1}")

        self.show()
        QApplication.processEvents()

    def _apply_sort_to_timeline(self, timeline_widget, hide_hidden_tasks):
        return

    def _current_sort_mode(self) -> str:
        return self.comboBox_sort.currentData() or "title_asc"

    def _shot_needs_conform(self, shot_card, shot_data: dict) -> bool:
        return False

    def all_task_widget_ids(self) -> list[int]:
        task_ids = []
        for i in range(self.timelines_tabs.count()):
            task_ids.extend(self.timeline_task_widget_ids(i))
        return task_ids

    def timeline_task_widget_ids(self, timeline_index: int) -> list[int]:
        task_ids = []
        if timeline_index < 0 or timeline_index >= self.timelines_tabs.count():
            return task_ids
        timeline_widget = self.timelines_tabs.widget(timeline_index)
        if timeline_widget is None:
            return task_ids
        for j in range(timeline_widget.shots_layout.count()):
            item = timeline_widget.shots_layout.itemAt(j)
            card = item.widget() if item else None
            if not isinstance(card, LazyFakeShotCard):
                continue
            layout = card.frame_tasks.layout()
            for k in range(layout.count()):
                task_item = layout.itemAt(k)
                task_widget = task_item.widget() if task_item else None
                if isinstance(task_widget, widgets.TaskWidget):
                    task_ids.append(task_widget._data.get("id"))
        return task_ids

    def find_task_widget(self, task_id: int) -> widgets.TaskWidget | None:
        for i in range(self.timelines_tabs.count()):
            timeline_widget = self.timelines_tabs.widget(i)
            for j in range(timeline_widget.shots_layout.count()):
                item = timeline_widget.shots_layout.itemAt(j)
                card = item.widget() if item else None
                if not isinstance(card, LazyFakeShotCard):
                    continue
                layout = card.frame_tasks.layout()
                for k in range(layout.count()):
                    task_item = layout.itemAt(k)
                    task_widget = task_item.widget() if task_item else None
                    if isinstance(task_widget, widgets.TaskWidget) and task_widget._data.get("id") == task_id:
                        return task_widget
        return None


class TaskMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _shots(self) -> list[list[dict]]:
        return [
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

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MaterializationHarness):
                widget.close()
                widget.deleteLater()
        self.app.processEvents()

    def test_saved_filter_state_restores_without_search_text(self):
        harness = MaterializationHarness(
            self._shots(),
            settings_initial={
                "remember_last_session": True,
                "nukedash_filter_state": {
                    "enabled": True,
                    "sort_mode": "title_desc",
                    "artist_id": 2,
                    "status_values": ["done"],
                    "show_hidden_shots": True,
                    "show_hidden_tasks": True,
                    "show_to_conform": False,
                },
            },
        )
        try:
            self.assertTrue(harness.checkBox_enable_filters.isChecked())
            self.assertTrue(harness.checkBox_enable_filters.isEnabled())
            self.assertTrue(harness.checkBox_hidden_shot.isChecked())
            self.assertTrue(harness.checkBox_hidden_tasks.isChecked())
            self.assertEqual(harness.comboBox_sort.currentData(), "title_desc")
            self.assertEqual(harness.comboBox_sort_artist.currentData(), 2)
            self.assertEqual(harness._status_filter_values, {"done"})
            self.assertEqual(harness.Search_bar.text(), "")
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_remember_last_session_off_clears_saved_filter_state(self):
        harness = MaterializationHarness(
            self._shots(),
            settings_initial={
                "remember_last_session": False,
                "nukedash_filter_state": {
                    "enabled": True,
                    "sort_mode": "title_desc",
                    "artist_id": 2,
                    "status_values": ["done"],
                    "show_hidden_shots": True,
                    "show_hidden_tasks": True,
                    "show_to_conform": True,
                },
            },
        )
        try:
            self.assertTrue(harness.checkBox_enable_filters.isChecked())
            self.assertTrue(harness.checkBox_enable_filters.isEnabled())
            self.assertFalse(harness.checkBox_hidden_shot.isChecked())
            self.assertFalse(harness.checkBox_hidden_tasks.isChecked())
            self.assertEqual(harness.comboBox_sort.currentData(), "title_asc")
            self.assertEqual(harness.comboBox_sort_artist.currentData(), None)
            self.assertEqual(harness._status_filter_values, set())
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_filtered_materialization_creates_only_matching_task_widgets(self):
        harness = MaterializationHarness(
            self._shots(),
            settings_initial={
                "remember_last_session": True,
                "nukedash_filter_state": {
                    "enabled": True,
                    "sort_mode": "title_asc",
                    "artist_id": None,
                    "status_values": ["done"],
                    "show_hidden_shots": False,
                    "show_hidden_tasks": False,
                    "show_to_conform": False,
                },
            },
        )
        try:
            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(sorted(harness.all_task_widget_ids()), [1, 6])
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_force_apply_materializes_visible_tasks_even_with_cached_signature(self):
        harness = MaterializationHarness(self._shots())
        try:
            harness._last_filter_sig = ("filters_disabled", 0)

            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(sorted(harness.all_task_widget_ids()), [1, 2, 3, 5, 6])
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_turning_filters_off_materializes_all_tasks_and_keeps_existing_widgets(self):
        harness = MaterializationHarness(
            self._shots(),
            settings_initial={
                "remember_last_session": True,
                "nukedash_filter_state": {
                    "enabled": True,
                    "sort_mode": "title_asc",
                    "artist_id": None,
                    "status_values": ["done"],
                    "show_hidden_shots": False,
                    "show_hidden_tasks": False,
                    "show_to_conform": False,
                },
            },
        )
        try:
            harness._apply_filters(force=True)
            self.app.processEvents()
            first_widget = harness.find_task_widget(1)
            self.assertIsNotNone(first_widget)
            self.assertEqual(sorted(harness.all_task_widget_ids()), [1, 6])

            harness.checkBox_enable_filters.setChecked(False)
            self.app.processEvents()
            while harness._task_materialize_queue:
                harness._process_task_materialize_queue()
                self.app.processEvents()

            self.assertFalse(harness.checkBox_enable_filters.isChecked())
            self.assertEqual(sorted(harness.all_task_widget_ids()), [1, 2, 3, 4, 5, 6])
            self.assertIs(harness.find_task_widget(1), first_widget)
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_only_current_timeline_materializes_tasks_until_tab_switch(self):
        shots = [
            [
                {
                    "id": 101,
                    "title": "Timeline A Shot",
                    "notes": "",
                    "hidden": False,
                    "tasks": [
                        make_task(1, title="A1", status="assigned", artist=1),
                        make_task(2, title="A2", status="done", artist=1),
                    ],
                }
            ],
            [
                {
                    "id": 201,
                    "title": "Timeline B Shot",
                    "notes": "",
                    "hidden": False,
                    "tasks": [
                        make_task(3, title="B1", status="assigned", artist=2),
                        make_task(4, title="B2", status="done", artist=2),
                    ],
                }
            ],
        ]
        harness = MaterializationHarness(shots)
        try:
            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(sorted(harness.timeline_task_widget_ids(0)), [1, 2])
            self.assertEqual(harness.timeline_task_widget_ids(1), [])

            harness.timelines_tabs.setCurrentIndex(1)
            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(sorted(harness.timeline_task_widget_ids(1)), [3, 4])
            self.assertEqual(sorted(harness.all_task_widget_ids()), [1, 2, 3, 4])
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_task_loading_label_tracks_queue_until_current_timeline_finishes(self):
        harness = MaterializationHarness(self._shots())
        harness._task_materialize_batch_size = 1
        try:
            harness._apply_filters(force=True)
            self.app.processEvents()
            self.assertEqual(harness.label_task_loading.text(), "Loading tasks...")

            harness._process_task_materialize_queue()
            self.app.processEvents()
            self.assertEqual(harness.label_task_loading.text(), "Loading tasks...")

            while harness._task_materialize_queue:
                harness._process_task_materialize_queue()
                self.app.processEvents()

            self.assertEqual(harness.label_task_loading.text(), "Tasks loaded")
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_task_loading_label_reports_loaded_when_first_batch_finishes_everything(self):
        harness = MaterializationHarness(self._shots())
        try:
            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(harness.label_task_loading.text(), "Tasks loaded")
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_task_loading_label_keeps_loaded_when_no_new_tasks_are_enqueued(self):
        harness = MaterializationHarness(self._shots())
        try:
            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(harness.label_task_loading.text(), "Tasks loaded")

            harness._apply_filters(force=True)
            self.app.processEvents()

            self.assertEqual(harness.label_task_loading.text(), "Tasks loaded")
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_remove_task_by_id_updates_raw_data_and_loaded_widgets(self):
        card = LazyFakeShotCard(
            {
                "id": 999,
                "title": "Shot 999",
                "tasks": [
                    make_task(11, title="One", status="done", artist=1),
                    make_task(12, title="Two", status="assigned", artist=2),
                ],
            }
        )
        try:
            card.set_visible_task_ids([11, 12])
            card.materialize_task_ids([11, 12])
            self.assertEqual(sorted(card._task_widgets_by_id.keys()), [11, 12])

            card.remove_task_by_id(12)

            remaining_ids = [task["id"] for task in card.data["tasks"]]
            self.assertEqual(remaining_ids, [11])
            self.assertEqual(sorted(card._task_widgets_by_id.keys()), [11])
        finally:
            card.close()
            card.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
