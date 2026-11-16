"""Isolated loopback HTTP transport; no production credentials or endpoints."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from test_outbox import batch


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = self.root / 'token'
        fd = os.open(str(self.token), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('test-selector.test-secret\n')
        self.received = []
        self.received_bindings = []
        self.redirect = False
        self.outage = False
        self.lose_ack = False
        self.committed = set()
        self.commit_gate = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            def do_GET(self):
                owner.received.append(('leaked', self.headers.get('Authorization'), None))
                self.send_response(200)
                self.end_headers()
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.received.append((self.path, self.headers.get('Authorization'), data))
                owner.received_bindings.append((self.headers.get('X-Shotbox-Time-Artist'),
                                               self.headers.get('X-Shotbox-Time-Device')))
                if owner.redirect:
                    self.send_response(302)
                    self.send_header('Location', owner.origin + '/leak')
                    self.end_headers()
                    return
                if owner.outage:
                    self.send_response(503)
                    self.send_header('Retry-After', '1')
                    self.end_headers()
                    self.wfile.write(b'{"code":"database_busy"}')
                    return
                item = data['batches'][0]
                key = (item['session_id'], item['sequence'])
                status = 'duplicate' if key in owner.committed else 'accepted'
                owner.committed.add(key)
                if owner.lose_ack:
                    owner.lose_ack = False
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                if owner.commit_gate is not None:
                    owner.commit_gate.wait(2)
                body = json.dumps(dict(results=[dict(session_id=item['session_id'], sequence=item['sequence'], status=status)])).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = 'http://127.0.0.1:%d' % self.server.server_port

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def queue(self, origin=None):
        from nuke_time_calc.outbox import Outbox
        return Outbox(self.root / 'state', server=origin or self.origin, artist_id='artist-1', device_id='device-1')

    def test_unavailable_shot_is_preserved_without_blocking_next_upload(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        q.put(batch(2))
        replies = [(400, {}, b'{"code":"invalid_shot"}'),
                   (200, {}, json.dumps({'results': [dict(session_id=batch(2)['session_id'],
                       sequence=2, status='accepted')]}).encode())]
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True,
                          transport=lambda *args: replies.pop(0))
        self.assertTrue(worker.run_once())
        self.assertEqual(worker.status()['code'], 'invalid_shot_quarantined')
        self.assertEqual(q.status()['quarantined_records'], 1)
        self.assertEqual(q.pending(), [batch(2)])
        self.assertTrue(worker.run_once())
        self.assertEqual(q.pending(), [])
        restarted = self.queue()
        self.assertEqual(restarted.status()['quarantined_records'], 1)
        with restarted._connect() as db:
            payload, pending = db.execute('SELECT payload, pending FROM batches WHERE sequence=1').fetchone()
        self.assertEqual(json.loads(payload), batch())
        self.assertEqual(pending, -1)

    def test_hardlinked_token_cannot_send_or_acknowledge(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        os.link(self.token, self.root / 'token-alias')
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'credential_error')
        self.assertEqual(self.received, [])
        self.assertEqual(q.pending(), [batch()])

    def test_default_post_sends_outbox_binding_after_credential_file_changes(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True)
        self.token.write_text('other-artist.other-device\n')
        self.assertTrue(worker.run_once())
        self.assertEqual(self.received[0][1], 'Bearer other-artist.other-device')
        self.assertEqual(self.received_bindings, [('artist-1', 'device-1')])

    def test_plain_http_requires_explicit_literal_loopback_exception(self):
        from nuke_time_calc.transport import Uploader
        with self.assertRaises(ValueError):
            Uploader(self.queue(), token_file=self.token)
        for origin in ['http://localhost:8080', 'http://example.test', 'http://127.0.0.1.evil.test', 'https://user:pass@example.test', 'https://example.test/path', 'https://example.test?x=y', 'https://example.test/#x']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Uploader(self.queue(origin), token_file=self.token, allow_loopback_http=True)
        Uploader(self.queue('https://example.test'), token_file=self.token)

    def test_private_token_file_required_and_rotation_is_loaded_each_attempt(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        now = [0.0]
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True, clock=lambda: now[0])
        self.token.chmod(0o644)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'credential_error')
        self.assertEqual(self.received, [])
        self.token.chmod(0o600)
        self.token.write_text('rotated.selector\n')
        now[0] = 60.0
        self.assertTrue(worker.run_once())
        self.assertEqual(self.received[0][1], 'Bearer rotated.selector')
        q.put(batch(2))
        alias = self.root / 'alias'
        alias.symlink_to(self.token)
        other = Uploader(q, token_file=alias, allow_loopback_http=True)
        self.assertFalse(other.run_once())
        self.assertEqual(other.status()['code'], 'credential_error')
        self.assertEqual(q.pending(), [batch(2)])

    def test_redirect_is_not_followed_with_bearer_header(self):
        from nuke_time_calc.transport import Uploader
        self.redirect = True
        q = self.queue()
        q.put(batch())
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'redirect_rejected')
        self.assertEqual(len(self.received), 1)
        self.assertEqual(q.pending(), [batch()])

    def test_only_exact_accepted_or_duplicate_ack_can_remove(self):
        from nuke_time_calc.transport import Uploader
        for result in [[], [dict(session_id=batch()['session_id'], sequence=2, status='accepted')], [dict(session_id=batch()['session_id'], sequence=True, status='accepted')], [dict(session_id=batch()['session_id'], sequence=1, status='rejected')], [dict(session_id=batch()['session_id'], sequence=1, status='accepted', extra='x')]]:
            q = self.queue()
            q.put(batch())
            worker = Uploader(q, token_file=self.token, allow_loopback_http=True,
                              transport=lambda *args: (200, {}, json.dumps(dict(results=result)).encode()))
            self.assertFalse(worker.run_once())
            self.assertEqual(worker.status()['code'], 'invalid_ack')
            self.assertEqual(q.pending(), [batch()])
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True,
                          transport=lambda *args: (200, {}, json.dumps(dict(results=[dict(session_id=batch()['session_id'], sequence=1, status='duplicate')])).encode()))
        self.assertTrue(worker.run_once())
        self.assertEqual(q.pending(), [])
        self.assertEqual(worker.status()['code'], 'duplicate')

    def test_classified_failures_backoff_and_retry_after_preserve_queue(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        now = [100.0]
        calls = []
        replies = [(429, {'Retry-After': '7'}, b'{"code":"throttled"}'),
                   (503, {}, b'{"code":"time_disabled"}'),
                   (401, {}, b'{"code":"authentication_failed"}')]
        def send(*args):
            calls.append(args[0])
            return replies.pop(0)
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True, transport=send,
                          clock=lambda: now[0])
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'throttled')
        self.assertEqual(worker.status()['next_attempt'], 107.0)
        self.assertFalse(worker.run_once())
        self.assertEqual(len(calls), 1)
        now[0] = 107.0
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'disabled')
        self.assertGreaterEqual(worker.status()['next_attempt'], 167.0)
        now[0] = worker.status()['next_attempt']
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'authentication_error')
        self.assertEqual(q.pending(), [batch()])
        self.assertNotIn('test-secret', repr(worker.status()))

    def test_real_outage_then_lost_ack_replays_after_restart_without_duplicate_work(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        self.outage = True
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'database_busy')
        self.assertEqual(q.pending(), [batch()])
        self.outage = False
        self.lose_ack = True
        worker = Uploader(self.queue(), token_file=self.token, allow_loopback_http=True)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'network_error')
        self.assertEqual(len(self.committed), 1)
        self.assertEqual(q.pending(), [batch()])
        restarted = Uploader(self.queue(), token_file=self.token, allow_loopback_http=True)
        self.assertTrue(restarted.run_once())
        self.assertEqual(restarted.status()['code'], 'duplicate')
        self.assertEqual(q.pending(), [])
        self.assertEqual(len(self.committed), 1)

    def test_background_worker_stop_is_bounded_while_network_is_blocked(self):
        import time
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        entered, release = threading.Event(), threading.Event()
        def send(*args):
            entered.set()
            release.wait(2)
            raise TimeoutError('never log this secret payload')
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True, transport=send)
        self.assertTrue(worker.start())
        self.assertFalse(worker.start())
        self.assertTrue(entered.wait(1))
        start = time.monotonic()
        q.put(batch(2))
        self.assertFalse(worker.stop(timeout=.02))
        self.assertLess(time.monotonic() - start, .5)
        self.assertTrue(worker.status()['running'])
        release.set()
        self.assertTrue(worker.stop(timeout=2))
        self.assertEqual(len(q.pending()), 2)
        self.assertEqual(worker.status()['code'], 'network_error')
        self.assertNotIn('secret', repr(worker.status()))

    def test_real_https_rejects_untrusted_certificate(self):
        import shutil
        import ssl
        import subprocess
        from nuke_time_calc.transport import Uploader
        if not shutil.which('openssl'):
            self.skipTest('openssl needed only to create isolated TLS fixture')
        key, cert = self.root / 'tls.key', self.root / 'tls.crt'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        tls = ThreadingHTTPServer(('127.0.0.1', 0), self.server.RequestHandlerClass)
        tls.socket = context.wrap_socket(tls.socket, server_side=True)
        thread = threading.Thread(target=tls.serve_forever, daemon=True)
        thread.start()
        try:
            q = self.queue('https://127.0.0.1:%d' % tls.server_port)
            q.put(batch())
            worker = Uploader(q, token_file=self.token)
            self.assertFalse(worker.run_once())
            self.assertEqual(worker.status()['code'], 'tls_error')
            self.assertEqual(q.pending(), [batch()])
            self.assertEqual(self.received, [])
        finally:
            tls.shutdown()
            tls.server_close()
            thread.join(2)

    def test_nonfinite_or_unbounded_worker_options_are_rejected(self):
        from nuke_time_calc.transport import Uploader
        for options in [dict(timeout=None), dict(timeout=float('inf')), dict(timeout=0), dict(poll_interval=0), dict(retry_base=-1), dict(retry_max=float('nan'))]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                Uploader(self.queue(), token_file=self.token, allow_loopback_http=True, **options)
        worker = Uploader(self.queue(), token_file=self.token, allow_loopback_http=True)
        with self.assertRaises(ValueError):
            worker.stop(timeout=float('inf'))

    def test_corrupt_outbox_is_observable_without_network_or_deletion(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        q.path.write_bytes(b'torn sqlite file')
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'outbox_error')
        self.assertEqual(self.received, [])
        self.assertEqual(q.path.read_bytes(), b'torn sqlite file')

    def test_missing_credential_backs_off_without_logging_or_busy_loop(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        now = [10.0]
        missing = self.root / 'missing-token'
        worker = Uploader(q, token_file=missing, allow_loopback_http=True, clock=lambda: now[0])
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['code'], 'credential_error')
        self.assertEqual(worker.status()['next_attempt'], 70.0)
        self.assertFalse(worker.run_once())
        self.assertEqual(worker.status()['failures'], 1)
        self.assertEqual(q.pending(), [batch()])

    def test_real_timeout_after_commit_is_replayed_as_duplicate(self):
        from nuke_time_calc.transport import Uploader
        self.commit_gate = threading.Event()
        q = self.queue()
        q.put(batch())
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True, timeout=.05)
        try:
            self.assertFalse(worker.run_once())
            self.assertEqual(worker.status()['code'], 'network_error')
            self.assertEqual(len(self.committed), 1)
            self.assertEqual(q.pending(), [batch()])
        finally:
            self.commit_gate.set()
        restarted = Uploader(self.queue(), token_file=self.token, allow_loopback_http=True)
        self.assertTrue(restarted.run_once())
        self.assertEqual(restarted.status()['code'], 'duplicate')
        self.assertEqual(len(self.committed), 1)

    def test_concurrent_process_uploaders_safely_share_one_queue(self):
        import subprocess
        q = self.queue()
        q.put(batch())
        script = """
