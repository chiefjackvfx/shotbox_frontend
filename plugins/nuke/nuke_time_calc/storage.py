"""Local private storage, using only the Python standard library.

Existing ancestors are trusted and never chmod'ed. Callers must not put state
under a directory another user can rename or modify. Symlinks/reparse points
are rejected, not resolved. No module import opens files or loads Win32 DLLs.

POSIX commits flush the file and containing directory. Windows requires local
persistent ACLs, flushes file buffers, and uses same-volume MoveFileExW (with
WRITE_THROUGH) or ACL-preserving ReplaceFileW for existing nonsecret startup
files. Microsoft documents MOVEFILE_WRITE_THROUGH's flush guarantee for
copy/delete moves, not as a directory-fsync equivalent; ReplaceFileW does not
support its WRITE_THROUGH flag. Thus neither Windows path claims physical
power-cut namespace durability equivalent to POSIX fsync(directory). SQLite
callers must also enable FULL/EXTRA synchronous transactions themselves.
"""
import os
from pathlib import Path
import stat
from contextlib import contextmanager
from functools import lru_cache


@lru_cache(maxsize=1)
def _windows():
    # Never import/load Windows-specific modules while Nuke imports on POSIX.
    if os.name != 'nt':
        raise OSError('Win32 storage requires Windows')
    return _Windows()


class _FileRotated(Exception):
    """An opened inode lost its last name during concurrent atomic rotation."""


