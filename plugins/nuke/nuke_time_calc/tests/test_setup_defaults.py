"""Offline setup intent never provisions credentials or starts reporting."""
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from plugins.nuke.nuke_time_calc import managed_setup


class SetupDefaultsTests(unittest.TestCase):
    def setUp(self):
        from plugins.nuke.nuke_time_calc import setup_defaults
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(setup_defaults, 'DEFAULTS_PATH',
                                    Path(self.tmp.name) / 'absent-defaults.json')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_explicit_private_lan_defaults_follow_current_normal_server_not_shared_host(self):
        import tempfile
        import json
        from plugins.nuke.nuke_time_calc import setup_defaults
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'defaults.json'
            path.write_text(json.dumps({'server': 'http://192.168.10.88:8000',
                                        'allow_lan_http': True}))
            self.assertEqual(setup_defaults.deployment_defaults(path), {
                'server': 'http://192.168.10.88:8000', 'allow_lan_http': True})
            for host in ('192.168.10.88', '10.1.2.3', '172.16.1.2'):
                origin = 'http://' + host + ':8000'
                result = setup_defaults.suggest(server_url=origin + '/api/', defaults_path=path)
                self.assertEqual(result['server'], origin)
                self.assertTrue(result['allow_lan_http'])
            for origin in ('http://8.8.8.8:8000', 'http://100.64.1.2:8000',
                           'http://127.0.0.1:8000', 'http://localhost:8000',
                           'http://192.168.10.88:8443', 'http://10.1.2.3:8000/other', ''):
                self.assertNotIn('server', setup_defaults.suggest(
                    server_url=origin, defaults_path=path))
            for flag in (False, 'true', 1, None):
                path.write_text(json.dumps({'server': 'http://192.168.10.88:8000',
                                            'allow_lan_http': flag}))
                self.assertEqual(setup_defaults.deployment_defaults(path), {})

    def test_deployment_ca_resolves_only_existing_bundled_relative_public_file(self):
        import tempfile
        import json
        from plugins.nuke.nuke_time_calc import setup_defaults
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            defaults = root / 'nuke_time_defaults.json'
            ca = root / 'plugins' / 'certs' / 'lan.pem'
            ca.parent.mkdir(parents=True)
            ca.write_text('public certificate fixture')
            defaults.write_text(json.dumps({'server': 'https://192.168.10.88:8443',
                                            'ca_file': 'plugins/certs/lan.pem'}))
            result = setup_defaults.suggest(defaults_path=defaults)
            self.assertEqual(result['server'], 'https://192.168.10.88:8443')
            self.assertEqual(result['ca_file'], str(ca))
            other = setup_defaults.suggest(defaults_path=defaults, server_url='https://other.example')
            self.assertNotIn('ca_file', other)
            for value in ('missing.pem', str(ca), '../outside.pem', 'plugins/certs/../certs/lan.pem'):
                defaults.write_text(json.dumps({'server': 'https://192.168.10.88:8443', 'ca_file': value}))
                self.assertNotIn('ca_file', setup_defaults.suggest(defaults_path=defaults))
            ca.unlink()
            defaults.write_text(json.dumps({'server': 'https://192.168.10.88:8443', 'ca_file': 'plugins/certs/lan.pem'}))
            self.assertNotIn('ca_file', setup_defaults.suggest(defaults_path=defaults))

    def test_shared_defaults_accept_only_public_https_origin(self):
        import tempfile
        import json
        from plugins.nuke.nuke_time_calc import setup_defaults
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nuke_time_defaults.json'
            self.assertIsNone(setup_defaults.deployment_origin(path))
            for value in ({'server': 'https://shotbox.example'}, {},
                          {'server': 'http://shotbox.example'},
                          {'server': 'https://shotbox.example/api/'},
                          {'server': 'https://shotbox.example', 'artist_id': 1},
                          {'server': 'https://shotbox.example', 'token': 'secret'},
                          {'server': 'https://u:secret@shotbox.example'}, []):
                path.write_text(json.dumps(value))
                expected = 'https://shotbox.example' if value == {'server': 'https://shotbox.example'} else None
                self.assertEqual(setup_defaults.deployment_origin(path), expected)
            path.write_text('{invalid')
            self.assertIsNone(setup_defaults.deployment_origin(path))
            path.write_text('{"server": "https://deployment.example"}')
            self.assertEqual(setup_defaults.suggest(defaults_path=path)['server'], 'https://deployment.example')
            self.assertEqual(setup_defaults.suggest(defaults_path=path, server_url='https://explicit.example/api/')['server'], 'https://explicit.example')

    def test_suggestions_default_enabled_and_partitioned_by_local_profile_artist_device(self):
        from plugins.nuke.nuke_time_calc import setup_defaults
        device = 'b97b4ee3-33ba-4594-9e80-abba065cc482'
        with mock.patch.object(managed_setup, 'default_config_path', return_value=Path('/private/alice/config.json')):
            result = setup_defaults.suggest(server_url='https://shotbox.example/api/', artist_id=7,
                                            device_id=device)
        self.assertEqual(result, {
            'enabled': True, 'mappings': [], 'server': 'https://shotbox.example',
            'artist_id': 7, 'device_id': device,
            'token_file': '/private/alice/credentials/7/' + device + '.token',
            'outbox_dir': '/private/alice/outbox/7/' + device})
        other = setup_defaults.suggest(artist_id=8, device_id=device,
                                       config_path=Path('/private/bob/config.json'))
        self.assertNotEqual(other['outbox_dir'], result['outbox_dir'])
        self.assertNotIn('server', other)
        unlinked = setup_defaults.suggest(artist_id='jack', device_id=device)
        self.assertNotIn('artist_id', unlinked)
        self.assertNotIn('token_file', unlinked)
        self.assertNotIn('outbox_dir', unlinked)

    def test_linked_artist_is_positive_numeric_and_active_in_available_snapshot(self):
        from plugins.nuke.nuke_time_calc import setup_defaults
        self.assertEqual(setup_defaults.linked_artist(7), 7)
        self.assertEqual(setup_defaults.linked_artist('7'), 7)
        self.assertEqual(setup_defaults.linked_artist(7, [{'id': 7, 'is_active': True}]), 7)
        self.assertEqual(setup_defaults.linked_artist(7, [{'id': 7}]), 7)
        for value in (None, '', 'jack', 0, -1, True, 7.5, '1.5'):
            self.assertIsNone(setup_defaults.linked_artist(value))
        for users in ([], [{'id': 8}], [{'id': 7, 'is_active': False}]):
            self.assertIsNone(setup_defaults.linked_artist(7, users))

    def test_https_api_url_becomes_origin_without_upgrading_http(self):
        from plugins.nuke.nuke_time_calc import setup_defaults
        self.assertEqual(setup_defaults.https_origin('https://shotbox.example:8443/api/'),
                         'https://shotbox.example:8443')
        for value in ('http://shotbox.example/api/', 'https://u:p@shotbox.example',
                      'https://shotbox.example/other', 'https://shotbox.example/?secret=x',
                      'https://shotbox.example:invalid', '', None):
            with self.subTest(value=value):
                self.assertIsNone(setup_defaults.https_origin(value))