import sys
from nuke_time_calc.outbox import Outbox
from nuke_time_calc.transport import Uploader
q = Outbox(sys.argv[1], server=sys.argv[2], artist_id='artist-1', device_id='device-1')
u = Uploader(q, token_file=sys.argv[3], allow_loopback_http=True)
u.run_once()
"""
        processes = [subprocess.Popen([os.sys.executable, '-c', script, str(self.root / 'state'), self.origin, str(self.token)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        results = [(process, process.communicate(timeout=10)) for process in processes]
        for process, (_, err) in results:
            self.assertEqual(process.returncode, 0, err.decode())
        self.assertEqual(q.pending(), [])
        self.assertEqual(len(self.committed), 1)

    def test_malformed_or_oversized_ack_is_retained_and_classified(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        for raw in [b'', b'{"results":', b'x' * 1048577,
                    b'{"results":[],"results":[]}', b'[' * 2000 + b']' * 2000]:
            worker = Uploader(q, token_file=self.token, allow_loopback_http=True,
                              transport=lambda *args: (200, {}, raw))
            self.assertFalse(worker.run_once())
            self.assertEqual(worker.status()['code'], 'invalid_ack')
            self.assertEqual(q.pending(), [batch()])

    def test_real_post_envelope_ack_removes_only_sent_key(self):
        from nuke_time_calc.transport import Uploader
        q = self.queue()
        q.put(batch())
        q.put(batch(2))
        worker = Uploader(q, token_file=self.token, allow_loopback_http=True)
        self.assertTrue(worker.run_once())
        self.assertEqual(q.pending(), [batch(2)])
        self.assertEqual(self.received, [('/api/time/batches', 'Bearer test-selector.test-secret', dict(schema_version=2, batches=[batch()]))])
        self.assertEqual(worker.status()['code'], 'accepted')
