"""Explicit RFC1918 HTTP policy; all fixtures are local and disposable."""
import unittest
from types import SimpleNamespace


class LanConfigTests(unittest.TestCase):
    def test_config_accepts_only_explicit_private_lan_http(self):
        from nuke_time_calc.managed_setup import _validate_config
        self.assertIs(_validate_config({})['allow_lan_http'], False)
        for host in ('10.0.0.1', '172.16.0.1', '172.31.255.254', '192.168.10.88'):
            origin = 'http://' + host + ':8000'
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError):
                    _validate_config(dict(server=origin))
                result = _validate_config(dict(server=origin, allow_lan_http=True))
                self.assertEqual(result['server'], origin)
                self.assertIs(result['allow_lan_http'], True)
        for flag in (None, 0, 1, 'true', [], {}):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                _validate_config(dict(allow_lan_http=flag))


class LanUploaderTests(unittest.TestCase):
    def test_uploader_has_separate_explicit_lan_switch(self):
        from nuke_time_calc.transport import Uploader
        for host in ('10.255.255.254', '172.16.0.1', '192.168.10.88'):
            queue = SimpleNamespace(binding=('http://' + host + ':8000', '1', 'device'))
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    Uploader(queue, token_file='/unused')
                with self.assertRaises(ValueError):
                    Uploader(queue, token_file='/unused', allow_loopback_http=True)
                worker = Uploader(queue, token_file='/unused', allow_lan_http=True)
                self.assertEqual(worker.url, queue.binding[0] + '/api/time/batches')
        for flag in (None, 0, 1, 'true'):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                Uploader(SimpleNamespace(binding=('https://example.test', '1', 'device')),
                         token_file='/unused', allow_lan_http=flag)
    def test_public_alias_and_malformed_origins_are_rejected_everywhere(self):
        from nuke_time_calc.managed_setup import _validate_config
        from nuke_time_calc.transport import Uploader
        origins = [
            'http://8.8.8.8', 'http://100.64.0.1', 'http://169.254.1.1',
            'http://172.15.255.255', 'http://172.32.0.1', 'http://192.169.0.1',
            'http://127.0.0.1', 'http://localhost', 'http://shotbox.local',
            'http://192.168.10.88.example', 'http://192.168.10.88.',
            'http://3232238168', 'http://192.168.010.88', 'http://[::ffff:192.168.10.88]',
            'http://192.168.10.88/path', 'http://192.168.10.88?x=1',
            'http://192.168.10.88?', 'http://192.168.10.88#',
            'http://user@192.168.10.88', 'http://@192.168.10.88',
            'http://192.168.10.88\\evil', 'http://192.168.10.88\x00',
            'http://192.168.10.88\n', 'http://192.168.10.88:',
            'http://192.168.10.88:bad', 'http://192.168.10.88:0',
            'http://192.168.10.88:65536', 'http://192.168.10.88:-1',
        ]
        for origin in origins:
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError):
                    _validate_config(dict(server=origin, allow_lan_http=True))
                with self.assertRaises(ValueError):
                    Uploader(SimpleNamespace(binding=(origin, '1', 'device')),
                             token_file='/unused', allow_lan_http=True)

    def test_real_lan_http_delivery_preserves_binding_headers(self):
        import ipaddress
        import json
        import os
        import socket
        import tempfile
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from test_outbox import batch
        from nuke_time_calc.managed_setup import load_config, save_config
        from nuke_time_calc.runtime import build_runtime
        # Route selection does not transmit UDP data; use only this host's address.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            address = probe.getsockname()[0]
        if not any(ipaddress.ip_address(address) in ipaddress.ip_network(network)
                   for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
            self.skipTest('host has no RFC1918 route address')
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path, dict(self.headers), payload))
                item = payload['batches'][0]
                body = json.dumps(dict(results=[dict(session_id=item['session_id'],
                    sequence=item['sequence'], status='accepted')])).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer((address, 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
                from pathlib import Path
                token = Path(root) / 'token'
                token.write_text('fixture-selector.fixture-secret\n')
                token.chmod(0o600)
                config_path = Path(root) / 'config.json'
                save_config(dict(enabled=True, allow_lan_http=True,
                    server='http://%s:%s' % (address, server.server_port), artist_id=1,
                    device_id='12345678-1234-4234-8234-123456789abc',
                    token_file=str(token), outbox_dir=root + '/outbox'), config_path)
                runtime = build_runtime(load_config(config_path), object(), config_path)
                runtime.outbox.put(batch())
                self.assertTrue(runtime.uploader.run_once())
                self.assertEqual(runtime.outbox.pending(), [])
                self.assertEqual(len(received), 1)
                path, headers, payload = received[0]
                self.assertEqual(path, '/api/time/batches')
                self.assertEqual(headers['X-Shotbox-Time-Artist'], '1')
                self.assertEqual(headers['X-Shotbox-Time-Device'],
                                 '12345678-1234-4234-8234-123456789abc')
                self.assertEqual(headers['Authorization'], 'Bearer fixture-selector.fixture-secret')
                self.assertEqual(payload, dict(schema_version=2, batches=[batch()]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


class LanRuntimeTests(unittest.TestCase):
    def test_runtime_passes_only_present_flag(self):
        import os
        import tempfile
        from nuke_time_calc.runtime import build_runtime
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            config = dict(server='https://example.test', artist_id=1,
                          device_id='device', token_file=root + '/token', outbox_dir=root + '/outbox')
            calls = []
            def factory(outbox, **options):
                calls.append(options)
                return object()
            build_runtime(config, object(), uploader_factory=factory)
            self.assertNotIn('allow_lan_http', calls[-1])
            for value in (True, False):
                build_runtime(dict(config, allow_lan_http=value), object(), uploader_factory=factory)
                self.assertIs(calls[-1]['allow_lan_http'], value)

    def test_lan_consent_is_immutable_while_running(self):
        from nuke_time_calc.runtime import BoundResolver
        for before, after in ((False, True), (True, False), (True, None)):
            with self.subTest(before=before, after=after):
                initial = dict(enabled=True, server='https://example.test', allow_lan_http=before)
                current = dict(initial)
                resolver = BoundResolver(initial, None, loader=lambda path: current)
                self.assertIsNone(resolver('/unknown.nk'))
                if after is None:
                    del current['allow_lan_http']
                else:
                    current['allow_lan_http'] = after
                with self.assertRaisesRegex(ValueError, 'binding changed'):
                    resolver('/unknown.nk')
        current = dict(enabled=True, server='https://example.test', allow_lan_http=False)
        resolver = BoundResolver(dict(enabled=True, server='https://example.test'), None,
                                 loader=lambda path: current)
        self.assertIsNone(resolver('/unknown.nk'))


if __name__ == '__main__':
    unittest.main()
