"""Offline setup intent; reporting still requires private credentials and Save."""
import os
from pathlib import Path
from uuid import uuid4

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from input_behaviour import install_input_wheel_guard
from plugins.nuke.nuke_time_calc import managed_setup, setup_defaults

POSIX_STORAGE_SUPPORTED = os.name == 'posix'


class NukeTimeSettingsWidget(QWidget):
    def __init__(self, parent=None, *, config_path=None,
                 install_callback=None, uninstall_callback=None):
        super().__init__(parent)
        install_input_wheel_guard(QApplication.instance())
        self.config_path = config_path
        self._install = install_callback or managed_setup.install
        self._uninstall = uninstall_callback or managed_setup.uninstall
        self._context = {'server_url': ''}
        self._default_device = None
        self._saved_profile = True
        self._edited_keys = set()
        self._applying_defaults = True
        layout = QVBoxLayout(self)
        self.warning_label = QLabel(
            'Activity estimates, not labour or billing records. Enabled by default for '
            'new setups; reporting requires private credentials and Save. Disable existing '
            'local diagnostics before enabling the live collector. Changes require a '
            'new Nuke session. Defaults are automatic for your linked artist and this '
            'device; no timesheets are required. Your operator supplies a private '
            'per-device credential file; no credentials are displayed here. '
            'Use defaults fills only untouched, empty fields; Save never installs startup.')
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        self.lan_warning_label = QLabel('')
        self.lan_warning_label.setWordWrap(True)
        layout.addWidget(self.lan_warning_label)
        self.enabled_check = QCheckBox('Enable Nuke time reporting')
        layout.addWidget(self.enabled_check)
        self.storage_label = QLabel('')
        self.storage_label.setWordWrap(True)
        if not POSIX_STORAGE_SUPPORTED:
            self.enabled_check.setEnabled(False)
            self.storage_label.setText('Reporting is unsupported on Windows: private POSIX storage is required. Settings may be saved disabled.')
        layout.addWidget(self.storage_label)
        form = QFormLayout()
        layout.addLayout(form)
        for name, title in (
            ('server', 'Shotbox server origin'), ('artist', 'Artist ID'),
            ('device', 'Device UUID'), ('credential', 'Credential file path'),
            ('outbox', 'Outbox folder'),
        ):
            field = QLineEdit()
            setattr(self, name + '_edit', field)
            if name in ('credential', 'outbox'):
                row = QHBoxLayout()
                row.addWidget(field)
                button = QPushButton('Browse…')
                setattr(self, name + '_browse_button', button)
                button.clicked.connect(lambda checked=False, name=name: self._browse(name))
                row.addWidget(button)
                form.addRow(title, row)
            else:
                form.addRow(title, field)
        self.defaults_button = QPushButton('Use defaults')
        layout.addWidget(self.defaults_button)
        self.defaults_button.clicked.connect(self._use_defaults)
        self.save_button = QPushButton('Save Nuke time settings')
        layout.addWidget(self.save_button)
        self.status_label = QLabel('Disabled — not yet configured.')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.save_button.clicked.connect(self._save)
        self.install_button = QPushButton('Install managed Nuke startup')
        self.uninstall_button = QPushButton('Uninstall managed Nuke startup')
        layout.addWidget(self.install_button)
        layout.addWidget(self.uninstall_button)
        self.install_button.clicked.connect(lambda: self._change_installation(True))
        self.uninstall_button.clicked.connect(lambda: self._change_installation(False))
        try:
            config = managed_setup.load_config(self.config_path)
        except Exception:
            self.status_label.setText('Cannot load Nuke time settings. Check the private configuration file.')
            self.save_button.setEnabled(False)
        else:
            path = Path(self.config_path if self.config_path is not None else managed_setup.default_config_path())
            self._saved_profile = path.exists() and (
                config['enabled'] or any(key in config for _, key in self._fields())
                or bool(config.get('mappings')))
            if not self._saved_profile:
                self._default_device = str(uuid4())
            self.enabled_check.setChecked(POSIX_STORAGE_SUPPORTED and (
                config['enabled'] if self._saved_profile else True))
            for field, key in self._fields():
                field.setText(str(config.get(key, '')))
            self.status_label.setText('Enabled in saved configuration.' if config['enabled'] else 'Disabled.')
            self._use_defaults()
        self._applying_defaults = False
        for field, key in self._fields():
            field.textChanged.connect(lambda _text, key=key: self._mark_edited(key))
        self.server_edit.textChanged.connect(self._update_lan_warning)
        self._update_lan_warning()

    def _mark_edited(self, key):
        if not self._applying_defaults:
            self._edited_keys.add(key)

    def update_context(self, *, server_url=None, artist_id=None, users=None):
        """Use an already-loaded desktop snapshot; never query users or rebind state."""
        self._context = dict(server_url=server_url, artist_id=artist_id, users=users)
        self._use_defaults()
        self._update_lan_warning()

    def _update_lan_warning(self):
        self.lan_warning_label.setText(
            'HTTP LAN uploads are unencrypted'
            if setup_defaults.lan_origin(self.server_edit.text()) else '')

    def _use_defaults(self):
        if self._saved_profile:
            return
        defaults = setup_defaults.suggest(
            **self._context, device_id=self._default_device, config_path=self.config_path)
        self._applying_defaults = True
        try:
            for field, key in self._fields():
                if not field.text() and key not in self._edited_keys and key in defaults:
                    field.setText(str(defaults[key]))
        finally:
            self._applying_defaults = False
        missing = []
        if not self.server_edit.text():
            missing.append('a Shotbox server origin needs setup by your operator')
        if not self.artist_edit.text():
            missing.append('select an active linked artist in Settings')
        intent = ('Enabled by default' if self.enabled_check.isChecked() else
                  'Disabled in this form')
        self.status_label.setText(
            intent + ' — ' + ('; '.join(missing) + '. ' if missing else 'Defaults ready. ')
            + 'Reporting requires an operator-issued private per-device credential and Save.')

    def _browse(self, name):
        if name == 'credential':
            value, _ = QFileDialog.getOpenFileName(self, 'Select operator-issued credential file')
        else:
            value = QFileDialog.getExistingDirectory(self, 'Select private outbox folder')
        if value:
            getattr(self, name + '_edit').setText(value)

    def _change_installation(self, installing):
        try:
            if installing:
                self._install(config_path=self.config_path)
            else:
                self._uninstall(config_path=self.config_path, remove_config=False)
        except Exception:
            self.status_label.setText('Startup change failed. Check Nuke startup permissions and the managed block.')
            return
        self.status_label.setText('Managed startup installed.' if installing else 'Managed startup removed; configuration, credentials and outbox retained.')
        self.status_label.setText(self.status_label.text() + ' Start a new Nuke session. Unsaved fields were not applied.')

    def _fields(self):
        return ((self.server_edit, 'server'), (self.artist_edit, 'artist_id'),
                (self.device_edit, 'device_id'), (self.credential_edit, 'token_file'),
                (self.outbox_edit, 'outbox_dir'))

    def _save(self):
        if self.enabled_check.isChecked() and not POSIX_STORAGE_SUPPORTED:
            self.status_label.setText('Not saved. Reporting is unsupported on this platform; private POSIX storage is required.')
            return
        try:
            current = managed_setup.load_config(self.config_path)
            config = dict(current, enabled=self.enabled_check.isChecked())
            for field, key in self._fields():
                value = field.text()
                if value:
                    config[key] = int(value) if key == 'artist_id' else value
                else:
                    config.pop(key, None)
            config.pop('allow_lan_http', None)
            server = config.get('server', '')
            if server.startswith('http:'):
                defaults = setup_defaults.suggest(**self._context)
                if not (defaults.get('allow_lan_http') is True
                        and defaults.get('server') == server):
                    self.status_label.setText('Not saved. HTTP requires the configured private LAN server and explicit deployment opt-in.')
                    return
                config['allow_lan_http'] = True
            binding_keys = ('server', 'artist_id', 'device_id', 'token_file', 'allow_lan_http')
            switching = any(
                current.get(key, False if key == 'allow_lan_http' else None)
                != config.get(key, False if key == 'allow_lan_http' else None)
                for key in binding_keys)
            if switching and any(key in current for key in binding_keys):
                if current['enabled']:
                    self.status_label.setText('Disable and save the existing configuration first; then change its binding.')
                    return
                if config.get('outbox_dir') == current.get('outbox_dir'):
                    self.status_label.setText('Not saved. A changed binding requires a different, empty outbox folder. Existing queues are preserved.')
                    return
                config['mappings'] = []
            if config.get('outbox_dir') != current.get('outbox_dir'):
                outbox = Path(config.get('outbox_dir', ''))
                if outbox.exists() and (not outbox.is_dir() or any(outbox.iterdir())):
                    self.status_label.setText('Not saved. Select a new or empty outbox folder; existing queues cannot be adopted.')
                    return
            if config['enabled']:
                credential = Path(config.get('token_file', ''))
                if (not credential.is_file() or credential.is_symlink()
                        or (os.name != 'nt' and credential.stat().st_mode & 0o077)):
                    self.status_label.setText('Not saved. Select an existing private credential file supplied by your operator.')
                    return
            managed_setup.save_config(config, self.config_path)
        except Exception:
            self.status_label.setText('Not saved. Check the Shotbox server origin, positive artist ID, canonical UUID and absolute paths.')
            return
        self.status_label.setText('Saved. Start a new Nuke session for changes to take effect.')
        self._saved_profile = True
