"""Invoke registered menu callbacks; fake host, real runtime lifecycle."""
import unittest
from types import SimpleNamespace

from test_local_test import Host
from nuke_time_calc.runtime import Runtime, register


class MenuFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.dialogs = []
        self.host.message = self.dialogs.append
        self.deferred = []
        self.config = {'enabled': False}
        self.context = None
        self.worker = SimpleNamespace(start=lambda: True, stop=lambda timeout=0: True,
                                      status=lambda: {'running': True})
        self.adapter = SimpleNamespace(installed=True, uninstall=lambda: None,
            status=lambda: {'installed': True, 'running': True})
        self.runtime = Runtime(nuke=self.host, identity='device-test',
            resolve=lambda path: self.context, sink=lambda batch: None,
            uploader=self.worker, installer=lambda **kwargs: self.adapter)
        self.controller = register(self.host,
            config_loader=lambda path: dict(self.config),
            runtime_factory=lambda *args: self.runtime, defer=self.deferred.append)
        self.menu = self.host.main.findItem('shotbox').findItem('nuke_time_calc')
        self.addCleanup(self.runtime.disable)

    def invoke(self, label):
        self.menu.findItem(label)()
        self.assertTrue(self.dialogs, 'manual menu action must show a Nuke dialog')
        return self.dialogs[-1]

    def test_deferred_startup_and_internal_lifecycle_never_show_dialogs(self):
        self.config['enabled'] = True
        self.deferred.pop()()
        self.assertTrue(self.runtime.enabled)
        self.controller.disable()
        self.controller.enable()
        self.assertEqual(self.dialogs, [])
        self.controller.disable()
        def fail(path):
            raise ValueError('secret-startup-payload')
        self.controller.config_loader = fail
        self.controller.cancelled = False
        self.controller.deferred_enable()
        self.assertEqual(self.dialogs, [])
        self.assertNotIn('secret-startup-payload', ''.join(self.host.messages))

    def test_host_without_message_falls_back_to_script_editor(self):
        del self.host.message
        self.menu.findItem('Reporting status')()
        self.assertIn('Shotbox automatic setup', self.host.messages[-1])
        self.assertEqual(self.dialogs, [])

    def test_internal_disable_reports_inactive_and_removes_callbacks(self):
        self.config['enabled'] = True
        self.controller.enable()
        self.controller.disable()
        text = self.invoke('Reporting status')
        self.assertIn('not active', text.lower())
        self.assertFalse(self.runtime.enabled)
        self.assertFalse(any(self.host.callbacks.values()))

    def test_factory_failure_shows_only_exception_class(self):
        self.config['enabled'] = True
        def fail(*args):
            raise ValueError('secret-config-payload')
        self.controller.runtime_factory = fail
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIn('ValueError', text)
        self.assertNotIn('secret-config-payload', text)
        self.assertNotIn('collecting', text.lower())

    def test_existing_diagnostic_adapter_is_reported_without_replacement(self):
        self.config['enabled'] = True
        self.host._nuke_time_calc_adapter = object()
        self.context = {'job_id': 2, 'shot_id': 3}
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIn('CollectorAlreadyInstalled', text)
        self.assertNotIn('collecting', text.lower())
        self.assertIsNone(self.runtime.adapter)

    def test_status_does_not_serialize_private_runtime_payloads(self):
        self.config['enabled'] = True
        self.controller.enable()
        self.runtime.outbox = SimpleNamespace(status=lambda: {'secret': 'outbox-secret'})
        self.worker.status = lambda: {'secret': 'upload-secret'}
        text = self.invoke('Reporting status')
        self.assertNotIn('outbox-secret', text)
        self.assertNotIn('upload-secret', text)

    def test_status_does_not_display_raw_error_payloads(self):
        self.config['enabled'] = True
        self.controller.enable()
        self.runtime.last_error = 'ValueError: secret-config-token'
        text = self.invoke('Reporting status')
        self.assertIn('ReportingError', text)
        self.assertNotIn('secret-config-token', text)

    def test_status_failure_is_visible_and_redacts_exception_text(self):
        self.config['enabled'] = True
        self.controller.enable()
        def fail():
            raise OSError('secret-status-payload')
        self.worker.status = fail
        text = self.invoke('Reporting status')
        self.assertIn('OSError', text)
        self.assertNotIn('Settings', text)
        self.assertNotIn('secret-status-payload', text)

    def test_missing_running_evidence_does_not_claim_collection(self):
        self.config['enabled'] = True
        self.context = {'job_id': 2, 'shot_id': 3}
        self.adapter.status = lambda: {'installed': True}
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertNotIn('collecting', text.lower())
        self.assertIn('not confirmed', text.lower())

    def test_failed_disable_reports_pending_cleanup_not_stopped(self):
        self.config['enabled'] = True
        self.controller.enable()
        remove = self.host.removeOnScriptLoad
        def fail(callback):
            raise OSError('private-disable-token')
        self.host.removeOnScriptLoad = fail
        self.controller.disable()
        text = self.invoke('Reporting status')
        self.assertIn('OSError', text)
        self.assertIn('cleanup', text.lower())
        self.assertNotIn('stopped', text.lower())
        self.assertNotIn('private-disable-token', text)
        self.assertTrue(self.runtime._hooks)
        self.host.removeOnScriptLoad = remove
        self.controller.disable()
        self.assertFalse(self.runtime._hooks)

    def test_internal_disable_cancels_deferred_start_without_modal(self):
        self.config['enabled'] = True
        self.controller.disable()
        text = self.invoke('Reporting status')
        self.assertIn('not active', text.lower())
        self.deferred.pop()()
        self.assertIsNone(self.controller.runtime)
        self.assertEqual(len(self.dialogs), 1)

    def test_paused_collector_is_not_reported_as_disabled_or_collecting(self):
        self.config['enabled'] = True
        self.context = {'job_id': 2, 'shot_id': 3}
        self.adapter.status = lambda: {'running': False, 'last_error': 'ValueError'}
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIn('paused', text.lower())
        self.assertIn('ValueError', text)
        self.assertNotIn('collecting', text.lower())
        self.assertNotIn('disabled', text.lower())

    def test_enable_failure_with_retained_runtime_is_not_reported_as_success(self):
        self.config['enabled'] = True
        self.context = {'job_id': 2, 'shot_id': 3}
        def fail(**kwargs):
            raise OSError('secret-token-and-config-payload')
        self.runtime.installer = fail
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIs(self.controller.runtime, self.runtime)
        self.assertIn('OSError', text)
        self.assertIn('error', text.lower())
        self.assertNotIn('Settings', text)
        self.assertNotIn('Save', text)
        self.assertNotIn('collecting', text.lower())
        self.assertNotIn('secret-token', text)
        self.assertNotIn('secret-token', ''.join(self.host.messages))

    def test_enable_reports_collecting_only_for_running_collector(self):
        self.config['enabled'] = True
        self.context = {'job_id': 2, 'shot_id': 3}
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIn('collecting', text.lower())
        self.assertTrue(self.runtime.adapter.installed)

    def test_enable_and_status_report_waiting_for_verified_shot(self):
        self.config['enabled'] = True
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIn('waiting for a verified shot', text.lower())
        self.assertNotIn('Settings', text)
        self.assertNotIn('Save', text)
        self.assertNotIn('collecting', text.lower())
        self.assertTrue(self.runtime.enabled)
        self.dialogs.clear()
        self.assertIn('waiting for a verified shot', self.invoke('Reporting status').lower())

    def test_internal_enable_respects_config_without_mutation(self):
        self.controller.enable()
        text = self.invoke('Reporting status')
        self.assertIn('not active', text.lower())
        self.assertNotIn('Settings', text)
        self.assertNotIn('Save', text)
        self.assertEqual(self.config, {'enabled': False})
        self.assertIsNone(self.controller.runtime)

    def test_registered_status_is_visible_and_explains_configuration(self):
        text = self.invoke('Reporting status')
        self.assertIn('not active', text.lower())
        self.assertNotIn('Settings', text)
        self.assertNotIn('Save', text)
        self.assertIsNone(self.controller.runtime)

    def test_registration_exposes_status_without_manual_reporting_toggles(self):
        self.assertIsNotNone(self.menu.findItem('Reporting status'))
        self.assertIsNone(self.menu.findItem('Enable configured reporting'))
        self.assertIsNone(self.menu.findItem('Disable reporting for this session'))
        self.assertIs(register(self.host), self.controller)

    def test_missing_configuration_points_to_automatic_setup(self):
        text = self.invoke('Reporting status')
        self.assertIn('Waiting for Shotbox automatic setup', text)
        self.assertIn('linked artist', text)


if __name__ == '__main__':
    unittest.main()
