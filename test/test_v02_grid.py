from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PyQt6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QBoxLayout, QScrollArea, QWidget

from flow_layout import FlowLayout
from masonry_layout import MasonryLayout
import page_nukedash
import settings
import widgets


class HeightWidget(QWidget):
    def __init__(self, height, parent):
        super().__init__(parent)
        self.natural_height = height

    def sizeHint(self):
        return QSize(900, self.natural_height)

    def minimumSizeHint(self):
        return QSize(0, 0)


class MasonryLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.container = QWidget()
        self.layout = MasonryLayout(self.container, h_spacing=8, v_spacing=8)
        self.cards = []
        for index, height in enumerate((200, 100, 50, 80)):
            card = HeightWidget(height, self.container)
            card.setObjectName(f"shot-{index}")
            self.layout.addWidget(card)
            self.cards.append(card)
        self.addCleanup(self.container.deleteLater)

    def place(self, width):
        self.layout.setGeometry(QRect(0, 0, width, 1000))

    def test_equal_widths_leave_spare_space_and_pack_shortest_column(self):
        self.place(1450)
        self.assertEqual([card.width() for card in self.cards], [720] * 4)
        self.assertEqual([(card.x(), card.y()) for card in self.cards], [(0, 0), (728, 0), (728, 108), (728, 166)])
        self.assertEqual(self.layout.heightForWidth(1450), 246)

    def test_hidden_cards_do_not_leave_space_and_sort_is_deterministic(self):
        self.cards[1].hide()
        self.place(1450)
        self.assertEqual(self.cards[2].y(), 0)
        self.assertEqual(self.cards[3].y(), 58)
        widgets._ensure_order(self.layout, ["shot-3", "shot-0", "shot-2", "shot-1"])
        self.place(1450)
        self.assertEqual((self.cards[3].x(), self.cards[3].y()), (0, 0))
        self.assertEqual((self.cards[0].x(), self.cards[0].y()), (728, 0))

    def test_column_breakpoints_spacing_and_minimum_height(self):
        self.place(1447)
        self.assertEqual(self.cards[1].x(), 0)
        self.place(1448)
        self.assertEqual(self.cards[1].x(), 728)
        self.cards[0].setMinimumHeight(300)
        self.assertEqual(self.layout.heightForWidth(1448), 300)
        self.layout.setSpacing(20)
        self.place(1460)
        self.assertEqual(self.cards[2].y(), 120)
        self.place(320)
        self.assertEqual(self.cards[0].width(), 320)

    def test_insert_remove_empty_layout_and_non_divisible_width(self):
        self.layout.insertWidget(0, self.cards[3])
        self.assertIs(self.layout.itemAt(0).widget(), self.cards[3])
        self.place(1451)
        self.assertLessEqual(abs(self.cards[3].width() - self.cards[0].width()), 1)
        self.assertEqual(self.cards[0].geometry().right(), 1447)
        for card in self.cards:
            self.layout.removeWidget(card)
        self.assertEqual(self.layout.heightForWidth(100), 0)


class FakeAPI:
    def __init__(self):
        self.writes = []

    def username_from_id(self, artist):
        return "Unassigned" if not artist else "Test Artist"

    def get_users(self):
        return []

    def update_task(self, task_id, **fields):
        self.writes.append((task_id, fields))
        return None


class FakeFolders:
    def convert_path(self, path):
        return path


def shot_data(index, directory, count):
    return {
        "id": index, "title": f"test_shot_{index:03}", "base_path": directory,
        "duration": 100, "edit_inpoint": 20, "edit_outpoint": 120,
        "colourspace": "ARRI LogC4", "notes": "Camera and shot notes remain readable.",
        "last_conform": "test_shot_v002.mov",
        "tasks": [
            {"id": index * 100 + i, "title": f"Task {i}: paint and cleanup",
             "status": "approved" if i % 2 else "assigned", "artist": 1,
             "notes": "Task notes", "progress": 100 if i % 2 else 25,
             "priority": 5, "budget_hours": 2, "hidden": False}
            for i in range(count)
        ],
    }


