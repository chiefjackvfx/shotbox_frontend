"""Runtime lifecycle tests with fake Nuke, not live host validation."""
import unittest
from types import SimpleNamespace
from test_adapter import FakeNuke


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.nuke = FakeNuke()
        self.nuke.GUI = True
        self.nuke.path = 'Root'
        self.nuke.root = lambda: SimpleNamespace(name=lambda: self.nuke.path)
        self.events = []
        self.nuke.tprint = self.events.append
        self.context = None
        self.worker = SimpleNamespace(start=lambda: self.events.append('upload-start'),
                                      stop=lambda timeout=0: self.events.append('upload-stop'),
                                      status=lambda: {'state': 'idle'})
        self.adapter = SimpleNamespace(installed=True,
            uninstall=lambda: self.events.append('uninstall'),
            status=lambda: {'installed': True},
            refresh_context=lambda: self.events.append('refresh'))
        self.calls = []

    def install(self, **kwargs):
        self.calls.append(kwargs)
        self.nuke._nuke_time_calc_adapter = self.adapter
        return self.adapter

    def make(self):
        from nuke_time_calc.runtime import Runtime
        return Runtime(nuke=self.nuke, identity='device-bound',
                       resolve=lambda path: self.context,
                       sink=self.events.append, uploader=self.worker,
                       installer=self.install)

    def test_unknown_start_waits_until_recognised_load(self):
        runtime = self.make()
        runtime.enable()
        self.assertEqual(self.calls, [])
        self.assertEqual(runtime.status()['coverage'], 'unresolved_script')
        self.context = {'job_id': 2, 'shot_id': 3, 'script_path': '/shots/a/comp.nk'}
        self.nuke.path = self.context['script_path']
        self.nuke.fire('OnScriptLoad')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['schema_version'], 2)
        self.assertEqual(self.calls[0]['context_resolver'](), self.context)
        self.assertEqual(runtime.status()['coverage'], 'collecting')


    def test_disable_removes_hooks_and_preserves_adapter_for_pending_data(self):
        self.context = {'job_id': 2, 'shot_id': 3, 'script_path': '/a.nk'}
        runtime = self.make().enable()
        runtime.disable()
        self.assertIn('uninstall', self.events)
        self.assertIn('upload-stop', self.events)
        self.assertIs(runtime.adapter, self.adapter)
        self.assertFalse(runtime.status()['enabled'])
        self.assertEqual(runtime.status()['coverage'], 'disabled')
        self.assertTrue(all(not values for values in self.nuke.callbacks.values()))
        runtime.disable()
        self.assertEqual(self.events.count('uninstall'), 1)

    def test_failed_hook_removal_cleans_other_resources_and_retries_without_duplicates(self):
        self.context = {'job_id': 2, 'shot_id': 3, 'script_path': '/a.nk'}
        runtime = self.make().enable()
        remove = self.nuke.removeOnScriptSave
        def fail(callback):
            raise OSError('host callback removal unavailable')
        self.nuke.removeOnScriptSave = fail
        runtime.disable()
        self.assertFalse(runtime.enabled)
        self.assertIn('uninstall', self.events)
        self.assertIn('upload-stop', self.events)
        self.assertEqual(runtime.status()['last_error'], 'OSError')
        self.assertEqual(self.nuke.callbacks['OnScriptLoad'], [])
        self.assertEqual(len(self.nuke.callbacks['OnScriptSave']), 1)
        runtime.enable()
        self.assertFalse(runtime.enabled)
        self.assertEqual(len(self.nuke.callbacks['OnScriptSave']), 1)
        self.assertEqual(self.events.count('upload-start'), 1)
        self.nuke.removeOnScriptSave = remove
        runtime.disable()
        self.assertFalse(any(self.nuke.callbacks.values()))
        self.assertEqual(self.events.count('uninstall'), 1)

    def test_existing_diagnostic_collector_is_not_replaced_or_retried(self):
        self.nuke._nuke_time_calc_adapter = object()
        self.context = {'job_id': 2, 'shot_id': 3, 'script_path': '/a.nk'}
        runtime = self.make().enable()
        self.assertEqual(self.calls, [])
        self.assertEqual(runtime.status()['last_error'], 'CollectorAlreadyInstalled')
        runtime.disable()
        self.assertIsNotNone(self.nuke._nuke_time_calc_adapter)

    def test_status_reports_stopped_context_not_collecting(self):
        self.context = {'job_id': 2, 'shot_id': 3, 'script_path': '/a.nk'}
        self.adapter.status = lambda: {'installed': True, 'running': False,
                                      'last_error': 'ValueError'}
        runtime = self.make().enable()
        self.assertEqual(runtime.status()['coverage'], 'paused')
        runtime.disable()


    def test_reenable_retries_retained_batches_before_new_collector(self):
        self.context = {'job_id': 2, 'shot_id': 3, 'script_path': '/a.nk'}
        retained = SimpleNamespace(pending_count=1, blocked=False)
        retained.retry_pending = lambda: self.events.append('retry')
        self.adapter.collector = retained
        def uninstall():
            self.events.append('uninstall')
            self.adapter.installed = False
            del self.nuke._nuke_time_calc_adapter
        self.adapter.uninstall = uninstall
        runtime = self.make().enable()
        runtime.disable()
        runtime.enable()
        self.assertIn('retry', self.events)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(runtime.status()['last_error'], 'PendingCollectorData')
        retained.pending_count = 0
        self.nuke.fire('OnScriptLoad')
        self.assertEqual(len(self.calls), 2)
        runtime.disable()


    def test_rapid_reenable_refuses_stopping_real_worker_until_explicit_retry(self):
        import os
        import tempfile
        import threading
        import time
        from pathlib import Path
        from nuke_time_calc.outbox import Outbox
        from nuke_time_calc.transport import Uploader
        from test_outbox import batch

        entered, release = threading.Event(), threading.Event()
        attempts = []
        def blocked_transport(*args):
            attempts.append(threading.current_thread())
            entered.set()
            release.wait(3)
            return 503, {}, b'{}'

        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder:
            root = Path(folder)
            token = root / 'token'
            token.write_text('test-selector.test-secret\n')
            token.chmod(0o600)
            queue = Outbox(root / 'outbox', server='https://shotbox.test',
                           artist_id='artist-1', device_id='device-1')
            queue.put(batch())
            self.worker = Uploader(queue, token_file=token, transport=blocked_transport)
            runtime = self.make()
            try:
                runtime.enable()
                self.assertTrue(entered.wait(2))
                old_thread = attempts[0]
                started = time.monotonic()
                runtime.disable()
                runtime.enable()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertFalse(runtime.enabled)
                self.assertEqual(runtime.status()['last_error'], 'UploaderStopping')
                self.assertTrue(old_thread.is_alive())
                self.assertEqual(len(attempts), 1)
                self.assertFalse(any(self.nuke.callbacks.values()))
                release.set()
                old_thread.join(2)
                self.assertFalse(old_thread.is_alive())
                runtime.enable()
                self.assertTrue(runtime.enabled)
                self.assertTrue(runtime.status()['upload']['running'])
                self.assertIsNone(runtime.status()['last_error'])
            finally:
                release.set()
                runtime.disable()
                self.worker.stop(timeout=2)

    def test_failed_worker_start_rolls_back_hooks_and_enabled_flag(self):
        def fail():
            raise OSError('test worker creation failure')
        self.worker.start = fail
        runtime = self.make()
        with self.assertRaises(OSError):
            runtime.enable()
        self.assertFalse(runtime.enabled)
        self.assertTrue(all(not items for items in self.nuke.callbacks.values()))


    def test_failed_qt_setup_and_rollback_keeps_owned_cleanup_retryable(self):
        from test_adapter import FakeApp, Timer
        from test_core import Clock
        from nuke_time_calc.nuke_adapter import install
        from nuke_time_calc.runtime import Runtime
        app = FakeApp()
        clock = Clock()
        class FailingTimer:
            def __init__(self):
                raise OSError('timer construction failed')
        qt = SimpleNamespace(QTimer=FailingTimer, Qt=SimpleNamespace(ApplicationActive=1))
        disconnect = app.applicationStateChanged.disconnect
        def failed_disconnect(callback):
            raise RuntimeError('temporary signal cleanup failure')
        app.applicationStateChanged.disconnect = failed_disconnect
        context = {'job_id': 2, 'shot_id': 3, 'script_path': '/a.nk'}
        runtime = Runtime(nuke=self.nuke, identity='test-device',
            resolve=lambda path: context, sink=self.events.append, uploader=self.worker,
            installer=lambda **kw: install(**kw, qt_core=qt, app=app,
                monotonic=clock.monotonic, utc_now=clock.utc))
        runtime.enable()
        self.assertEqual(len(app.applicationStateChanged.callbacks), 1)
        self.assertIs(runtime.adapter, self.nuke._nuke_time_calc_adapter)
        self.nuke.fire('OnScriptLoad')
        self.assertEqual(len(app.applicationStateChanged.callbacks), 1)
        app.applicationStateChanged.disconnect = disconnect
        qt.QTimer = Timer
        runtime.disable()
        self.assertEqual(app.applicationStateChanged.callbacks, [])
        self.assertIsNone(getattr(self.nuke, '_nuke_time_calc_adapter', None))
        runtime.enable()
        self.assertTrue(runtime.adapter.collector.running)
        runtime.disable()
        self.assertEqual(app.applicationStateChanged.callbacks, [])


