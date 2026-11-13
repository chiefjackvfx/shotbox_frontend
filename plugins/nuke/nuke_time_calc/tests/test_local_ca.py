"""Additional local CA trust tested against isolated, genuinely signed TLS."""
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import test_transport
from test_outbox import batch


class LocalCATests(unittest.TestCase):
    # Reuse the existing real receiver/acknowledgement fixture, not its tests.
    setUp = test_transport.TransportTests.setUp
    close_server = test_transport.TransportTests.close_server
    queue = test_transport.TransportTests.queue

    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            raise unittest.SkipTest('openssl required for isolated TLS fixture')
        cls.cert_temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        cls.addClassCleanup(cls.cert_temp.cleanup)
        cls.certs = Path(cls.cert_temp.name)
        def openssl(*args):
            subprocess.run(['openssl', *args], cwd=cls.certs, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for name in ('ca', 'wrong-ca'):
            openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', name + '.key', '-out', name + '.pem', '-days', '1',
                    '-subj', '/CN=' + name, '-addext', 'basicConstraints=critical,CA:TRUE',
                    '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        openssl('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'server.key',
                '-out', 'server.csr', '-subj', '/CN=127.0.0.1')
        (cls.certs / 'extensions').write_text(
            'subjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\n'
            'keyUsage=critical,digitalSignature,keyEncipherment\n'
            'extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\n'
            'authorityKeyIdentifier=keyid,issuer\n')
        openssl('x509', '-req', '-in', 'server.csr', '-CA', 'ca.pem',
                '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'server.pem',
                '-days', '1', '-extfile', 'extensions')

    def tls_origin(self, hostname='127.0.0.1'):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.certs / 'server.pem'), str(self.certs / 'server.key'))
        server = ThreadingHTTPServer(('127.0.0.1', 0), self.server.RequestHandlerClass)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(close)
        return 'https://%s:%d' % (hostname, server.server_port)

    def test_configured_additional_ca_delivers_and_acknowledges_real_tls(self):
        from nuke_time_calc.transport import Uploader
        queue = self.queue(self.tls_origin())
        queue.put(batch())
        worker = Uploader(queue, token_file=self.token, ca_file=str(self.certs / 'ca.pem'))
        self.assertTrue(worker.run_once())
        self.assertEqual(worker.status()['code'], 'accepted')
        self.assertEqual(queue.pending(), [])
        self.assertEqual(len(self.received), 1)

    def test_invalid_ca_configuration_fails_before_delivery_even_with_injected_transport(self):
        from nuke_time_calc.transport import Uploader
        malformed = self.root / 'malformed.pem'
        malformed.write_text('not a certificate')
        link = self.root / 'linked.pem'
        link.symlink_to(self.certs / 'ca.pem')
        linked_dir = self.root / 'linked-directory'
        linked_dir.symlink_to(self.certs, target_is_directory=True)
        queue = self.queue(self.tls_origin())
        for ca_file in ('relative.pem', '', '/bad\x00.pem', self.root / 'missing.pem',
                        self.root, malformed, link, linked_dir / 'ca.pem', 42):
            with self.subTest(ca_file=repr(ca_file)):
                with self.assertRaisesRegex(ValueError, '^tls_configuration_error$'):
                    Uploader(queue, token_file=self.root / 'unread-token', ca_file=ca_file,
                             transport=lambda *args: self.fail('must not deliver'))
        self.assertEqual(self.received, [])


class CAConfigTests(unittest.TestCase):
    def test_additional_ca_is_optional_and_round_trips_safe_absolute_path(self):
        from nuke_time_calc.managed_setup import save_config, load_config
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as folder:
            root = Path(folder)
            path = root / 'config.json'
            ca_file = str(root / 'public-ca.pem')
            save_config(dict(enabled=False, ca_file=ca_file), path)
            self.assertEqual(load_config(path)['ca_file'], ca_file)
            before = path.read_bytes()
            for value in ('relative.pem', '', '/bad\x00.pem', str(root / '..' / 'ca.pem')):
                with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                    save_config(dict(enabled=False, ca_file=value), path)
                self.assertEqual(path.read_bytes(), before)
            directory = root / 'certs'
            directory.mkdir()
            linked = root / 'linked'
            linked.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                save_config(dict(enabled=False, ca_file=str(linked / 'ca.pem')), path)
            with self.assertRaises(ValueError):
                save_config(dict(enabled=False, ca_file=ca_file, verify_tls=False), path)
            save_config(dict(enabled=False), path)
            self.assertNotIn('ca_file', load_config(path))

    def test_runtime_factory_forwards_ca_only_when_explicitly_configured(self):
        from nuke_time_calc.runtime import build_runtime
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as folder:
            config = dict(server='https://shotbox.test', artist_id=1, device_id='device',
                          token_file=folder + '/token', outbox_dir=folder + '/outbox',
                          ca_file=folder + '/public-ca.pem')
            captured = []
            def factory(outbox, **options):
                captured.append(options)
                return SimpleNamespace()
            build_runtime(config, SimpleNamespace(), uploader_factory=factory)
            self.assertEqual(captured, [dict(token_file=config['token_file'],
                                            ca_file=config['ca_file'])])
            del config['ca_file']
            # Old injected factories do not need to accept the new optional keyword.
            build_runtime(config, SimpleNamespace(),
                          uploader_factory=lambda outbox, *, token_file: SimpleNamespace())

    def test_ca_addition_removal_or_change_requires_runtime_restart(self):
        from nuke_time_calc.runtime import BoundResolver
        for before, after in ((None, '/public-ca.pem'), ('/old.pem', '/new.pem'),
                              ('/old.pem', None)):
            config = dict(enabled=True, mappings=[])
            if before is not None:
                config['ca_file'] = before
            current = dict(config)
            resolve = BoundResolver(config, None, loader=lambda path: current)
            self.assertIsNone(resolve('/shots/comp.nk'))
            if after is None:
                del current['ca_file']
            else:
                current['ca_file'] = after
            with self.subTest(before=before, after=after):
                with self.assertRaisesRegex(ValueError, 'restart required'):
                    resolve('/shots/comp.nk')