class _Windows:
    """ctypes bindings: HANDLEs are pointer-sized, Win32 DWORDs are 32-bit.

    References (Microsoft Learn API names): CreateFileW, GetTokenInformation
    (TokenUser), GetSecurityInfo, ConvertStringSecurityDescriptorToSecurityDescriptorW,
    GetFileInformationByHandle, MoveFileExW and ReplaceFileW.
    """
    READ_CONTROL = 0x00020000
    FILE_READ_ATTRIBUTES = 0x80
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    CREATE_NEW = 1
    REPARSE = 0x400
    DIRECTORY = 0x10
    # OPEN_REPARSE_POINT prevents following the final junction/symlink;
    # BACKUP_SEMANTICS is needed to inspect directories (no privileges enabled).
    OPEN_FLAGS = 0x00200000 | 0x02000000

    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.w = w
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.advapi = c.WinDLL('advapi32', use_last_error=True)
        pointer = c.c_void_p
        pp = c.POINTER(pointer)
        pd = c.POINTER(w.DWORD)
        self.invalid_handle = pointer(-1).value

        class SecurityAttributes(c.Structure):
            _fields_ = [('nLength', w.DWORD), ('lpSecurityDescriptor', pointer),
                        ('bInheritHandle', w.BOOL)]

        class FileInformation(c.Structure):
            _fields_ = [('dwFileAttributes', w.DWORD), ('ftCreationTime', w.FILETIME),
                        ('ftLastAccessTime', w.FILETIME), ('ftLastWriteTime', w.FILETIME),
                        ('dwVolumeSerialNumber', w.DWORD), ('nFileSizeHigh', w.DWORD),
                        ('nFileSizeLow', w.DWORD), ('nNumberOfLinks', w.DWORD),
                        ('nFileIndexHigh', w.DWORD), ('nFileIndexLow', w.DWORD)]

        class Acl(c.Structure):
            _fields_ = [('AclRevision', w.BYTE), ('Sbz1', w.BYTE),
                        ('AclSize', w.WORD), ('AceCount', w.WORD), ('Sbz2', w.WORD)]

        class Ace(c.Structure):
            _fields_ = [('AceType', w.BYTE), ('AceFlags', w.BYTE),
                        ('AceSize', w.WORD), ('Mask', w.DWORD), ('SidStart', w.DWORD)]

        self.SecurityAttributes, self.FileInformation = SecurityAttributes, FileInformation
        self.Acl, self.Ace = Acl, Ace
        sa = c.POINTER(SecurityAttributes)

        def bind(dll, name, result, args):
            func = getattr(dll, name)
            func.restype, func.argtypes = result, args
            setattr(self, name, func)

        bind(self.kernel, 'CreateFileW', w.HANDLE,
             [w.LPCWSTR, w.DWORD, w.DWORD, sa, w.DWORD, w.DWORD, w.HANDLE])
        bind(self.kernel, 'CreateDirectoryW', w.BOOL, [w.LPCWSTR, sa])
        bind(self.kernel, 'CloseHandle', w.BOOL, [w.HANDLE])
        bind(self.kernel, 'GetCurrentProcess', w.HANDLE, [])
        bind(self.kernel, 'GetFileInformationByHandle', w.BOOL,
             [w.HANDLE, c.POINTER(FileInformation)])
        bind(self.kernel, 'GetFileType', w.DWORD, [w.HANDLE])
        bind(self.kernel, 'FlushFileBuffers', w.BOOL, [w.HANDLE])
        bind(self.kernel, 'MoveFileExW', w.BOOL, [w.LPCWSTR, w.LPCWSTR, w.DWORD])
        bind(self.kernel, 'ReplaceFileW', w.BOOL,
             [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, pointer, pointer])
        bind(self.kernel, 'GetDriveTypeW', w.UINT, [w.LPCWSTR])
        bind(self.kernel, 'GetVolumeInformationW', w.BOOL,
             [w.LPCWSTR, w.LPWSTR, w.DWORD, pd, pd, pd, w.LPWSTR, w.DWORD])
        bind(self.kernel, 'LocalFree', pointer, [pointer])
        bind(self.advapi, 'OpenProcessToken', w.BOOL, [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)])
        bind(self.advapi, 'GetTokenInformation', w.BOOL,
             [w.HANDLE, c.c_int, pointer, w.DWORD, pd])
        bind(self.advapi, 'ConvertSidToStringSidW', w.BOOL, [pointer, pp])
        bind(self.advapi, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', w.BOOL,
             [w.LPCWSTR, w.DWORD, pp, pd])
        bind(self.advapi, 'GetSecurityInfo', w.DWORD,
             [w.HANDLE, c.c_int, w.DWORD, pp, pp, pp, pp, pp])
        bind(self.advapi, 'IsValidAcl', w.BOOL, [pointer])
        bind(self.advapi, 'IsValidSid', w.BOOL, [pointer])
        bind(self.advapi, 'GetAce', w.BOOL, [pointer, w.DWORD, pp])
        self.user_sid = self._current_user_sid()

    def ok(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    def _sid_text(self, sid):
        if not sid or not self.IsValidSid(sid):
            raise ValueError('invalid storage owner or ACL SID')
        text = self.c.c_void_p()
        self.ok(self.ConvertSidToStringSidW(sid, self.c.byref(text)))
        try:
            return self.c.wstring_at(text.value)
        finally:
            self.LocalFree(text)

    def _current_user_sid(self):
        token = self.w.HANDLE()
        self.ok(self.OpenProcessToken(self.GetCurrentProcess(), 0x0008, self.c.byref(token)))
        try:
            size = self.w.DWORD()
            self.GetTokenInformation(token, 1, None, 0, self.c.byref(size))
            if not size.value or self.c.get_last_error() != 122:  # INSUFFICIENT_BUFFER
                raise self.c.WinError(self.c.get_last_error())
            buffer = self.c.create_string_buffer(size.value)
            self.ok(self.GetTokenInformation(token, 1, buffer, size, self.c.byref(size)))
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is PSID.
            return self._sid_text(self.c.cast(buffer, self.c.POINTER(self.c.c_void_p))[0])
        finally:
            self.CloseHandle(token)

    @contextmanager
    def security(self, directory=False):
        inherit = 'OICI' if directory else ''
        descriptor = self.c.c_void_p()
        # Set the owner explicitly: an elevated administrator's default owner
        # can be the Administrators group rather than TokenUser.
        sddl = 'O:{0}D:P(A;{1};FA;;;{0})(A;{1};FA;;;SY)(A;{1};FA;;;BA)'.format(
            self.user_sid, inherit)
        self.ok(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, self.c.byref(descriptor), None))
        attributes = self.SecurityAttributes(self.c.sizeof(self.SecurityAttributes), descriptor, False)
        try:
            yield self.c.byref(attributes)
        finally:
            self.LocalFree(descriptor)

    def local_volume(self, path):
        root = path.anchor
        # Fail closed for DRIVE_UNKNOWN/NO_ROOT_DIR/REMOTE/CDROM etc. Local
        # fixed and removable disks are accepted only when persistent ACLs exist.
        if self.GetDriveTypeW(root) not in (2, 3):
            raise ValueError('private storage requires a local disk')
        flags = self.w.DWORD()
        self.ok(self.GetVolumeInformationW(root, None, 0, None, None,
                                          self.c.byref(flags), None, 0))
        if not flags.value & 0x00000008:  # FILE_PERSISTENT_ACLS
            raise ValueError('private storage requires persistent filesystem ACLs')

    @contextmanager
    def handle(self, path, access=None, creation=OPEN_EXISTING, attributes=None):
        if access is None:
            access = self.READ_CONTROL | self.FILE_READ_ATTRIBUTES
        handle = self.CreateFileW(str(path), access, 1 | 2 | 4, attributes, creation,
                                  self.OPEN_FLAGS, None)
        if handle == self.invalid_handle:
            raise self.c.WinError(self.c.get_last_error())
        try:
            yield handle
        finally:
            self.CloseHandle(handle)

    def information(self, handle, directory=None):
        info = self.FileInformation()
        self.ok(self.GetFileInformationByHandle(handle, self.c.byref(info)))
        if info.dwFileAttributes & self.REPARSE:
            raise ValueError('reparse storage paths are forbidden')
        if self.GetFileType(handle) != 1:  # FILE_TYPE_DISK
            raise ValueError('storage must be a disk file or directory')
        if directory is not None:
            if bool(info.dwFileAttributes & self.DIRECTORY) != directory:
                raise ValueError('unexpected storage object type')
            if not directory and info.nNumberOfLinks == 0:
                raise _FileRotated()
            if not directory and info.nNumberOfLinks != 1:
                raise ValueError('hardlinked storage files are forbidden')
        return info

    def private_acl(self, handle):
        owner, dacl, descriptor = (self.c.c_void_p() for _ in range(3))
        error = self.GetSecurityInfo(handle, 1, 1 | 4, self.c.byref(owner), None,
                                     self.c.byref(dacl), None, self.c.byref(descriptor))
        if error:
            raise self.c.WinError(error)
        try:
            if self._sid_text(owner) != self.user_sid:
                raise ValueError('storage owner must be the current user')
            if not dacl.value or not self.IsValidAcl(dacl):
                raise ValueError('storage requires a valid non-null DACL')
            acl = self.c.cast(dacl, self.c.POINTER(self.Acl)).contents
            seen_user = False
            for index in range(acl.AceCount):
                pointer = self.c.c_void_p()
                self.ok(self.GetAce(dacl, index, self.c.byref(pointer)))
                ace = self.c.cast(pointer, self.c.POINTER(self.Ace)).contents
                # Reject deny/object/callback ACEs rather than attempting an
                # incomplete effective-rights calculation. Inherit-only broad
                # ACEs also fail (they could expose future SQLite journals).
                if ace.AceType != 0 or ace.AceSize < self.c.sizeof(self.Ace):
                    raise ValueError('unsupported storage ACL entry')
                sid = self._sid_text(pointer.value + self.Ace.SidStart.offset)
                if sid not in (self.user_sid, 'S-1-5-18', 'S-1-5-32-544'):
                    raise ValueError('storage DACL grants access outside private principals')
                if sid == self.user_sid and not ace.AceFlags & 0x08 and ace.Mask:
                    seen_user = True
            if not seen_user:
                raise ValueError('storage DACL must grant current-user access')
        finally:
            self.LocalFree(descriptor)

    def inspect(self, path, directory=False):
        self.local_volume(path)
        for _ in range(32):
            try:
                with self.handle(path) as handle:
                    self.information(handle, directory=directory)
                    self.private_acl(handle)
                return
            except _FileRotated:
                continue
        raise OSError('storage changed repeatedly during private inspection')

    def mkdir(self, path):
        self.local_volume(path)
        with self.security(directory=True) as attributes:
            if not self.CreateDirectoryW(str(path), attributes):
                error = self.c.get_last_error()
                if error != 183:  # ERROR_ALREADY_EXISTS
                    raise self.c.WinError(error)
        self.inspect(path, directory=True)

    def create(self, path):
        self.local_volume(path)
        try:
            with self.security() as attributes:
                with self.handle(path, self.GENERIC_WRITE | self.READ_CONTROL,
                                 self.CREATE_NEW, attributes) as handle:
                    self.information(handle, directory=False)
                    self.private_acl(handle)
                    self.ok(self.FlushFileBuffers(handle))
        except OSError as exc:
            if exc.winerror not in (80, 183):
                raise
            self.inspect(path)
            return False
        return True

    def open_fd(self, path, write=False):
        for _ in range(32):
            try:
                return self._open_fd_once(path, write)
            except _FileRotated:
                continue
        raise OSError('storage changed repeatedly during private open')

    def _open_fd_once(self, path, write=False):
        """Transfer a single native HANDLE to the CRT; never double-close it."""
        import msvcrt
        self.local_volume(path)
        access = self.GENERIC_WRITE if write else self.GENERIC_READ
        handle = self.CreateFileW(str(path), access | self.READ_CONTROL, 1 | 2 | 4,
                                  None, self.OPEN_EXISTING, self.OPEN_FLAGS, None)
        if handle == self.invalid_handle:
            raise self.c.WinError(self.c.get_last_error())
        try:
            self.information(handle, directory=False)
            self.private_acl(handle)
            flags = (os.O_WRONLY if write else os.O_RDONLY) | os.O_BINARY | os.O_NOINHERIT
            fd = msvcrt.open_osfhandle(handle, flags)
        except BaseException:
            self.CloseHandle(handle)
            raise
        # open_osfhandle takes ownership. os.close/fdopen now closes HANDLE.
        return fd

    def replace(self, source, target, private):
        if not private and target.exists():
            # ReplaceFile preserves the existing startup DACL. Neither of the
            # IGNORE_* flags is safe: an ACL-merge failure must fail closed.
            # REPLACEFILE_WRITE_THROUGH is explicitly unsupported by Microsoft.
            self.ok(self.ReplaceFileW(str(target), str(source), None, 0, None, None))
        else:
            # Sibling path, no COPY_ALLOWED: cannot degrade to copy/delete.
            # WRITE_THROUGH's documented flush guarantee is for copy/delete;
            # this is NOT a promise of POSIX directory-fsync power-cut safety.
            # For new nonprivate files do not overwrite a concurrent creator:
            # doing so would discard that creator's unrelated startup ACL.
            flags = 0x8 | (0x1 if private else 0)
            self.ok(self.MoveFileExW(str(source), str(target), flags))


