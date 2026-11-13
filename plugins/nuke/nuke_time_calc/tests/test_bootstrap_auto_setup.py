import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from nuke_time_calc.runtime import Bootstrap


class AutomaticBootstrapTests(unittest.TestCase):
    def test_missing_setup_retries_when_shotbox_finishes_provisioning(self):
        config = {'enabled': False}
        runtime = Mock()
        runtime.enabled = False
        runtime.enable.side_effect = lambda: setattr(runtime, 'enabled', True)
        factory = Mock(return_value=runtime)
        controller = Bootstrap(SimpleNamespace(tprint=lambda message: None), None,
                               lambda path: dict(config), factory)
        controller.deferred_enable()
        self.assertIsNone(controller.runtime)
        config['enabled'] = True
        controller.deferred_enable()
        factory.assert_called_once()
        runtime.enable.assert_called_once()
        self.assertTrue(runtime.enabled)

    def test_real_registration_schedules_quiet_retry_until_config_is_ready(self):
        from unittest.mock import patch
        from nuke_time_calc.runtime import register
        from test_local_test import Host
        deferred = []
        class Timer:
            def __init__(self):
                self.active = False
                self.timeout = SimpleNamespace(connect=lambda callback: setattr(self, 'callback', callback))
            @staticmethod
            def singleShot(delay, callback):
                deferred.append(callback)
            def setInterval(self, interval):
                self.interval = interval
            def start(self):
                self.active = True
            def stop(self):
                self.active = False
        config = {'enabled': False}
        runtime = Mock()
        runtime.enabled = False
        runtime.enable.side_effect = lambda: setattr(runtime, 'enabled', True)
        host = Host()
        with patch('nuke_time_calc.local_test._qt_modules', return_value=(SimpleNamespace(QTimer=Timer), None, None)):
            controller = register(host, config_loader=lambda path: dict(config), runtime_factory=lambda *args: runtime)
        deferred.pop()()
        timer = getattr(controller, '_setup_timer', None)
        self.assertIsNotNone(timer, 'Missing setup must leave an automatic retry timer')
        self.assertTrue(timer.active)
        self.assertEqual(timer.interval, 30000)
        config['enabled'] = True
        timer.callback()
        runtime.enable.assert_called_once()
        self.assertFalse(timer.active)
