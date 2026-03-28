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

from PyQt6.QtWidgets import QApplication

import page_assignment_board


TEST_JOBS = [
    {"id": 1, "title": "Job One", "timelines": []},
    {"id": 2, "title": "Job Two", "timelines": []},
]


class AssignmentBoardSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_visible_board_applies_jobs_immediately(self):
        with mock.patch.object(
            page_assignment_board.AssignmentBoardPage, "_load_users", lambda self: None
        ):
            board = page_assignment_board.AssignmentBoardPage()
            try:
                board.show()
                self.app.processEvents()

                board.set_jobs_data(TEST_JOBS)
                self.app.processEvents()

                self.assertEqual(board.job_combo.count(), 2)
                self.assertEqual(board.job_combo.currentData(), 1)
            finally:
                board.close()
                board.deleteLater()
                self.app.processEvents()

    def test_hidden_board_keeps_requested_job_until_jobs_arrive(self):
        with mock.patch.object(
            page_assignment_board.AssignmentBoardPage, "_load_users", lambda self: None
        ):
            board = page_assignment_board.AssignmentBoardPage()
            try:
                board.set_active_job_id(2)
                board.set_jobs_data(TEST_JOBS)

                board.show()
                self.app.processEvents()

                self.assertEqual(board.job_combo.currentData(), 2)
                self.assertEqual(board._active_job_id, 2)
            finally:
                board.close()
                board.deleteLater()
                self.app.processEvents()

    def test_show_hidden_checkbox_controls_hidden_task_filter(self):
        with mock.patch.object(
            page_assignment_board.AssignmentBoardPage, "_load_users", lambda self: None
        ):
            board = page_assignment_board.AssignmentBoardPage()
            try:
                self.assertFalse(board.show_hidden_checkbox.isChecked())
                self.assertFalse(board._should_show_task({"hidden": True, "status": "in_progress"}))

                board.show_hidden_checkbox.setChecked(True)

                self.assertTrue(board._show_hidden_tasks)
                self.assertTrue(board._should_show_task({"hidden": True, "status": "in_progress"}))
                self.assertFalse(board._should_show_task({"hidden": True, "status": "done"}))
            finally:
                board.close()
                board.deleteLater()
                self.app.processEvents()

    def test_stop_auto_refresh_checkbox_emits_pause_signal(self):
        with mock.patch.object(
            page_assignment_board.AssignmentBoardPage, "_load_users", lambda self: None
        ):
            board = page_assignment_board.AssignmentBoardPage()
            try:
                emitted = []
                board.auto_refresh_pause_changed.connect(lambda paused: emitted.append(paused))

                board.pause_auto_refresh_checkbox.setChecked(True)
                board.pause_auto_refresh_checkbox.setChecked(False)

                self.assertEqual(emitted, [True, False])
                self.assertFalse(board._pause_auto_refresh)
            finally:
                board.close()
                board.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