def supported_platform():
    return os.name in ('posix', 'nt')


def _windows_path_syntax(raw):
    """Reject Win32 normalization aliases before Path can erase components."""
    from pathlib import PureWindowsPath
    value = raw.replace('/', '\\')
    path = PureWindowsPath(value)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':'
            or path.drive[0] not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'):
        raise ValueError('a local drive-absolute Windows path is required')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$', 'CLOCK$'}
    reserved.update(prefix + digit for prefix in ('COM', 'LPT')
                    for digit in '123456789¹²³')
    for part in value[3:].split('\\'):
        if (part in ('.', '..') or part.endswith(('.', ' '))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split('.')[0].rstrip(' ').upper() in reserved):
            raise ValueError('unsafe Windows path component')


def safe_path(path):
    raw = os.fspath(path)
    if not isinstance(raw, str) or '\x00' in raw:
        raise ValueError('expected a text path without NUL')
    if not supported_platform():
        raise OSError('private storage supports POSIX and Windows only')
    if os.name == 'nt':
        _windows_path_syntax(raw)
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('expected an absolute non-traversing path')
    for part in (path,) + tuple(path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('symlink storage paths are forbidden')
        if os.name == 'nt':
            backend = _windows()
            with backend.handle(part, access=backend.FILE_READ_ATTRIBUTES) as handle:
                backend.information(handle)
    return path


def sync_directory(path):
    """POSIX entry durability. Windows has no equivalent directory-fsync promise."""
    path = safe_path(path)
    if os.name == 'nt':
        # FlushFileBuffers on a directory is not a supported POSIX fsync
        # substitute. Files are flushed before same-volume replacement; the
        # remaining physical power-loss/namespace persistence gap is explicit.
        backend = _windows()
        with backend.handle(path, access=backend.FILE_READ_ATTRIBUTES) as handle:
            backend.information(handle, directory=True)
        return
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _posix_private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_mode & 0o077
            or info.st_uid != os.getuid() or (not directory and info.st_nlink != 1)):
        raise ValueError('storage must be private, owned, regular and singly linked')


