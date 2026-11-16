"""Native Win32 acceptance tests (not simulated by POSIX chmod/mocks).

Run on Windows from the plugin parent with a disposable local NTFS TMPDIR:
  python -m unittest discover -s nuke_time_calc/tests -p 'test_storage*.py' -v
No secrets, user startup folders, network shares or credentials are required.
"""
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'requires native Windows ACL and handle APIs')
class NativeWindowsStorageTests(unittest.TestCase):
    def setUp(self):
        from nuke_time_calc import storage
        self.storage = storage
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_private_directory_and_file_have_protected_current_user_acl_at_creation(self):
        storage = self.storage
        directory = storage.private_directory(self.root / 'state' / 'queue')
        target = directory / 'token'
        self.assertTrue(storage.create_private_file(target))
        storage.check_private_file(target)
        # Read real security descriptors independently with PowerShell/.NET;
        # SID comparisons avoid localized account-name assumptions.
        import subprocess
        script = r'''
$p = $env:SHOTBOX_STORAGE_TEST_TARGET
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($name in @($p, (Split-Path -Parent $p))) {
  $acl = Get-Acl -LiteralPath $name
  if (-not $acl.AreAccessRulesProtected) { throw 'DACL is not protected' }
  if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $identity) {
    throw 'unexpected owner'
  }
  $rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
  $seen = @()
  foreach ($rule in $rules) {
    if ($rule.IdentityReference.Value -notin @($identity, 'S-1-5-18', 'S-1-5-32-544')) {
      throw 'broad ACE'
    }
    if ($rule.AccessControlType -ne 'Allow') { throw 'unexpected ACE type' }
    $seen += $rule.IdentityReference.Value
  }
  if ($identity -notin $seen) { throw 'user ACE missing' }
}
'''
        env = dict(os.environ, SHOTBOX_STORAGE_TEST_TARGET=str(target))
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                 '-Command', "$ErrorActionPreference='Stop';" + script],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def powershell(self, script, **paths):
        import subprocess
        env = dict(os.environ)
        env.update({'SHOTBOX_TEST_' + name.upper(): str(value) for name, value in paths.items()})
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                 '-Command', "$ErrorActionPreference='Stop';" + script],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_rejects_broad_acl_without_chmod_shortcut(self):
        storage = self.storage
        target = self.root / 'token'
        storage.create_private_file(target)
        self.powershell(r'''
$p=$env:SHOTBOX_TEST_TARGET
$a=Get-Acl -LiteralPath $p
$r=New-Object System.Security.AccessControl.FileSystemAccessRule(
    [System.Security.Principal.SecurityIdentifier]'S-1-1-0', 'Read', 'Allow')
$a.AddAccessRule($r)
Set-Acl -LiteralPath $p -AclObject $a
''', target=target)
        with self.assertRaises(ValueError):
            storage.check_private_file(target)
        with self.assertRaises(ValueError):
            storage.private_read(target)
        with self.assertRaises(ValueError):
            storage.create_private_file(target)

    def test_private_reads_share_delete_and_keep_original_open_handle(self):
        storage = self.storage
        target = self.root / 'state' / 'token'
        storage.atomic_write(target, b'old-test-value')
        # The native fd must own (and close exactly once) its HANDLE. Keeping
        # this handle open must not prevent another process rotating the token.
        fd = storage._private_fd(target)
        try:
            storage.atomic_write(target, b'new-test-value')
            self.assertEqual(os.read(fd, 100), b'old-test-value')
            self.assertEqual(storage.private_read(target), b'new-test-value')
        finally:
            os.close(fd)
        storage.check_private_file(target)

    def test_nonprivate_replace_preserves_existing_startup_dacl(self):
        storage = self.storage
        target = self.root / 'menu.py'
        target.write_bytes(b'# user startup\n')
        self.powershell(r'''
$p=$env:SHOTBOX_TEST_TARGET
$a=Get-Acl -LiteralPath $p
$a.SetAccessRuleProtection($true, $true)
$r=New-Object System.Security.AccessControl.FileSystemAccessRule(
    [System.Security.Principal.SecurityIdentifier]'S-1-5-32-545', 'ReadAndExecute', 'Allow')
$a.AddAccessRule($r)
Set-Acl -LiteralPath $p -AclObject $a
''', target=target)
        before = self.powershell('(Get-Acl -LiteralPath $env:SHOTBOX_TEST_TARGET).Sddl', target=target)
        storage.atomic_write(target, b'# user startup\n# managed\n', private=False)
        self.assertEqual(target.read_bytes(), b'# user startup\n# managed\n')
        after = self.powershell('(Get-Acl -LiteralPath $env:SHOTBOX_TEST_TARGET).Sddl', target=target)
        self.assertEqual(after, before)
        with self.assertRaises(ValueError):
            storage.check_private_file(target)

    def test_security_is_private_immediately_when_native_create_returns(self):
        from unittest.mock import patch
        backend = self.storage._windows()
        original_file = backend.CreateFileW
        original_directory = backend.CreateDirectoryW
        observed = []

        def file_create(path, access, sharing, attributes, disposition, flags, template):
            handle = original_file(path, access, sharing, attributes, disposition, flags, template)
            if disposition == backend.CREATE_NEW and handle != backend.invalid_handle:
                self.assertIsNotNone(attributes)
                backend.information(handle, directory=False)
                backend.private_acl(handle)
                observed.append('file')
            return handle

        def directory_create(path, attributes):
            result = original_directory(path, attributes)
            if result:
                self.assertIsNotNone(attributes)
                # The native API receives an extended path; inspect accepts
                # the ordinary validated local path used by the public API.
                backend.inspect(Path(path[4:]), directory=True)
                observed.append('directory')
            return result

        # These wrappers call the real native APIs and inspect real returned
        # objects before production code can perform any later ACL operation.
        with patch.object(backend, 'CreateFileW', side_effect=file_create), \
                patch.object(backend, 'CreateDirectoryW', side_effect=directory_create):
            directory = self.storage.private_directory(self.root / 'private')
            self.storage.create_private_file(directory / 'created')
            self.storage.atomic_write(directory / 'token', b'fixture-only')
        self.assertIn('directory', observed)
        self.assertGreaterEqual(observed.count('file'), 2)

    def test_reparse_ancestors_and_hardlinks_are_rejected(self):
        import subprocess
        storage = self.storage
        real = storage.private_directory(self.root / 'real')
        junction = self.root / 'junction'
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(real)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)
        with self.assertRaises(ValueError):
            storage.safe_path(junction / 'token')
        with self.assertRaises(ValueError):
            storage.private_directory(junction / 'nested')
        target = real / 'token'
        storage.create_private_file(target)
        os.link(target, real / 'hardlink')
        with self.assertRaises(ValueError):
            storage.private_read(target)

    def test_null_dacl_is_rejected(self):
        storage = self.storage
        target = self.root / 'token'
        storage.create_private_file(target)
        backend = storage._windows()
        c, w = backend.c, backend.w
        set_info = backend.advapi.SetSecurityInfo
        set_info.argtypes = [w.HANDLE, c.c_int, w.DWORD] + [c.c_void_p] * 4
        set_info.restype = w.DWORD
        with backend.handle(target, access=0x00040000) as handle:  # WRITE_DAC
            error = set_info(handle, 1, 4 | 0x80000000, None, None, None, None)
            self.assertEqual(error, 0)
        with self.assertRaises(ValueError):
            storage.check_private_file(target)

    def test_failure_opening_native_temp_does_not_leave_a_sibling(self):
        from unittest.mock import patch
        storage = self.storage
        directory = storage.private_directory(self.root / 'state')
        target = directory / 'token'
        with patch.object(storage._windows(), 'open_fd', side_effect=OSError('fixture open failure')):
            with self.assertRaises(OSError):
                storage.atomic_write(target, b'fixture-only')
        self.assertEqual(list(directory.iterdir()), [])

    def test_long_paths_support_private_state_and_reopening_outbox(self):
        import shutil
        from nuke_time_calc.outbox import Outbox
        from test_outbox import batch
        storage = self.storage
        self.addCleanup(shutil.rmtree, str(storage.filesystem_path(self.root)))
        root = self.root.joinpath(*(['nested folder-' + 'a' * 60] * 5))
        self.assertGreater(len(str(root)), 260)
        directory = storage.private_directory(root)
        token = directory / 'token'
        storage.atomic_write(token, b'fixture-first')
        storage.atomic_write(token, b'fixture-second')
        self.assertEqual(storage.private_read(token), b'fixture-second')
        binding = dict(server='https://shotbox.test', artist_id=1, device_id='test-device')
        outbox = Outbox(directory / 'queue', **binding)
        outbox.put(batch())
        reopened = Outbox(directory / 'queue', **binding)
        self.assertEqual(reopened.pending(), [batch()])
        self.assertEqual(reopened.status(), outbox.status())
        self.assertTrue(storage.filesystem_path(outbox.path).is_file())


if __name__ == '__main__':
    unittest.main()