class BootstrapTests(unittest.TestCase):
    def test_headless_register_does_not_read_configuration(self):
        from nuke_time_calc.runtime import register
        host = SimpleNamespace(GUI=False)
        def forbidden(*args):
            raise AssertionError('headless must not load state')
        self.assertIsNone(register(nuke_module=host, config_loader=forbidden))


    def test_disabled_config_adds_status_menu_without_starting_worker(self):
        from nuke_time_calc.runtime import register
        from test_local_test import Host
        host = Host()
        deferred = []
        def forbidden(*args):
            raise AssertionError('disabled config must not create runtime')
        controller = register(host, config_loader=lambda path: {'enabled': False},
            runtime_factory=forbidden, defer=deferred.append)
        deferred.pop()()
        self.assertEqual(controller.status()['coverage'], 'disabled')
        top = host.main.findItem('shotbox').findItem('nuke_time_calc')
        self.assertIsNotNone(top.findItem('Reporting status'))
        self.assertIs(register(host), controller)


    def test_disabled_configuration_stops_existing_runtime_on_enable(self):
        from nuke_time_calc.runtime import register
        from test_local_test import Host
        host = Host()
        config = {'enabled': True}
        events = []
        runtime = SimpleNamespace(enable=lambda: events.append('start'),
                                  disable=lambda: events.append('stop'),
                                  status=lambda: {'coverage': 'collecting'})
        deferred = []
        controller = register(host, config_loader=lambda path: dict(config),
            runtime_factory=lambda *args: runtime, defer=deferred.append)
        deferred.pop()()
        config['enabled'] = False
        controller.enable()
        self.assertEqual(events, ['start', 'stop'])


    def test_enabled_registration_uses_production_factory_by_default(self):
        from unittest.mock import patch
        from nuke_time_calc import runtime as module
        from test_local_test import Host
        host = Host()
        events = []
        worker_runtime = SimpleNamespace(enable=lambda: events.append('enabled'))
        deferred = []
        with patch.object(module, 'build_runtime', return_value=worker_runtime) as factory:
            module.register(host, config_loader=lambda path: {'enabled': True},
                            defer=deferred.append)
            deferred.pop()()
            self.assertEqual(events, ['enabled'])
            factory.assert_called_once()


