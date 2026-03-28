from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

import page_nukedash
import project_load_profiler


class DummySignal:
    def __init__(self):
        self._callbacks = []
        self.emitted = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        self.emitted.append(args)
        for callback in list(self._callbacks):
            callback(*args)


class FakeSettingsManager:
    def __init__(self, startup_tab: int = 0, extra_settings: dict | None = None):
        self.values = {
            "django_username": None,
            "startup_tab": startup_tab,
            "remember_window_size": False,
            "show_startup_loading_dialog": False,
            "enable_assignment_board": False,
            "enable_review_page": False,
            "enable_activity_page": False,
            "enable_import_page": False,
            "enable_xml_import_page": False,
            "debug_modes.project_load_profiler": False,
        }
        if extra_settings:
            self.values.update(extra_settings)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_polling_interval_ms(self):
        return 5000

    def save(self):
        return True


class FakeSettingsPage(QWidget):
    def __init__(self, settings_manager):
        super().__init__()
        self.settings_changed = DummySignal()
        self.server_url_changed = DummySignal()


class FakeLoadingDialog:
    def __init__(self):
        self.messages = []
        self.hidden = False

    def show_loading(self, parent=None, message=None):
        self.messages.append(message)
        self.hidden = False

    def hide_loading(self):
        self.hidden = True

    def set_message(self, message):
        self.messages.append(message)

    def set_detail(self, detail):
        return

    def set_progress(self, current, total):
        return

    def isVisible(self):
        return False

    def center_on_parent(self):
        return


class FakeWorker:
    def __init__(self):
        self.fetch_calls = 0

    def fetch(self):
        self.fetch_calls += 1


class FakeNukeDash(QWidget):
    def __init__(self):
        super().__init__()
        self._initial_load = False
        self._loading_dialog = FakeLoadingDialog()


class FakeSimplePage(QWidget):
    pass


class FakeNotificationSystem:
    pass


def import_main_module():
    sys.modules.pop("main", None)

    fake_modules = {}
    module_specs = {
        "activity_page": ("ActivityPage", FakeSimplePage),
        "page_assignment_board": ("AssignmentBoardPage", FakeSimplePage),
        "review_page": ("ReviewPage", FakeSimplePage),
        "importer_page": ("ImporterPage", FakeSimplePage),
        "import_xml_v2": ("XMLImportPage", FakeSimplePage),
        "duration_updater": ("DurationUpdaterPage", FakeSimplePage),
        "shotbox_notifications": ("NotificationSystem", FakeNotificationSystem),
    }
    for module_name, (symbol_name, symbol_value) in module_specs.items():
        module = types.ModuleType(module_name)
        setattr(module, symbol_name, symbol_value)
        fake_modules[module_name] = module

    with mock.patch.dict(sys.modules, fake_modules, clear=False):
        return importlib.import_module("main"), fake_modules


def build_main_window(startup_tab: int = 0, extra_settings: dict | None = None):
    main_module, fake_modules = import_main_module()
    settings_manager = FakeSettingsManager(startup_tab=startup_tab, extra_settings=extra_settings)
    with mock.patch.dict(sys.modules, fake_modules, clear=False), \
        mock.patch.object(main_module, "get_settings_manager", return_value=settings_manager), \
        mock.patch.object(main_module, "page_nukedash", FakeNukeDash), \
        mock.patch.object(main_module, "SettingsPage", FakeSettingsPage), \
        mock.patch.object(main_module.MainWindow, "_apply_initial_settings", lambda self: None), \
        mock.patch.object(main_module.MainWindow, "_apply_notification_settings", lambda self: None), \
        mock.patch.object(main_module.MainWindow, "_apply_window_settings", lambda self: None), \
        mock.patch.object(main_module.MainWindow, "_apply_runtime_settings_to_pages", lambda self: None), \
        mock.patch.object(main_module.MainWindow, "_wire_review_page", lambda self: None), \
        mock.patch.object(main_module.MainWindow, "_wire_assignment_board", lambda self: None), \
        mock.patch.object(main_module.MainWindow, "_wire_activity_page", lambda self: None):
        return main_module.MainWindow()


