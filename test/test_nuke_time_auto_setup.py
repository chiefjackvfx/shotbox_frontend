"""Isolated, offline always-on provisioning contract tests (no Qt)."""
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.nuke.nuke_time_calc import managed_setup, setup_defaults


class AutoSetupTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.cache' / 'temp'
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'private' / 'config.json'
        self.nuke = self.root / 'nuke'
        self.calls = []
        patch = mock.patch.object(setup_defaults, 'DEFAULTS_PATH', self.root / 'absent')
        patch.start()
        self.addCleanup(patch.stop)

    def request(self, url, body, timeout):
        data = json.loads(body)
        self.calls.append((url, data, timeout))
        # Credentials must already be durable when the backend receives them.
        self.assertTrue(list(self.config.parent.glob('bindings/*/credential.token')))
        return 200, {}, json.dumps(dict(artist_id=data['artist_id'],
            device_id=data['device_id'], status='ready', expires_at='2099-01-01T00:00:00Z')).encode()

    def ensure(self, **kw):
        module = importlib.import_module('nuke_time_auto_setup')
        options = dict(server_url='https://shotbox.example/api/', artist_id=7,
                       config_path=self.config, nuke_dir=self.nuke, request=self.request)
        options.update(kw)
        return module.ensure_setup(**options)

    def test_lost_ack_can_finish_from_a_fresh_python_process(self):
        import subprocess
        def lost(*args):
            raise OSError('acknowledgement lost')
        self.assertEqual(self.ensure(request=lost)['status'], 'retry')
        token = next(self.config.parent.glob('bindings/*/credential.token')).read_bytes()
        script = '''
import json, sys
from pathlib import Path
import nuke_time_auto_setup
from plugins.nuke.nuke_time_calc import setup_defaults
config, nuke_dir = map(Path, sys.argv[1:])
setup_defaults.DEFAULTS_PATH = config.parent / 'absent-defaults'
def request(url, body, timeout):
    metadata = json.loads(next(config.parent.glob('bindings/*/provision.json')).read_text())
    payload = json.loads(body)
    assert payload['token'] == metadata['token']
    assert payload['device_id'] == metadata['device_id']
    return 200, {}, json.dumps(dict(artist_id=payload['artist_id'], device_id=payload['device_id'],
        status='ready', expires_at='2099-01-01T00:00:00Z')).encode()
result = nuke_time_auto_setup.ensure_setup(server_url='https://shotbox.example/api/', artist_id=7,
    config_path=config, nuke_dir=nuke_dir, request=request)
assert result['status'] == 'ready', result
print(result['status'])
'''
        process = subprocess.run([sys.executable, '-c', script, str(self.config), str(self.nuke)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), 'ready')
        self.assertEqual(next(self.config.parent.glob('bindings/*/credential.token')).read_bytes(), token)
        self.assertTrue(managed_setup.load_config(self.config)['enabled'])

    def test_duplicate_provision_keys_are_corruption_not_new_identity(self):
        self.ensure()
        metadata = next(self.config.parent.glob('bindings/*/provision.json'))
        data = metadata.read_text()
        damaged = data[:-1] + ', "artist_id": 7}'
        metadata.write_text(damaged)
        result = self.ensure()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(metadata.read_text(), damaged)
        self.assertEqual(len(self.calls), 1)

    def test_missing_current_binding_directory_does_not_recreate_identity(self):
        self.ensure()
        before = self.config.read_bytes()
        root = next(self.config.parent.glob('bindings/*'))
        preserved = self.config.parent / 'preserved-binding'
        root.rename(preserved)
        result = self.ensure()
        self.assertEqual(result['status'], 'error')
        self.assertFalse(root.exists())
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)

    def test_binding_directory_entries_are_synced_before_network(self):
        from unittest.mock import patch
        import stat
        synced = set()
        original = os.fsync
        def fsync(fd):
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                synced.add(info.st_ino)
            return original(fd)
        def request(url, body, timeout):
            root = next(self.config.parent.glob('bindings/*'))
            for directory in (self.config.parent, root.parent, root):
                self.assertIn(directory.stat().st_ino, synced)
            return self.request(url, body, timeout)
        with patch.object(os, 'fsync', fsync):
            result = self.ensure(request=request)
        self.assertEqual(result['status'], 'ready')

    def test_malformed_ack_matrix_cannot_activate(self):
        variants = [
            lambda data: dict(data, device_id='73028436-d805-45e7-9ffb-bd9b0e8b04b5'),
            lambda data: dict(data, artist_id=True),
            lambda data: dict(data, status='pending'),
            lambda data: dict(data, expires_at='2000-01-01T00:00:00Z'),
            lambda data: dict(data, expires_at='2099-01-01'),
            lambda data: dict(data, expires_at=None),
            lambda data: dict(data, token='backend-must-never-echo'),
            lambda data: [],
        ]
        for index, change in enumerate(variants):
            def bad(url, body, timeout):
                payload = json.loads(body)
                ack = dict(artist_id=payload['artist_id'], device_id=payload['device_id'],
                           status='ready', expires_at='2099-01-01T00:00:00Z')
                return 200, {}, json.dumps(change(ack)).encode()
            with self.subTest(index=index):
                result = self.ensure(artist_id=index + 10, request=bad)
                self.assertEqual(result['status'], 'error')
                self.assertFalse(self.config.exists())

    def test_oversize_ack_and_duplicate_keys_are_refused(self):
        def oversized(url, body, timeout):
            payload = json.loads(body)
            return 200, {}, b' ' * 65537 + json.dumps(dict(artist_id=7,
                device_id=payload['device_id'], status='ready', expires_at='2099-01-01T00:00:00Z')).encode()
        for request in [oversized, lambda *args: (200, {}, b'{"artist_id":7,"artist_id":7}')]:
            result = self.ensure(request=request)
            self.assertEqual(result['code'], 'invalid_ack')
            self.assertFalse(self.config.exists())

    def test_explicit_lan_policy_uses_normal_server_and_never_invents_https(self):
        defaults = self.root / 'deployment.json'
        defaults.write_text(json.dumps(dict(server='http://192.168.1.4:8000', allow_lan_http=True)))
        with mock.patch.object(setup_defaults, 'DEFAULTS_PATH', defaults):
            result = self.ensure(server_url='http://192.168.1.8:8000/api/')
        self.assertEqual(result['status'], 'ready')
        saved = managed_setup.load_config(self.config)
        self.assertEqual(saved['server'], 'http://192.168.1.8:8000')
        self.assertTrue(saved['allow_lan_http'])

    def test_existing_public_namespace_fails_before_switch_writes(self):
        self.ensure()
        original = self.config.read_bytes()
        self.config.parent.chmod(0o755)
        result = self.ensure(artist_id=8)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.config.parent.stat().st_mode & 0o777, 0o755)

    def test_missing_provision_file_never_regenerates_credential(self):
        self.ensure()
        metadata = next(self.config.parent.glob('bindings/*/provision.json'))
        token = next(self.config.parent.glob('bindings/*/credential.token'))
        original = token.read_bytes()
        metadata.unlink()
        result = self.ensure()
        self.assertEqual(result['status'], 'error')
        self.assertFalse(metadata.exists())
        self.assertEqual(token.read_bytes(), original)

    def test_startup_install_is_reversible_and_preserves_other_content(self):
        self.nuke.mkdir()
        menu = self.nuke / 'menu.py'
        original = b'# Studio startup\nimport site\n'
        menu.write_bytes(original)
        self.ensure()
        installed = menu.read_bytes()
        self.ensure()
        self.assertEqual(menu.read_bytes(), installed)
        self.assertTrue(installed.startswith(original))
        managed_setup.uninstall(nuke_dir=self.nuke, config_path=self.config)
        self.assertEqual(menu.read_bytes(), original)

    def test_renews_within_fourteen_days_using_same_token(self):
        from datetime import datetime
        self.ensure()
        now = datetime.fromisoformat('2098-12-20T00:00:00+00:00').timestamp()
        result = self.ensure(clock=lambda: now)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1], self.calls[1][1])

    def test_expired_binding_is_disabled_if_renewal_is_offline(self):
        from datetime import datetime
        self.ensure()
        now = datetime.fromisoformat('2099-01-02T00:00:00+00:00').timestamp()
        def offline(*args):
            raise OSError('offline')
        result = self.ensure(clock=lambda: now, request=offline)
        self.assertEqual(result['status'], 'retry')
        self.assertFalse(managed_setup.load_config(self.config)['enabled'])

    def test_nonempty_queue_survives_repeated_setup_and_server_switch(self):
        from plugins.nuke.nuke_time_calc.outbox import Outbox
        from plugins.nuke.nuke_time_calc.tests.test_outbox import batch
        self.ensure()
        config = managed_setup.load_config(self.config)
        queue = Outbox(config['outbox_dir'], server=config['server'],
                       artist_id=config['artist_id'], device_id=config['device_id'])
        queue.put(batch())
        self.ensure()
        self.assertEqual(queue.pending(), [batch()])
        self.ensure(server_url='https://other.example/api/')
        other = managed_setup.load_config(self.config)
        self.assertNotEqual(other['outbox_dir'], config['outbox_dir'])
        self.assertEqual(queue.pending(), [batch()])
        self.ensure()
        restored = managed_setup.load_config(self.config)
        self.assertEqual(restored['outbox_dir'], config['outbox_dir'])
        self.assertEqual(restored['device_id'], config['device_id'])
        self.assertEqual(queue.pending(), [batch()])

    def test_always_on_overrides_legacy_disabled_and_keeps_mappings(self):
        self.ensure()
        config = managed_setup.load_config(self.config)
        config['enabled'] = False
        config['mappings'] = [dict(job_id=1, shot_id=2, base_path=str(self.root / 'shot'))]
        managed_setup.save_config(config, self.config)
        self.ensure()
        self.assertEqual(managed_setup.load_config(self.config), dict(config, enabled=True))

    def test_corrupt_metadata_is_not_recreated_or_enrolled(self):
        self.ensure()
        metadata = next(self.config.parent.glob('bindings/*/provision.json'))
        token = next(self.config.parent.glob('bindings/*/credential.token'))
        original = token.read_bytes()
        for damaged in [b'', b'{invalid', b'{}']:
            metadata.write_bytes(damaged)
            result = self.ensure()
            self.assertEqual(result['status'], 'error')
            self.assertEqual(metadata.read_bytes(), damaged)
            self.assertEqual(token.read_bytes(), original)
        self.assertEqual(len(self.calls), 1)

    def test_symlink_and_hardlinked_credentials_are_rejected_without_overwrite(self):
        self.ensure()
        token = next(self.config.parent.glob('bindings/*/credential.token'))
        original = token.read_bytes()
        other = self.root / 'other-token'
        token.rename(other)
        token.symlink_to(other)
        self.assertEqual(self.ensure()['status'], 'error')
        self.assertEqual(other.read_bytes(), original)
        token.unlink()
        os.link(other, token)
        self.assertEqual(self.ensure()['status'], 'error')
        self.assertEqual(other.read_bytes(), original)
        self.assertEqual(len(self.calls), 1)

    def test_symlink_namespace_and_hardlinked_config_are_rejected(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        self.config.parent.symlink_to(target)
        result = self.ensure()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(list(target.iterdir()), [])
        self.config.parent.unlink()
        self.ensure()
        os.link(self.config, self.root / 'config-link')
        content = self.config.read_bytes()
        self.assertEqual(self.ensure()['status'], 'error')
        self.assertEqual(self.config.read_bytes(), content)

    def test_private_namespace_does_not_chmod_shared_cache_prefix(self):
        shared = self.root / 'shared'
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        config = shared / 'nuke-time' / 'workstation' / 'config.json'
        self.ensure(config_path=config, request=lambda url, body, timeout: (200, {},
            json.dumps(dict(artist_id=7, device_id=json.loads(body)['device_id'],
                            status='ready', expires_at='2099-01-01T00:00:00Z')).encode()))
        self.assertEqual(shared.stat().st_mode & 0o777, 0o755)
        self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)

    def test_storage_validation_failure_never_requests_or_installs(self):
        module = importlib.import_module('nuke_time_auto_setup')
        with mock.patch.object(module.storage, 'private_directory',
                               side_effect=ValueError('unsupported filesystem or unsafe ACL')):
            result = self.ensure()
        self.assertEqual(result, dict(status='error', code='private_state_error'))
        self.assertEqual(self.calls, [])
        self.assertFalse(self.config.exists())
        self.assertFalse((self.nuke / 'menu.py').exists())

    def test_unsafe_startup_path_fails_before_enrollment_or_activation(self):
        target = self.root / 'other-startup'
        target.mkdir()
        try:
            self.nuke.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires Developer Mode or privilege')
            raise
        result = self.ensure()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.calls, [])
        self.assertFalse(self.config.exists())
        self.assertEqual(list(target.iterdir()), [])

    def test_backend_approved_platform_runs_setup_and_replays_lost_ack(self):
        # Isolate the caller's platform guard, not pathlib or the storage backend.
        # Native ACL behavior is covered by the storage tests on Windows.
        from types import SimpleNamespace
        from plugins.nuke.nuke_time_calc import outbox, storage
        module = importlib.import_module('nuke_time_auto_setup')
        with mock.patch.object(module, 'os', SimpleNamespace(name='nt')), \
                mock.patch.object(outbox, 'os', SimpleNamespace(name='nt')), \
                mock.patch.object(storage, 'supported_platform', return_value=True):
            def lost(url, body, timeout):
                self.calls.append((url, json.loads(body), timeout))
                raise OSError('lost acknowledgement')
            self.assertEqual(self.ensure(request=lost)['status'], 'retry')
            self.assertFalse((self.nuke / 'menu.py').exists())
            self.assertEqual(self.ensure()['status'], 'ready')
            self.assertEqual(self.calls[0][1], self.calls[1][1])
            config = managed_setup.load_config(self.config)
            queue = outbox.Outbox(config['outbox_dir'], server=config['server'],
                artist_id=config['artist_id'], device_id=config['device_id'])
            from plugins.nuke.nuke_time_calc.tests.test_outbox import batch
            queue.put(batch())
            self.assertEqual(self.ensure()['status'], 'ready')
            self.assertEqual(queue.pending(), [batch()])
            from plugins.nuke.nuke_time_calc.transport import Uploader
            sent = []
            def upload(url, body, token, timeout):
                sent.append((json.loads(body), token))
                item = json.loads(body)['batches'][0]
                return 200, {}, json.dumps(dict(results=[dict(session_id=item['session_id'],
                    sequence=item['sequence'], status='duplicate')])).encode()
            worker = Uploader(queue, token_file=config['token_file'], transport=upload)
            self.assertTrue(worker.run_once())
            self.assertEqual(sent[0][0]['batches'], [batch()])
            self.assertEqual(sent[0][1], self.calls[0][1]['token'])
            self.assertEqual(queue.pending(), [])
            from plugins.nuke.nuke_time_calc.outbox import BatchConflict
            changed = dict(batch(), reason='changed')
            with self.assertRaises(BatchConflict):
                queue.put(changed)

    def test_unsupported_backend_is_rejected_before_state_writes(self):
        module = importlib.import_module('nuke_time_auto_setup')
        with mock.patch.object(module.storage, 'supported_platform', return_value=False):
            result = self.ensure()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['code'], 'unsupported_platform')
        self.assertFalse(self.config.parent.exists())

    def test_default_transport_rejects_redirect_without_forwarding_secret(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.path)
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(307)
                self.send_header('Location', '/stolen')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            module = importlib.import_module('nuke_time_auto_setup')
            status, _, _ = module._post('http://127.0.0.1:%s/enroll' % server.server_port,
                                        b'{"token":"test-only"}', 5.0)
            self.assertEqual(status, 307)
            self.assertEqual(seen, ['/enroll'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_redirect_ack_is_explicitly_refused(self):
        result = self.ensure(request=lambda *args: (302, {'Location': 'https://evil.example'}, b''))
        self.assertEqual(result['code'], 'redirect_rejected')
        self.assertFalse(self.config.exists())

    def test_server_outage_is_retryable_without_enabling(self):
        result = self.ensure(request=lambda *args: (503, {}, b'not a successful ack'))
        self.assertEqual(result['status'], 'retry')
        self.assertFalse(self.config.exists())

    def test_missing_or_unlinked_artist_waits_without_network_or_state(self):
        for artist, users in [(None, None), (False, None), (0, None), ('x', None), (7, []), (7, [{'id': 7, 'is_active': False}])]:
            with self.subTest(artist=artist, users=users):
                result = self.ensure(artist_id=artist, users=users)
                self.assertEqual(result['status'], 'waiting')
                self.assertFalse(self.config.parent.exists())
        self.assertEqual(self.calls, [])

    def test_unsafe_origin_fails_before_credentials_or_network(self):
        for server in ['http://192.168.1.4/api/', 'http://evil.example',
                       'https://user:secret@shotbox.example', 'https://shotbox.example/other',
                       'https://shotbox.example:0', 'https://shotbox.example\\\\evil']:
            with self.subTest(server=server):
                result = self.ensure(server_url=server)
                self.assertEqual(result['status'], 'error')
                self.assertFalse(self.config.parent.exists())
        self.assertEqual(self.calls, [])

    def test_switch_disables_original_binding_on_network_failure_and_preserves_files(self):
        self.ensure()
        before = managed_setup.load_config(self.config)
        before['mappings'] = [dict(job_id=1, shot_id=2, base_path=str(self.root / 'shot'))]
        managed_setup.save_config(before, self.config)
        queue = next(Path(before['outbox_dir']).rglob('outbox.sqlite3'))
        content = queue.read_bytes()
        def offline(*args):
            saved = managed_setup.load_config(self.config)
            self.assertFalse(saved['enabled'])
            self.assertEqual(saved['artist_id'], 7)
            raise OSError('offline')
        result = self.ensure(artist_id=8, request=offline)
        self.assertEqual(result['status'], 'retry')
        disabled = managed_setup.load_config(self.config)
        self.assertEqual(disabled, dict(before, enabled=False))
        self.assertEqual(queue.read_bytes(), content)
        result = self.ensure(artist_id=8)
        self.assertEqual(result['status'], 'ready')
        after = managed_setup.load_config(self.config)
        self.assertEqual(after['mappings'], [])
        self.assertNotEqual(after['outbox_dir'], before['outbox_dir'])
        self.assertNotEqual(after['device_id'], before['device_id'])
        self.assertEqual(queue.read_bytes(), content)

    def test_lost_ack_retries_with_same_durable_credential(self):
        def lost(url, body, timeout):
            self.calls.append((url, json.loads(body), timeout))
            raise OSError('SECRET-EXCEPTION')
        result = self.ensure(request=lost)
        self.assertEqual(result['status'], 'retry')
        self.assertNotIn('SECRET-EXCEPTION', repr(result))
        self.assertFalse(self.config.exists())
        result = self.ensure()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.calls[0][1], self.calls[1][1])

    def test_bad_ack_never_enables_config(self):
        def bad(url, body, timeout):
            data = json.loads(body)
            return 200, {}, json.dumps(dict(artist_id=99, device_id=data['device_id'],
                status='ready', expires_at='2099-01-01T00:00:00Z')).encode()
        result = self.ensure(request=bad)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(self.config.exists())
        self.assertFalse((self.nuke / 'menu.py').exists())

    def test_repeat_keeps_binding_credential_queue_and_does_not_reenroll(self):
        first = self.ensure()
        config = managed_setup.load_config(self.config)
        token = Path(config['token_file']).read_bytes()
        queue = next(Path(config['outbox_dir']).rglob('outbox.sqlite3'))
        original_inode = queue.stat().st_ino
        second = self.ensure()
        self.assertEqual(first, second)
        self.assertEqual(Path(config['token_file']).read_bytes(), token)
        self.assertEqual(queue.stat().st_ino, original_inode)
        self.assertEqual(len(self.calls), 1)

    def test_ready_creates_private_credentials_enabled_config_and_startup(self):
        result = self.ensure()
        self.assertEqual(result['status'], 'ready')
        config = managed_setup.load_config(self.config)
        self.assertTrue(config['enabled'])
        self.assertEqual(config['artist_id'], 7)
        token = Path(config['token_file']).read_text().strip()
        self.assertRegex(token, r'^[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$')
        self.assertNotIn(token, repr(result))
        self.assertEqual(self.calls[0][0], 'https://shotbox.example/api/time/devices/auto-setup')
        self.assertEqual(self.calls[0][2], 5.0)
        self.assertIn(b'SHOTBOX NUKE TIME CALC MANAGED', (self.nuke / 'menu.py').read_bytes())
        for path in self.config.parent.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o077, 0, str(path))
        self.assertEqual(self.config.parent.stat().st_mode & 0o077, 0)
