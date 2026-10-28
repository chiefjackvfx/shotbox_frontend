from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QTimer, Qt
from PyQt6.QtGui import QAction, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QDialog, QMainWindow, QMessageBox,
                            QProgressDialog, QStyle, QStyleOptionComboBox)

import filesIO
import app_cache
import http_help
import nuke_headless_tasks
import page_nukedash as dash
import widgets
from test.test_page_nukedash_status_filter import FakeSettingsManager

START_LOCK_MAINTENANCE = dash.page_nukedash._start_lock_maintenance_timers

USERS = [{'id': 10, 'username': 'artist', 'first_name': 'Artist', 'groups': [1]}]


def jobs_data(root='/tmp/shotbox-no-such-shot'):
    return [{'id': 1, 'title': 'Job One', 'timelines': [{'id': 101, 'title': 'Timeline One', 'shots': [
        {'id': 1001, 'title': 'SH010', 'base_path': root, 'hidden': False, 'tasks': [
            {'id': 7, 'title': 'Comp', 'artist': 10, 'status': 'in_progress',
             'priority': 5, 'budget_hours': 2, 'notes': '', 'hidden': False, 'progress': 25},
        ]},
    ]}]}]


class NukeDashRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettingsManager({'remember_last_session': True})
        self.pages = []
        self.windows = []
        self.requests = []
        self.gui_thread = threading.get_ident()
        http_help.DjangoAPI._cached_users = None
        http_help.DjangoAPI._cached_users_base_url = None
        def request(api, method, url, **kwargs):
            operation = kwargs.get('operation')
            self.requests.append((operation, threading.get_ident(), kwargs.get('json')))
            data = USERS if operation == 'get_users' else []
            if method == 'PATCH':
                data = kwargs.get('json', {})
            return SimpleNamespace(json=lambda: data)
        for patcher in (
            mock.patch.object(http_help.DjangoAPI, '_request', request),
            mock.patch.object(dash, 'get_settings_manager', return_value=self.settings),
            mock.patch.object(widgets, 'HAS_MULTIMEDIA', False),
            mock.patch.object(widgets.ShotCard, '_refresh_matchmove_state', return_value=None),
            mock.patch.object(dash.page_nukedash, '_refresh_timeline_matchmove_state', return_value=None),
            mock.patch.object(dash.page_nukedash, '_start_lock_maintenance_timers', return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        for page in self.pages:
            page.shutdown()
            page.close()
            page.deleteLater()
        for root in self.windows:
            root.close()
            root.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        http_help.DjangoAPI._cached_users = None
        http_help.DjangoAPI._cached_users_base_url = None

    def wait(self, condition, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.001)
        self.fail('Timed out waiting for Qt/worker completion')

    def page(self, jobs=None):
        page = dash.page_nukedash()
        self.pages.append(page)
        page.show()
        page._shot_file_poll_timer.stop()
        page._shot_file_tooltip_timer.stop()
        self.wait(lambda: not page._fetch_in_flight)
        if jobs is not None:
            page._on_data(jobs)
            self.wait(lambda: page._active_job_id == jobs[0]['id'] and not page._is_chunked_loading)
            self.wait(lambda: bool(page._iter_timeline_shot_cards()))
            page._apply_filters(force=True)
            self.wait(lambda: not page._task_materialize_queue)
        self.app.processEvents()
        return page

    def shot(self, page):
        return page._iter_timeline_shot_cards()[0]

    def task(self, page, task_id=7):
        return self.shot(page)._task_widgets_by_id[task_id]

    def lock_page(self):
        page = self.page(jobs_data())
        card = self.shot(page)
        page._lock_read_api.get_shot = mock.Mock(return_value={
            **card.data, 'nuke_in_use': 'None',
        })
        START_LOCK_MAINTENANCE(page)
        return page, card

    def test_nuke_claim_runs_on_worker_and_launches_once_after_success(self):
        from datetime import datetime, timezone
        page, card = self.lock_page()
        claim_threads = []
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        def claim(*args, **kwargs):
            claim_threads.append(threading.get_ident())
            return {'ok': True, 'shot': {'id': card.data['id'], 'nuke_in_use': f'chief@desk|{stamp}'}}
        page._lock_worker.api.update_shot_lock = mock.Mock(side_effect=claim)
        with mock.patch.object(http_help.DjangoAPI, 'get_lock_owner_id', return_value='chief@desk'), \
                mock.patch.object(card.filesIO, 'open_file') as launch:
            page._handle_nuke_open_request(card, '/show/SH010_v001.nk')
            page._handle_nuke_open_request(card, '/show/SH010_v001.nk')
            self.wait(lambda: launch.called)
            launch.assert_called_once_with('/show/SH010_v001.nk')
            page._lock_worker.api.update_shot_lock.assert_called_once_with(
                card.data['id'], force=False, owner_id='chief@desk',
            )
            self.assertEqual(card._lock_state, 'mine')
            self.assertTrue(all(ident != self.gui_thread for ident in claim_threads))

    def test_conflicting_claim_updates_owner_and_never_launches_nuke(self):
        page, card = self.lock_page()
        page._lock_worker.api.update_shot_lock = mock.Mock(return_value={
            'ok': False, 'conflict': True, 'nuke_in_use': 'other@desk|20000101T000000Z',
            'lock_status': {'owner': 'other@desk'},
        })
        with mock.patch.object(card.filesIO, 'open_file') as launch, \
                mock.patch.object(page, '_warn_lock_conflict') as warning:
            page._handle_nuke_open_request(card, '/show/SH010_v001.nk')
            self.wait(lambda: warning.called)
            launch.assert_not_called()
            self.assertIn('other', card.btn_open_nuke.text())

    def test_saved_and_live_release_settings_reach_lock_worker(self):
        self.settings.set('nuke_lock_release_grace_sec', 180)
        self.settings.set('nuke_lock_auto_release', False)
        page, card = self.lock_page()
        self.assertEqual(page._lock_worker.release_grace_sec, 180)
        self.assertFalse(page._lock_worker.auto_release)
        page.set_nuke_lock_release_settings(45, True)
        self.wait(lambda: page._lock_worker.release_grace_sec == 45 and page._lock_worker.auto_release)

    def test_manual_cleanup_runs_on_worker_and_immediately_clears_visible_lock(self):
        page, card = self.lock_page()
        owner = 'chief@desk'
        shot = {**card.data, 'nuke_in_use': f'{owner}|20000101T000000Z'}
        threads, previews, results = [], [], []
        callback_threads = []
        def get_shots():
            threads.append(threading.get_ident())
            return [dict(shot)]
        def get_shot(shot_id):
            threads.append(threading.get_ident())
            return dict(shot)
        def release(shot_id, **kwargs):
            threads.append(threading.get_ident())
            shot['nuke_in_use'] = 'None'
            return {'ok': True, 'shot': dict(shot)}
        def preview_ready(preview):
            callback_threads.append(threading.get_ident())
            previews.append(preview)
        def clear_finished(result):
            callback_threads.append(threading.get_ident())
            results.append(result)
        api = page._lock_worker.api
        api.get_shots = mock.Mock(side_effect=get_shots)
        api.get_shot = mock.Mock(side_effect=get_shot)
        api.get_lock_owner_ids = mock.Mock(return_value={owner})
        api.update_shot_lock = mock.Mock(side_effect=release)
        page.lock_clear_preview_ready.connect(preview_ready)
        page.lock_clear_finished.connect(clear_finished)
        page._on_worker_lock_updated(shot['id'], shot['nuke_in_use'])
        self.assertEqual(card._lock_state, 'stale')
        page.lock_clear_preview_requested.emit(True)
        self.wait(lambda: bool(previews))
        api.update_shot_lock.assert_not_called()
        page.lock_clear_requested.emit(previews[0])
        self.wait(lambda: bool(results) and card.data.get('nuke_in_use') == 'None')
        api.update_shot_lock.assert_called_once_with(shot['id'], release=True, owner_id=owner)
        self.assertEqual(card._lock_state, 'none')
        self.assertEqual(len(results[0]['cleared']), 1)
        self.assertTrue(threads)
        self.assertTrue(all(ident != self.gui_thread for ident in threads))
        self.assertEqual(callback_threads, [self.gui_thread, self.gui_thread])

    def test_saved_and_live_detector_debug_settings_reach_lock_worker(self):
        self.settings.set('debug_modes.nuke_detector', True)
        with mock.patch('nuke_detector.print_snapshot_debug') as output:
            page, card = self.lock_page()
            self.wait(lambda: output.called)
            self.assertTrue(page._lock_worker.debug)
            page.set_nuke_detector_debug(False)
            self.wait(lambda: not page._lock_worker.debug)
            output.reset_mock()
            page.set_nuke_detector_debug(True)
            self.wait(lambda: output.called)
            self.assertTrue(page._lock_worker.debug)

    def test_saved_detector_interval_updates_running_timer_and_scan_frequency(self):
        self.settings.set('nuke_detector_interval_sec', 12)
        self.settings.set('debug_modes.nuke_detector', True)
        with mock.patch('nuke_detector.print_snapshot_debug') as output:
            page, card = self.lock_page()
            self.wait(lambda: output.called)
            self.assertEqual(page._lock_worker.timer.interval(), 12000)
            output.reset_mock()
            page.set_nuke_detector_interval(1)
            self.wait(lambda: page._lock_worker.timer.interval() == 1000)
            self.wait(lambda: output.called)
            self.assertEqual(page._lock_interval_ms, 1000)

    def test_opening_recovered_alias_renews_original_owner_without_force_take(self):
        page, card = self.lock_page()
        page._lock_read_api.get_shot.return_value = {
            **card.data, 'nuke_in_use': 'os-user@desk|20000101T000000Z',
        }
        page._lock_read_api.get_lock_owner_id = mock.Mock(return_value='chief@desk')
        page._lock_read_api.get_lock_owner_ids = mock.Mock(return_value={'chief@desk', 'os-user@desk'})
        requests = []
        page.lock_claim_requested.connect(requests.append)
        # Keep the real worker idle: this test checks the GUI's claim request.
        page.lock_claim_requested.disconnect(page._lock_worker.claim)
        popup = mock.Mock()
        popup.addButton.side_effect = ['cancel', 'open']
        popup.clickedButton.return_value = 'open'
        with mock.patch.object(dash, 'QMessageBox', return_value=popup) as message_box:
            message_box.Icon = QMessageBox.Icon
            message_box.ButtonRole = QMessageBox.ButtonRole
            page._handle_nuke_open_request(card, '/show/SH010_v001.nk')
        self.assertEqual(requests[0]['owner_id'], 'os-user@desk')
        self.assertTrue(requests[0]['already_owned'])
        self.assertFalse(requests[0]['force'])
        popup.setWindowTitle.assert_called_once_with('Already Locked By You')

    def test_close_waits_for_lock_claim_and_cleans_unlaunched_lock(self):
        page, card = self.lock_page()
        started, release = threading.Event(), threading.Event()
        def update(*args, **kwargs):
            if not kwargs.get('release'):
                started.set()
                release.wait(3)
            return {'ok': True}
        page._lock_worker.api.update_shot_lock = mock.Mock(side_effect=update)
        with mock.patch.object(card.filesIO, 'open_file') as launch:
            try:
                page._handle_nuke_open_request(card, '/show/SH010_v001.nk')
                self.assertTrue(started.wait(1))
                self.assertFalse(page.close())
            finally:
                release.set()
            self.wait(lambda: not page._lock_thread.isRunning() and not page.isVisible())
            launch.assert_not_called()
            self.assertTrue(page._lock_worker.api.update_shot_lock.call_args.kwargs['release'])

    def test_startup_and_manual_refresh_fetch_on_worker_thread(self):
        page = self.page()
        startup = [item for item in self.requests if item[0] in ('get_api_root', 'get_users')]
        self.assertEqual(len(startup), 2)
        self.assertTrue(all(item[1] != self.gui_thread for item in startup))
        called = threading.Event()
        release = threading.Event()
        def slow_api():
            called.set()
            release.wait(2)
            return []
        with mock.patch.object(page._worker.api, 'getAPI', side_effect=slow_api):
            try:
                before = time.monotonic()
                page.refresh_clicked()
                self.assertLess(time.monotonic() - before, .2)
                self.assertTrue(called.wait(1))
                events = []
                QTimer.singleShot(0, lambda: events.append(True))
                self.app.processEvents()
                self.assertEqual(events, [True])
            finally:
                release.set()
            self.wait(lambda: not page._fetch_in_flight)

    def test_assigned_cards_reuse_user_cache(self):
        jobs = jobs_data()
        tasks = jobs[0]['timelines'][0]['shots'][0]['tasks']
        tasks.extend({**tasks[0], 'id': index} for index in range(8, 38))
        page = self.page(jobs)
        self.assertEqual(len(self.shot(page)._task_widgets_by_id), 31)
        self.assertEqual(self.task(page).btn_assigned.text(), 'Artist')
        self.assertEqual(sum(item[0] == 'get_users' for item in self.requests), 1)
        page._apply_filters(force=True)
        self.assertEqual(sum(item[0] == 'get_users' for item in self.requests), 1)

    def apply_theme(self):
        previous = self.app.styleSheet()
        self.app.setStyleSheet(Path(dash.SCRIPT_DIR, 'ui/dark_v01.qss').read_text())
        self.addCleanup(self.app.setStyleSheet, previous)

    def test_jobs_dropdown_resizes_after_asynchronous_startup_and_refresh(self):
        self.apply_theme()
        ready = threading.Event()
        jobs = [
            {'id': 1, 'title': 'Production Job - Autumn Campaign', 'timelines': []},
            {'id': 2, 'title': 'Second Production', 'timelines': []},
        ]
        def fetch():
            ready.wait(2)
            return jobs
        with mock.patch.object(http_help.DjangoAPI, 'getAPI', side_effect=fetch):
            page = dash.page_nukedash()
            self.pages.append(page)
            page.resize(1200, 800)
            page.show()
            self.app.processEvents()
            self.assertEqual(page.comboBox_jobs.count(), 0)
            ready.set()
            self.wait(lambda: page._active_job_id == 1 and not page._is_chunked_loading)
            self.app.processEvents()
            combo = page.comboBox_jobs
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            field = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,
                option, QStyle.SubControl.SC_ComboBoxEditField, combo)
            self.assertGreaterEqual(field.width() - 4,
                option.fontMetrics.horizontalAdvance(jobs[0]['title']))
            combo.setCurrentIndex(1)
            self.wait(lambda: page._active_job_id == 2 and not page._is_chunked_loading)
            page.refresh_clicked()
            self.wait(lambda: not page._fetch_in_flight)
            self.assertEqual(combo.currentData(), 2)
            self.assertEqual(combo.currentText(), 'Second Production')

    def test_filters_stay_on_one_line_with_uniform_box_text(self):
        self.apply_theme()
        page = self.page(jobs_data())
        controls = [page.label_mode_state, page.checkBox_enable_filters,
                    page.checkBox_show_to_conform, page.checkBox_hidden_shot,
                    page.checkBox_hidden_tasks, page.label_sort, page.comboBox_sort,
                    page.label, page.comboBox_sort_artist, page.label_2,
                    page.comboBox_sort_status, page.Search_bar, page.Label_results]
        for width in (800, 1200, 1600):
            with self.subTest(width=width):
                page.resize(width, 800)
                self.app.processEvents()
                self.assertEqual(page.width(), width)
                self.assertLess(page.search_bar.height(), 64)
                contents = page._filter_row_scroll.widget()
                rects = [QRect(control.mapTo(contents, QPoint()), control.size())
                         for control in controls]
                for i, rect in enumerate(rects):
                    self.assertTrue(contents.rect().contains(rect),
                                    controls[i].objectName())
                    self.assertLessEqual(abs(rect.center().y() - rects[0].center().y()), 1)
                    for other in rects[i + 1:]:
                        self.assertFalse(rect.intersects(other))
                for combo in (page.comboBox_sort, page.comboBox_sort_artist,
                              page.comboBox_sort_status):
                    option = QStyleOptionComboBox()
                    combo.initStyleOption(option)
                    field = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,
                        option, QStyle.SubControl.SC_ComboBoxEditField, combo)
                    self.assertGreaterEqual(field.width() - 4,
                        option.fontMetrics.horizontalAdvance(combo.currentText()))
                boxes = (page.comboBox_sort, page.comboBox_sort_artist,
                         page.comboBox_sort_status, page.Search_bar)
                self.assertEqual(len({box.height() for box in boxes}), 1)
                self.assertEqual(len({box.font().pixelSize() for box in boxes}), 1)
                self.assertEqual(page.comboBox_sort_status.lineEdit().font().pixelSize(),
                                 page.comboBox_sort.font().pixelSize())
                if width == 800:
                    self.assertGreater(page._filter_row_scroll.horizontalScrollBar().maximum(), 0)
                if width == 1600:
                    self.assertEqual(page._filter_row_scroll.horizontalScrollBar().maximum(), 0)
                    gap = rects[5].left() - rects[4].right()
                    self.assertGreater(gap, 24)
        self.assertEqual(page.label_sort.text(), 'Sort')
        self.assertEqual(page.label.text(), 'Artist')
        self.assertEqual(page.label_2.text(), 'Status')

    def test_missing_user_cache_never_fetches_users_while_rendering(self):
        page = self.page(jobs_data())
        http_help.DjangoAPI._cached_users = None
        http_help.DjangoAPI._cached_users_base_url = None
        with mock.patch.object(http_help.DjangoAPI, 'get_users', side_effect=RuntimeError('Offline')) as users:
            self.shot(page)._sync_task_widgets_from_data()
        users.assert_not_called()
        self.assertEqual(self.task(page).btn_assigned.text(), 'User 10')
        http_help.DjangoAPI._cached_users = USERS
        http_help.DjangoAPI._cached_users_base_url = page._worker.api.base_url
        page._on_users_data(USERS)
        self.assertEqual(self.task(page).btn_assigned.text(), 'Artist')

    def test_user_lookup_cache_is_scoped_to_server(self):
        old = http_help.DjangoAPI(base_url='http://old.test/api/')
        new = http_help.DjangoAPI(base_url='http://new.test/api/')
        http_help.DjangoAPI._cached_users = USERS
        http_help.DjangoAPI._cached_users_base_url = old.base_url
        with mock.patch.object(new, 'get_users') as request:
            self.assertEqual(old.cached_username_from_id(10), 'Artist')
            self.assertIsNone(new.cached_username_from_id(10))
        request.assert_not_called()

    def test_empty_initial_response_finishes_modal_loading(self):
        page = self.page()
        page._initial_load = True
        page._loading_dialog.show_loading(page, 'Connecting...')
        page._on_data([])
        self.assertFalse(page._initial_load)
        self.assertFalse(page._loading_dialog.isVisible())
        self.assertEqual(page.Label_results.text(), '0 results')

    def test_removed_job_selects_fallback_and_removes_old_shots(self):
        page = self.page(jobs_data())
        page._on_data([{'id': 2, 'title': 'Job Two', 'timelines': []}])
        self.wait(lambda: page._active_job_id == 2 and not page._is_chunked_loading)
        self.assertEqual(page.comboBox_jobs.currentData(), 2)
        self.assertEqual(page.timelines_tabs.count(), 0)
        page._on_data([])
        self.assertIsNone(page._active_job_id)
        self.assertEqual(page.comboBox_jobs.count(), 0)
        self.assertEqual(page.timelines_tabs.count(), 0)

    def test_real_status_checkbox_and_label_click_toggle_only_target(self):
        page = self.page(jobs_data())
        combo = page.comboBox_sort_status
        index = combo.model().index(2, 0)
        target = combo.model().item(2).data(Qt.ItemDataRole.UserRole)
        for x in (8, 80):
            page._set_all_status_filters(False)
            combo.showPopup()
            self.app.processEvents()
            rect = combo.view().visualRect(index)
            QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton,
                             pos=QPoint(rect.left() + x, rect.center().y()))
            self.app.processEvents()
            self.assertEqual(page._status_filter_values, {target})
            combo.hidePopup()
        combo.showPopup()
        self.app.processEvents()
        rect = combo.view().visualRect(index)
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(rect.left() + 8, rect.center().y()))
        self.app.processEvents()
        self.assertEqual(page._status_filter_values, set())
        combo.hidePopup()

    def test_status_edit_reapplies_filters_when_embedded(self):
        page = self.page(jobs_data())
        root = QMainWindow()
        root.setCentralWidget(page)
        self.windows.append(root)
        root.show()
        page._status_filter_values = {'in_progress'}
        page._apply_filters(force=True)
        task = self.task(page)
        self.assertIs(task.window(), root)
        action = QAction('Done')
        action.setData('done')
        response = {**task._data, 'status': 'done', 'progress': 100}
        with mock.patch.object(task._api, 'update_task', return_value=response):
            task._on_status_action(action)
        self.assertTrue(self.shot(page).isHidden())
        self.assertEqual(page.Label_results.text(), '0 results')

    def test_create_posts_all_fields_once_and_shows_new_task(self):
        page = self.page(jobs_data())
        shot = self.shot(page)
        values = {'title': 'Paint', 'notes': 'Remove wire', 'artist': 10, 'status': 'assigned',
                  'budget_hours': 3.5, 'priority': 8}
        dialog = SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted, get_values=lambda: values)
        with mock.patch.object(widgets, 'TaskCreateDialog', return_value=dialog), \
                mock.patch.object(shot._api, 'create_task', return_value={'id': 8, **values}) as create, \
                mock.patch.object(shot._api, 'update_task') as update:
            shot._on_add_task_clicked()
        create.assert_called_once_with(shot_id=1001, **values)
        update.assert_not_called()
        self.wait(lambda: 8 in shot._task_widgets_by_id)
        self.assertEqual(shot._task_widgets_by_id[8].btn_task_title.text(), 'Paint')

    def test_create_failure_reports_error_without_phantom_task(self):
        page = self.page(jobs_data())
        shot = self.shot(page)
        dialog = SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted, get_values=lambda: {'title': 'Paint'})
        with mock.patch.object(widgets, 'TaskCreateDialog', return_value=dialog), \
                mock.patch.object(shot._api, 'create_task', side_effect=RuntimeError('Validation failed')), \
                mock.patch.object(widgets.QMessageBox, 'warning') as warning:
            shot._on_add_task_clicked()
        self.assertEqual([task['id'] for task in shot.data['tasks']], [7])
        warning.assert_called_once()

    def test_hide_shot_updates_visibility_and_count_immediately(self):
        page = self.page(jobs_data())
        shot = self.shot(page)
        with mock.patch.object(shot._api, 'update_shot', return_value={**shot.data, 'hidden': True}):
            shot.on_hide_btn()
        self.assertTrue(shot.isHidden())
        self.assertEqual(page.Label_results.text(), '0 results')

    def test_scroll_position_uses_actual_outer_scroll_area(self):
        page = self.page(jobs_data())
        page._setup_scroll_detection()
        bar = page.preview_scroll.verticalScrollBar()
        bar.setRange(0, 1000)
        bar.setValue(321)
        self.assertTrue(page._is_scrolling)
        page._save_session_state()
        self.assertEqual(self.settings.get('last_scroll_position'), 321)
        bar.setValue(0)
        page._restore_scroll_position(321)
        self.assertEqual(bar.value(), 321)

    def test_pause_reasons_are_independent_and_checkbox_respects_pause(self):
        page = self.page()
        page.checkBox_refresh.setChecked(True)
        page.set_auto_refresh_paused(True, reason='assignment')
        page.set_auto_refresh_paused(True, reason='thumbnails')
        page.set_auto_refresh_paused(False, reason='thumbnails')
        self.assertFalse(page._timer.isActive())
        page.checkBox_refresh.setChecked(False)
        page.checkBox_refresh.setChecked(True)
        self.assertFalse(page._timer.isActive())
        page.set_auto_refresh_paused(False, reason='assignment')
        self.assertTrue(page._timer.isActive())
        page.checkBox_refresh.setChecked(False)
        page.set_auto_refresh_paused(False, reason='assignment')
        self.assertFalse(page._timer.isActive())

    def test_to_conform_discovers_render_on_excluded_shot(self):
        page = self.page(jobs_data())
        shot = self.shot(page)
        page.show_to_conform = True
        page._apply_filters(force=True)
        self.assertTrue(shot.isHidden())
        snapshot = filesIO.ShotFileStateSnapshot(shot_dir=shot.shot_dir, render_path='/tmp/new.mov',
            render_relpath='SH010_v003.mov', render_display='SH010_v003.mov', render_version=3)
        with mock.patch.object(page.filesIO, 'scan_shot_file_state', return_value=snapshot):
            page._poll_visible_shot_card_file_state_batch()
            self.wait(lambda: not shot.isHidden())
        self.assertEqual(page.Label_results.text(), '1 results')

    def test_failed_preview_update_retries_same_unchanged_snapshot(self):
        page = self.page(jobs_data())
        shot = self.shot(page)
        snapshot = filesIO.ShotFileStateSnapshot(shot_dir=shot.shot_dir, preview_path='/tmp/new.mp4',
            preview_name='SH010_v002.mp4', preview_version=2)
        with mock.patch.object(page.filesIO, 'scan_shot_file_state', return_value=snapshot), \
                mock.patch.object(page._shot_card_api, 'update_shot', side_effect=[RuntimeError('Offline'), {}]) as update, \
                mock.patch.object(dash.time, 'monotonic', return_value=100) as clock:
            page._poll_visible_shot_card_file_state_batch()
            self.assertIn(1001, page._preview_updates_pending)
            clock.return_value = 101
            page._poll_visible_shot_card_file_state_batch()
            self.assertEqual(update.call_count, 1)
            clock.return_value = 106
            page._poll_visible_shot_card_file_state_batch()
        self.assertEqual(update.call_count, 2)
        self.assertFalse(page._preview_updates_pending)
        update.assert_called_with(1001, preview_video='renders/precomp/previews/SH010_v002.mp4')

    def test_removed_thumbnail_clears_image_and_ignores_inflight_download(self):
        page = self.page(jobs_data())
        shot = self.shot(page)
        image = QPixmap(2, 2)
        image.fill(Qt.GlobalColor.red)
        callbacks = []
        with mock.patch.object(widgets.ImageLoader, 'instance') as loader:
            loader.return_value.load.side_effect = lambda url, callback: callbacks.append(callback)
            shot.set_thumbnail('http://example.test/pending.png')
            shot.set_thumbnail(None)
            callbacks[0](image)
        self.assertIsNone(shot._thumb_orig)
        self.assertIsNone(shot._thumb_sig)
        self.assertIsNone(shot._thumb_pending_url)
        self.assertTrue(shot.label_thumbnail.pixmap().isNull())

    def test_page_destruction_cancels_queued_refresh_callbacks(self):
        page = self.page(jobs_data())
        page._on_data(jobs_data())  # Queues filters for 50ms later.
        page.shutdown()
        self.pages.remove(page)
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QTest.qWait(100)  # Would abort on a dangling singleShot lambda.

    def test_shutdown_cancels_delayed_work_even_if_page_stays_alive(self):
        page = self.page()
        callbacks = []
        dash._single_shot(page, 20, lambda: callbacks.append(True))
        self.assertTrue(page.shutdown())
        QTest.qWait(40)
        self.assertEqual(callbacks, [])

    def test_close_defers_until_running_api_worker_finishes(self):
        page = self.page()
        started, release = threading.Event(), threading.Event()
        def slow_api():
            started.set()
            release.wait(3)
            return []
        with mock.patch.object(page._worker.api, 'getAPI', side_effect=slow_api):
            try:
                page._request_api_refresh()
                self.assertTrue(started.wait(1))
                self.assertFalse(page.close())
                self.assertTrue(page._thread.isRunning())
            finally:
                release.set()
            self.wait(lambda: not page._thread.isRunning() and not page.isVisible())

    def test_preview_cancel_remains_cancelled_and_releases_pause(self):
        page = self.page(jobs_data())
        self.shot(page).data['original_clip'] = '/plates/source.mov'
        result = SimpleNamespace(success=False, error='Cancelled', relative_path='', output_lines=[])
        generator = mock.Mock()
        generator.nuke_available = True
        def cancel(**kwargs):
            page.findChildren(QProgressDialog)[0].cancel()
            return result
        generator.generate_preview.side_effect = cancel
        with mock.patch.object(nuke_headless_tasks, 'PreviewGenerator', return_value=generator), \
                mock.patch.object(nuke_headless_tasks, 'preview_input_exists', return_value=True), \
                mock.patch.object(dash.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(dash.QMessageBox, 'information') as info, \
                mock.patch.object(page, '_request_api_refresh'):
            page._on_make_all_previews_clicked()
        self.assertTrue(info.call_args.args[2].startswith('Cancelled.'))
        self.assertNotIn('previews', page._refresh_pause_reasons)
        generator.begin_preview_batch.assert_called_once_with()
        generator.end_preview_batch.assert_called_once_with()

    def test_preview_button_uses_overwrite_generator_for_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            previews = Path(directory) / 'renders/precomp/previews'
            previews.mkdir(parents=True)
            (previews / 'SH010_plate_01_v001.mp4').touch()
            jobs = jobs_data(directory)
            jobs[0]['timelines'][0]['shots'][0]['original_clip'] = '/plates/source.mov'
            page = self.page(jobs)
            self.settings.set('preview_overwrite', True)
            result = SimpleNamespace(success=True, error='', relative_path='renders/precomp/previews/SH010_plate_01_v001.mp4', output_lines=[])
            generator = mock.Mock()
            generator.nuke_available = True
            generator.generate_preview_with_overwrite.return_value = result
            with mock.patch.object(nuke_headless_tasks, 'PreviewGenerator', return_value=generator), \
                    mock.patch.object(nuke_headless_tasks, 'preview_input_exists', return_value=True), \
                    mock.patch.object(dash.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), \
                    mock.patch.object(dash.QMessageBox, 'information'), \
                    mock.patch.object(page, '_request_api_refresh'):
                page._on_make_all_previews_clicked()
            generator.generate_preview_with_overwrite.assert_called_once()
            generator.generate_preview.assert_not_called()

    def test_successful_preview_batch_reports_done(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = jobs_data(directory)
            jobs[0]['timelines'][0]['shots'][0]['original_clip'] = '/plates/source.mov'
            page = self.page(jobs)
            result = SimpleNamespace(success=True, error='', relative_path='renders/precomp/previews/SH010_plate_01_v001.mp4', output_lines=[])
            generator = mock.Mock()
            generator.nuke_available = True
            generator.generate_preview.return_value = result
            with mock.patch.object(nuke_headless_tasks, 'PreviewGenerator', return_value=generator), \
                    mock.patch.object(nuke_headless_tasks, 'preview_input_exists', return_value=True), \
                    mock.patch.object(dash.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), \
                    mock.patch.object(dash.QMessageBox, 'information') as info, \
                    mock.patch.object(page, '_request_api_refresh'):
                page._on_make_all_previews_clicked()
            self.assertTrue(info.call_args.args[2].startswith('Done!'))
            self.assertIn('Completed: 1', info.call_args.args[2])
            self.assertNotIn('previews', page._refresh_pause_reasons)
            generator.begin_preview_batch.assert_called_once_with()
            generator.end_preview_batch.assert_called_once_with()

    def test_denoise_button_queues_selected_timeline_sources_including_hidden_shots(self):
        with tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp")) as directory:
            root = Path(directory)
            source = root / "source.mov"
            source.write_bytes(b"native decoding tested separately")
            secondary = root / "secondary.mov"
            secondary.write_bytes(b"secondary source")
            first = {"id": 1, "title": "SH010", "base_path": str(root / "SH010"),
                     "original_clips": [str(source), str(secondary)]}
            hidden = {"id": 2, "title": "SH020", "base_path": str(root / "SH020"), "original_clip": str(source), "hidden": True}
            missing = {"id": 3, "title": "SH030", "base_path": str(root / "SH030")}
            other = {"id": 4, "title": "SH040", "base_path": str(root / "SH040"), "original_clip": str(source)}
            job = {"id": 1, "title": "Current job", "timelines": [
                {"id": 11, "title": "One", "shots": [first, hidden, missing]},
                {"id": 12, "title": "Two", "shots": [other]},
            ]}
            page = self.page([job])
            controller = mock.Mock(busy=False)
            with mock.patch.object(page, "_ensure_denoise_controller", return_value=controller), \
                    mock.patch("denoise_controller.choose_output_mode", return_value="overwrite") as choice:
                page.btn_denoise.click()
            tasks = controller.start_batch.call_args.args[0]
            self.assertEqual([task["shot"]["id"] for task in tasks], [1, 1, 2])
            self.assertEqual([task["media"]["source"] for task in tasks], [str(source), str(secondary), str(source)])
            self.assertTrue(all(task["media"]["mode"] == "overwrite" for task in tasks))
            self.assertEqual(controller.start_batch.call_args.kwargs, {"skipped": 1})
            choice.assert_called_once_with(page, 3, 1, timeline_name='One', include_destination=True)
            alias = {**first, "id": 99}
            collected, skipped = page._collect_denoise_tasks({"timelines": [{"shots": [first, alias]}]}, filesIO.Folders())
            self.assertEqual(len(collected), 2)
            self.assertEqual(skipped, 0)
            with mock.patch.object(page, "_ensure_denoise_controller", return_value=controller), \
                    mock.patch("denoise_controller.choose_output_mode", return_value="new"):
                page._handle_denoise_request(mock.Mock(data=first), str(secondary))
            single = controller.start_batch.call_args.args[0]
            self.assertEqual(len(single), 1)
            self.assertEqual(single[0]["media"]["mode"], "new")
            self.assertEqual(single[0]["media"]["plate"], "plate_02")
            self.assertEqual(single[0]["media"]["source"], str(secondary))
            page.timelines_tabs.setCurrentIndex(1)
            with mock.patch.object(page, "_ensure_denoise_controller", return_value=controller), \
                    mock.patch("denoise_controller.choose_output_mode", return_value='overwrite') as choice:
                page.btn_denoise.click()
            self.assertEqual([task['shot']['id'] for task in controller.start_batch.call_args.args[0]], [4])
            choice.assert_called_once_with(page, 1, 0, timeline_name='Two', include_destination=True)

    def test_denoise_without_selected_timeline_does_not_collect_entire_job(self):
        page = self.page(jobs_data())
        with mock.patch.object(page, '_active_timeline_data', return_value=None), \
                mock.patch.object(page, '_collect_denoise_tasks') as collect, \
                mock.patch.object(dash.QMessageBox, 'information') as information:
            page.btn_denoise.click()
        collect.assert_not_called()
        self.assertIn('No timeline selected', information.call_args.args[2])

    def test_stale_or_missing_single_clip_does_not_denoise_another_source(self):
        page = self.page(jobs_data())
        with mock.patch.object(page, '_ensure_denoise_controller') as controller, \
                mock.patch('denoise_controller.choose_output_mode') as choose, \
                mock.patch.object(dash.QMessageBox, 'information') as information:
            page._handle_denoise_request(self.shot(page), '/no-longer-listed.mov')
            page._handle_denoise_request(self.shot(page), None)
        choose.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(information.call_count, 2)

    def test_denoise_button_decline_or_missing_sources_does_not_launch(self):
        page = self.page(jobs_data())
        with mock.patch.object(page, "_ensure_denoise_controller") as controller, \
                mock.patch.object(dash.QMessageBox, "information") as information, \
                mock.patch("denoise_controller.choose_output_mode", return_value=None) as choice:
            page.btn_denoise.click()
            information.assert_called_once()
            choice.assert_not_called()
            with mock.patch.object(page, "_collect_denoise_tasks", return_value=([{"shot": {}, "media": {"source": "/test.mov"}}], 0)):
                page.btn_denoise.click()
            choice.assert_called_once()
            controller.assert_not_called()

    def test_denoise_controller_keeps_toolbar_and_refresh_in_sync(self):
        page = self.page(jobs_data())
        controller = page._ensure_denoise_controller()
        self.assertIs(page._ensure_denoise_controller(), controller)
        controller.busy_changed.emit(True)
        self.assertFalse(page.btn_denoise.isEnabled())
        self.assertIn("denoise", page._refresh_pause_reasons)
        controller.busy_changed.emit(False)
        self.assertTrue(page.btn_denoise.isEnabled())
        self.assertNotIn("denoise", page._refresh_pause_reasons)
        with mock.patch.object(dash.sys, "platform", "win32"):
            controller.busy_changed.emit(False)
            self.assertTrue(page.btn_denoise.isEnabled())
        with mock.patch.object(dash.sys, "platform", "darwin"):
            controller.busy_changed.emit(False)
            self.assertFalse(page.btn_denoise.isEnabled())



if __name__ == '__main__':
    unittest.main()