class BoundResolverTests(unittest.TestCase):
    def test_refreshes_mappings_but_rejects_changed_artist_binding(self):
        from nuke_time_calc.runtime import BoundResolver
        config = dict(enabled=True, server='https://shotbox.test', artist_id=1,
                      device_id='device-a', token_file='/private/token',
                      outbox_dir='/private/outbox', mappings=[])
        current = dict(config)
        resolve = BoundResolver(config, None, loader=lambda path: current)
        self.assertIsNone(resolve('/shots/a/comp.nk'))
        current['mappings'] = [dict(job_id=2, shot_id=3, base_path='/shots/a')]
        self.assertEqual(resolve('/shots/a/comp.nk')['shot_id'], 3)
        current['artist_id'] = 9
        with self.assertRaises(ValueError):
            resolve('/shots/a/comp.nk')


    def test_direct_reopen_symlink_cannot_charge_the_wrong_shot(self):
        import os
        import tempfile
        from pathlib import Path
        from nuke_time_calc.runtime import BoundResolver
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder:
            root = Path(folder)
            a = root / 'a'
            b = root / 'b'
            a.mkdir()
            b.mkdir()
            (b / 'comp.nk').write_text('')
            (a / 'linked.nk').symlink_to(b / 'comp.nk')
            config = dict(enabled=True, mappings=[dict(job_id=2, shot_id=3, base_path=str(a))])
            resolve = BoundResolver(config, None, loader=lambda path: config)
            self.assertIsNone(resolve(str(a / 'linked.nk')))


