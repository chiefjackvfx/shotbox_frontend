"""Windows visible/private displays, targeted messages, and owned process jobs.

No desktop switching, foreground activation, global input, or administrator
privileges are used. Win32 imports are lazy so Linux can test the adapter.
"""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from nuke_denoise_display import DenoiseCancelled, PrivateDisplay, child_environment, stop_process

DWORD, LONG, BOOL = C.c_uint32, C.c_int32, C.c_int32
HANDLE, SIZE_T = C.c_void_p, C.c_size_t


class WindowNotReady(RuntimeError):
    def __init__(self, hwnd, width, height):
        self.hwnd, self.width, self.height = int(hwnd), width, height
        super().__init__(f"Window {self.hwnd} has an empty client area ({width} x {height}).")


class WindowObscured(RuntimeError):
    pass


class Point(C.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class Rect(C.Structure):
    _fields_ = [(name, LONG) for name in ("left", "top", "right", "bottom")]


class BitmapInfo(C.Structure):
    _fields_ = [("size", DWORD), ("width", LONG), ("height", LONG),
                ("planes", C.c_uint16), ("bits", C.c_uint16), ("compression", DWORD),
                ("image_size", DWORD), ("xppm", LONG), ("yppm", LONG),
                ("colors_used", DWORD), ("colors_important", DWORD), ("color", DWORD)]


class BasicLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", DWORD),
                ("minimum_working_set", SIZE_T), ("maximum_working_set", SIZE_T),
                ("active_process_limit", DWORD), ("affinity", SIZE_T),
                ("priority_class", DWORD), ("scheduling_class", DWORD)]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic", BasicLimits), ("io", C.c_uint64 * 6),
                ("process_memory", SIZE_T), ("job_memory", SIZE_T),
                ("peak_process_memory", SIZE_T), ("peak_job_memory", SIZE_T)]


class StartupInfo(C.Structure):
    _fields_ = [("size", DWORD), ("reserved", C.c_wchar_p), ("desktop", C.c_wchar_p), ("title", C.c_wchar_p),
                *[(name, DWORD) for name in ("x", "y", "width", "height", "columns", "rows", "fill", "flags")],
                ("show", C.c_uint16), ("reserved_size", C.c_uint16), ("reserved_pointer", HANDLE),
                ("stdin", HANDLE), ("stdout", HANDLE), ("stderr", HANDLE)]


