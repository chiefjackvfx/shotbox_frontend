"""Local bootstrap contract tests: fake host/Qt, real collector and disk."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_adapter import FakeNuke, FakeApp, Timer
from test_core import Clock


class Menu:
    def __init__(self):
        self.items = {}

    def findItem(self, name):
        return self.items.get(name)

    def addMenu(self, name):
        assert name not in self.items
        self.items[name] = Menu()
        return self.items[name]

    def addCommand(self, name, callback):
        assert name not in self.items
        self.items[name] = callback


class Host(FakeNuke):
    GUI = True

    def __init__(self):
        super().__init__()
        self.main = Menu()
        self.messages = []
        self.path = 'Root'

    def menu(self, name):
        assert name == 'Nuke'
        return self.main

    def root(self):
        return SimpleNamespace(name=lambda: self.path)

    def tprint(self, text):
        self.messages.append(text)


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'diagnostics'

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('nuke_time_calc.local_test'),
                             'Local bootstrap must exist')
        from nuke_time_calc import local_test
        return local_test

    def start(self):
        module = self.module()
        self.clock = Clock()
        self.deferred = []
        self.app = FakeApp()
        class DeferredTimer(Timer):
            singleShot = staticmethod(lambda delay, callback: self.deferred.append(callback))
        self.qt = SimpleNamespace(QTimer=DeferredTimer, Qt=SimpleNamespace(ApplicationActive=1))
        widgets = SimpleNamespace(QApplication=SimpleNamespace(instance=lambda: self.app))
        qt_patch = patch.object(module, '_qt_modules', return_value=(self.qt, None, widgets), create=True)
        qt_patch.start()
        self.addCleanup(qt_patch.stop)
        from nuke_time_calc.nuke_adapter import install
        def installer(**kwargs):
            return install(**kwargs, monotonic=self.clock.monotonic, utc_now=self.clock.utc)
        self.controller = module.register(self.host, self.directory, installer=installer)
        self.assertEqual(len(self.deferred), 1)
        self.deferred.pop()()
        return self.controller

    def test_auto_enable_writes_private_per_session_diagnostic_jsonl(self):
        controller = self.start()
        self.assertIsNotNone(controller.adapter)
        self.host.fire('KnobChanged')
        self.clock.advance(4)
        self.host.fire('OnScriptSave')
        files = list(self.directory.glob('*.jsonl'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text())
        self.assertEqual(files[0].stem, record['session_id'])
        self.assertEqual(record['comp_seconds'], 4)
        self.assertIn('Jack', record['identity'])
        self.assertIn('not authenticated', record['identity'])
        self.assertIn('local-test', record['identity'])
        self.assertEqual(record['context']['project_id'], 'local-test')
        self.assertEqual(record['context']['shot_id'], 'Root')
        self.assertEqual(record['context']['script_path'], 'Root')
        self.assertFalse(record['context']['shotbox_mapping'])
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.host.path = '/show/shot/comp.nk'
        self.host.fire('OnScriptSave')
        records = [json.loads(line) for line in files[0].read_text().splitlines()]
        self.assertEqual(records[-1]['context']['shot_id'], self.host.path)
        self.assertEqual(records[-1]['context']['script_path'], self.host.path)
        self.assertEqual(records[-2]['context']['shot_id'], 'Root')

    def test_disable_preserves_full_queue_and_held_interval_until_recovery(self):
        controller = self.start()
        adapter = controller.adapter
        def fail(record):
            raise OSError('disk unavailable')
        adapter.collector.emit = fail
        for _ in range(128):
            self.host.fire('OnScriptSave')
        self.host.fire('KnobChanged')
        self.clock.advance(3)
        controller.disable()
        self.assertFalse(adapter.installed)
        self.assertFalse(adapter.collector.running)
        self.assertEqual(adapter.collector.pending_count, 128)
        self.assertEqual(adapter.collector.comp_seconds, 3)
        controller.enable()
        self.assertIs(controller.adapter, adapter)
        self.assertFalse(adapter.installed)
        self.assertEqual(adapter.collector.comp_seconds, 3)
        adapter.collector.emit = controller.emit
        controller.enable()
        self.assertIsNot(controller.adapter, adapter)
        self.assertTrue(controller.adapter.installed)
        records = [json.loads(line) for line in
                   (self.directory / (adapter.collector.session_id + '.jsonl')).read_text().splitlines()]
        self.assertEqual(len(records), 129)
        self.assertEqual(records[-1]['comp_seconds'], 3)
        self.assertEqual(records[-1]['reason'], 'local_test_recovery')
        controller.enable()
        self.assertEqual(len(self.host.callbacks['KnobChanged']), 1)

    def test_status_reports_estimates_and_retained_data_nonmodally(self):
        controller = self.start()
        self.host.fire('KnobChanged')
        self.clock.advance(2)
        controller.adapter.collector.tick()
        text = self.host.main.items['shotbox'].items['nuke_time_calc'].items['Status']()
        for value in ('estimates', 'local diagnostics', 'no API', 'not authenticated',
                      'NOT Shotbox mapping', 'running=True', 'pending=0',
                      'unsealed=2.000s', str(self.directory)):
            self.assertIn(value, text)
        self.assertEqual(self.host.messages[-1], text)

    def test_open_folder_uses_qt_local_url_not_shell(self):
        controller = self.start()
        opened = []
        core = SimpleNamespace(QUrl=SimpleNamespace(fromLocalFile=lambda path: ('local', path)))
        gui = SimpleNamespace(QDesktopServices=SimpleNamespace(openUrl=lambda url: opened.append(url) or True))
        with patch.object(self.module(), '_qt_modules', return_value=(core, gui, None)):
            self.assertTrue(controller.open_folder())
        self.assertEqual(opened, [('local', str(self.directory))])
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    def test_disable_before_deferred_start_cancels_automatic_enable(self):
        controller = self.start()
        controller.disable()
        # Simulate a still-queued startup callback on a second host.
        second = Host()
        waiting = self.module().register(second, self.directory)
        waiting.disable()
        self.deferred.pop()()
        self.assertIsNone(waiting.adapter)
        self.assertFalse(any(second.callbacks.values()))

    def test_uninstall_error_keeps_adapter_and_stops_collection(self):
        controller = self.start()
        adapter = controller.adapter
        self.host.fire('KnobChanged')
        self.clock.advance(3)
        def broken_remove(callback):
            raise RuntimeError('host refused removal')
        self.host.removeAfterRender = broken_remove
        controller.disable()
        self.assertIs(controller.adapter, adapter)
        self.assertFalse(adapter.collector.running)
        controller.enable()
        self.assertIs(controller.adapter, adapter)
        self.assertIn('RuntimeError', controller.status())
        records = [json.loads(line) for line in
                   (self.directory / (adapter.collector.session_id + '.jsonl')).read_text().splitlines()]
        self.assertEqual(records[-1]['comp_seconds'], 3)

    def test_menu_startup_error_is_nonmodal(self):
        def broken_menu(name):
            raise RuntimeError('menus unavailable')
        self.host.menu = broken_menu
        try:
            result = self.module().register(self.host, self.directory)
        except RuntimeError:
            self.fail('Startup errors must not escape into Nuke')
        self.assertIsNone(result)
        self.assertIn('RuntimeError', self.host.messages[-1])
        self.assertFalse(any(self.host.callbacks.values()))

    def test_error_paused_adapter_recovers_in_place_after_flushing(self):
        controller = self.start()
        adapter = controller.adapter
        def fail(record):
            raise OSError('disk unavailable')
        adapter.collector.emit = fail
        for _ in range(128):
            self.host.fire('OnScriptSave')
        self.host.fire('KnobChanged')
        self.clock.advance(2)
        self.host.fire('OnScriptSave')
        controller.enable()
        self.assertIs(controller.adapter, adapter)
        self.assertFalse(adapter.collector.running)
        adapter.collector.emit = controller.emit
        controller.enable()
        self.assertIs(controller.adapter, adapter)
        self.assertTrue(adapter.collector.running)
        self.assertEqual(adapter.collector.pending_count, 0)
        self.assertFalse(adapter.collector.blocked)
        self.assertIn('error=none', controller.status())

    def test_headless_register_has_no_side_effects(self):
        self.host.GUI = False
        self.assertIsNone(self.module().register(self.host, self.directory))
        self.assertEqual(self.host.main.items, {})
        self.assertFalse(self.directory.exists())

    def test_register_reuses_exact_menu_and_commands(self):
        module = self.module()
        top = self.host.main.addMenu('shotbox')
        sub = top.addMenu('nuke_time_calc')
        first = module.register(self.host, self.directory)
        second = module.register(self.host, self.directory)
        self.assertIs(first, second)
        self.assertIs(self.host.main.items['shotbox'], top)
        self.assertIs(top.items['nuke_time_calc'], sub)
        self.assertEqual(set(sub.items), {'Status', 'Enable local test',
                                        'Disable local test', 'Open diagnostics folder'})


if __name__ == '__main__':
    unittest.main()
