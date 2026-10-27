from __future__ import annotations

import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

import numpy as np

import app_cache
import nuke_denoise as supervisor
import nuke_denoise_child as child
import nuke_denoise_display as displays
import nuke_denoise_windows as windows


class WindowsDenoiseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.api = mock.Mock()
        self.api.create_desktop.return_value = (0x100000001, 0x100000002)
        self.api.create_job.return_value = 0x100000003
        self.api.windows.return_value = [(0x100000004, "Neat Video 6.1.2 Studio")]
        self.api.unexpected_dialog.return_value = False
        self.api.window_details.return_value = {"class": "#32770", "text": "NeatVideo6 OFX 64", "children": []}
        self.display = windows.WindowsDisplay(self.directory, lambda: False, api=self.api)

    def canvas(self, *names):
        image = np.zeros((1000, 1400, 3), dtype=np.uint8)
        x = 10
        for name in names:
            template = self.display.templates[name]
            h, w = template.shape[:2]
            y = 960 if name == "apply" else 20
            image[y:y + h, x:x + w] = template
            x += w + 20
        return image

    def test_private_desktop_verifies_fresh_profile_before_apply_and_cleans_handles(self):
        with self.display:
            self.api.screenshot.return_value = self.canvas("auto_profile")
            self.assertFalse(self.display.poll_profile())
            self.api.click.assert_called_once()
            self.assertFalse((self.directory / "profile-confirmed").exists())
            self.api.screenshot.return_value = self.canvas("build_profile", "adjust_ready", "apply")
            with mock.patch.object(displays.time, "monotonic", return_value=10) as clock:
                self.assertFalse(self.display.poll_profile())
                clock.return_value = 10.4
                self.assertTrue(self.display.poll_profile())
            self.assertTrue((self.directory / "profile-confirmed").exists())
            self.assertEqual(self.api.click.call_count, 2)
        self.api.close_job.assert_called_once_with(0x100000003)
        self.api.close_desktop.assert_called_once_with(0x100000002, 0x100000001)

    def test_apply_requires_enabled_controls_to_remain_ready(self):
        with self.display:
            self.display.clicked_auto = True
            with mock.patch.object(displays.time, "monotonic", return_value=10) as clock:
                self.api.screenshot.return_value = self.canvas("build_profile", "adjust_ready", "apply")
                self.assertFalse(self.display.poll_profile())
                self.api.screenshot.return_value = self.canvas("build_profile", "adjust_ready")
                clock.return_value = 10.4
                self.assertFalse(self.display.poll_profile())
                self.assertIsNone(self.display.ready_since)
                self.api.screenshot.return_value = self.canvas("build_profile", "adjust_ready", "apply")
                clock.return_value = 10.5
                self.assertFalse(self.display.poll_profile())
                clock.return_value = 10.9
                self.assertTrue(self.display.poll_profile())

    def test_failed_apply_click_does_not_leave_a_profile_receipt(self):
        with self.display:
            self.display.clicked_auto = True
            self.api.screenshot.return_value = self.canvas("build_profile", "adjust_ready", "apply")
            with mock.patch.object(displays.time, "monotonic", return_value=10) as clock:
                self.assertFalse(self.display.poll_profile())
                clock.return_value = 10.4
                self.api.click.side_effect = OSError("Apply message failed")
                with self.assertRaisesRegex(OSError, "Apply message failed"):
                    self.display.poll_profile()
            self.assertFalse((self.directory / "profile-confirmed").exists())
            self.assertTrue((self.directory / "before-apply.png").is_file())
            neat = json.loads((self.directory / "neat-version.json").read_text())
            self.assertEqual(neat["version"], "6.1.2")

    def test_rejects_handles_outside_private_desktop_without_sending_input(self):
        with self.display:
            with self.assertRaisesRegex(RuntimeError, "left the private desktop"):
                self.display._click(123, (50, 30))
            self.api.click.assert_not_called()

    def test_unexpected_modal_is_captured_and_prevents_profile_receipt(self):
        with self.display:
            self.api.windows.return_value = [(222, "License warning")]
            self.api.unexpected_dialog.return_value = True
            self.api.screenshot.return_value = self.canvas()
            with self.assertRaisesRegex(RuntimeError, "Unexpected dialog"):
                self.display.poll_profile()
            self.assertTrue((self.directory / "unexpected-dialog.png").is_file())
            self.assertFalse((self.directory / "profile-confirmed").exists())

    def test_cancel_and_partial_startup_failures_close_job_and_desktop(self):
        with self.display:
            self.display.cancelled = lambda: True
            with self.assertRaises(displays.DenoiseCancelled):
                self.display.poll_profile()
        self.api.close_job.assert_called_once()
        other = windows.WindowsDisplay(self.directory, lambda: False, api=self.api)
        self.api.create_job.side_effect = OSError("No job handle")
        with self.assertRaisesRegex(OSError, "No job handle"):
            other.__enter__()
        self.assertEqual(self.api.close_desktop.call_count, 2)

    def test_black_dialog_capture_reports_native_text_without_clicking(self):
        with self.display:
            self.api.windows.return_value = [(222, "NeatVideo6 OFX 64")]
            self.api.unexpected_dialog.return_value = True
            self.api.window_details.return_value = {
                "class": "#32770", "text": "NeatVideo6 OFX 64",
                "children": [{"class": "Static", "text": "Example plugin startup error"}]}
            self.api.screenshot.return_value = self.canvas()
            with self.assertRaisesRegex(RuntimeError, "Example plugin startup error") as caught:
                self.display.poll_profile()
            self.assertIn("Capture is black", str(caught.exception))
            details = json.loads((self.directory / "unexpected-dialog.json").read_text())
            self.assertEqual(details["children"][0]["text"], "Example plugin startup error")
            self.api.click.assert_not_called()
            self.assertFalse((self.directory / "profile-confirmed").exists())

    def test_native_dialog_control_text_uses_bounded_cross_process_messages(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.user = mock.Mock()
        api.callback_type = ctypes.CFUNCTYPE(windows.BOOL, windows.HANDLE, ctypes.c_ssize_t)
        def class_name(hwnd, buffer, length):
            buffer.value = "#32770" if hwnd == 222 else "Static"
        def text(hwnd, message, length, address, flags, timeout, result):
            self.assertEqual((message, flags, timeout), (0xD, 0x23, 100))
            buffer = ctypes.create_unicode_buffer("Plugin title" if hwnd == 222 else "Plugin startup message")
            ctypes.memmove(address, buffer, ctypes.sizeof(buffer))
            return True
        api.user.GetClassNameW.side_effect = class_name
        api.user.SendMessageTimeoutW.side_effect = text
        api.user.EnumChildWindows.side_effect = lambda hwnd, callback, data: callback(333, data)
        details = api.window_details(222)
        self.assertEqual(details["text"], "Plugin title")
        self.assertEqual(details["children"][0]["text"], "Plugin startup message")
        self.assertFalse(details["children"][0]["text_timeout"])

    def test_black_wm_print_capture_retries_bounded_client_paint(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.user, api.gdi = mock.Mock(), mock.Mock()
        pixels = ctypes.create_string_buffer(24)
        def rect(hwnd, pointer):
            value = ctypes.cast(pointer, ctypes.POINTER(windows.Rect)).contents
            value.right, value.bottom = 3, 2
            return True
        def bitmap(dc, info, usage, pointer, section, offset):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(pixels)
            return 5
        def paint(hwnd, message, dc, flags, mode, timeout, result):
            self.assertEqual(timeout, 1500)
            if message == 0x318:
                ctypes.memmove(pixels, bytes(range(24)), 24)
            return True
        api.user.GetClientRect.side_effect = rect
        api.user.SendMessageTimeoutW.side_effect = paint
        api.gdi.CreateDIBSection.side_effect = bitmap
        with mock.patch.object(ctypes, "set_last_error", create=True):
            image = api.screenshot(222)
        self.assertEqual([call.args[1] for call in api.user.SendMessageTimeoutW.call_args_list], [0x317, 0x318])
        self.assertEqual(image[1, 2].tolist(), [20, 21, 22])
        api.gdi.DeleteObject.assert_called_once_with(5)
        api.user.ReleaseDC.assert_called_once()

    def test_job_cleanup_waits_without_terminating_a_dying_process_again(self):
        process = mock.Mock()
        process.terminate.side_effect = PermissionError("TerminateProcess: Access is denied")
        with self.display:
            self.display.child = process
            process.wait.side_effect = lambda **_: self.api.close_job.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        process.terminate.assert_not_called()
        process.close.assert_called_once()
        self.api.close_desktop.assert_called_once()

    def test_job_cleanup_preserves_the_original_profiling_error(self):
        process = mock.Mock()
        process.terminate.side_effect = PermissionError("TerminateProcess: Access is denied")
        with self.assertRaisesRegex(RuntimeError, "Unexpected dialog: NeatVideo6 OFX 64"):
            with self.display:
                self.display.child = process
                raise RuntimeError("Unexpected dialog: NeatVideo6 OFX 64")
        process.terminate.assert_not_called()
        process.close.assert_called_once()

    def test_job_cleanup_timeout_still_releases_desktop(self):
        process = mock.Mock()
        process.wait.side_effect = subprocess.TimeoutExpired(["Nuke.exe"], 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            with self.display:
                self.display.child = process
        process.close.assert_called_once()
        self.api.close_desktop.assert_called_once()

    def test_windows_launch_uses_private_desktop_and_no_inherited_x11(self):
        environment = self.display.environment({"DISPLAY": ":0", "XAUTHORITY": "user", "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(environment["QT_QPA_PLATFORM"], "windows")
        self.assertEqual(environment["SHOTBOX_DENOISE_WINDOWS_JOB"], self.display.job_name)
        self.assertNotIn("DISPLAY", environment)
        self.assertNotIn("XAUTHORITY", environment)

    def test_windows_platform_dispatch_does_not_discover_xvfb(self):
        with mock.patch.object(displays.sys, "platform", "win32"), \
                mock.patch.object(displays.shutil, "which", side_effect=AssertionError("No X11 tools on Windows")):
            self.assertEqual(displays.discover_tools(), {})
            self.assertIsInstance(displays.private_display(self.directory, lambda: False), windows.WindowsDisplay)

    def test_native_64bit_structures_have_windows_layout(self):
        self.assertEqual(ctypes.sizeof(windows.Rect), 16)
        self.assertEqual(ctypes.sizeof(windows.Point), 8)
        self.assertEqual(ctypes.sizeof(windows.BitmapInfo), 44)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(windows.BasicLimits), 64)
            self.assertEqual(ctypes.sizeof(windows.ExtendedLimits), 144)
            self.assertEqual(ctypes.sizeof(windows.StartupInfo), 104)
            self.assertEqual(ctypes.sizeof(windows.StartupInfoEx), 112)
            self.assertEqual(ctypes.sizeof(windows.ProcessInfo), 24)

    def test_native_failures_use_saved_ctypes_error_and_preserve_operation(self):
        with mock.patch.object(ctypes, "get_last_error", return_value=5, create=True), \
                mock.patch.object(ctypes, "FormatError", return_value="Access is denied.\r\n", create=True), \
                mock.patch.object(ctypes, "WinError", side_effect=lambda code, detail: OSError(code, detail), create=True) as error:
            with self.assertRaises(OSError) as caught:
                windows.WindowsAPI.checked(False, "CreateProcessW")
        self.assertEqual(caught.exception.errno, 5)
        self.assertIn("CreateProcessW: Access is denied.", str(caught.exception))
        error.assert_called_once_with(5, "CreateProcessW: Access is denied.")

    def test_native_failure_without_error_code_does_not_report_success(self):
        with mock.patch.object(ctypes, "get_last_error", return_value=0, create=True), \
                mock.patch.object(ctypes, "FormatError", create=True) as formatter, \
                mock.patch.object(ctypes, "WinError", side_effect=lambda code, detail: OSError(code, detail), create=True):
            with self.assertRaisesRegex(OSError, "Capture: Windows returned failure without an error code"):
                windows.WindowsAPI.checked(False, "Capture")
        formatter.assert_not_called()

    def test_win32_bindings_preserve_64bit_handles_and_pointer_arguments(self):
        libraries = {name: mock.Mock() for name in ("user32", "kernel32", "gdi32")}
        with mock.patch.object(ctypes, "WinDLL", side_effect=lambda name, **kwargs: libraries[name], create=True), \
                mock.patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True):
            windows.WindowsAPI()
        self.assertIs(libraries["user32"].CreateDesktopW.restype, ctypes.c_void_p)
        self.assertIs(libraries["kernel32"].CreateJobObjectW.restype, ctypes.c_void_p)
        self.assertEqual(libraries["kernel32"].CreateProcessW.argtypes[-2:],
                         [ctypes.POINTER(windows.StartupInfoEx), ctypes.POINTER(windows.ProcessInfo)])
        self.assertIs(libraries["user32"].PostMessageW.argtypes[2], ctypes.c_size_t)
        self.assertIs(libraries["user32"].PostMessageW.argtypes[3], ctypes.c_ssize_t)

    def test_job_kill_on_close_is_configured_before_children_join(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.kernel = mock.Mock()
        api.kernel.CreateJobObjectW.return_value = 0x100000003
        def configure(handle, info_class, pointer, size):
            limits = ctypes.cast(pointer, ctypes.POINTER(windows.ExtendedLimits)).contents
            self.assertEqual(limits.basic.flags, 0x2000)
            self.assertEqual(info_class, 9)
            self.assertEqual(size, ctypes.sizeof(windows.ExtendedLimits))
            return True
        api.kernel.SetInformationJobObject.side_effect = configure
        self.assertEqual(api.create_job("Local\\test"), 0x100000003)
        api.close_job(0x100000003)
        api.kernel.TerminateJobObject.assert_called_once_with(0x100000003, 1)

    def test_create_process_sets_real_desktop_atomic_job_and_only_standard_handles(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.kernel = mock.Mock()
        api.kernel.GetCurrentProcess.return_value = 0x100000001
        duplicate_values, attributes = iter((0x100000010, 0x100000011)), {}
        def duplicate(source, handle, target, pointer, access, inherit, options):
            self.assertTrue(inherit)
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = next(duplicate_values)
            return True
        def initialize(pointer, count, flags, size):
            self.assertEqual(count, 2)
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = 128
            return pointer is not None
        def attribute(pointer, flags, key, values, size, previous, returned):
            array = ctypes.cast(values, ctypes.POINTER(ctypes.c_void_p))
            attributes[key] = [array[index] for index in range(size // ctypes.sizeof(ctypes.c_void_p))]
            return True
        command = ["C:/Program Files/Nuke/Nuke.exe", "--tg", "C:/shot with spaces/start.py"]
        def create(application, line, process_security, thread_security, inherit, flags, environment, cwd, startup_pointer, process_pointer):
            self.assertEqual(application, command[0])
            self.assertEqual(line.value, subprocess.list2cmdline(command))
            startup = ctypes.cast(startup_pointer, ctypes.POINTER(windows.StartupInfoEx)).contents
            self.assertEqual(startup.startup.desktop, "private-desktop")
            self.assertEqual(startup.startup.size, ctypes.sizeof(windows.StartupInfoEx))
            self.assertEqual((startup.startup.stdin, startup.startup.stdout, startup.startup.stderr),
                             (0x100000010, 0x100000011, 0x100000011))
            self.assertEqual(attributes, {0x20002: [0x100000010, 0x100000011], 0x2000D: [0x100000005]})
            self.assertTrue(inherit)
            self.assertEqual(flags, 0x08080404)
            self.assertEqual(cwd, str(self.directory))
            self.assertIn("TEMP=cache\0", environment[:])
            info = ctypes.cast(process_pointer, ctypes.POINTER(windows.ProcessInfo)).contents
            info.process, info.thread, info.pid = 0x100000020, 0x100000021, 333
            return True
        api.kernel.DuplicateHandle.side_effect = duplicate
        api.kernel.InitializeProcThreadAttributeList.side_effect = initialize
        api.kernel.UpdateProcThreadAttribute.side_effect = attribute
        api.kernel.CreateProcessW.side_effect = create
        with mock.patch.dict(sys.modules, msvcrt=SimpleNamespace(get_osfhandle=lambda descriptor: descriptor)), \
                (self.directory / "nuke.log").open("wb") as log:
            process = api.create_process(command, "private-desktop", 0x100000005,
                                         {"TEMP": "cache"}, str(self.directory), log)
        self.assertEqual(process.pid, 333)
        api.kernel.ResumeThread.assert_not_called()
        self.assertEqual(api.kernel.CloseHandle.call_args_list, [mock.call(0x100000010), mock.call(0x100000011)])
        api.kernel.DeleteProcThreadAttributeList.assert_called_once()

    def test_native_process_wait_timeout_and_exit_259_are_distinguished(self):
        api = mock.Mock()
        info = windows.ProcessInfo(process=0x100000001, thread=0x100000002, pid=333)
        process = windows.WindowsProcess(api, info, ["Nuke.exe"])
        api.kernel.WaitForSingleObject.return_value = 0x102
        self.assertIsNone(process.poll())
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=.1)
        api.kernel.ResumeThread.return_value = 1
        process.resume()
        self.assertIsNone(info.thread)
        api.kernel.WaitForSingleObject.return_value = 0
        def exit_code(handle, pointer):
            ctypes.cast(pointer, ctypes.POINTER(windows.DWORD))[0] = 259
            return True
        api.kernel.GetExitCodeProcess.side_effect = exit_code
        self.assertEqual(process.wait(timeout=1), 259)
        process.close()
        self.assertIsNone(info.process)

    def test_fixture_desktop_can_be_created_without_rebinding_a_qt_thread(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.user, api.kernel = mock.Mock(), mock.Mock()
        api.user.GetThreadDesktop.return_value = 0x100000001
        api.user.CreateDesktopW.return_value = 0x100000002
        self.assertEqual(api.create_desktop("private", attach=False), (0x100000001, 0x100000002))
        api.user.SetThreadDesktop.assert_not_called()

    def test_window_capture_uses_bounded_wm_print_and_releases_gdi_resources(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.user, api.gdi = mock.Mock(), mock.Mock()
        pixels = ctypes.create_string_buffer(bytes(range(24)))
        def get_rect(hwnd, pointer):
            rect = ctypes.cast(pointer, ctypes.POINTER(windows.Rect)).contents
            rect.right, rect.bottom = 3, 2
            return True
        def create_bitmap(dc, info, usage, pointer, section, offset):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(pixels)
            return 0x100000005
        api.user.GetClientRect.side_effect = get_rect
        api.user.GetDC.return_value = 0x100000004
        api.user.SendMessageTimeoutW.return_value = True
        api.gdi.CreateCompatibleDC.return_value = 0x100000006
        api.gdi.CreateDIBSection.side_effect = create_bitmap
        api.gdi.SelectObject.return_value = 0x100000007
        with mock.patch.object(ctypes, "set_last_error", create=True) as clear_error:
            image = api.screenshot(0x100000003)
        clear_error.assert_called_once_with(0)
        self.assertEqual(image.shape, (2, 3, 3))
        self.assertEqual(image[1, 2].tolist(), [20, 21, 22])
        self.assertEqual(api.user.SendMessageTimeoutW.call_args.args[1:6], (0x317, 0x100000006, 0x1C, 0x23, 1500))
        api.gdi.DeleteObject.assert_called_once_with(0x100000005)
        api.gdi.DeleteDC.assert_called_once_with(0x100000006)
        api.user.ReleaseDC.assert_called_once_with(0x100000003, 0x100000004)

    def test_targeted_mouse_messages_follow_nested_control_coordinates(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.user = mock.Mock()
        api.user.ChildWindowFromPointEx.side_effect = [0x100000005, 0x100000005]
        def map_point(source, target, pointer, count):
            point = ctypes.cast(pointer, ctypes.POINTER(windows.Point)).contents
            point.x -= 10
            point.y -= 20
        api.user.MapWindowPoints.side_effect = map_point
        api.user.IsWindowEnabled.return_value = True
        api.user.PostMessageW.return_value = True
        api.click(0x100000004, (40, 50))
        self.assertEqual(api.user.PostMessageW.call_args_list,
                         [mock.call(0x100000005, message, buttons, (30 << 16) | 30)
                          for message, buttons in ((0x200, 0), (0x201, 1), (0x202, 0))])

    def test_supervisor_windows_branch_keeps_host_events_environment_and_cleanup(self):
        source = self.directory / "source.mov"
        source.write_text("source")
        request = supervisor.create_request(self.directory, {"source": str(source), "shot_dir": str(self.directory), "shot_name": "test"}, "C:/Program Files/Nuke/Nuke.exe")
        display = mock.Mock(number="private-desktop")
        display.environment.side_effect = lambda base: {**base, "QT_QPA_PLATFORM": "windows"}
        context = mock.MagicMock()
        context.__enter__.return_value = display
        process = mock.Mock(returncode=0)
        process.poll.return_value = 0
        def launch(*args, **kwargs):
            (self.directory / "host-events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in
                ({"stage": "profile_ready"}, {"stage": "rendering", "completed": 3, "total": 3},
                 {"stage": "complete", "completed": 3, "total": 3})))
            return process
        with mock.patch.object(supervisor.sys, "platform", "win32"), \
                mock.patch.object(supervisor, "discover_tools", return_value={}), \
                mock.patch.object(supervisor, "private_display", return_value=context), \
                mock.patch.object(display, "launch", side_effect=launch) as popen:
            result = supervisor.run(request, lambda event: None)
        self.assertEqual(result["stage"], "complete")
        self.assertTrue(result["profiled"])
        environment = popen.call_args.args[1]
        self.assertEqual(environment["QT_QPA_PLATFORM"], "windows")
        self.assertEqual(environment["TEMP"], str(self.directory / "tmp"))
        self.assertEqual(environment["TMP"], environment["TEMP"])
        context.__exit__.assert_called_once()

    def test_real_nuke_identity_is_recorded_before_suspended_thread_resumes(self):
        process = mock.Mock(pid=333, returncode=0)
        process.poll.return_value = 0
        self.api.create_process.return_value = process
        with self.display, mock.patch("psutil.Process") as identity, mock.patch.object(child, "record_process") as record:
            process.resume.side_effect = lambda: record.assert_called_once_with(identity.return_value, self.directory)
            command = ["C:/Nuke with spaces/Nuke.exe", "-t"]
            self.assertIs(self.display.launch(command, {}, str(self.directory), "log"), process)
            self.assertEqual(self.api.create_process.call_args.args[:3], (command, self.display.number, self.display.job))

    def test_receipt_failure_terminates_suspended_process_without_resuming(self):
        process = mock.Mock(pid=333, returncode=0)
        process.poll.return_value = 0
        self.api.create_process.return_value = process
        with self.display, mock.patch("psutil.Process"), \
                mock.patch.object(child, "record_process", side_effect=OSError("Cannot record identity")):
            with self.assertRaisesRegex(OSError, "Cannot record identity"):
                self.display.launch(["Nuke.exe"], {}, str(self.directory), "log")
            process.terminate.assert_called_once()
            process.resume.assert_not_called()

    def test_recorded_cleanup_on_windows_checks_process_identity_and_avoids_posix_calls(self):
        (self.directory / "child-333.json").write_text(json.dumps({"pid": 333, "created": 10.0}))
        process = mock.Mock()
        process.create_time.return_value = 11.0
        process.children.return_value = [mock.Mock()]
        with mock.patch.object(displays.sys, "platform", "win32"), mock.patch("psutil.Process", return_value=process):
            self.assertFalse(displays.stop_recorded_children(self.directory))
            process.terminate.assert_not_called()
            process.create_time.return_value = 10.0
            self.assertTrue(displays.stop_recorded_children(self.directory))
            process.terminate.assert_called_once()
            process.children.return_value[0].terminate.assert_called_once()

    def test_native_verifier_writes_truthful_success_and_failure_reports(self):
        from test.denoise_windows_acceptance import verify
        with mock.patch.object(app_cache, "CACHE_ROOT", self.directory / "cache"), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch("platform.platform", return_value="Windows test"), \
                mock.patch("test.denoise_windows_acceptance.resolve_executable", return_value="Nuke.exe"), \
                mock.patch("test.denoise_windows_acceptance.preflight", return_value={"nuke": "17.1v2", "stage": "complete"}), \
                mock.patch("test.denoise_template_acceptance.acceptance", return_value={"frames": 1, "batch": {"completed": 2}}) as single, \
                redirect_stdout(io.StringIO()):
            self.assertTrue(verify("Nuke.exe")["success"])
            single.side_effect = RuntimeError("Window capture failed")
            self.assertFalse(verify("Nuke.exe")["success"])
            single.assert_called_with("Nuke.exe", expected_version="17.1v2")
        reports = [json.loads(path.read_text()) for path in (self.directory / "cache/temp").glob("*/verification.json")]
        self.assertEqual(sorted(report["success"] for report in reports), [False, True])
        failed = next(report for report in reports if not report["success"])
        self.assertEqual(failed["error"], "Window capture failed")
        self.assertEqual(failed["stage"], "single")

    def test_windows_verifier_selects_only_target_installation(self):
        from test.denoise_windows_acceptance import resolve_executable
        older = self.directory / "Nuke17.0v1/Nuke17.0.exe"
        older.parent.mkdir()
        older.touch()
        with mock.patch.dict("os.environ", {"ProgramFiles": str(self.directory)}):
            with self.assertRaisesRegex(RuntimeError, "17.1v2 executable not found"):
                resolve_executable()
            target = self.directory / "Nuke17.1v2/Nuke17.1.exe"
            target.parent.mkdir()
            target.touch()
            self.assertEqual(resolve_executable(), str(target.resolve()))

    def test_startup_probe_records_version_license_and_plugin_failures(self):
        from test.denoise_test_support import startup_probe
        noise = mock.Mock()
        noise.Class.return_value = "OFXcom.absoft.NeatVideo6_v6"
        nuke = SimpleNamespace(NUKE_VERSION_STRING="17.0v1", env={"nukex": True}, createNode=mock.Mock(return_value=noise))
        with mock.patch.dict(sys.modules, {"nuke": nuke}), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Expected NukeX 17.1v2"):
                startup_probe(self.directory, "17.1v2")
            status = json.loads((self.directory / "startup-status.json").read_text())
            self.assertEqual(status["stage"], "nuke_version")
            nuke.createNode.assert_not_called()
            nuke.NUKE_VERSION_STRING = "17.1v2"
            nuke.env["nukex"] = False
            with self.assertRaisesRegex(RuntimeError, "NukeX mode is unavailable"):
                startup_probe(self.directory, "17.1v2")
            status = json.loads((self.directory / "startup-status.json").read_text())
            self.assertEqual(status["stage"], "nukex_license")
            nuke.env["nukex"] = True
            nuke.createNode.side_effect = RuntimeError("Plugin not found")
            with self.assertRaisesRegex(RuntimeError, "Plugin not found"):
                startup_probe(self.directory, "17.1v2")
            status = json.loads((self.directory / "startup-status.json").read_text())
            self.assertEqual(status["stage"], "neat_plugin")
            self.assertEqual(status["error"], "Plugin not found")
            nuke.createNode.side_effect = None
            self.assertIs(startup_probe(self.directory, "17.1v2"), noise)
            status = json.loads((self.directory / "startup-status.json").read_text())
            self.assertEqual(status["stage"], "complete")
            self.assertEqual(status["completed"], ["import_nuke", "nuke_version", "nukex_license", "neat_plugin"])

    def test_preflight_failure_prevents_single_and_batch_jobs(self):
        from test.denoise_windows_acceptance import verify
        with mock.patch.object(app_cache, "CACHE_ROOT", self.directory / "cache"), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch("platform.platform", return_value="Windows test"), \
                mock.patch("test.denoise_windows_acceptance.resolve_executable", return_value="Nuke.exe"), \
                mock.patch("test.denoise_windows_acceptance.preflight", side_effect=RuntimeError("Plugin not found")), \
                mock.patch("test.denoise_template_acceptance.acceptance") as single, redirect_stdout(io.StringIO()):
            report = verify()
        self.assertFalse(report["success"])
        self.assertEqual(report["stage"], "preflight")
        single.assert_not_called()

    def test_missing_target_executable_still_produces_a_failed_report(self):
        from test.denoise_windows_acceptance import verify
        with mock.patch.object(app_cache, "CACHE_ROOT", self.directory / "cache"), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch("platform.platform", return_value="Windows test"), redirect_stdout(io.StringIO()):
            report = verify(str(self.directory / "missing.exe"))
        self.assertFalse(report["success"])
        self.assertEqual(report["stage"], "executable_selection")
        self.assertIn("17.1v2 executable not found", report["error"])
        self.assertTrue(list((self.directory / "cache/temp").glob("*/verification.json")))

    def test_native_fixture_launcher_does_not_move_existing_qt_thread(self):
        from test.denoise_test_support import host_command
        context = mock.MagicMock()
        display = context.__enter__.return_value
        display.environment.side_effect = lambda environment: environment
        display.launch.return_value.wait.return_value = 0
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(windows, "WindowsDisplay", return_value=context) as constructor, \
                mock.patch.object(subprocess, "run") as run:
            host_command("Nuke.exe", "fixture", self.directory, {})
        self.assertFalse(constructor.call_args.kwargs["attach"])
        display.launch.assert_called_once()
        run.assert_not_called()

    def test_fixture_exit_and_timeout_show_actual_nuke_log_in_verifier_error(self):
        from test.denoise_test_support import host_command
        for failure in (subprocess.CalledProcessError(1, ["Nuke.exe"]), subprocess.TimeoutExpired(["Nuke.exe"], 90)):
            with self.subTest(failure=type(failure).__name__):
                def fail(command, **kwargs):
                    kwargs["stdout"].write(b"Nuke startup\nRuntimeError: fixture plugin could not load\n")
                    raise failure
                with mock.patch.object(sys, "platform", "linux"), mock.patch.object(subprocess, "run", side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, "fixture plugin could not load") as caught:
                        host_command("Nuke.exe", "fixture", self.directory, {})
                self.assertIn(str(self.directory / "fixture.log"), str(caught.exception))
                self.assertIs(caught.exception.__cause__, failure)


if __name__ == "__main__":
    unittest.main()
