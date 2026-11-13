import ast
import os
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication
from nuke_time_setup_controller import AutomaticNukeTimeSetup


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_setup_runs_off_gui_thread_and_reports_ready_without_controls(self):
        calls, results = [], []
        main_thread = threading.get_ident()
        def setup(**context):
            calls.append((threading.get_ident(), context))
            return {'status': 'ready', 'code': 'ready'}
        controller = AutomaticNukeTimeSetup(setup=setup)
        self.addCleanup(controller.close)
        controller.status_changed.connect(results.append)
        controller.set_context(server_url='http://192.168.10.88:8000', artist_id=1)
        self.assertEqual(calls, [])
        self.pump_until(lambda: bool(results))
        self.assertNotEqual(calls[0][0], main_thread)
        self.assertEqual(calls[0][1], {'server_url': 'http://192.168.10.88:8000', 'artist_id': 1})
        self.assertEqual(results, [{'status': 'ready', 'code': 'ready'}])
        self.assertGreaterEqual(controller.timer.interval(), 3600000)

    def test_context_changes_coalesce_and_old_result_is_not_published(self):
        entered, release = threading.Event(), threading.Event()
        calls, results = [], []
        def setup(**context):
            calls.append(context)
            if context['artist_id'] == 1:
                entered.set()
                release.wait(2)
            return {'status': 'ready', 'code': 'ready', 'artist_id': context['artist_id']}
        controller = AutomaticNukeTimeSetup(setup=setup)
        self.addCleanup(controller.close)
        self.addCleanup(release.set)
        controller.status_changed.connect(results.append)
        controller.set_context(server_url='http://192.168.10.88:8000', artist_id=1)
        self.pump_until(entered.is_set)
        controller.set_context(server_url='http://192.168.10.88:8000', artist_id=2)
        controller.set_context(server_url='http://192.168.10.88:8000', artist_id=3)
        self.app.processEvents()
        release.set()
        self.pump_until(lambda: bool(results))
        self.assertEqual([row['artist_id'] for row in calls], [1, 3])
        self.assertEqual(results, [{'status': 'ready', 'code': 'ready', 'artist_id': 3}])

    def test_worker_failure_is_sanitized_and_retries_automatically(self):
        def broken(**context):
            raise RuntimeError('private-token-must-not-appear')
        controller = AutomaticNukeTimeSetup(setup=broken)
        self.addCleanup(controller.close)
        results = []
        controller.status_changed.connect(results.append)
        with mock.patch('nuke_time_setup_controller.QThreadPool.globalInstance') as pool:
            controller.set_context(server_url='http://192.168.10.88:8000', artist_id=1)
            self.app.processEvents()
            self.assertEqual(pool.return_value.start.call_count, 1)
            controller.task.run()
        self.app.processEvents()
        self.assertEqual(results, [{'status': 'retry', 'code': 'setup_failed'}])
        self.assertTrue(controller.timer.isActive())
        self.assertLessEqual(controller.timer.interval(), 30000)

    def test_close_ignores_pending_result_and_never_restarts_timer(self):
        controller = AutomaticNukeTimeSetup(setup=lambda **kw: {'status': 'ready', 'code': 'ready'})
        results = []
        controller.status_changed.connect(results.append)
        with mock.patch('nuke_time_setup_controller.QThreadPool.globalInstance'):
            controller.set_context(server_url='http://192.168.10.88:8000', artist_id=1)
            self.app.processEvents()
            task = controller.task
            controller.close()
            task.run()
            self.app.processEvents()
            controller.set_context(server_url='http://192.168.10.88:8000', artist_id=2)
            self.app.processEvents()
        self.assertEqual(results, [])
        self.assertFalse(controller.timer.isActive())
        self.assertIsNone(controller.task)


class MainWindowWiringTests(unittest.TestCase):
    def test_automatic_setup_uses_only_normal_shotbox_identity_and_updates_handoff(self):
        tree = ast.parse((Path(__file__).parents[1] / 'main.py').read_text())
        window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
        functions = {n.name: n for n in window.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('_sync_automatic_nuke_time', functions)
        namespace = {}
        exec(compile(ast.Module(body=[functions['_sync_automatic_nuke_time']], type_ignores=[]), 'main.py', 'exec'), namespace)
        fake = mock.Mock()
        current = {'server_url': 'http://192.168.10.88:8000/api/', 'django_username': 1}
        fake._settings_manager.get.side_effect = current.get
        namespace['_sync_automatic_nuke_time'](fake)
        fake._nuke_time_setup.set_context.assert_called_once_with(server_url=current['server_url'], artist_id=1)
        fake.page_nukedash.set_time_reporting_context.assert_called_once_with(current['server_url'], 1)
        for name, called in [('__init__', '_start_automatic_nuke_time'), ('_on_settings_changed', '_sync_automatic_nuke_time'), ('_on_server_url_changed', '_sync_automatic_nuke_time')]:
            self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == called for n in ast.walk(functions[name])), name)
