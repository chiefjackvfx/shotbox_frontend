from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QMimeData, QPoint, Qt
from PyQt6.QtGui import QAction, QImage
from PyQt6.QtWidgets import QApplication, QDialog

import http_help
import page_assignment_board as board_module

USERS = [
    {"id": 10, "username": "artist1", "first_name": "One"},
    {"id": 20, "username": "artist2", "first_name": "Two"},
]


def jobs_data():
    return [{"id": 1, "title": "Job One", "timelines": [
        {"id": 101, "title": "Edit One", "shots": [
            {"id": 1001, "title": "SH010", "tasks": [
                {"id": 7, "shot": 1001, "title": "Comp", "artist": 10,
                 "status": "in_progress", "priority": 5, "budget_hours": "2.00",
                 "notes": "Original note", "hidden": False, "progress": 75},
            ]},
        ]},
        {"id": 102, "title": "Edit Two", "shots": []},
    ]}]


class AssignmentBoardBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.users_patch = mock.patch.object(http_help.DjangoAPI, "get_users", return_value=USERS)
        self.get_users = self.users_patch.start()
        self.addCleanup(self.users_patch.stop)
        network = mock.patch.object(http_help.DjangoAPI, "_request", side_effect=AssertionError("Unexpected HTTP request"))
        network.start()
        self.addCleanup(network.stop)
        self.boards = []

    def tearDown(self):
        for board in self.boards:
            board.close()
            board.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        with board_module._THUMB_CACHE_LOCK:
            board_module._THUMB_CACHE.clear()

    def wait_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.001)
        self.fail("Timed out waiting for Qt/worker completion")

    def make_board(self, jobs=None):
        board = board_module.AssignmentBoardPage()
        self.boards.append(board)
        board.show()
        board.set_jobs_data(jobs_data() if jobs is None else jobs)
        self.finish_build(board)
        return board

    def finish_build(self, board):
        self.wait_until(lambda: not board._pending_tasks)
        self.app.processEvents()

    def card(self, board, artist=10, task_id=7):
        return board.columns[artist].task_cards[task_id][1]

    def raw_task(self, board, task_id=7):
        return board._task_records[task_id][1]

    def test_artist_menu_preserves_task_and_shot_metadata(self):
        jobs = jobs_data()
        jobs[0]["timelines"][0]["shots"][0]["thumbnail"] = "/media/shot.png"
        with mock.patch.object(board_module.ShotGroupCard, "_apply_thumbnail"):
            board = self.make_board(jobs)
            response = {**self.raw_task(board), "artist": 20}
            action = QAction("Two")
            action.setData(20)
            with mock.patch.object(http_help.DjangoAPI, "update_task", return_value=response) as update:
                self.card(board)._on_artist_action(action)
            update.assert_called_once_with(7, artist=20)
            self.assertNotIn(7, board.columns[10].task_cards)
            self.assertEqual(self.card(board, 20).btn_task_title.text(), "Comp")
            group = board.columns[20].shot_groups[1001]
            self.assertEqual(group.title_label.text(), "SH010")
            self.assertTrue(group.shot_data["thumbnail_url"].endswith("/media/shot.png"))
            self.assertEqual(board.columns[20].task_count_label.text(), "1")
            self.assertEqual(self.raw_task(board)["shot"], 1001)
            board.show_hidden_checkbox.setChecked(True)
            self.finish_build(board)
            self.assertEqual(self.card(board, 20).btn_assigned.text(), "Two")

    def test_artist_menu_can_unassign_without_losing_shot(self):
        board = self.make_board()
        action = QAction("Unassigned")
        action.setData(None)
        with mock.patch.object(http_help.DjangoAPI, "update_task", return_value={**self.raw_task(board), "artist": None}):
            self.card(board)._on_artist_action(action)
        self.assertEqual(self.card(board, "unassigned")._task_ref["shot_id"], 1001)
        self.assertIsNone(self.raw_task(board)["artist"])

    def test_failed_drag_restores_assignment_and_reports_error_on_gui_thread(self):
        board = self.make_board()
        gui_thread = threading.get_ident()
        results = []
        with mock.patch.object(http_help.DjangoAPI, "update_task", side_effect=RuntimeError("Save failed")), \
                mock.patch.object(board.toast, "show_message", side_effect=lambda text: results.append((text, threading.get_ident()))):
            board.move_task(7, 20)
            self.assertIn(7, board.columns[20].task_cards)
            self.assertFalse(self.card(board, 20).isEnabled())
            self.wait_until(lambda: not board._pending_assignments)
        self.assertIn(7, board.columns[10].task_cards)
        self.assertNotIn(7, board.columns[20].task_cards)
        self.assertEqual(self.raw_task(board)["artist"], 10)
        self.assertEqual(results, [("Assignment failed", gui_thread)])
        self.assertTrue(self.card(board).isEnabled())

    def test_failed_assignment_from_unassigned_restores_null_artist(self):
        jobs = jobs_data()
        jobs[0]["timelines"][0]["shots"][0]["tasks"][0]["artist"] = None
        board = self.make_board(jobs)
        with mock.patch.object(http_help.DjangoAPI, "update_task", side_effect=RuntimeError("Save failed")):
            board.move_task(7, 10)
            self.wait_until(lambda: not board._pending_assignments)
        self.assertIsNone(self.raw_task(board)["artist"])
        self.assertIn(7, board.columns["unassigned"].task_cards)

    def test_successful_drag_confirms_and_survives_filters(self):
        board = self.make_board()
        with mock.patch.object(http_help.DjangoAPI, "update_task", return_value={"id": 7, "artist": 20}) as update:
            board.move_task(7, 20)
            self.wait_until(lambda: not board._pending_assignments)
        update.assert_called_once_with(7, artist=20)
        self.assertEqual(board.toast.message.text(), "Task assigned to Two")
        self.assertTrue(board.toast.isVisible())
        board.show_hidden_checkbox.setChecked(True)
        self.finish_build(board)
        self.assertTrue(self.card(board, 20).isEnabled())

    def test_pending_assignment_survives_refresh_and_rolls_back_into_current_columns(self):
        board = self.make_board()
        started, release = threading.Event(), threading.Event()
        def update(*args, **kwargs):
            started.set()
            if not release.wait(2):
                raise RuntimeError("Test worker timed out")
            raise RuntimeError("Save failed")
        with mock.patch.object(http_help.DjangoAPI, "update_task", side_effect=update) as save:
            try:
                board.move_task(7, 20)
                self.assertTrue(started.wait(1))
                board.set_jobs_data(jobs_data(), force=True)
                self.finish_build(board)
                self.assertIn(7, board.columns[20].task_cards)
                board.move_task(7, 10)
                self.assertEqual(save.call_count, 1)
            finally:
                release.set()
            self.wait_until(lambda: not board._pending_assignments)
        self.assertIn(7, board.columns[10].task_cards)
        self.assertNotIn(7, board.columns[20].task_cards)

    def test_assignment_completion_after_tab_switch_does_not_touch_deleted_columns(self):
        board = self.make_board()
        started, release = threading.Event(), threading.Event()
        def update(*args, **kwargs):
            started.set()
            release.wait(2)
            raise RuntimeError("Save failed")
        with mock.patch.object(http_help.DjangoAPI, "update_task", side_effect=update):
            try:
                board.move_task(7, 20)
                self.assertTrue(started.wait(1))
                board.hide()
                # A cached snapshot arriving while hidden still shows the
                # optimistic assignment; failure must correct it as well.
                board.set_jobs_data(list(board._jobs_by_id.values()))
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            finally:
                release.set()
            self.wait_until(lambda: not board._pending_assignments)
        self.assertFalse(board.columns)
        board.show()
        self.finish_build(board)
        self.assertIn(7, board.columns[10].task_cards)

    def test_success_during_drag_corrects_queued_stale_snapshot(self):
        board = self.make_board()
        with mock.patch.object(http_help.DjangoAPI, "update_task", return_value={"id": 7, "artist": 20}):
            board.notify_drag_state(True)
            board.move_task(7, 20)
            board.set_jobs_data(jobs_data())
            self.wait_until(lambda: not board._pending_assignments)
            board.notify_drag_state(False)
            self.finish_build(board)
        self.assertIn(7, board.columns[20].task_cards)
        self.assertEqual(self.raw_task(board)["artist"], 20)

    def test_unknown_artist_is_preserved_in_unassigned_column(self):
        board = self.make_board()
        self.card(board)._apply_updated_task({**self.raw_task(board), "artist": 99})
        self.assertEqual(self.card(board, "unassigned")._data["artist"], 99)
        self.assertEqual(self.card(board, "unassigned")._task_ref["shot"], "SH010")
        board.show_hidden_checkbox.setChecked(True)
        self.finish_build(board)
        self.assertEqual(self.raw_task(board)["artist"], 99)
        self.assertIn(7, board.columns["unassigned"].task_cards)

    def test_refresh_updates_current_job_without_resetting_timeline(self):
        board = self.make_board()
        board.set_active_timeline_id(101)
        latest = jobs_data()
        latest[0]["timelines"][0]["shots"][0]["tasks"][0]["title"] = "Updated"
        with mock.patch.object(board._api, "getAPI", return_value=latest):
            board.refresh_btn.click()
        self.finish_build(board)
        self.assertEqual(self.card(board).btn_task_title.text(), "Updated")
        self.assertEqual(board.timeline_combo.currentData(), 101)
        self.assertEqual(board._active_timeline_id, 101)

    def test_refresh_adds_removes_and_reassigns_tasks(self):
        board = self.make_board()
        latest = jobs_data()
        latest[0]["timelines"][0]["shots"][0]["tasks"] = [
            {"id": 8, "title": "New task", "artist": 20, "status": "assigned"},
        ]
        board.set_jobs_data(latest)
        self.finish_build(board)
        self.assertNotIn(7, board.columns[10].task_cards)
        self.assertEqual(self.card(board, 20, 8).btn_task_title.text(), "New task")
        self.assertNotIn(7, board._task_records)

    def test_refresh_failure_preserves_existing_board(self):
        board = self.make_board()
        with mock.patch.object(board._api, "getAPI", side_effect=RuntimeError("Offline")):
            board.refresh_btn.click()
        self.assertEqual(self.card(board).btn_task_title.text(), "Comp")
        self.assertEqual(board.toast.message.text(), "Could not refresh board")

    def test_refresh_waits_for_open_editor_and_preserves_its_saved_changes(self):
        board = self.make_board()
        card = self.card(board)
        dialog = QDialog(card)
        dialog.setModal(True)
        dialog.show()
        self.app.processEvents()
        latest = jobs_data()
        latest[0]["title"] = "Updated job"
        try:
            board.set_jobs_data(latest)
            self.assertIs(self.card(board), card)
            self.assertIsNotNone(board._pending_jobs_data)
            card._apply_updated_task({**self.raw_task(board), "title": "Saved edit"})
        finally:
            dialog.accept()
        self.wait_until(lambda: board._pending_jobs_data is None)
        self.finish_build(board)
        self.assertEqual(board.job_combo.currentText(), "Updated job")
        self.assertEqual(self.card(board).btn_task_title.text(), "Saved edit")

    def test_new_refresh_supersedes_snapshot_queued_while_editing(self):
        board = self.make_board()
        dialog = QDialog(self.card(board))
        dialog.setModal(True)
        dialog.show()
        self.app.processEvents()
        try:
            board.set_jobs_data(jobs_data())
        finally:
            dialog.accept()
        latest = jobs_data()
        latest[0]["timelines"][0]["shots"][0]["tasks"][0]["title"] = "Newest"
        board.set_jobs_data(latest)
        board._apply_pending_jobs()
        self.finish_build(board)
        self.assertIsNone(board._pending_jobs_data)
        self.assertEqual(self.card(board).btn_task_title.text(), "Newest")

    def test_edits_and_hidden_status_survive_filter_rebuilds(self):
        board = self.make_board()
        response = {**self.raw_task(board), "title": "Renamed", "notes": "New note", "priority": 9,
                    "budget_hours": "4.50", "status": "assigned"}
        self.card(board)._apply_updated_task(response)
        board.show_hidden_checkbox.setChecked(True)
        self.finish_build(board)
        data = self.card(board)._data
        for key in ("title", "notes", "priority", "budget_hours", "status"):
            self.assertEqual(data[key], response[key])
        self.card(board)._apply_updated_task({**response, "hidden": True})
        board.show_hidden_checkbox.setChecked(False)
        self.finish_build(board)
        self.assertNotIn(7, board.columns[10].task_cards)
        board.show_hidden_checkbox.setChecked(True)
        self.finish_build(board)
        self.assertTrue(self.card(board)._data["hidden"])
        self.assertEqual(self.card(board).btn_task_title.text(), "Renamed")

    def test_done_task_returns_with_updated_state_when_done_filter_disabled(self):
        board = self.make_board()
        self.card(board)._apply_updated_task({**self.raw_task(board), "status": "done", "progress": 100})
        self.assertNotIn(7, board.columns[10].task_cards)
        board.hide_done_checkbox.setChecked(False)
        self.finish_build(board)
        self.assertEqual(self.card(board)._data["status"], "done")
        self.assertEqual(self.card(board)._data["progress"], 100)

    def test_removed_job_falls_back_and_clears_old_timeline_and_tasks(self):
        board = self.make_board()
        board.set_active_timeline_id(101)
        board.set_jobs_data([{"id": 2, "title": "Job Two", "timelines": []}])
        self.finish_build(board)
        self.assertEqual(board.job_combo.currentData(), 2)
        self.assertEqual(board._active_job_id, 2)
        self.assertIsNone(board._active_timeline_id)
        self.assertEqual(board.timeline_combo.count(), 1)
        self.assertFalse(any(column.tasks for column in board.columns.values()))

    def test_empty_refresh_cancels_chunked_build(self):
        board = self.make_board()
        board.set_jobs_data(jobs_data(), force=True)
        board.set_jobs_data([])
        self.app.processEvents()
        self.assertIsNone(board._active_job_id)
        self.assertFalse(board.columns)
        self.assertFalse(board._pending_tasks)
        self.assertFalse(board._task_records)

    def test_delete_updates_counts_and_groups_and_stays_deleted_after_filter(self):
        board = self.make_board()
        with mock.patch.object(http_help.DjangoAPI, "delete_task", return_value=True) as delete:
            self.card(board).btn_delete_task.click()
        delete.assert_called_once_with(7)
        column = board.columns[10]
        self.assertFalse(column.tasks)
        self.assertFalse(column.task_cards)
        self.assertFalse(column.shot_groups)
        self.assertEqual(column.task_count_label.text(), "0")
        self.assertEqual(column.footer_left.text(), "0 tasks")
        self.assertNotIn(7, board._task_records)
        board.show_hidden_checkbox.setChecked(True)
        self.finish_build(board)
        self.assertFalse(board.columns[10].tasks)

    def test_delete_failure_keeps_task_and_reports_error(self):
        board = self.make_board()
        with mock.patch.object(http_help.DjangoAPI, "delete_task", side_effect=RuntimeError("Offline")):
            self.card(board).btn_delete_task.click()
        self.assertIn(7, board.columns[10].task_cards)
        self.assertEqual(board.toast.message.text(), "Could not delete task")

    def test_creation_sends_all_fields_once_and_survives_filters(self):
        board = self.make_board()
        values = {"title": "Paint", "notes": "Remove wire", "status": "assigned", "priority": 8,
                  "budget_hours": 3.5, "artist": 20}
        dialog = SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted, get_values=lambda: values)
        response = {"id": 8, "shot": 1001, "hidden": False, **values}
        with mock.patch.object(board_module, "TaskCreateDialog", return_value=dialog), \
                mock.patch.object(board._api, "create_task", return_value=response) as create, \
                mock.patch.object(board._api, "update_task") as update:
            board.columns[10].shot_groups[1001].add_task_btn.click()
        create.assert_called_once_with(shot_id=1001, **values)
        update.assert_not_called()
        self.assertEqual(self.card(board, 20, 8).btn_task_title.text(), "Paint")
        board.show_hidden_checkbox.setChecked(True)
        self.finish_build(board)
        self.assertEqual(self.card(board, 20, 8)._data["notes"], "Remove wire")

    def test_creation_failure_shows_error_without_phantom_task(self):
        board = self.make_board()
        dialog = SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted, get_values=lambda: {"title": "Paint"})
        with mock.patch.object(board_module, "TaskCreateDialog", return_value=dialog), \
                mock.patch.object(board._api, "create_task", side_effect=RuntimeError("Validation failed")), \
                mock.patch.object(board._api, "update_task") as update:
            board.columns[10].shot_groups[1001].add_task_btn.click()
        update.assert_not_called()
        self.assertEqual(set(board._task_records), {7})
        self.assertEqual(board.toast.message.text(), "Could not create task")

    def test_assigned_cards_use_board_users_without_per_card_requests(self):
        jobs = jobs_data()
        tasks = jobs[0]["timelines"][0]["shots"][0]["tasks"]
        tasks.extend({**tasks[0], "id": i} for i in range(8, 60))
        with mock.patch.object(http_help.DjangoAPI, "username_from_id") as lookup:
            board = self.make_board(jobs)
            self.assertEqual(len(board.columns[10].tasks), 53)
            self.assertEqual(self.card(board).btn_assigned.text(), "One")
            lookup.assert_not_called()
        self.get_users.assert_called_once()
        self.assertEqual(self.card(board)._data["progress"], 75)

    def test_uncached_thumbnail_is_displayed_after_worker_download(self):
        board = self.make_board()
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        image.fill(0x112233)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        data = bytes(buffer.data())
        with mock.patch.object(board_module.requests, "get", return_value=SimpleNamespace(ok=True, content=data)):
            group = board_module.ShotGroupCard(
                {"shot_id": 99, "shot": "Thumb", "thumbnail_url": "http://example.test/thumb.png"},
                parent=board, column=board.columns[10],
            )
            self.wait_until(lambda: group._thumb_orig is not None)
        self.assertFalse(group.thumbnail_label.pixmap().isNull())
        self.assertEqual(group.thumbnail_label.text(), "")

    def test_thumbnail_download_finishing_after_group_deletion_is_safe(self):
        board = self.make_board()
        started, release = threading.Event(), threading.Event()
        def download(*args, **kwargs):
            started.set()
            release.wait(2)
            return SimpleNamespace(ok=True, content=b"downloaded bytes")
        submit = board_module._THUMB_EXECUTOR.submit
        futures = []
        def tracked_submit(worker):
            future = submit(worker)
            futures.append(future)
            return future
        with mock.patch.object(board_module.requests, "get", side_effect=download), \
                mock.patch.object(board_module._THUMB_EXECUTOR, "submit", side_effect=tracked_submit):
            try:
                group = board_module.ShotGroupCard(
                    {"shot_id": 99, "shot": "Thumb", "thumbnail_url": "http://example.test/late.png"},
                    parent=board, column=board.columns[10],
                )
                self.assertTrue(started.wait(1))
                group.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            finally:
                release.set()
            # The download completes even though its Qt receiver no longer exists.
            self.assertIsNone(futures[0].result(timeout=2))
            self.app.processEvents()

    def test_text_and_malformed_task_drops_are_ignored(self):
        board = self.make_board()
        for payload in (b"ordinary text", b"7", b"invalid", b"\xff"):
            mime = QMimeData()
            mime.setText(payload.decode("ascii", errors="replace"))
            if payload in (b"invalid", b"\xff"):
                mime.setData(board_module._TASK_MIME_TYPE, payload)
            event = mock.Mock()
            event.mimeData.return_value = mime
            event.source.return_value = self.card(board)
            board.columns[20].dragEnterEvent(event)
            board.columns[20].dropEvent(event)
            event.acceptProposedAction.assert_not_called()
            event.ignore.assert_called()
        self.assertIn(7, board.columns[10].task_cards)

    def test_valid_internal_drop_moves_task(self):
        board = self.make_board()
        mime = QMimeData()
        mime.setData(board_module._TASK_MIME_TYPE, b"7")
        event = mock.Mock()
        event.mimeData.return_value = mime
        event.source.return_value = self.card(board)
        with mock.patch.object(http_help.DjangoAPI, "update_task", return_value={"id": 7, "artist": 20}):
            board.columns[20].dragEnterEvent(event)
            board.columns[20].dropEvent(event)
            self.wait_until(lambda: not board._pending_assignments)
        event.acceptProposedAction.assert_called()
        self.assertIn(7, board.columns[20].task_cards)

    def test_drag_handle_hit_uses_card_coordinates(self):
        board = self.make_board()
        card = self.card(board)
        position = card.drag_handle.mapTo(card, card.drag_handle.rect().center())
        self.assertTrue(card._is_drag_handle_hit(position))
        self.assertFalse(card._is_drag_handle_hit(QPoint(card.width() - 1, card.height() - 1)))

    def test_drag_sets_task_format_and_releases_refresh_pause_on_error(self):
        board = self.make_board()
        card = self.card(board)
        pauses = []
        board.drag_state_changed.connect(pauses.append)
        card._drag_allowed = True
        card._drag_start_pos = QPoint(0, 0)
        event = mock.Mock()
        event.buttons.return_value = Qt.MouseButton.LeftButton
        event.pos.return_value = QPoint(10, 10)
        with mock.patch.object(board_module, "QDrag") as drag:
            drag.return_value.exec.side_effect = RuntimeError("Drag cancelled by platform")
            with self.assertRaises(RuntimeError):
                card.mouseMoveEvent(event)
        mime = drag.return_value.setMimeData.call_args.args[0]
        self.assertEqual(bytes(mime.data(board_module._TASK_MIME_TYPE)), b"7")
        self.assertEqual(pauses, [True, False])
        self.assertFalse(board._drag_active)


class TaskCreationRequestTests(unittest.TestCase):
    def test_create_posts_all_task_fields_in_single_request(self):
        api = http_help.DjangoAPI()
        fields = {"artist": 20, "status": "assigned", "priority": 8, "budget_hours": 3.5}
        response = mock.Mock()
        response.json.return_value = {"id": 8, **fields}
        with mock.patch.object(api, "_request", return_value=response) as request:
            result = api.create_task(shot_id=1001, title="Paint", notes="Remove wire", **fields)
        request.assert_called_once_with(
            "POST", f"{api.base_url}tasks", operation="create_task",
            json={"shot": 1001, "title": "Paint", "notes": "Remove wire", **fields},
        )
        self.assertEqual(result["id"], 8)


if __name__ == "__main__":
    unittest.main()