class V02CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # All data and files are synthetic. Any accidental HTTP request fails.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = FakeAPI()
        for patcher in (
            mock.patch("requests.sessions.Session.request", side_effect=AssertionError("No network calls permitted")),
            mock.patch.object(widgets.http_help, "DjangoAPI", return_value=self.api),
            mock.patch.object(widgets.filesIO, "Folders", return_value=FakeFolders()),
            mock.patch.object(widgets, "HAS_MULTIMEDIA", False),
            mock.patch.object(widgets, "THUMBNAILS_ENABLED", True),
            mock.patch.object(widgets, "THUMBNAIL_TARGET_WIDTH", 160),
            mock.patch.object(widgets, "find_latest_matchmove_project", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.timeline = widgets.TimelineFrame(
            {"id": 1, "shots": [shot_data(1, self.temp.name, 5), shot_data(2, self.temp.name, 1), shot_data(3, self.temp.name, 2)]},
            layout_mode="list", task_style="checklist", api=self.api, folders=FakeFolders(),
        )
        self.addCleanup(self.timeline.deleteLater)
        self.cards = [self.timeline.shots_layout.itemAt(i).widget() for i in range(3)]
        for card in self.cards:
            ids = [task["id"] for task in card.data["tasks"]]
            card.set_visible_task_ids(ids)
            card.materialize_task_ids(ids)
            image = QPixmap(320, 180)
            image.fill(Qt.GlobalColor.darkCyan)
            card._thumb_orig = image
            card._apply_thumb_scale()

    def layout_v02(self, width=1450):
        self.timeline.set_layout_mode("v02_grid", 8)
        self.timeline.resize(width + 40, 1000)
        self.timeline.show()
        self.app.processEvents()
        self.timeline.shots_layout.setGeometry(QRect(0, 0, width, 1000))

    def test_switching_preserves_widgets_handlers_state_and_legacy_arrangement(self):
        card = self.cards[0]
        task = next(iter(card._task_widgets_by_id.values()))
        original_header_parent = card.frame_6.parentWidget()
        original_note_parent = task.edit_notes_inline.parentWidget()
        task.edit_notes_inline.setText("Unsaved edit survives switching")
        button = card.btn_latest_render
        button.setProperty("file_path", "/tmp/synthetic_render.mov")
        self.layout_v02()
        self.assertIsInstance(self.timeline.shots_layout, MasonryLayout)
        self.assertEqual(card.frame_6.parentWidget(), card.content_container)
        self.assertIs(next(iter(card._task_widgets_by_id.values())), task)
        self.assertEqual(task.task_layout.direction(), QBoxLayout.Direction.LeftToRight)
        self.assertIs(task.edit_notes_inline.parentWidget(), original_note_parent)
        for mode in ("grid", "v02_grid", "list", "v02_grid", "list"):
            self.timeline.set_layout_mode(mode, 8)
        self.assertIs(card.frame_6.parentWidget(), original_header_parent)
        self.assertIs(card.btn_latest_render, button)
        self.assertEqual(button.property("file_path"), "/tmp/synthetic_render.mov")
        self.assertEqual(task.edit_notes_inline.text(), "Unsaved edit survives switching")
        self.assertEqual(task.task_layout.direction(), QBoxLayout.Direction.LeftToRight)
        self.assertFalse(card.label_notes.wordWrap())
        self.assertEqual(self.api.writes, [])

    def test_real_cards_pack_without_horizontal_overflow_and_reflow_narrowly(self):
        self.layout_v02()
        widths = [card.width() for card in self.cards]
        self.assertEqual(widths, [720] * 3)
        self.assertEqual(self.cards[2].x(), self.cards[1].x())
        self.assertLess(self.cards[2].y(), self.cards[0].height() + 8)
        card = self.cards[0]
        for width in (700, 600, 519, 400, 320, 600):
            self.timeline.shots_layout.setGeometry(QRect(0, 0, width, 3000))
            card.layout().activate()
            self.assertEqual(card.width(), width)
            expected = QBoxLayout.Direction.TopToBottom if width < 520 else QBoxLayout.Direction.LeftToRight
            self.assertEqual(card.horizontalLayout_2.direction(), expected)
            for control in (card.btn_open_nuke, card.btn_latest_render, card.label_shot):
                self.assertLessEqual(control.mapTo(card, control.rect().bottomRight()).x(), card.width())

    def test_actual_scroll_area_layout_is_attached_and_fits_dark_theme(self):
        original_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_style)
        theme = (Path(__file__).resolve().parents[1] / "ui" / "dark_v01.qss").read_text()
        self.app.setStyleSheet(theme)
        self.timeline.set_layout_mode("v02_grid", 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.timeline)
        scroll.resize(1280, 600)
        scroll.show()
        self.addCleanup(scroll.deleteLater)
        self.assertIs(self.timeline.shots_layout.parentWidget(), self.timeline.frame)
        for width in (1280, 400, 320, 2048):
            scroll.resize(width, 600)
            for _ in range(4):
                self.app.processEvents()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(scroll.verticalScrollBar().maximum(), max(0, self.timeline.height() - scroll.viewport().height()))
            for card in self.cards:
                self.assertTrue(card.isVisible())
                self.assertGreater(card.height(), 150)
                self.assertGreater(card.width(), 200)
                card.layout().activate()
                for control in (card.btn_latest_render, card.label_last_conform, card.label_shot):
                    self.assertGreater(control.width(), 0)
                    self.assertLessEqual(control.mapTo(card, control.rect().bottomRight()).x(), card.width())
        self.timeline.set_layout_mode("grid", 8)
        self.app.processEvents()
        self.assertIs(self.timeline.shots_layout.parentWidget(), self.timeline.frame)

    def test_widest_content_sets_shared_width_without_filling_the_row(self):
        original_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_style)
        self.app.setStyleSheet((Path(__file__).resolve().parents[1] / "ui" / "dark_v01.qss").read_text())
        self.layout_v02()
        card = self.cards[0]
        filename = "winter010_" + "comp_" * 12 + "v002.nk"
        card._set_nuke_file_state(file_path="/tmp/" + filename, file_name=filename, file_mtime=None)
        widest = max(candidate.preferred_layout_width() for candidate in self.cards)
        self.assertGreater(widest, 720)
        for viewport in (widest * 2 + 108, widest * 3 + 216):
            self.timeline.shots_layout.setGeometry(QRect(0, 0, viewport, 5000))
            self.assertEqual([candidate.width() for candidate in self.cards], [widest] * 3)
            self.assertLess(max(candidate.geometry().right() for candidate in self.cards), viewport - 1)
        card.hide()
        self.timeline.shots_layout.setGeometry(QRect(0, 0, viewport, 5000))
        visible_width = max(candidate.preferred_layout_width() for candidate in self.cards[1:])
        self.assertLess(visible_width, widest)
        self.assertEqual([candidate.width() for candidate in self.cards[1:]], [visible_width] * 2)
        self.cards[2].hide()
        self.timeline.shots_layout.setGeometry(QRect(0, 0, viewport, 5000))
        self.assertEqual(self.cards[1].width(), visible_width)

    def test_changed_filename_updates_shared_width_without_window_resize(self):
        self.timeline.set_layout_mode("v02_grid", 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.timeline)
        scroll.resize(2048, 800)
        scroll.show()
        self.addCleanup(scroll.deleteLater)
        for _ in range(10):
            self.app.processEvents()
        card = self.cards[0]
        original_width = card.width()
        filename = "comp_" * 8 + "v002.nk"
        card._set_nuke_file_state(file_path="/tmp/" + filename, file_name=filename, file_mtime=None)
        for _ in range(10):
            self.app.processEvents()
        self.assertGreater(card.width(), original_width)
        shared_width = max(candidate.preferred_layout_width() for candidate in self.cards)
        self.assertEqual([candidate.width() for candidate in self.cards], [shared_width] * 3)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        card._set_nuke_file_state(file_path=None, file_name=None, file_mtime=None)
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual([candidate.width() for candidate in self.cards], [original_width] * 3)

    def test_height_queries_never_resize_thumbnails_or_rebuild_task_rows(self):
        self.layout_v02()
        card = self.cards[0]
        task = next(iter(card._task_widgets_by_id.values()))
        signature = card._thumb_scale_signature
        thumbnail_size = card.label_thumbnail.size()
        first_row = task.task_layout.itemAt(0).layout()
        width = card._v02_layout_width
        with mock.patch.object(card, "prepare_layout_width", wraps=card.prepare_layout_width) as prepare:
            for probe in (0, 1, 100, 320, 519, 2048, 1210) * 3:
                self.timeline.shots_layout.heightForWidth(probe)
            prepare.assert_not_called()
        self.assertEqual(card._v02_layout_width, width)
        self.assertEqual(card.label_thumbnail.size(), thumbnail_size)
        self.assertEqual(card._thumb_scale_signature, signature)
        self.assertIs(task.task_layout.itemAt(0).layout(), first_row)

    def test_compact_header_keeps_frame_values_inline_and_reflows_long_titles(self):
        self.layout_v02()
        card = self.cards[0]
        for width in (600, 400, 320, 600):
            self.timeline.shots_layout.setGeometry(QRect(0, 0, width, 3000))
            card.layout().activate()
            self.assertTrue(card.label_shot.wordWrap())
            title_bottom = card.label_shot.mapTo(card.frame_6, card.label_shot.rect().bottomLeft()).y()
            for label in (card.label_frame_range, card.label_edit_inpoint, card.label_edit_outpoint):
                if width < 520:
                    self.assertGreater(label.mapTo(card.frame_6, label.rect().topLeft()).y(), title_bottom)
                else:
                    self.assertLessEqual(label.mapTo(card.frame_6, label.rect().topLeft()).y(), title_bottom)
                    self.assertGreaterEqual(card.label_shot.width(), card.label_shot.fontMetrics().horizontalAdvance(card.label_shot.text()))
            self.assertLessEqual(card.btn_red.mapTo(card, card.btn_red.rect().bottomRight()).x(), card.width())
        card.label_shot.setText("long_shot_name_" * 30)
        card.set_compact_mode(False)
        self.app.processEvents()
        self.timeline.shots_layout.invalidate()
        self.timeline.shots_layout.setGeometry(QRect(0, 0, 600, 3000))
        card.layout().activate()
        self.assertGreater(card.label_shot.height(), card.label_shot.fontMetrics().height() * 2)
        self.assertGreaterEqual(card.label_shot.width(), card.width() - 40)
        self.assertEqual(card.horizontalLayout_6.direction(), QBoxLayout.Direction.TopToBottom)
        self.timeline.set_layout_mode("grid", 8)
        self.assertFalse(card.label_shot.wordWrap())
        self.assertEqual(card.horizontalLayout_6.direction(), QBoxLayout.Direction.LeftToRight)

    def test_initial_and_reloaded_jobs_apply_v02_card_presentation(self):
        from test.test_main_window_assignment_board_and_load_timing import ChunkedJobLoadHarness

        harness = ChunkedJobLoadHarness()
        self.addCleanup(harness.deleteLater)
        harness._effective_shots_layout_mode = lambda: "v02_grid"
        harness._task_style = "checklist"
        harness._shot_card_api = self.api
        harness.filesIO = FakeFolders()
        job = {"id": 91, "title": "Synthetic test job", "timelines": [
            {"id": 92, "title": "Synthetic timeline", "shots": [shot_data(93, self.temp.name, 2)]},
        ]}
        for _ in range(2):
            harness._start_job_loading(job)
            timeline = harness.timelines_tabs.widget(0)
            card = timeline.shots_layout.itemAt(0).widget()
            self.assertTrue(card._v02_grid)
            self.assertIs(card.frame_6.parentWidget(), card.content_container)
            self.assertEqual(card.horizontalLayout_6.direction(), QBoxLayout.Direction.LeftToRight)
            self.assertTrue(card.label_shot.wordWrap())
            ids = [task["id"] for task in card.data["tasks"]]
            card.set_visible_task_ids(ids)
            card.materialize_task_ids(ids)
            self.assertTrue(all(task._v02_grid for task in card._task_widgets_by_id.values()))
        self.assertEqual(self.api.writes, [])

    def test_loaded_multimedia_thumbnails_remain_visible_and_layout_settles(self):
        if not hasattr(widgets, "QMediaPlayer"):
            self.skipTest("Qt multimedia is unavailable")

        class LayoutEvents(QObject):
            count = 0

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.LayoutRequest:
                    self.count += 1
                return False

        pixmap = QPixmap(320, 180)
        pixmap.fill(Qt.GlobalColor.darkCyan)
        loader = mock.Mock()
        loader.load.side_effect = lambda url, callback: QTimer.singleShot(0, lambda: callback(pixmap))
        with mock.patch.object(widgets, "HAS_MULTIMEDIA", True), \
             mock.patch.object(widgets, "QMediaPlayer"), \
             mock.patch.object(widgets, "QAudioOutput"), \
             mock.patch.object(widgets.ImageLoader, "instance", return_value=loader):
            data = shot_data(99, self.temp.name, 2)
            data["thumbnail"] = "/synthetic-thumbnail.jpg"
            timeline = widgets.TimelineFrame({"id": 99, "shots": [data]}, layout_mode="v02_grid", task_style="checklist")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(timeline)
            scroll.resize(1280, 600)
            scroll.show()
            self.addCleanup(scroll.deleteLater)
            card = timeline.shots_layout.itemAt(0).widget()
            ids = [task["id"] for task in data["tasks"]]
            card.set_visible_task_ids(ids)
            card.materialize_task_ids(ids)
            observer = LayoutEvents()
            for widget in (timeline, timeline.frame, card, card.frame_5):
                widget.installEventFilter(observer)
            for _ in range(20):
                self.app.processEvents()
            self.assertTrue(card._preview_stack.isVisible())
            self.assertTrue(card.label_thumbnail.isVisible())
            self.assertFalse(card.label_thumbnail.pixmap().isNull())
            self.assertEqual(card._preview_stack.size(), QSize(160, 90))
            self.assertEqual(card._preview_stack.currentIndex(), 0)
            settled = observer.count
            for _ in range(20):
                for probe in (0, 100, 1210):
                    timeline.shots_layout.heightForWidth(probe)
                self.app.processEvents()
            self.assertEqual(observer.count, settled)
            self.assertEqual(card._preview_stack.size(), QSize(160, 90))

    def test_v02_task_rows_are_shorter_and_restore_legacy_control_sizes(self):
        original_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_style)
        self.app.setStyleSheet((Path(__file__).resolve().parents[1] / "ui" / "dark_v01.qss").read_text())
        self.layout_v02()
        task = next(iter(self.cards[0]._task_widgets_by_id.values()))
        self.assertLessEqual(task.sizeHint().height(), 36)
        self.assertEqual(task.check_done_task.size(), QSize(20, 20))
        self.timeline.set_layout_mode("list", 8)
        self.assertEqual(task.check_done_task.size(), QSize(28, 28))

    def test_folder_actions_stay_below_file_buttons_beside_preview(self):
        original_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_style)
        self.app.setStyleSheet((Path(__file__).resolve().parents[1] / "ui" / "dark_v01.qss").read_text())
        self.layout_v02(600)
        card = self.cards[0]
        self.assertIs(card.shot_btns.parentWidget(), card.frame_4)
        for thumbnail_width in (80, 160, 240, 360, 160):
            card.set_thumbnail_width(thumbnail_width)
            for _ in range(4):
                self.app.processEvents()
            self.timeline.shots_layout.setGeometry(QRect(0, 0, 600, 3000))
            card.layout().activate()
            for button in (card.btn_open_nuke, card.btn_open_assets, card.btn_open_precomp, card.btn_latest_render):
                self.assertGreater(button.width(), 0)
                self.assertGreater(button.height(), 0)
                self.assertGreaterEqual(button.mapTo(card, button.rect().topLeft()).x(), card.label_thumbnail.mapTo(card, card.label_thumbnail.rect().topRight()).x())
                self.assertLessEqual(button.mapTo(card, button.rect().bottomRight()).x(), card.width())
            files_bottom = max(button.mapTo(card, button.rect().bottomRight()).y() for button in (card.btn_open_nuke, card.btn_latest_render))
            for button in (card.btn_open_assets, card.btn_open_precomp):
                self.assertGreater(button.mapTo(card, button.rect().topLeft()).y(), files_bottom)
        card.set_thumbnail_width(360)
        self.assertTrue(card._v02_actions_split)
        card.set_thumbnails_enabled(False)
        self.assertTrue(card._v02_actions_split)
        card.set_thumbnails_enabled(True)
        self.assertIs(card.shot_btns.parentWidget(), card.frame_4)
        self.assertEqual(self.api.writes, [])

    def test_single_line_tasks_reflow_without_losing_active_edits(self):
        original_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_style)
        self.app.setStyleSheet((Path(__file__).resolve().parents[1] / "ui" / "dark_v01.qss").read_text())
        self.layout_v02(600)
        card = self.cards[0]
        task = next(iter(card._task_widgets_by_id.values()))
        task._show_inline_title_editor(True)
        task.edit_title_inline.setText("Unsaved compact title")
        task.edit_notes_inline.setText("Unsaved compact note")
        for width in (600, 480, 320, 600):
            self.timeline.resize(width + 40, 1000)
            for _ in range(4):
                self.app.processEvents()
            self.timeline.shots_layout.setGeometry(QRect(0, 0, width, 3000))
            card.layout().activate()
            task.layout().activate()
            task.task_layout.activate()
            self.assertEqual(task.task_layout.direction(), QBoxLayout.Direction.LeftToRight if width >= 600 else QBoxLayout.Direction.TopToBottom)
            self.assertTrue(task._title_edit_active)
            self.assertEqual(task.edit_title_inline.text(), "Unsaved compact title")
            self.assertEqual(task.edit_notes_inline.text(), "Unsaved compact note")
            for control in (task.check_done_task, task.title_stack, task.edit_notes_inline, task.btn_status,
                            task.btn_assigned, task.btn_priority, task.btn_budget_hours, task.btn_hide_task, task.btn_delete_task):
                self.assertTrue(control.isVisible())
                self.assertGreater(control.width(), 0)
                self.assertLessEqual(control.mapTo(task, control.rect().bottomRight()).x(), task.width())
                if width >= 600:
                    self.assertLessEqual(abs(control.mapTo(task, control.rect().center()).y() - task.height() // 2), 2)
            if width >= 600:
                self.assertGreaterEqual(task.title_stack.width(), 100)
                self.assertGreaterEqual(task.edit_notes_inline.width(), 70)
        self.assertEqual(self.api.writes, [])

    def test_switch_from_visible_legacy_grid_restores_hidden_shots_and_editors(self):
        self.timeline.set_layout_mode("grid", 8)
        self.timeline.resize(1800, 1200)
        self.timeline.show()
        self.app.processEvents()
        self.cards[1].hide()
        task = next(iter(self.cards[0]._task_widgets_by_id.values()))
        task._show_inline_title_editor(True)
        task.edit_title_inline.setText("Draft task title")
        for mode in ("v02_grid", "list", "v02_grid", "grid"):
            self.timeline.set_layout_mode(mode, 8)
            self.app.processEvents()
            self.assertFalse(self.cards[0].isHidden())
            self.assertTrue(self.cards[1].isHidden())
            self.assertTrue(task._title_edit_active)
            self.assertEqual(task.edit_title_inline.text(), "Draft task title")
        self.assertEqual(self.api.writes, [])

    def test_multimedia_preview_container_is_reused_without_audio_or_file_io(self):
        if not hasattr(widgets, "QMediaPlayer"):
            self.skipTest("Qt multimedia is unavailable")
        with mock.patch.object(widgets, "HAS_MULTIMEDIA", True), \
             mock.patch.object(widgets, "QMediaPlayer"), \
             mock.patch.object(widgets, "QAudioOutput"):
            card = widgets.ShotCard(shot_data(99, self.temp.name, 0), api=self.api, folders=FakeFolders())
        self.addCleanup(card.deleteLater)
        stack = card._preview_stack
        player = card._video_player
        for v02 in (True, False, True, False):
            card.set_v02_grid(v02)
            self.assertIs(card._preview_stack, stack)
            self.assertIs(card._video_player, player)
            self.assertIs(stack.parentWidget(), card.frame_5)
            self.assertIs(card.label_thumbnail.parentWidget(), card._thumb_container)

    def test_file_text_is_retained_and_task_controls_still_write_to_fake_api(self):
        self.layout_v02()
        card = self.cards[0]
        name = "very_long_file_name_" * 30 + ".nk"
        card._set_nuke_file_state(file_path="/tmp/" + name, file_name=name, file_mtime=None)
        self.assertEqual(card.btn_open_nuke.text(), name)
        task = next(iter(card._task_widgets_by_id.values()))
        task._on_progress_increment_requested()
        self.assertTrue(self.api.writes)
        self.assertEqual(self.api.writes[-1][0], task._task_id)

    def test_nuke_fits_text_and_render_fills_remaining_single_line(self):
        original_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_style)
        self.app.setStyleSheet((Path(__file__).resolve().parents[1] / "ui" / "dark_v01.qss").read_text())
        self.layout_v02(600)
        card = self.cards[0]
        card.set_thumbnail_width(240)
        for filename, script in (
            ("blinds_030_v004_####.exr 1001-1056", "blinds_030_v004.nk"),
            ("long_render_filename_" * 6 + "v004_####.exr", "long_script_filename_" * 6 + ".nk"),
        ):
            card._set_render_file_state(render_path="/tmp/" + filename, render_display=filename)
            card._set_nuke_file_state(file_path="/tmp/" + script, file_name=script, file_mtime=None)
            for width in (600, 320, 1100):
                for _ in range(4):
                    self.app.processEvents()
                self.timeline.shots_layout.setGeometry(QRect(0, 0, width, 5000))
                card.layout().activate()
                for button in (card.btn_open_nuke, card.btn_latest_render):
                    self.assertFalse(button.hasHeightForWidth())
                    self.assertEqual(button.height(), button.sizeHint().height())
                    self.assertEqual(button.elide_mode, Qt.TextElideMode.ElideMiddle)
                    self.assertLessEqual(button.mapTo(card, button.rect().bottomRight()).x(), card.width())
                self.assertEqual(card.btn_open_nuke.width(), card.btn_open_nuke.sizeHint().width())
                self.assertEqual(card.btn_latest_render.y(), card.btn_open_nuke.y())
                self.assertEqual(card.btn_open_nuke.width() + card.btn_latest_render.width() + 6, card.shot_btns.contentsRect().width())
                self.assertGreaterEqual(card.btn_latest_render.width(), card.btn_open_nuke.width())
                self.assertEqual(card.btn_latest_render.text(), filename)
                self.assertIn(filename, card.btn_latest_render.toolTip())
        self.timeline.set_layout_mode("list", 8)
        self.assertFalse(card.btn_latest_render.hasHeightForWidth())

    def test_long_notes_and_filenames_change_height_without_widening_cards(self):
        card = self.cards[0]
        card.data.update({
            "title": "very_long_shot_name_" * 20,
            "notes": "Readable shot notes with several details. " * 40,
            "original_clip": "very_long_original_clip_" * 20 + ".mov",
        })
        card.update_from_data(card.data)
        self.layout_v02()
        self.assertEqual(card.width(), self.cards[1].width())
        self.assertGreater(card.label_notes.height(), card.label_notes.fontMetrics().height() * 3)
        self.assertLessEqual(card.label_notes.mapTo(card, card.label_notes.rect().bottomRight()).x(), card.width())
        self.assertIn(card.data["original_clip"], card.label_original_clip.toolTip())

    def test_task_style_compact_density_filtering_and_lazy_tasks(self):
        self.layout_v02()
        card = self.cards[0]
        for style in ("card", "checklist"):
            self.timeline.set_task_style(style)
            self.assertTrue(all(task._v02_grid for task in card._task_widgets_by_id.values()))
        self.timeline.set_compact_mode(True)
        self.assertFalse(card.btn_open_assets.isVisible())
        self.assertEqual(card.horizontalLayout_2.direction(), QBoxLayout.Direction.TopToBottom)
        self.timeline.set_compact_mode(False)
        self.assertTrue(card.btn_open_assets.isVisible())
        for width in (80, 160, 240, 360):
            card.set_thumbnail_width(width)
            self.assertEqual(card.label_thumbnail.width(), width)
        card.set_thumbnails_enabled(False)
        self.assertTrue(card.label_thumbnail.isHidden())
        card.set_thumbnails_enabled(True)
        self.assertFalse(card.label_thumbnail.isHidden())
        visible_ids = list(card._task_widgets_by_id)[:1]
        card.set_visible_task_ids(visible_ids)
        self.assertEqual(sum(not task.isHidden() for task in card._task_widgets_by_id.values()), 1)
        added = dict(card.data["tasks"][0], id=9999, title="Lazily added task")
        card.data["tasks"].append(added)
        card.set_visible_task_ids([*visible_ids, added["id"]])
        card.materialize_task_ids([added["id"]])
        self.assertTrue(card._task_widgets_by_id[added["id"]]._v02_grid)
        self.assertEqual(self.api.writes, [])

    def test_layout_mode_resolution_and_settings_round_trip(self):
        for compact in (False, True):
            self.assertEqual(page_nukedash.resolve_effective_shots_layout_mode("v02_grid", compact, 1600), "v02_grid")
        self.assertEqual(page_nukedash.resolve_effective_shots_layout_mode("list", True, 1600), "grid")
        self.assertIsInstance(widgets._create_shots_layout("grid"), FlowLayout)
        manager = settings.SettingsManager(str(Path(self.temp.name) / "settings.yaml"))
        self.assertEqual(manager.get("shots_layout_mode"), "list")
        manager.set("shots_layout_mode", "v02_grid")
        self.assertEqual(settings.SettingsManager(manager.settings_path).get("shots_layout_mode"), "v02_grid")
        with mock.patch.object(settings.SettingsPage, "_load_django_users"), \
             mock.patch.object(settings.SettingsPage, "_refresh_update_panel"):
            page = settings.SettingsPage(manager)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.shots_layout_combo.currentData(), "v02_grid")
        self.assertEqual(page.shots_layout_combo.currentText(), "Grid — v02")
        self.assertEqual(
            [page.shots_layout_combo.itemData(i) for i in range(page.shots_layout_combo.count())],
            ["list", "grid", "v02_grid"],
        )

    def test_previous_grid_preference_loads_and_saves_as_v02(self):
        path = Path(self.temp.name) / "previous_settings.yaml"
        path.write_text("shots_layout_mode: modern_grid\ncard_spacing: 13\n")
        manager = settings.SettingsManager(str(path))
        self.assertEqual(manager.get("shots_layout_mode"), "v02_grid")
        self.assertEqual(manager.get("card_spacing"), 13)
        for compact in (False, True):
            self.assertEqual(page_nukedash.resolve_effective_shots_layout_mode("modern_grid", compact, 600), "v02_grid")
        self.assertIsInstance(widgets._create_shots_layout("modern_grid"), MasonryLayout)
        with mock.patch.object(settings.SettingsPage, "_load_django_users"), \
             mock.patch.object(settings.SettingsPage, "_refresh_update_panel"):
            page = settings.SettingsPage(manager)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.shots_layout_combo.currentData(), "v02_grid")
        manager.set("shots_layout_mode", "modern_grid")
        self.assertEqual(manager.get("shots_layout_mode"), "v02_grid")
        self.assertIn("shots_layout_mode: v02_grid", path.read_text())
        self.assertEqual(settings.SettingsManager(str(path)).get("shots_layout_mode"), "v02_grid")


if __name__ == "__main__":
    unittest.main()