class StartupInfoEx(C.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", HANDLE)]


class ProcessInfo(C.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class WindowsProcess:
    """The small process interface used by the denoise supervisor."""

    def __init__(self, api, info, command):
        self.api, self.info, self.args = api, info, command
        self.pid, self.returncode = info.pid, None

    def resume(self):
        if self.api.kernel.ResumeThread(self.info.thread) == 0xFFFFFFFF:
            self.api.checked(False, "ResumeThread")
        self.api.kernel.CloseHandle(self.info.thread)
        self.info.thread = None

    def poll(self):
        if self.returncode is None:
            # WaitForSingleObject distinguishes a running process from an exit
            # whose actual code happens to be STILL_ACTIVE (259).
            status = self.api.kernel.WaitForSingleObject(self.info.process, 0)
            if status == 0xFFFFFFFF:
                self.api.checked(False, "WaitForSingleObject")
            if status == 0:
                code = DWORD()
                self.api.checked(self.api.kernel.GetExitCodeProcess(self.info.process, C.byref(code)), "GetExitCodeProcess")
                self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            milliseconds = 0xFFFFFFFF if timeout is None else max(0, min(int(timeout * 1000), 0xFFFFFFFE))
            status = self.api.kernel.WaitForSingleObject(self.info.process, milliseconds)
            if status == 0x102:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if status != 0:
                self.api.checked(False, "WaitForSingleObject")
            self.poll()
        return self.returncode

    def terminate(self):
        if self.poll() is None:
            if not self.api.kernel.TerminateProcess(self.info.process, 1) and self.poll() is None:
                self.api.checked(False, "TerminateProcess")

    kill = terminate

    def close(self):
        self.wait(timeout=5)
        for name in ("thread", "process"):
            handle = getattr(self.info, name)
            if handle:
                self.api.kernel.CloseHandle(handle)
                setattr(self.info, name, None)


class WindowsAPI:
    """Pointer-sized signatures are explicit, including on 64-bit Python."""

    def __init__(self):
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.gdi = C.WinDLL("gdi32", use_last_error=True)
        self.callback_type = C.WINFUNCTYPE(BOOL, HANDLE, C.c_ssize_t)

        def bind(dll, name, result, *arguments):
            function = getattr(dll, name)
            function.restype, function.argtypes = result, list(arguments)

        bind(self.user, "CreateDesktopW", HANDLE, C.c_wchar_p, C.c_wchar_p, HANDLE, DWORD, DWORD, HANDLE)
        bind(self.user, "GetThreadDesktop", HANDLE, DWORD)
        bind(self.user, "SetThreadDesktop", BOOL, HANDLE)
        bind(self.user, "CloseDesktop", BOOL, HANDLE)
        bind(self.user, "EnumDesktopWindows", BOOL, HANDLE, self.callback_type, C.c_ssize_t)
        bind(self.user, "EnumChildWindows", BOOL, HANDLE, self.callback_type, C.c_ssize_t)
        bind(self.user, "IsWindowVisible", BOOL, HANDLE)
        bind(self.user, "IsWindowEnabled", BOOL, HANDLE)
        bind(self.user, "IsIconic", BOOL, HANDLE)
        bind(self.user, "ClientToScreen", BOOL, HANDLE, C.POINTER(Point))
        bind(self.user, "WindowFromPoint", HANDLE, Point)
        bind(self.user, "GetAncestor", HANDLE, HANDLE, C.c_uint)
        bind(self.user, "GetWindowThreadProcessId", DWORD, HANDLE, C.POINTER(DWORD))
        bind(self.user, "GetWindow", HANDLE, HANDLE, C.c_uint)
        bind(self.user, "GetWindowTextLengthW", C.c_int, HANDLE)
        bind(self.user, "GetWindowTextW", C.c_int, HANDLE, C.c_wchar_p, C.c_int)
        bind(self.user, "GetClassNameW", C.c_int, HANDLE, C.c_wchar_p, C.c_int)
        bind(self.user, "GetClientRect", BOOL, HANDLE, C.POINTER(Rect))
        bind(self.user, "GetDC", HANDLE, HANDLE)
        bind(self.user, "ReleaseDC", C.c_int, HANDLE, HANDLE)
        bind(self.user, "SendMessageTimeoutW", C.c_ssize_t, HANDLE, C.c_uint, SIZE_T, C.c_ssize_t,
             C.c_uint, C.c_uint, C.POINTER(SIZE_T))
        bind(self.user, "PostMessageW", BOOL, HANDLE, C.c_uint, SIZE_T, C.c_ssize_t)
        bind(self.user, "ChildWindowFromPointEx", HANDLE, HANDLE, Point, C.c_uint)
        bind(self.user, "MapWindowPoints", C.c_int, HANDLE, HANDLE, C.POINTER(Point), C.c_uint)
        bind(self.kernel, "GetCurrentThreadId", DWORD)
        bind(self.kernel, "GetCurrentProcess", HANDLE)
        bind(self.kernel, "CreateJobObjectW", HANDLE, HANDLE, C.c_wchar_p)
        bind(self.kernel, "SetInformationJobObject", BOOL, HANDLE, C.c_int, HANDLE, DWORD)
        bind(self.kernel, "TerminateJobObject", BOOL, HANDLE, C.c_uint)
        bind(self.kernel, "CloseHandle", BOOL, HANDLE)
        bind(self.kernel, "DuplicateHandle", BOOL, HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, BOOL, DWORD)
        bind(self.kernel, "InitializeProcThreadAttributeList", BOOL, HANDLE, DWORD, DWORD, C.POINTER(SIZE_T))
        bind(self.kernel, "UpdateProcThreadAttribute", BOOL, HANDLE, DWORD, SIZE_T, HANDLE, SIZE_T, HANDLE, HANDLE)
        bind(self.kernel, "DeleteProcThreadAttributeList", None, HANDLE)
        bind(self.kernel, "CreateProcessW", BOOL, C.c_wchar_p, C.c_wchar_p, HANDLE, HANDLE, BOOL, DWORD,
             HANDLE, C.c_wchar_p, C.POINTER(StartupInfoEx), C.POINTER(ProcessInfo))
        bind(self.kernel, "ResumeThread", DWORD, HANDLE)
        bind(self.kernel, "WaitForSingleObject", DWORD, HANDLE, DWORD)
        bind(self.kernel, "GetExitCodeProcess", BOOL, HANDLE, C.POINTER(DWORD))
        bind(self.kernel, "TerminateProcess", BOOL, HANDLE, C.c_uint)
        bind(self.gdi, "CreateCompatibleDC", HANDLE, HANDLE)
        bind(self.gdi, "CreateDIBSection", HANDLE, HANDLE, C.POINTER(BitmapInfo), C.c_uint, C.POINTER(HANDLE), HANDLE, DWORD)
        bind(self.gdi, "SelectObject", HANDLE, HANDLE, HANDLE)
        bind(self.gdi, "DeleteObject", BOOL, HANDLE)
        bind(self.gdi, "DeleteDC", BOOL, HANDLE)
        bind(self.gdi, "GdiFlush", BOOL)
        bind(self.gdi, "BitBlt", BOOL, HANDLE, C.c_int, C.c_int, C.c_int, C.c_int,
             HANDLE, C.c_int, C.c_int, DWORD)

    @staticmethod
    def checked(value, operation):
        if not value:
            # WinDLL(use_last_error=True) stores a private thread-local copy.
            # WinError() without a code reads the real thread error instead,
            # after ctypes has swapped it back, and can report stale success.
            code = C.get_last_error()
            detail = C.FormatError(code).strip() if code else "Windows returned failure without an error code"
            raise C.WinError(code, f"{operation}: {detail}")
        return value

    def create_desktop(self, name, *, attach=True):
        original = self.checked(self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId()), "GetThreadDesktop")
        # READOBJECTS | CREATEWINDOW | ENUMERATE | WRITEOBJECTS; no SWITCHDESKTOP.
        desktop = self.checked(self.user.CreateDesktopW(name, None, None, 0, 0xC3, None), "CreateDesktop")
        try:
            if attach:
                self.checked(self.user.SetThreadDesktop(desktop), "SetThreadDesktop")
        except BaseException:
            self.user.CloseDesktop(desktop)
            raise
        return original, desktop

    def close_desktop(self, desktop, original):
        self.checked(self.user.SetThreadDesktop(original), "Restore thread desktop")
        self.checked(self.user.CloseDesktop(desktop), "CloseDesktop")

    def create_job(self, name):
        job = self.checked(self.kernel.CreateJobObjectW(None, name), "CreateJobObject")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self.checked(self.kernel.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)), "SetInformationJobObject")
        except BaseException:
            self.kernel.CloseHandle(job)
            raise
        return job

    def close_job(self, job):
        try:
            self.checked(self.kernel.TerminateJobObject(job, 1), "TerminateJobObject")
        finally:
            self.kernel.CloseHandle(job)

    def create_process(self, command, desktop, job, environment, cwd, log):
        """Create suspended with an explicit desktop and atomic job membership.

        CPython STARTUPINFO exposes no lpDesktop; setting a Python attribute on
        it is silently ignored by _winapi.CreateProcess. Use the real struct.
        PROC_THREAD_ATTRIBUTE_JOB_LIST also closes the spawn/assign crash gap.
        """
        import msvcrt
        duplicates, attributes, initialized = [], None, False
        try:
            current = self.kernel.GetCurrentProcess()
            with open(os.devnull, "rb") as null:
                for stream in (null, log):
                    duplicate = HANDLE()
                    self.checked(self.kernel.DuplicateHandle(current, msvcrt.get_osfhandle(stream.fileno()), current,
                                                            C.byref(duplicate), 0, True, 2), "DuplicateHandle")
                    duplicates.append(duplicate.value)
            size = SIZE_T()
            self.kernel.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
            attributes = C.create_string_buffer(size.value)
            self.checked(self.kernel.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)), "InitializeProcThreadAttributeList")
            initialized = True
            handles, jobs = (HANDLE * 2)(*duplicates), (HANDLE * 1)(job)
            for key, values in ((0x20002, handles), (0x2000D, jobs)):
                self.checked(self.kernel.UpdateProcThreadAttribute(attributes, 0, key, C.cast(values, HANDLE),
                                                                  C.sizeof(values), None, None), "UpdateProcThreadAttribute")
            startup = StartupInfoEx()
            startup.startup.size = C.sizeof(startup)
            startup.startup.desktop = desktop
            startup.startup.flags = 0x100 | 0x80  # USESTDHANDLES | FORCEOFFFEEDBACK
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = duplicates[0], duplicates[1], duplicates[1]
            startup.attributes = C.cast(attributes, HANDLE)
            info = ProcessInfo()
            command_line = C.create_unicode_buffer(subprocess.list2cmdline(command))
            block = C.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in
                                                     sorted(environment.items(), key=lambda pair: pair[0].casefold())) + "\0\0")
            # SUSPENDED | NO_WINDOW | UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO
            flags = 0x4 | 0x08000000 | 0x400 | 0x80000
            self.checked(self.kernel.CreateProcessW(command[0], command_line, None, None, True, flags,
                                                   block, cwd, C.byref(startup), C.byref(info)), "CreateProcessW")
            return WindowsProcess(self, info, command)
        finally:
            if initialized:
                self.kernel.DeleteProcThreadAttributeList(attributes)
            for handle in duplicates:
                self.kernel.CloseHandle(handle)

    def windows(self, desktop):
        result = []

        @self.callback_type
        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                size = self.user.GetWindowTextLengthW(hwnd)
                text = C.create_unicode_buffer(size + 1)
                self.user.GetWindowTextW(hwnd, text, len(text))
                if text.value:
                    result.append((hwnd, text.value))
            return True

        self.checked(self.user.EnumDesktopWindows(desktop, visit, 0), "EnumDesktopWindows")
        return result

    def unexpected_dialog(self, hwnd):
        name = C.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, len(name))
        # Windows text-input helpers can be owned by Nuke too. When Neat's
        # modal editor disables that owner, these helpers satisfy the generic
        # modal test despite not being plugin dialogs (the language bar may
        # even be 0 x 20). Match native classes, never titles or dimensions.
        if name.value in {"CiceroUIWndFrame", "MSCTFIME UI", "IME"}:
            return False
        owner = self.user.GetWindow(hwnd, 4)  # GW_OWNER
        return name.value == "#32770" or bool(owner and not self.user.IsWindowEnabled(owner))

    def window_pid(self, hwnd):
        pid = DWORD()
        return pid.value if self.user.GetWindowThreadProcessId(hwnd, C.byref(pid)) else None

    def is_viewable(self, hwnd):
        return bool(self.user.IsWindowVisible(hwnd) and not self.user.IsIconic(hwnd))

    def point_is_visible(self, hwnd, point):
        """Hit-test the intended client coordinate without activating any window."""
        rect = Rect()
        if not self.user.GetClientRect(hwnd, C.byref(rect)):
            return False
        x, y = point
        if not (0 <= x < rect.right - rect.left and 0 <= y < rect.bottom - rect.top):
            return False
        screen = Point(x, y)
        if not self.user.ClientToScreen(hwnd, C.byref(screen)):
            return False
        hit = self.user.WindowFromPoint(screen)
        # Another window from the same Nuke process can still obscure Neat.
        return bool(hit and self.user.GetAncestor(hit, 2) == hwnd)  # GA_ROOT

    def window_details(self, hwnd):
        """Read native dialog controls without relying on offscreen painting."""
        def describe(handle):
            name, text = C.create_unicode_buffer(256), C.create_unicode_buffer(2048)
            self.user.GetClassNameW(handle, name, len(name))
            result = SIZE_T()
            # WM_GETTEXT works with controls in another process. Bound each
            # call and the total number of controls; never invoke a button.
            received = self.user.SendMessageTimeoutW(handle, 0x000D, len(text), C.addressof(text),
                                                     0x23, 100, C.byref(result))
            return {"handle": int(handle), "class": name.value,
                    "text": text.value if received else "", "text_timeout": not bool(received)}

        details = describe(hwnd)
        rect = Rect()
        details["client_size"] = ([rect.right - rect.left, rect.bottom - rect.top]
                                  if self.user.GetClientRect(hwnd, C.byref(rect)) else None)
        details["children"] = []

        @self.callback_type
        def visit(child, _):
            details["children"].append(describe(child))
            return len(details["children"]) < 32

        self.user.EnumChildWindows(hwnd, visit, 0)
        return details

    def screenshot(self, hwnd, *, visible=False):
        import numpy as np
        rect = Rect()
        self.checked(self.user.GetClientRect(hwnd, C.byref(rect)), "GetClientRect")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width == 0 or height == 0:
            raise WindowNotReady(hwnd, width, height)
        if not (1 <= width <= 16384 and 1 <= height <= 16384):
            raise RuntimeError(f"Window {int(hwnd)} returned an invalid client size: {width} x {height}.")
        screen = self.checked(self.user.GetDC(hwnd), "GetDC")
        dc = bitmap = previous = None
        try:
            dc = self.checked(self.gdi.CreateCompatibleDC(screen), "CreateCompatibleDC")
            info = BitmapInfo(size=40, width=width, height=-height, planes=1, bits=32)
            pixels = HANDLE()
            bitmap = self.checked(self.gdi.CreateDIBSection(screen, C.byref(info), 0, C.byref(pixels), None, 0), "CreateDIBSection")
            previous = self.checked(self.gdi.SelectObject(dc, bitmap), "SelectObject")
            if visible:
                # Read the displayed client pixels. Qt's WM_PRINT handlers can
                # return success without painting, producing a black capture.
                self.checked(self.gdi.BitBlt(dc, 0, 0, width, height, screen, 0, 0, 0x00CC0020),
                             "Capture visible Neat Video window")  # SRCCOPY
                self.gdi.GdiFlush()
                raw = C.string_at(pixels, width * height * 4)
                return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
            # WM_PRINT draws the client and children, including a hidden desktop.
            # Unlike PrintWindow, this call has a bounded wait if Neat is hung.
            result = SIZE_T()
            # SendMessageTimeout need not set an error on generic failure.
            # Clear stale errors from earlier calls before capturing its result.
            C.set_last_error(0)
            self.checked(self.user.SendMessageTimeoutW(hwnd, 0x0317, dc, 0x1C, 0x23, 1500, C.byref(result)), "Capture Neat Video window")
            self.gdi.GdiFlush()
            raw = C.string_at(pixels, width * height * 4)
            image = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
            if not image.any():
                # Some custom window procedures only implement WM_PRINTCLIENT.
                # Keep this retry bounded too; a successful message alone does
                # not establish that the target actually painted anything.
                C.set_last_error(0)
                self.checked(self.user.SendMessageTimeoutW(hwnd, 0x0318, dc, 0x1C, 0x23, 1500, C.byref(result)),
                             "Capture Neat Video client area")
                self.gdi.GdiFlush()
                raw = C.string_at(pixels, width * height * 4)
                image = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
            return image
        finally:
            if previous:
                self.gdi.SelectObject(dc, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if dc:
                self.gdi.DeleteDC(dc)
            self.user.ReleaseDC(hwnd, screen)

    def click(self, hwnd, point):
        target, location = hwnd, Point(*point)
        for _ in range(32):
            child = self.user.ChildWindowFromPointEx(target, location, 7)
            if not child or child == target:
                break
            self.user.MapWindowPoints(target, child, C.byref(location), 1)
            target = child
        if not self.user.IsWindowEnabled(target):
            raise RuntimeError("The Neat Video control is disabled.")
        coordinates = (int(location.x) & 0xFFFF) | ((int(location.y) & 0xFFFF) << 16)
        for message, buttons in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
            self.checked(self.user.PostMessageW(target, message, buttons, coordinates), "Click Neat Video control")


class WindowsDisplay(PrivateDisplay):
    """Reuse profile verification while replacing X11 transport with Win32."""

    supported_neat_versions = ("6.1.2", "6.1.3")

    def __init__(self, directory, cancelled, *, notify=None, api=None, attach=True):
        # The X11 constructor discovers Xvfb; Windows has no external tools.
        self.directory, self.cancelled = Path(directory), cancelled
        self.notify = notify or (lambda event: None)
        self.api = api
        self.attach = attach
        self.child = self.desktop = self.original = self.job = None
        token = uuid.uuid4().hex
        self.number = "ShotBoxDenoise_" + token
        self.job_name = "Local\\ShotBoxDenoise_" + token
        self.clicked_auto = self.applied = False
        self.ready_since = None
        self.templates = {}
        self.pending_dialog = None
        self.capture_retry = None
        self.loading_since = None
        self.editor_seen = False
        self.clicked_check = False
        self.check_preview_since = None

    def _load_templates(self):
        super()._load_templates()
        for key in ("profile_check", "profile_check_active", "profile_check_preview"):
            if self.templates.get(key) is None:
                raise RuntimeError(f"Windows denoise control image is missing: {key}.")

    def _match(self, pixels, name):
        if name != "profile_check_preview":
            return super()._match(pixels, name)
        import cv2
        import numpy as np
        template = self.templates[name]
        # Assist Image marks the rendered Profile Check preview. Match only
        # opaque white text and its black outline, not the footage behind it.
        mask = np.where(np.all(template >= 245, axis=2) | np.all(template <= 10, axis=2), 255, 0).astype(np.uint8)
        region = pixels[:130]
        if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
            return None
        scores = cv2.matchTemplate(region, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        scores[~np.isfinite(scores)] = 1
        score, _, point, _ = cv2.minMaxLoc(scores)
        if score <= .003:
            return point[0] + template.shape[1] // 2, point[1] + template.shape[0] // 2
        return None

    def _profile_is_ready(self, pixels):
        if not self.clicked_check:
            return super()._profile_is_ready(pixels)
        # Entering Profile Check clears the sample selection and can change
        # Build Profile back to Auto Profile even though the profile is ready.
        return (self._match(pixels, "adjust_ready") and self._match(pixels, "profile_check_active")
                and (self._match(pixels, "auto_profile") or self._match(pixels, "build_profile")))

    def _before_apply(self, window, pixels):
        import cv2
        if not self.clicked_check:
            active = self._match(pixels, "profile_check_active")
            point = self._match(pixels, "profile_check")
            if not active and not point:
                self.ready_since = None
                return False
            cv2.imwrite(str(self.directory / "before-profile-check.png"), pixels)
            if not active:
                self._click(window, point)
            self.clicked_check = True
            self.ready_since = None
            self.notify({"stage": "checking_profile", "message": "Profile Check enabled; waiting for its preview."})
            return False
        if not self._match(pixels, "profile_check_preview"):
            self.check_preview_since = None
            return False
        now = time.monotonic()
        if self.check_preview_since is None:
            self.check_preview_since = now
        if now - self.check_preview_since < .5:
            return False
        cv2.imwrite(str(self.directory / "profile-check-ready.png"), pixels)
        return True

    def __enter__(self):
        try:
            self.api = self.api or WindowsAPI()
            self.original, self.desktop = self.api.create_desktop(self.number, attach=self.attach)
            self.job = self.api.create_job(self.job_name)
            self._load_templates()
            self.notify({"stage": "debug", "source": "Display", "message": f"Windows private desktop ready: {self.number}"})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def environment(self, base):
        result = dict(base, QT_QPA_PLATFORM="windows", QT_ENABLE_HIGHDPI_SCALING="0",
                      SHOTBOX_DENOISE_DESKTOP=self.number, SHOTBOX_DENOISE_WINDOWS_JOB=self.job_name)
        for key in ("DISPLAY", "XAUTHORITY", "QT_QPA_PLATFORMTHEME", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
                    "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
            result.pop(key, None)
        return result

    def launch(self, command, environment, cwd, log):
        import psutil
        from nuke_denoise_child import record_process
        process = self.api.create_process(command, self.number, self.job,
                                          child_environment(self.directory, environment), cwd, log)
        self.child = process
        try:
            # Record the real Nuke identity while its initial thread is suspended.
            record_process(psutil.Process(process.pid), self.directory)
            process.resume()
            return process
        except BaseException:
            process.terminate()
            raise

    def _check(self):
        if self.cancelled():
            raise DenoiseCancelled("Denoise cancelled.")

    def windows(self):
        windows = self.api.windows(self.desktop)
        if any(title.startswith("Neat Video ") for _, title in windows):
            self.editor_seen = True
        return windows

    def screenshot(self, hwnd):
        return self.api.screenshot(hwnd)

    def _unexpected_dialog(self, hwnd):
        return self.api.unexpected_dialog(hwnd)

    def poll_profile(self):
        self.dialog_seen = False
        try:
            result = super().poll_profile()
            self.capture_retry = None
            return result
        except WindowNotReady as error:
            # Visible top-level windows can still have no client area while
            # opening. Retry without clicking or confirming a profile.
            self.ready_since = None
            now = time.monotonic()
            if self.capture_retry is None or self.capture_retry[0] != error.hwnd:
                self.capture_retry = (error.hwnd, now)
            if now - self.capture_retry[1] >= 2:
                details = {"handle": error.hwnd, "client_size": [error.width, error.height],
                           "windows": [(int(hwnd), title) for hwnd, title in self.windows()]}
                (self.directory / "window-capture-error.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
                raise RuntimeError(f"Neat Video window did not become ready: {error} See window-capture-error.json.") from error
            return False
        finally:
            if self.ready_since is None:
                self.check_preview_since = None
            if not self.dialog_seen:
                self.pending_dialog = None

    def _prepare_dialog(self, hwnd, title):
        self.dialog_seen = True
        key = (hwnd, title)
        now = time.monotonic()
        if self.pending_dialog is None or self.pending_dialog[0] != key:
            self.pending_dialog = (key, now)
            self.notify({"stage": "debug", "source": "Display",
                         "message": f"Waiting for modal window to settle: {title}"})
        # Allow the generic Neat title during input preparation. The visible
        # Windows diagnostic showed progress before the versioned editor, but
        # this title alone cannot distinguish loading from a warning. Wait
        # boundedly; never dismiss it or click through it by title.
        if not self.editor_seen and not self.clicked_auto and title == "NeatVideo6 OFX 64":
            if self.loading_since is None:
                self.loading_since = now
                self.notify({"stage": "waiting_profile", "message": "Waiting for Nuke to prepare Neat Video input frames…"})
            return now - self.loading_since >= 120
        # Other dialogs still get the short settling interval and diagnostics.
        return now - self.pending_dialog[1] >= 2

    def _dialog_screenshot(self, hwnd):
        return self.screenshot(hwnd)

    def _dialog_details(self, hwnd):
        from nuke_denoise_accessibility import read_dialog
        details = self.api.window_details(hwnd)
        texts = [item["text"] for item in details.get("children", []) if item.get("text")]
        if not texts:
            accessibility = read_dialog(self, hwnd)
            details["accessibility"] = accessibility
            for item in accessibility.get("elements", []):
                for field in ("name", "help"):
                    text = item.get(field)
                    if text and text != details.get("text") and text not in texts:
                        texts.append(text)
        (self.directory / "unexpected-dialog.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
        self.notify({"stage": "debug", "source": "Display", "message": "Dialog details: " + json.dumps(details)})
        if texts:
            return " Dialog controls: " + " | ".join(texts)
        error = details.get("accessibility", {}).get("error")
        suffix = f" Accessibility: {error}" if error else ""
        return " No dialog control text; see unexpected-dialog.json." + suffix

    def _click(self, hwnd, point):
        # Only handles enumerated on this job's unique desktop are accepted.
        if hwnd not in {window for window, _ in self.windows()}:
            raise RuntimeError("Neat Video window left the private desktop.")
        self.api.click(hwnd, point)

    def __exit__(self, *_):
        # Closing the owned job already requests termination of every member.
        # Wait for that asynchronous termination before closing process handles;
        # a second TerminateProcess call can return ERROR_ACCESS_DENIED while
        # the original termination is still finishing.
        job, self.job = self.job, None
        try:
            if job is not None:
                self.api.close_job(job)
        finally:
            try:
                if self.child is not None:
                    if job is not None:
                        self.child.wait(timeout=5)
                    else:
                        stop_process(self.child)
            finally:
                try:
                    if self.child is not None:
                        self.child.close()
                finally:
                    if self.desktop is not None:
                        self.api.close_desktop(self.desktop, self.original)
                        self.desktop = None


class VisibleWindowsDisplay(WindowsDisplay):
    """Run on the current desktop, restricted to the Nuke process we own."""

    visible = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.number = None  # NULL lpDesktop inherits the current desktop.
        self.waiting_visible = False
        self.black_since = None

    def _load_templates(self):
        from nuke_denoise_controls import WindowsControlMatcher
        self.control_matcher = WindowsControlMatcher()
        self.templates = self.control_matcher.templates

    def _match(self, pixels, name):
        previous_scale = self.control_matcher.scale
        point = self.control_matcher.match(pixels, name)
        if previous_scale is None and self.control_matcher.scale is not None:
            details = {"reference": "Windows Neat Video 6.1.3", "scale": self.control_matcher.scale,
                       "capture_size": [pixels.shape[1], pixels.shape[0]]}
            (self.directory / "control-scale.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
            self.notify({"stage": "debug", "source": "Display",
                         "message": "Windows control recognition calibrated: " + json.dumps(details)})
        return point

    def __enter__(self):
        try:
            self.api = self.api or WindowsAPI()
            self.job = self.api.create_job(self.job_name)
            self._load_templates()
            self.notify({"stage": "debug", "source": "Display",
                         "message": "Windows visible denoise: keep Neat Video controls uncovered while profiling."})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def environment(self, base):
        result = super().environment(base)
        result.pop("SHOTBOX_DENOISE_DESKTOP", None)
        return result

    def windows(self):
        if self.child is None or self.child.poll() is not None:
            return []
        owned = [(hwnd, title) for hwnd, title in self.api.windows(None)
                 if self.api.window_pid(hwnd) == self.child.pid]
        if any(title.startswith("Neat Video ") for _, title in owned):
            self.editor_seen = True
        return owned

    def _require_owned_visible(self, hwnd):
        if hwnd not in {window for window, _ in self.windows()}:
            raise RuntimeError("Neat Video window no longer belongs to this denoise process.")
        if not self.api.is_viewable(hwnd):
            raise WindowObscured("Restore this job's Neat Video window to continue profiling.")

    def screenshot(self, hwnd):
        self._require_owned_visible(hwnd)
        pixels = self.api.screenshot(hwnd, visible=True)
        self._require_owned_visible(hwnd)
        if pixels.any():
            self.black_since = None
        else:
            now = time.monotonic()
            if self.black_since is None:
                self.black_since = now
            elif now - self.black_since >= 10:
                import cv2
                cv2.imwrite(str(self.directory / "profile-window.png"), pixels)
                raise RuntimeError("Visible Neat Video capture stayed black for 10 seconds. "
                                   "Keep the editor on screen; see profile-window.png.")
        return pixels

    def _click(self, hwnd, point):
        self._require_owned_visible(hwnd)
        if not self.api.point_is_visible(hwnd, point):
            raise WindowObscured("Uncover this job's Neat Video controls to continue profiling.")
        self.api.click(hwnd, point)

    def poll_profile(self):
        try:
            result = super().poll_profile()
            self.waiting_visible = False
            return result
        except WindowObscured as error:
            self.ready_since = self.check_preview_since = self.black_since = None
            if not self.waiting_visible:
                self.notify({"stage": "waiting_visible", "message": str(error)})
                self.waiting_visible = True
            return False
