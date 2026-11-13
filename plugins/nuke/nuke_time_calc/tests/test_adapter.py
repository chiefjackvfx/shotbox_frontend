"""Adapter contract tests using fake Nuke/Qt; NOT live Nuke validation."""
import importlib.util
import sys
import unittest
from types import SimpleNamespace
from test_core import Clock


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class Timer:
    def __init__(self):
        self.timeout = Signal()
        self.active = False
        self.interval = None

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class FakeApp:
    def __init__(self):
        self.applicationStateChanged = Signal()
        self.state = 1

    def applicationState(self):
        return self.state


class FakeNuke:
    def __init__(self):
        self.callbacks = {}
        self.is_executing = False
        for suffix in ("KnobChanged", "OnUserCreate", "OnDestroy", "OnScriptLoad",
                       "OnScriptSave", "OnScriptClose", "BeforeRender", "AfterRender"):
            setattr(self, "add" + suffix, self._adder(suffix))
            setattr(self, "remove" + suffix, self._remover(suffix))

    def _adder(self, name):
        def add(callback):
            self.callbacks.setdefault(name, []).append(callback)
        return add

    def _remover(self, name):
        def remove(callback):
            self.callbacks[name].remove(callback)
        return remove

    def fire(self, name):
        for callback in list(self.callbacks.get(name, [])):
            callback()

    def executing(self):
        return self.is_executing


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.records = []
        self.nuke = FakeNuke()
        self.app = FakeApp()
        self.qt = SimpleNamespace(QTimer=Timer, Qt=SimpleNamespace(ApplicationActive=1))
        self.context = {"project_id": "p1", "shot_id": "s1"}
        self.resolutions = 0

    def resolve(self):
        self.resolutions += 1
        return self.context

    def install(self, **kwargs):
        from nuke_time_calc.nuke_adapter import install
        options = dict(enabled=True, identity="artist-test", emit=self.records.append,
                       context_resolver=self.resolve, nuke_module=self.nuke,
                       qt_core=self.qt, app=self.app, monotonic=self.clock.monotonic,
                       utc_now=self.clock.utc)
        options.update(kwargs)
        return install(**options)

    def test_default_disabled_import_does_not_load_host_or_register(self):
        self.assertIsNotNone(importlib.util.find_spec("nuke_time_calc.nuke_adapter"),
                             "Adapter has not been implemented")
        from nuke_time_calc.nuke_adapter import install
        self.assertIsNone(install())
        self.assertIsNone(self.install(enabled=False))
        self.assertEqual(self.nuke.callbacks, {})
        self.assertNotIn("nuke", sys.modules)
        self.assertNotIn("PySide2", sys.modules)
        self.assertNotIn("PySide6", sys.modules)


    def test_explicit_install_collects_activity_on_save_without_per_knob_io(self):
        adapter = self.install()
        self.assertIsNotNone(adapter, "Enabled installation must create an adapter")
        self.nuke.fire("KnobChanged")
        self.clock.advance(4)
        self.nuke.fire("OnUserCreate")
        self.assertEqual(self.records, [])
        self.assertEqual(self.resolutions, 1)
        self.nuke.fire("OnScriptSave")
        self.assertEqual(self.records[-1]["comp_seconds"], 4)
        self.assertEqual(self.records[-1]["reason"], "script_save")
        self.assertTrue(adapter.collector.running)


    def test_timer_settles_idle_and_emits_only_at_checkpoint(self):
        adapter = self.install()
        self.assertTrue(hasattr(adapter, "timer"), "Timer wiring is missing")
        self.assertTrue(adapter.timer.active)
        self.assertEqual(adapter.timer.interval, 30000)
        self.nuke.fire("KnobChanged")
        self.clock.advance(30)
        adapter.timer.timeout.emit()
        self.assertEqual(self.records, [])
        self.clock.advance(30)
        adapter.timer.timeout.emit()
        self.assertEqual(len(self.records), 1, "Timer must emit a checkpoint")
        self.assertEqual(self.records[-1]["comp_seconds"], 20)
        self.assertEqual(self.records[-1]["reason"], "checkpoint")
        self.assertEqual(self.resolutions, 1, "No context resolution in timer hot path")


    def test_adapter_focus_uses_initial_state_and_settles_focus_loss(self):
        self.app.state = 0
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(3)
        self.app.applicationStateChanged.emit(1)
        self.nuke.fire("KnobChanged")
        self.clock.advance(4)
        self.app.applicationStateChanged.emit(0)
        self.clock.advance(2)
        self.nuke.fire("OnScriptSave")
        self.assertEqual(self.records[-1]["comp_seconds"], 4)


    def test_render_excludes_blocked_qt_interval_and_does_not_fake_activity(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(3)
        self.nuke.is_executing = True
        self.nuke.fire("BeforeRender")
        self.clock.advance(12)  # no Qt ticks while foreground render blocks
        self.nuke.fire("KnobChanged")
        self.nuke.is_executing = False
        self.nuke.fire("AfterRender")
        self.clock.advance(2)
        self.nuke.fire("OnScriptSave")
        self.assertEqual(self.records[-1]["comp_seconds"], 3)
        self.assertFalse(adapter.collector.rendering)


    def test_cancel_without_after_render_is_conservative_until_next_timer(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(3)
        self.nuke.is_executing = True
        self.nuke.fire("BeforeRender")
        self.clock.advance(5)
        adapter.timer.timeout.emit()
        self.assertTrue(adapter.collector.rendering)
        self.nuke.is_executing = False  # cancellation: no AfterRender callback
        self.clock.advance(4)
        self.nuke.fire("KnobChanged")  # ignored until render state reconciles
        adapter.timer.timeout.emit()
        self.assertFalse(adapter.collector.rendering)
        self.clock.advance(2)
        self.nuke.fire("KnobChanged")
        self.clock.advance(1)
        self.nuke.fire("OnScriptSave")
        self.assertEqual(self.records[-1]["comp_seconds"], 4)


    def test_script_close_flushes_and_load_starts_without_assumed_activity(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(4)
        self.nuke.fire("OnScriptClose")
        self.assertFalse(adapter.collector.running)
        self.assertEqual(self.records[-1]["comp_seconds"], 4)
        self.clock.advance(8)
        self.nuke.fire("KnobChanged")
        self.nuke.fire("OnScriptLoad")
        self.clock.advance(2)
        self.nuke.fire("OnDestroy")
        self.clock.advance(3)
        self.nuke.fire("OnScriptSave")
        self.assertEqual(self.records[-1]["comp_seconds"], 3)


    def test_context_resolver_changes_flush_old_shot_on_save(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(4)
        self.context = {"project_id": "p1", "shot_id": "s2"}
        self.nuke.fire("OnScriptSave")
        self.assertEqual(adapter.collector.context["shot_id"], "s2")
        self.assertEqual(self.records[0]["context"]["shot_id"], "s1")
        self.assertEqual(self.records[0]["comp_seconds"], 4)
        self.assertEqual(self.records[0]["reason"], "context_switch")
        self.assertEqual(self.records[1]["context"]["shot_id"], "s2")
        self.assertEqual(self.records[1]["comp_seconds"], 0)


    def test_unresolved_load_stops_old_context_and_can_recover_explicitly(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(3)
        self.context = None
        self.nuke.fire("OnScriptLoad")
        self.assertFalse(adapter.collector.running)
        self.assertEqual(self.records[0]["comp_seconds"], 3)
        self.clock.advance(4)
        self.nuke.fire("KnobChanged")
        self.context = {"project_id": "p1", "shot_id": "s2"}
        self.assertTrue(adapter.refresh_context())
        self.assertTrue(adapter.collector.running)
        self.clock.advance(2)
        self.nuke.fire("OnScriptSave")
        self.assertEqual(self.records[-1]["comp_seconds"], 0)
        self.assertEqual(self.records[-1]["context"]["shot_id"], "s2")


    def test_uninstall_flushes_once_removes_hooks_and_exposes_plain_status(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(4)
        self.assertTrue(hasattr(adapter, "uninstall"), "Reversible uninstall is missing")
        adapter.uninstall()
        adapter.uninstall()
        self.assertEqual(len(self.records), 1)
        self.assertEqual(self.records[0]["comp_seconds"], 4)
        self.assertEqual(self.records[0]["reason"], "uninstall")
        self.assertFalse(adapter.timer.active)
        self.assertFalse(any(self.nuke.callbacks.values()))
        self.assertEqual(self.app.applicationStateChanged.callbacks, [])
        status = adapter.status()
        self.assertFalse(status["installed"])
        self.assertFalse(status["running"])
        self.assertEqual(status["pending_count"], 0)
        self.assertIn("session_id", status)
        import json
        json.dumps(status, allow_nan=False)


    def test_uninstall_cleanup_failure_still_stops_collector_and_retries_only_failed_target(self):
        for target in ('timer_stop', 'tick_disconnect', 'focus_disconnect', 'host_remove'):
            with self.subTest(target=target):
                self.setUp()
                adapter = self.install()
                self.nuke.fire('KnobChanged')
                self.clock.advance(2)
                owner, name = {
                    'timer_stop': (adapter.timer, 'stop'),
                    'tick_disconnect': (adapter.timer.timeout, 'disconnect'),
                    'focus_disconnect': (self.app.applicationStateChanged, 'disconnect'),
                    'host_remove': (self.nuke, 'removeAfterRender'),
                }[target]
                original = getattr(owner, name)
                def fail(*args):
                    raise OSError('resource unavailable')
                setattr(owner, name, fail)
                with self.assertRaises(OSError):
                    adapter.uninstall()
                self.assertFalse(adapter.installed)
                self.assertFalse(adapter.collector.running)
                self.assertEqual(adapter.status()['last_error'], 'OSError')
                self.assertEqual(self.nuke.callbacks['KnobChanged'], [])
                if target != 'focus_disconnect':
                    self.assertEqual(self.app.applicationStateChanged.callbacks, [])
                if target != 'timer_stop':
                    self.assertFalse(adapter.timer.active)
                self.assertIs(getattr(self.nuke, '_nuke_time_calc_adapter', None), adapter)
                with self.assertRaises(RuntimeError):
                    self.install()
                setattr(owner, name, original)
                adapter.uninstall()
                self.assertFalse(adapter.timer.active)
                self.assertEqual(adapter.timer.timeout.callbacks, [])
                self.assertEqual(self.app.applicationStateChanged.callbacks, [])
                self.assertFalse(any(self.nuke.callbacks.values()))
                self.assertIsNone(getattr(self.nuke, '_nuke_time_calc_adapter', None))

    def test_invalid_enable_configuration_does_not_register_anything(self):
        from nuke_time_calc.nuke_adapter import install
        with self.assertRaises(ValueError):
            try:
                install(enabled=True)
            except TypeError:
                self.fail("Enable must validate configuration before calling resolver")
        for options in ({"identity": ""}, {"emit": None}, {"context_resolver": None}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.install(**options)
        self.assertEqual(self.nuke.callbacks, {})
        self.assertEqual(self.app.applicationStateChanged.callbacks, [])


    def test_explicit_install_can_resolve_host_dependencies_lazily(self):
        from unittest.mock import patch
        from nuke_time_calc.nuke_adapter import install
        widgets = SimpleNamespace(QApplication=SimpleNamespace(instance=lambda: self.app))
        for binding in ("PySide2", "PySide6"):
            with self.subTest(binding=binding):
                modules = {"nuke": self.nuke, "PySide2": None, "PySide6": None}
                modules[binding] = SimpleNamespace(QtCore=self.qt, QtWidgets=widgets)
                with patch.dict(sys.modules, modules):
                    try:
                        adapter = install(enabled=True, identity="artist-test",
                                          context_resolver=self.resolve, emit=self.records.append,
                                          monotonic=self.clock.monotonic, utc_now=self.clock.utc)
                    except AttributeError:
                        self.fail("Enabled install must resolve Nuke and Qt dependencies")
                    self.nuke.fire("KnobChanged")
                    self.clock.advance(1)
                    adapter.uninstall()
                    self.assertEqual(self.records[-1]["comp_seconds"], 1)


    def test_duplicate_install_refused_until_uninstalled(self):
        adapter = self.install()
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(len(self.nuke.callbacks["KnobChanged"]), 1)
        adapter.uninstall()
        replacement = self.install()
        self.assertTrue(replacement.status()["installed"])
        replacement.uninstall()


    def test_resolver_exception_is_visible_and_cannot_escape_nuke_callback(self):
        adapter = self.install()
        self.nuke.fire("KnobChanged")
        self.clock.advance(2)
        def broken_resolver():
            raise RuntimeError("context unavailable")
        adapter.context_resolver = broken_resolver
        try:
            self.nuke.fire("OnScriptSave")
        except RuntimeError:
            self.fail("Adapter callbacks must fail closed without disrupting Nuke")
        self.assertFalse(adapter.collector.running)
        self.assertEqual(adapter.status()["last_error"], "RuntimeError")
        self.assertEqual(self.records[-1]["comp_seconds"], 2)


    def test_queue_backpressure_is_visible_without_callback_exception_or_data_loss(self):
        def failing(record):
            raise OSError("offline")
        adapter = self.install(emit=failing)
        for _ in range(128):
            self.nuke.fire("OnScriptSave")
        self.nuke.fire("KnobChanged")
        self.clock.advance(3)
        try:
            self.nuke.fire("OnScriptSave")
        except BufferError:
            self.fail("Backpressure must be surfaced in status, not through host callbacks")
        self.assertTrue(adapter.status()["blocked"])
        self.assertFalse(adapter.status()["running"])
        self.assertEqual(adapter.status()["pending_count"], 128)
        self.assertEqual(adapter.status()["unsealed_comp_seconds"], 3)
        adapter.uninstall()
        adapter.collector.emit = self.records.append
        adapter.collector.flush("recovery")
        self.assertEqual(len(self.records), 129)
        self.assertEqual(self.records[-1]["comp_seconds"], 3)


    def test_closed_script_timer_does_not_emit_phantom_intervals(self):
        adapter = self.install()
        self.nuke.fire("OnScriptClose")
        count = len(self.records)
        for _ in range(120):
            self.clock.advance(1)
            adapter.timer.timeout.emit()
        self.assertEqual(len(self.records), count)


    def test_unsupported_host_is_rejected_before_any_registration(self):
        del self.nuke.removeAfterRender
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.nuke.callbacks, {})
        self.assertEqual(self.app.applicationStateChanged.callbacks, [])


    def test_failed_qt_setup_rolls_back_partial_signals_and_timer_on_every_retry(self):
        timers = []
        class BrokenTimer(Timer):
            def __init__(self):
                super().__init__()
                timers.append(self)
            def start(self):
                super().start()
                raise RuntimeError('Qt timer start failed')

        def broken_constructor():
            raise RuntimeError('Qt timer construction failed')

        for factory in (broken_constructor, BrokenTimer):
            self.qt.QTimer = factory
            for retry in range(3):
                with self.subTest(factory=factory, retry=retry):
                    with self.assertRaisesRegex(RuntimeError, 'Qt timer'):
                        self.install()
                    self.assertEqual(self.app.applicationStateChanged.callbacks, [])
                    self.assertFalse(any(self.nuke.callbacks.values()))
                    self.assertIsNone(getattr(self.nuke, '_nuke_time_calc_adapter', None))
                    for timer in timers:
                        self.assertFalse(timer.active)
                        self.assertEqual(timer.timeout.callbacks, [])

    def test_partial_registration_failure_rolls_back_hooks(self):
        def fail(callback):
            raise RuntimeError("host registration failed")
        self.nuke.addAfterRender = fail
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertFalse(any(self.nuke.callbacks.values()))
        self.assertEqual(self.app.applicationStateChanged.callbacks, [])
        self.assertIsNone(getattr(self.nuke, "_nuke_time_calc_adapter", None))


    def test_uninstall_with_full_queue_preserves_pending_and_cannot_restart(self):
        def failing(record):
            raise OSError("offline")
        adapter = self.install(emit=failing)
        for _ in range(128):
            self.nuke.fire("OnScriptSave")
        self.nuke.fire("KnobChanged")
        self.clock.advance(3)
        try:
            adapter.uninstall()
        except BufferError:
            self.fail("Uninstall must retain backpressure in status without raising")
        self.assertFalse(adapter.status()["installed"])
        self.assertFalse(adapter.status()["running"])
        self.assertEqual(adapter.status()["pending_count"], 128)
        self.assertEqual(adapter.status()["unsealed_comp_seconds"], 3)
        self.assertFalse(adapter.refresh_context())


if __name__ == "__main__":
    unittest.main()
