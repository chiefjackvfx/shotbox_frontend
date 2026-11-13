"""Storage contract; no live user state or credentials are used."""
import os
from pathlib import Path
import stat
import tempfile
import unittest


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_safe_path_rejects_traversal_relative_nul_and_symlink_ancestors(self):
        from nuke_time_calc import storage
        self.assertEqual(storage.safe_path(self.root / 'missing'), self.root / 'missing')
        for value in ['relative', self.root / '..' / 'elsewhere', str(self.root) + '\x00']:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                storage.safe_path(value)
        if os.name == 'posix':
            (self.root / 'link').symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError):
                storage.safe_path(self.root / 'link' / 'child')
        self.assertEqual(storage.supported_platform(), os.name in ('posix', 'nt'))

    @unittest.skipUnless(os.name == 'posix', 'POSIX mode and owner contract')
    def test_private_directories_create_missing_parents_without_changing_trusted_parent(self):
        from nuke_time_calc import storage
        os.chmod(self.root, 0o755)
        leaf = self.root / 'state' / 'queue'
        self.assertEqual(storage.private_directory(leaf), leaf)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        for path in [leaf.parent, leaf]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(storage.private_directory(leaf), leaf)
        broad = self.root / 'broad'
        broad.mkdir(mode=0o755)
        os.chmod(broad, 0o755)
        with self.assertRaises(ValueError):
            storage.private_directory(broad)
        with self.assertRaises(ValueError):
            storage.private_directory(self.root / 'state' / '..' / 'escape')

    def test_exclusive_create_and_bounded_private_read(self):
        from nuke_time_calc import storage
        target = self.root / 'secret'
        self.assertTrue(storage.create_private_file(target))
        self.assertFalse(storage.create_private_file(target))
        storage.check_private_file(target)
        self.assertEqual(storage.private_read(target), b'')
        target.write_bytes(b'not-a-real-credential')
        self.assertEqual(storage.private_read(target), b'not-a-real-credential')
        with self.assertRaises(ValueError):
            storage.private_read(target, max_bytes=4)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            os.chmod(target, 0o644)
            for operation in [storage.check_private_file, storage.private_read,
                              storage.create_private_file]:
                with self.subTest(operation=operation.__name__), self.assertRaises(ValueError):
                    operation(target)
            os.chmod(target, 0o600)
        linked = self.root / 'linked'
        os.link(target, linked)
        with self.assertRaises(ValueError):
            storage.check_private_file(target)
        with self.assertRaises(ValueError):
            storage.private_read(linked)
        with self.assertRaises(ValueError):
            storage.check_private_file(self.root)

    def test_atomic_write_replaces_whole_content_and_leaves_no_temp(self):
        from nuke_time_calc import storage
        target = self.root / 'private' / 'token'
        storage.atomic_write(target, b'first')
        storage.check_private_file(target)
        storage.atomic_write(target, b'second')
        self.assertEqual(storage.private_read(target), b'second')
        self.assertEqual(list(target.parent.iterdir()), [target])
        with self.assertRaises(TypeError):
            storage.atomic_write(target, 'not bytes')
        self.assertEqual(storage.private_read(target), b'second')

    @unittest.skipUnless(os.name == 'posix', 'POSIX startup mode preservation')
    def test_nonprivate_atomic_write_preserves_existing_startup_mode(self):
        from nuke_time_calc import storage
        target = self.root / 'menu.py'
        target.write_bytes(b'# user startup\n')
        os.chmod(target, 0o754)
        storage.atomic_write(target, b'# user startup\n# managed\n', private=False)
        self.assertEqual(target.read_bytes(), b'# user startup\n# managed\n')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o754)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_windows_path_syntax_rejects_devices_ads_and_ambiguous_components(self):
        from nuke_time_calc import storage
        for value in [r'\\server\share\token', r'\\?\C:\state\token',
                      r'\\.\pipe\token', r'C:state\token', r'\state\token',
                      r'C:\state\token:stream', r'C:\state\CON.txt',
                      r'C:\state\aux', r'C:\state\LPT1', r'C:\state\COM¹',
                      r'C:\state\CON .txt', r'C:\state\NUL .json',
                      r'C:\state\..\token', 'C:\\state\\trailing.',
                      'C:\\state\\trailing ', 'C:\\state\\line\nfeed']:
            with self.subTest(path=repr(value)), self.assertRaises(ValueError):
                storage._windows_path_syntax(value)
        storage._windows_path_syntax(r'C:\state\token.json')
        storage._windows_path_syntax('D:/safe state/token.json')

    def test_unsafe_atomic_targets_and_failed_flush_preserve_existing_data(self):
        from nuke_time_calc import storage
        from unittest.mock import patch
        target = self.root / 'state' / 'token'
        storage.atomic_write(target, b'unchanged')
        with patch.object(storage.os, 'fsync', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                storage.atomic_write(target, b'must-not-publish')
        self.assertEqual(storage.private_read(target), b'unchanged')
        self.assertEqual(list(target.parent.iterdir()), [target])
        linked = target.with_name('linked')
        os.link(target, linked)
        for private in [True, False]:
            with self.subTest(private=private), self.assertRaises(ValueError):
                storage.atomic_write(target, b'must-not-publish', private=private)
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_concurrent_exclusive_creation_and_atomic_rotation(self):
        from concurrent.futures import ThreadPoolExecutor
        from nuke_time_calc import storage
        directory = storage.private_directory(self.root / 'state')
        target = directory / 'token'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: storage.create_private_file(target), range(24)))
        self.assertEqual(results.count(True), 1)
        payloads = [bytes([i]) * 8192 for i in range(24)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda data: storage.atomic_write(target, data), payloads))
        self.assertIn(storage.private_read(target), payloads)
        storage.check_private_file(target)
        self.assertEqual(list(directory.iterdir()), [target])

    def test_independent_processes_exclusively_create_one_private_file(self):
        import subprocess
        import sys
        from nuke_time_calc import storage
        directory = storage.private_directory(self.root / 'state')
        target = directory / 'marker'
        code = ('import sys; from nuke_time_calc.storage import create_private_file; '
                'print(int(create_private_file(sys.argv[1])))')
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
        processes = [subprocess.Popen([sys.executable, '-c', code, str(target)], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(4)]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr.decode('utf-8', errors='replace'))
            outputs.append(stdout.strip())
        self.assertEqual(outputs.count(b'1'), 1)
        self.assertEqual(outputs.count(b'0'), 3)
        storage.check_private_file(target)

    @unittest.skipUnless(os.name == 'posix', 'POSIX unlinked-open inode semantics')
    def test_private_read_retries_an_inode_unlinked_by_atomic_rotation(self):
        from nuke_time_calc import storage
        from unittest.mock import patch
        target = self.root / 'token'
        storage.create_private_file(target)
        target.write_bytes(b'new-fixture')
        original = storage.os.fstat
        first = [True]

        def fstat(fd):
            info = original(fd)
            if first[0]:
                first[0] = False
                values = list(info)
                values[3] = 0  # old inode lost its final name after open()
                return os.stat_result(values)
            return info

        with patch.object(storage.os, 'fstat', side_effect=fstat):
            self.assertEqual(storage.private_read(target), b'new-fixture')
        self.assertFalse(first[0])


if __name__ == '__main__':
    unittest.main()