class FactoryTests(unittest.TestCase):
    def test_real_collector_persists_exact_batch_through_runtime_sink(self):
        import os
        import tempfile
        from pathlib import Path
        from test_adapter import FakeApp, Timer
        from test_core import Clock
        from nuke_time_calc.managed_setup import save_config
        from nuke_time_calc.runtime import build_runtime
        from nuke_time_calc.nuke_adapter import install
        from nuke_time_calc.outbox import Outbox
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder:
            root = Path(folder)
            config = dict(enabled=True, server='https://shotbox.test', artist_id=1,
                device_id='f2da093e-3a39-4f4d-8b04-c5f3164d9568',
                token_file=str(root / 'credential'), outbox_dir=str(root / 'outbox'),
                mappings=[dict(job_id=2, shot_id=3, base_path=str(root / 'shots'))])
            path = root / 'config.json'
            save_config(config, path)
            nuke = FakeNuke()
            nuke.root = lambda: SimpleNamespace(name=lambda: str(root / 'shots' / 'comp.nk'))
            clock = Clock()
            qt = SimpleNamespace(QTimer=Timer, Qt=SimpleNamespace(ApplicationActive=1))
            worker = SimpleNamespace(start=lambda: True, stop=lambda timeout=0: True,
                                     status=lambda: {'code': 'idle'})
            runtime = build_runtime(config, nuke, path,
                uploader_factory=lambda *a, **kw: worker,
                installer=lambda **kw: install(**kw, qt_core=qt, app=FakeApp(),
                    monotonic=clock.monotonic, utc_now=clock.utc))
            runtime.enable()
            nuke.fire('KnobChanged')
            clock.advance(3)
            nuke.fire('OnScriptSave')
            queue = Outbox(config['outbox_dir'], server=config['server'],
                           artist_id=1, device_id=config['device_id'])
            self.assertEqual(queue.pending()[-1]['comp_microseconds'], 3000000)
            self.assertEqual(queue.pending()[-1]['shot_id'], 3)
            self.assertEqual(runtime.status()['outbox']['pending_records'], 1)
            runtime.disable()


if __name__ == '__main__':
    unittest.main()