def private_directory(path):
    path = safe_path(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        if os.name == 'nt':
            _windows().mkdir(directory)
            continue
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        safe_path(directory)
        _posix_private(directory.lstat(), directory=True)
        sync_directory(directory.parent)
    if os.name == 'nt':
        _windows().inspect(path, directory=True)
    else:
        _posix_private(path.lstat(), directory=True)
    return path


def _private_fd(path):
    if os.name == 'nt':
        return _windows().open_fd(path)
    for _ in range(32):
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if info.st_nlink != 0:
                _posix_private(info)
                return fd
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
    raise OSError('storage changed repeatedly during private open')


def check_private_file(path):
    path = safe_path(path)
    if os.name == 'nt':
        _windows().inspect(path)
        return
    fd = _private_fd(path)
    os.close(fd)


def private_read(path, max_bytes=65536):
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('max_bytes must be a nonnegative integer')
    path = safe_path(path)
    with os.fdopen(_private_fd(path), 'rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('private file exceeds size limit')
    return data


def create_private_file(path):
    path = safe_path(path)
    if os.name == 'nt':
        return _windows().create(path)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        check_private_file(path)
        return False
    try:
        _posix_private(os.fstat(fd))
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)
    return True


def atomic_write(path, data, mode=0o600, private=True):
    """Flush a unique sibling before replacement; never follow a linked target."""
    import uuid
    if not isinstance(data, bytes):
        raise TypeError('atomic storage writes require bytes')
    if private and mode & 0o077:
        raise ValueError('private mode cannot grant group/other access')
    path = safe_path(path)
    if private:
        private_directory(path.parent)
    else:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        safe_path(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if private:
            check_private_file(path)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('startup target must be a regular singly linked file')
        else:
            mode = stat.S_IMODE(info.st_mode)
    name = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    created = False
    try:
        if os.name == 'nt':
            backend = _windows()
            # CREATE_NEW with SECURITY_ATTRIBUTES applies protection at
            # creation: no inherited-broad-ACL interval, even for the temp.
            if not backend.create(name):
                raise FileExistsError('atomic sibling already exists')
            created = True
            fd = backend.open_fd(name, write=True)
        else:
            fd = os.open(str(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            created = True
        with os.fdopen(fd, 'wb') as stream:
            if os.name != 'nt':
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            if os.name == 'nt':
                import msvcrt
                backend.ok(backend.FlushFileBuffers(msvcrt.get_osfhandle(stream.fileno())))
        safe_path(path)
        if os.name == 'nt':
            backend.replace(name, path, private)
        else:
            os.replace(str(name), str(path))
        sync_directory(path.parent)
    finally:
        if created:
            try:
                name.unlink()
            except FileNotFoundError:
                pass