class TimingHarness(QMainWindow):
    _set_loaded_time_text = page_nukedash.page_nukedash._set_loaded_time_text
    _start_load_timing = page_nukedash.page_nukedash._start_load_timing
    _finish_load_timing = page_nukedash.page_nukedash._finish_load_timing
    _fail_load_timing = page_nukedash.page_nukedash._fail_load_timing
    _finish_timed_load_after_data = page_nukedash.page_nukedash._finish_timed_load_after_data
    _set_project_load_profiler_enabled = page_nukedash.page_nukedash._set_project_load_profiler_enabled
    _start_project_switch_profile = page_nukedash.page_nukedash._start_project_switch_profile
    _complete_project_switch_profile = page_nukedash.page_nukedash._complete_project_switch_profile
    _fail_project_switch_profile = page_nukedash.page_nukedash._fail_project_switch_profile
    _emit_project_load_report_if_ready = page_nukedash.page_nukedash._emit_project_load_report_if_ready
    refresh_clicked = page_nukedash.page_nukedash.refresh_clicked
    _on_error = page_nukedash.page_nukedash._on_error
    _on_data = page_nukedash.page_nukedash._on_data
    _on_job_load_complete = page_nukedash.page_nukedash._on_job_load_complete
    _on_timeline_tab_changed = page_nukedash.page_nukedash._on_timeline_tab_changed

    def __init__(self):
        super().__init__()
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.label_loaded_time = QLabel("", central)
        self.comboBox_jobs = QComboBox(central)
        self.timelines_tabs = QTabWidget(central)
        self.timelines_tabs.addTab(QWidget(self.timelines_tabs), "Timeline 1")

        layout.addWidget(self.label_loaded_time)
        layout.addWidget(self.comboBox_jobs)
        layout.addWidget(self.timelines_tabs)

        self._loading_dialog = FakeLoadingDialog()
        self._worker = FakeWorker()
        self._load_timing_context = None
        self._load_timing_started_at = None
        self._set_loaded_time_text("--")
        self.report_lines = []
        self._project_load_profiler = project_load_profiler.ProjectLoadProfiler(
            enabled=False,
            printer=self.report_lines.append,
        )
        self._project_load_report_timer = page_nukedash.QTimer(self)
        self._project_load_report_timer.setSingleShot(True)
        self._project_load_report_timer.timeout.connect(self._emit_project_load_report_if_ready)
        self._project_load_report_poll_ms = 1
        self._project_load_report_grace_seconds = 0.0

        self._initial_load = False
        self._dropdowns_populated = True
        self._is_chunked_loading = False
        self._pending_refresh_jobs = None
        self._pending_scroll_restore = None
        self._session_restored = False
        self._active_job_id = 1
        self._jobs_by_id = {}
        self._pending_job_data = {"id": 1, "title": "Job A", "timelines": []}
        self._active_timeline_index = 0

        self.jobs_data_updated = DummySignal()
        self.active_job_changed = DummySignal()
        self.active_timeline_changed = DummySignal()

        self.show()
        QApplication.processEvents()

    def _populate_filter_dropdowns(self):
        self._dropdowns_populated = True

    def _reconcile_jobs_list(self, jobs):
        return

    def _silent_update_job(self, job_data):
        return

    def _apply_filters(self, force=False):
        return

    def _apply_compact_view_state(self):
        return

    def _save_session_state(self):
        return

    def _restore_session_state(self, restore_data):
        return

    def _load_timeline_if_needed(self, index):
        return


class MainWindowAssignmentBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_assignment_board_tab_is_not_created_when_feature_flag_is_off(self):
        window = build_main_window(startup_tab=0)
        try:
            tab_titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
            self.assertFalse(hasattr(window, "page_assignment_board"))
            self.assertFalse(hasattr(window, "page_review"))
            self.assertFalse(hasattr(window, "page_activity"))
            self.assertFalse(hasattr(window, "page_importer"))
            self.assertFalse(hasattr(window, "page_xmlImport"))
            self.assertNotIn("Assignment Board", tab_titles)
            self.assertNotIn("Review", tab_titles)
            self.assertNotIn("Import", tab_titles)
            self.assertNotIn("XML Import", tab_titles)
            self.assertNotIn("📋 Activity", tab_titles)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_startup_tab_review_mapping_falls_back_to_tasks_when_review_is_disabled(self):
        window = build_main_window(startup_tab=1)
        try:
            self.assertIs(window.tabs.currentWidget(), window.page_nukedash)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_startup_tab_activity_mapping_falls_back_to_tasks_when_activity_is_disabled(self):
        window = build_main_window(startup_tab=3)
        try:
            self.assertIs(window.tabs.currentWidget(), window.page_nukedash)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_startup_tab_xml_import_mapping_falls_back_to_tasks_when_xml_import_is_disabled(self):
        window = build_main_window(startup_tab=2)
        try:
            self.assertIs(window.tabs.currentWidget(), window.page_nukedash)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_optional_pages_can_be_reenabled_from_settings_on_next_launch(self):
        window = build_main_window(
            startup_tab=1,
            extra_settings={
                "enable_assignment_board": True,
                "enable_review_page": True,
                "enable_activity_page": True,
                "enable_import_page": True,
                "enable_xml_import_page": True,
            },
        )
        try:
            tab_titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
            self.assertIn("Assignment Board", tab_titles)
            self.assertIn("Review", tab_titles)
            self.assertIn("Import", tab_titles)
            self.assertIn("XML Import", tab_titles)
            self.assertIn("📋 Activity", tab_titles)
            self.assertIs(window.tabs.currentWidget(), window.page_review)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


class NukeDashLoadTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.harness = TimingHarness()

    def tearDown(self):
        self.harness.close()
        self.harness.deleteLater()
        self.app.processEvents()

    def test_default_label_is_placeholder(self):
        self.assertEqual(self.harness.label_loaded_time.text(), "Loaded in: --")

    def test_startup_path_formats_elapsed_time_on_job_complete(self):
        with mock.patch.object(page_nukedash.time, "perf_counter", side_effect=[10.0, 25.49]):
            self.harness._start_load_timing("startup")
            self.harness._on_job_load_complete()

        self.assertEqual(self.harness.label_loaded_time.text(), "Loaded in: 15.5s")

    def test_manual_refresh_path_updates_label_after_on_data(self):
        jobs = [{"id": 1, "title": "Job A", "timelines": []}]
        self.harness._jobs_by_id = {1: jobs[0]}
        self.harness._active_job_id = 1

        with mock.patch.object(page_nukedash.time, "perf_counter", side_effect=[100.0, 112.34]):
            self.harness.refresh_clicked()
            self.harness._on_data(jobs)

        self.assertEqual(self.harness._worker.fetch_calls, 1)
        self.assertEqual(self.harness.label_loaded_time.text(), "Loaded in: 12.3s")

    def test_job_switch_path_updates_label_after_job_load_complete(self):
        with mock.patch.object(page_nukedash.time, "perf_counter", side_effect=[5.0, 8.24]):
            self.harness._start_load_timing("job_switch")
            self.harness._on_job_load_complete()

        self.assertEqual(self.harness.label_loaded_time.text(), "Loaded in: 3.2s")

    def test_timeline_switch_does_not_change_loaded_time_label(self):
        self.harness._set_loaded_time_text("9.9s")
        self.harness._on_timeline_tab_changed(0)

        self.assertEqual(self.harness.label_loaded_time.text(), "Loaded in: 9.9s")

    def test_error_path_sets_failed_status(self):
        with mock.patch.object(page_nukedash.time, "perf_counter", return_value=1.0):
            self.harness._start_load_timing("manual_refresh")

        self.harness._on_error("boom")

        self.assertEqual(self.harness.label_loaded_time.text(), "Loaded in: failed")

    def test_manual_refresh_does_not_start_project_switch_profiler(self):
        self.harness._set_project_load_profiler_enabled(True)
        self.harness.refresh_clicked()

        self.assertFalse(self.harness._project_load_profiler.has_active_session())
        self.assertEqual(self.harness.report_lines, [])

    def test_timeline_switch_does_not_emit_project_switch_report(self):
        self.harness._set_project_load_profiler_enabled(True)
        self.harness._on_timeline_tab_changed(0)

        self.assertEqual(self.harness.report_lines, [])

    def test_project_switch_profiler_emits_report_once_when_enabled(self):
        self.harness._set_project_load_profiler_enabled(True)
        self.harness._start_project_switch_profile({"id": 9, "title": "Job Nine"})
        self.harness._project_load_profiler.record_wall_duration("job_lookup", 0.25)
        self.harness._project_load_profiler.record_wall_duration("timeline_build", 0.75)
        self.harness._project_load_profiler.record_work_duration(
            "shot_card_create", 1.50, count=3
        )
        self.harness._complete_project_switch_profile()

        header_lines = [
            line for line in self.harness.report_lines if "status=success" in line
        ]
        self.assertEqual(len(header_lines), 1)
        self.assertIn("job=Job Nine", header_lines[0])
        self.assertFalse(self.harness._project_load_profiler.has_active_session())

    def test_failed_project_switch_profiler_emits_failed_report(self):
        self.harness._set_project_load_profiler_enabled(True)
        self.harness._start_project_switch_profile({"id": 3, "title": "Job Three"})
        self.harness._fail_project_switch_profile("boom")

        header_lines = [
            line for line in self.harness.report_lines if "status=failed" in line
        ]
        self.assertEqual(len(header_lines), 1)
        self.assertIn("reason=boom", header_lines[0])


if __name__ == "__main__":
    unittest.main()
