"""Offline Qt coverage: private state is isolated; no live Nuke installation."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QApplication, QLineEdit
from app_cache import cache_directory
from plugins.nuke.nuke_time_calc import managed_setup


class NukeTimeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=cache_directory('temp'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'state' / 'config.json'
        self.calls = []
        from plugins.nuke.nuke_time_calc import setup_defaults
        self.defaults_patch = mock.patch.object(setup_defaults, 'DEFAULTS_PATH', self.root / 'absent-defaults.json')
        self.defaults_patch.start()
        self.addCleanup(self.defaults_patch.stop)

    def widget(self):
        from nuke_time_settings import NukeTimeSettingsWidget
        widget = NukeTimeSettingsWidget(
            config_path=self.config,
            install_callback=lambda **kw: self.calls.append(('install', kw)),
            uninstall_callback=lambda **kw: self.calls.append(('uninstall', kw)))
        self.addCleanup(widget.close)
        return widget

    def fill(self, widget):
        for field, value in {
            'server_edit': 'https://shotbox.example', 'artist_edit': '7',
            'device_edit': 'b97b4ee3-33ba-4594-9e80-abba065cc482',
            'credential_edit': str(self.root / 'credential'),
            'outbox_edit': str(self.root / 'queue'),
        }.items():
            getattr(widget, field).setText(value)

    def test_new_setup_enabled_by_default_but_inert_without_private_credential_and_save(self):
        widget = self.widget()
        self.assertTrue(widget.enabled_check.isChecked())
        widget.update_context(server_url='https://shotbox.example/api/', artist_id=7)
        widget.defaults_button.click()
        self.assertTrue(widget.enabled_check.isChecked())
        self.assertIn('Enabled by default', widget.status_label.text())
        self.assertIn('credential', widget.status_label.text())
        self.assertIn('save', widget.status_label.text().lower())
        self.assertFalse(self.config.parent.exists())
        self.assertEqual(self.calls, [])
        widget.save_button.click()
        self.assertFalse(self.config.exists())
        self.assertIn('private credential', widget.status_label.text())

    def test_new_setup_explicit_uncheck_survives_context_and_use_defaults(self):
        widget = self.widget()
        self.assertTrue(widget.enabled_check.isChecked())
        widget.enabled_check.setChecked(False)
        widget.update_context(server_url='https://shotbox.example/api/', artist_id=7)
        widget.defaults_button.click()
        self.assertFalse(widget.enabled_check.isChecked())
        self.assertIn('Disabled in this form', widget.status_label.text())
        self.assertFalse(self.config.exists())
        widget.save_button.click()
        self.assertFalse(managed_setup.load_config(self.config)['enabled'])
        self.assertFalse(self.widget().enabled_check.isChecked())

    def test_lan_defaults_use_settings_origin_warn_once_and_save_explicit_policy(self):
        import json
        from plugins.nuke.nuke_time_calc import setup_defaults
        defaults = self.root / 'defaults.json'
        defaults.write_text(json.dumps({'server': 'http://192.168.10.88:8000',
                                        'allow_lan_http': True}))
        self.config.parent.mkdir(mode=0o700)
        self.config.write_text('{"enabled": false}')
        self.config.chmod(0o600)
        with mock.patch.object(setup_defaults, 'DEFAULTS_PATH', defaults):
            widget = self.widget()
            self.assertEqual(widget.server_edit.text(), '')  # Wait for normal Settings origin.
            widget.update_context(server_url='http://10.2.3.4:8000/api/', artist_id=7)
            self.assertEqual(widget.server_edit.text(), 'http://10.2.3.4:8000')
            widget.defaults_button.click()
            warning = 'HTTP LAN uploads are unencrypted'
            self.assertEqual(widget.lan_warning_label.text().count(warning), 1)
            from PyQt6.QtWidgets import QLabel
            self.assertIn('Shotbox server origin', [label.text() for label in widget.findChildren(QLabel)])
            self.assertNotIn('HTTPS', widget.status_label.text())
            widget.enabled_check.setChecked(False)  # Explicit opt-out remains saveable.
            widget.save_button.click()
            saved = managed_setup.load_config(self.config)
            self.assertTrue(saved['allow_lan_http'])
            self.assertEqual(saved['server'], 'http://10.2.3.4:8000')
            self.assertFalse(saved['enabled'])
            widget.server_edit.setText('http://8.8.8.8:8000')
            widget.outbox_edit.setText(str(self.root / 'public-queue'))
            widget.save_button.click()
            self.assertEqual(managed_setup.load_config(self.config), saved)
            widget.server_edit.setText('http://10.9.9.9:8000')
            widget.save_button.click()
            self.assertEqual(managed_setup.load_config(self.config), saved)
            reopened = self.widget()
            reopened.update_context(server_url='http://10.2.3.4:8000/api/', artist_id=7)
            self.assertEqual(reopened.lan_warning_label.text().count(warning), 1)

    def test_manual_private_http_without_deployment_policy_cannot_save(self):
        widget = self.widget()
        self.fill(widget)
        widget.update_context(server_url='http://192.168.10.88:8000/api/', artist_id=7)
        widget.server_edit.setText('http://192.168.10.88:8000')
        widget.save_button.click()
        self.assertFalse(self.config.exists())
        self.assertIn('explicit deployment opt-in', widget.status_label.text())

    def test_existing_disabled_only_config_is_unconfigured_without_reading_credentials(self):
        import json
        from uuid import UUID
        self.config.parent.mkdir(mode=0o700)
        self.config.write_text(json.dumps({'enabled': False}))
        self.config.chmod(0o600)
        before = self.config.read_bytes()
        widget = self.widget()
        widget.update_context(server_url='https://shotbox.example/api/', artist_id=7)
        device = widget.device_edit.text()
        self.assertTrue(device, 'unconfigured existing file needs a device UUID')
        self.assertEqual(str(UUID(device)), device)
        self.assertEqual(widget.artist_edit.text(), '7')
        self.assertEqual(widget.credential_edit.text(), str(self.config.parent / 'credentials' / '7' / (device + '.token')))
        self.assertEqual(widget.outbox_edit.text(), str(self.config.parent / 'outbox' / '7' / device))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(widget.enabled_check.isChecked())
        widget.defaults_button.click()
        self.assertEqual(widget.device_edit.text(), device)
        widget.save_button.click()
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn('private credential', widget.status_label.text())
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        self.assertEqual(self.widget().device_edit.text(), device)

    def test_new_profile_defaults_autofill_linked_artist_without_writes_or_activation(self):
        from uuid import UUID
        widget = self.widget()
        widget.update_context(server_url='https://shotbox.example/api/', artist_id=7,
                              users=[{'id': 7, 'is_active': True}])
        device = widget.device_edit.text()
        self.assertTrue(device, 'unconfigured existing file needs a device UUID')
        self.assertEqual(str(UUID(device)), device)
        self.assertEqual(widget.server_edit.text(), 'https://shotbox.example')
        self.assertEqual(widget.artist_edit.text(), '7')
        self.assertEqual(widget.credential_edit.text(), str(self.config.parent / 'credentials' / '7' / (device + '.token')))
        self.assertEqual(widget.outbox_edit.text(), str(self.config.parent / 'outbox' / '7' / device))
        self.assertTrue(widget.enabled_check.isChecked())
        self.assertFalse(self.config.parent.exists())
        self.assertEqual(self.calls, [])
        widget.defaults_button.click()
        self.assertEqual(widget.device_edit.text(), device)
        self.assertIn('Defaults', widget.warning_label.text())
        self.assertIn('per-device', widget.warning_label.text())
        self.assertIn('timesheets', widget.warning_label.text())
        other = self.widget()
        self.assertNotEqual(other.device_edit.text(), device)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        reopened = self.widget()
        self.assertEqual(reopened.device_edit.text(), device)
        self.assertEqual(reopened.artist_edit.text(), '7')

    def test_defaults_explain_missing_endpoint_and_never_replace_edited_or_saved_bindings(self):
        from plugins.nuke.nuke_time_calc import setup_defaults
        with mock.patch.object(setup_defaults, 'deployment_origin', return_value=None):
            widget = self.widget()
            widget.update_context(server_url='http://shotbox.example', artist_id=None)
        self.assertEqual(widget.server_edit.text(), '')
        self.assertIn('Shotbox server origin', widget.status_label.text())
        self.assertIn('linked artist', widget.status_label.text())
        self.fill(widget)
        widget.server_edit.clear()  # Deliberately cleared, not an invitation to rebind.
        widget.update_context(server_url='https://replacement.example', artist_id=8)
        widget.defaults_button.click()
        self.assertEqual(widget.server_edit.text(), '')
        self.assertEqual(widget.artist_edit.text(), '7')
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        saved = managed_setup.load_config(self.config)
        saved['enabled'] = True
        managed_setup.save_config(saved, self.config)
        reopened = self.widget()
        before = [(key, field.text()) for field, key in reopened._fields()]
        reopened.update_context(server_url='https://replacement.example', artist_id=8)
        reopened.defaults_button.click()
        self.assertEqual([(key, field.text()) for field, key in reopened._fields()], before)
        self.assertTrue(reopened.enabled_check.isChecked())
        self.assertEqual(managed_setup.load_config(self.config), saved)

    def test_unsupported_platform_is_visible_and_cannot_enable_even_programmatically(self):
        import nuke_time_settings
        with mock.patch.object(nuke_time_settings, 'POSIX_STORAGE_SUPPORTED', False):
            widget = self.widget()
            self.assertFalse(widget.enabled_check.isEnabled())
            self.assertFalse(widget.enabled_check.isChecked())
            self.assertIn('unsupported', widget.storage_label.text())
            self.assertIn('Windows', widget.storage_label.text())
            self.fill(widget)
            widget.enabled_check.setChecked(True)
            widget.save_button.click()
        self.assertFalse(self.config.exists())
        self.assertIn('unsupported', widget.status_label.text())

    def test_explicit_save_roundtrips_and_reopening_preserves_config(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        saved = managed_setup.load_config(self.config)
        self.assertEqual(saved['artist_id'], 7)
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['mappings'], [])
        again = self.widget()
        self.assertEqual(again.server_edit.text(), 'https://shotbox.example')
        self.assertEqual(again.artist_edit.text(), '7')
        self.assertEqual(again.credential_edit.text(), str(self.root / 'credential'))
        self.assertFalse(again.enabled_check.isChecked())
        again.update_context(server_url='https://replacement.example', artist_id=8)
        again.defaults_button.click()
        self.assertFalse(again.enabled_check.isChecked())
        self.assertEqual(managed_setup.load_config(self.config), saved)
        self.assertIn('Saved', widget.status_label.text())
        self.assertEqual(self.calls, [])

    def test_enable_requires_private_existing_credential_without_reading_it(self):
        widget = self.widget()
        self.fill(widget)
        self.assertTrue(widget.enabled_check.isChecked())
        widget.save_button.click()
        self.assertFalse(self.config.exists())
        credential = self.root / 'credential'
        credential.write_text('never display this secret')
        credential.chmod(0o600)
        original = Path.read_text
        def protected_read(path, *args, **kwargs):
            self.assertNotEqual(path, credential, 'widget must never read credential')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'read_text', protected_read):
            widget.save_button.click()
        self.assertTrue(managed_setup.load_config(self.config)['enabled'])
        self.assertNotIn('never display', widget.status_label.text())
        self.assertEqual(self.calls, [])

    def test_binding_switch_requires_disabled_saved_state_and_fresh_queue(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        baseline = managed_setup.load_config(self.config)
        baseline['enabled'] = True
        baseline['mappings'] = [{'job_id': 1, 'shot_id': 2, 'base_path': '/show/shot'}]
        managed_setup.save_config(baseline, self.config)
        widget.artist_edit.setText('8')
        widget.enabled_check.setChecked(False)
        widget.outbox_edit.setText(str(self.root / 'new-queue'))
        widget.save_button.click()
        self.assertEqual(managed_setup.load_config(self.config), baseline)
        self.assertIn('Disable', widget.status_label.text())
        baseline['enabled'] = False
        managed_setup.save_config(baseline, self.config)
        widget.outbox_edit.setText(baseline['outbox_dir'])
        widget.save_button.click()
        self.assertEqual(managed_setup.load_config(self.config), baseline)
        new_queue = self.root / 'new-queue'
        new_queue.mkdir()
        (new_queue / 'pending.json').write_text('preserve pending')
        widget.outbox_edit.setText(str(new_queue))
        widget.save_button.click()
        self.assertEqual(managed_setup.load_config(self.config), baseline)
        (new_queue / 'pending.json').unlink()
        widget.save_button.click()
        saved = managed_setup.load_config(self.config)
        self.assertEqual(saved['artist_id'], 8)
        self.assertEqual(saved['mappings'], [])

    def test_explicit_install_and_uninstall_preserve_state_and_ignore_unsaved_fields(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        before = self.config.read_bytes()
        widget.server_edit.setText('https://unsaved.example')
        widget.install_button.click()
        widget.uninstall_button.click()
        self.assertEqual(self.calls, [
            ('install', {'config_path': self.config}),
            ('uninstall', {'config_path': self.config, 'remove_config': False})])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn('new Nuke session', widget.status_label.text())

    def test_browse_paths_updates_only_fields_and_cancellation_keeps_values(self):
        widget = self.widget()
        from nuke_time_settings import QFileDialog
        with mock.patch.object(QFileDialog, 'getOpenFileName', return_value=(str(self.root / 'credential'), '')):
            widget.credential_browse_button.click()
        with mock.patch.object(QFileDialog, 'getExistingDirectory', return_value=str(self.root / 'queue')):
            widget.outbox_browse_button.click()
        self.assertEqual(widget.credential_edit.text(), str(self.root / 'credential'))
        self.assertEqual(widget.outbox_edit.text(), str(self.root / 'queue'))
        with mock.patch.object(QFileDialog, 'getOpenFileName', return_value=('', '')):
            widget.credential_browse_button.click()
        with mock.patch.object(QFileDialog, 'getExistingDirectory', return_value=''):
            widget.outbox_browse_button.click()
        self.assertEqual(widget.credential_edit.text(), str(self.root / 'credential'))
        self.assertEqual(widget.outbox_edit.text(), str(self.root / 'queue'))
        self.assertFalse(self.config.exists())

    def test_changed_outbox_cannot_adopt_existing_pending_queue(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        baseline = managed_setup.load_config(self.config)
        foreign_queue = self.root / 'foreign-queue'
        foreign_queue.mkdir()
        pending = foreign_queue / 'pending.json'
        pending.write_text('do not adopt or delete')
        widget.outbox_edit.setText(str(foreign_queue))
        widget.save_button.click()
        self.assertEqual(managed_setup.load_config(self.config), baseline)
        self.assertEqual(pending.read_text(), 'do not adopt or delete')
        self.assertIn('empty', widget.status_label.text())

    def test_invalid_values_never_replace_saved_config(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        original = self.config.read_bytes()
        invalid = (
            ('server_edit', 'http://shotbox.example'),
            ('server_edit', 'https://user:secret@shotbox.example'),
            ('server_edit', 'https://shotbox.example/api'),
            ('artist_edit', '0'), ('artist_edit', '1.5'),
            ('device_edit', 'B97B4EE3-33BA-4594-9E80-ABBA065CC482'),
            ('credential_edit', 'relative/credential'),
            ('outbox_edit', 'relative/queue'),
        )
        for name, value in invalid:
            with self.subTest(name=name, value=value):
                self.fill(widget)
                getattr(widget, name).setText(value)
                widget.save_button.click()
                self.assertEqual(self.config.read_bytes(), original)
                self.assertNotIn(value, widget.status_label.text())

    def test_save_rereads_mappings_added_after_widget_opened(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        widget.save_button.click()
        config = managed_setup.load_config(self.config)
        config['mappings'] = [{'job_id': 1, 'shot_id': 2, 'base_path': '/show/shot'}]
        managed_setup.save_config(config, self.config)
        widget.save_button.click()
        self.assertEqual(managed_setup.load_config(self.config), config)

    def test_malformed_config_is_not_overwritten_or_exposed(self):
        self.config.parent.mkdir()
        self.config.write_text('{private error content')
        self.config.chmod(0o600)
        before = self.config.read_bytes()
        widget = self.widget()
        self.assertFalse(widget.save_button.isEnabled())
        self.assertIn('Cannot load', widget.status_label.text())
        self.assertNotIn('private error content', widget.status_label.text())
        self.fill(widget)
        widget.save_button.click()
        self.assertEqual(self.config.read_bytes(), before)

    def test_callback_and_save_errors_are_quiet_and_redacted(self):
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        secret = 'SECRET-NOT-FOR-UI'
        with mock.patch.object(managed_setup, 'save_config', side_effect=OSError(secret)):
            widget.save_button.click()
        self.assertIn('Not saved', widget.status_label.text())
        self.assertNotIn(secret, widget.status_label.text())
        for button, attribute in ((widget.install_button, '_install'),
                                  (widget.uninstall_button, '_uninstall')):
            with mock.patch.object(widget, attribute, side_effect=OSError(secret)):
                button.click()
            self.assertIn('failed', widget.status_label.text())
            self.assertNotIn(secret, widget.status_label.text())
            self.assertTrue(button.isEnabled())
        self.assertFalse(self.config.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX mode check')
    def test_world_readable_credentials_cannot_enable_reporting(self):
        widget = self.widget()
        self.fill(widget)
        credential = self.root / 'credential'
        credential.write_text('private-secret')
        credential.chmod(0o644)
        widget.enabled_check.setChecked(True)
        widget.save_button.click()
        self.assertFalse(self.config.exists())
        self.assertIn('private credential', widget.status_label.text())

    def test_wheel_does_not_edit_fields_or_enable_reporting(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent
        widget = self.widget()
        self.fill(widget)
        widget.enabled_check.setChecked(False)
        for field, _ in widget._fields():
            before = field.text()
            field.setFocus()
            event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(),
                                QPoint(0, 120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(field, event)
            self.assertEqual(field.text(), before)
        self.assertFalse(widget.enabled_check.isChecked())
        self.assertFalse(self.config.exists())

    def test_render_and_edit_are_inert_until_explicit_save(self):
        widget = self.widget()
        self.assertTrue(widget.enabled_check.isChecked())
        self.fill(widget)
        widget.enabled_check.setChecked(True)
        self.app.processEvents()
        self.assertFalse(self.config.exists())
        self.assertEqual(self.calls, [])
        self.assertEqual(len(widget.findChildren(QLineEdit)), 5)
        self.assertIn('not labour', widget.warning_label.text())
        self.assertIn('new Nuke session', widget.warning_label.text())
        self.assertIn('diagnostics', widget.warning_label.text())
        self.assertTrue(hasattr(self.app, '_input_wheel_guard'))
