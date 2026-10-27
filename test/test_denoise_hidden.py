"""Private-desktop key transport and bounded visible fallback."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock
import app_cache
import nuke_denoise as supervisor
import nuke_denoise_display as displays
import nuke_denoise_windows as windows


class HiddenDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp'))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.api = mock.Mock()
        self.api.create_desktop.return_value = (888, 999)
        self.api.create_job.return_value = 123
        self.api.windows.return_value = [(333, 'Neat Video 6.1.3 Studio'), (444, 'Other Neat')]
        self.api.window_pid.side_effect = lambda hwnd: 101 if hwnd == 333 else 202
        self.api.unexpected_dialog.return_value = False
        self.api.is_viewable.return_value = True
        self.api.ready_for_key.return_value = True
        self.api.post_key.side_effect = lambda hwnd, key: {'key': key, 'events_posted': 4}
        self.process = mock.Mock(pid=101)
        self.process.poll.return_value = None
        self.display = windows.HiddenWindowsDisplay(self.directory, lambda: False, api=self.api)

    def test_private_keyboard_flow_uses_no_global_input_or_capture(self):
        with self.display, mock.patch.object(windows.time, 'monotonic', return_value=10) as clock:
            self.display.child = self.process
            self.assertFalse(self.display.visible)
            self.assertEqual(self.display.templates, {})
            self.assertEqual(self.display.environment({})['SHOTBOX_DENOISE_DESKTOP'], self.display.number)
            self.assertFalse(self.display.poll_profile())
            clock.return_value = 11.2
            self.assertFalse(self.display.poll_profile())
            clock.return_value = 12.3
            self.assertFalse(self.display.poll_profile())
            clock.return_value = 17
            self.assertFalse(self.display.poll_profile())
            self.api.post_key.assert_has_calls([mock.call(333, 0x71), mock.call(333, 0x0D)])
            self.api.key_press.assert_not_called()
            self.api.screenshot.assert_not_called()
            self.api.windows.assert_called_with(999)
            self.assertFalse((self.directory / 'profile-confirmed').exists())
            (self.directory / 'profile-confirmed').touch()
            self.api.windows.return_value = []
            self.assertTrue(self.display.poll_profile())
        self.api.close_job.assert_called_once_with(123)
        self.api.close_desktop.assert_called_once_with(999, 888)
        self.api.set_dpi_context.assert_not_called()

    def test_timeout_and_cancel_do_not_confirm_profile(self):
        with self.display, mock.patch.object(windows.time, 'monotonic', return_value=10) as clock:
            self.display.child = self.process
            self.display.poll_profile()
            clock.return_value = 100
            with self.assertRaisesRegex(windows.HiddenProfilingUnavailable, '90 seconds'):
                self.display.poll_profile()
            self.display.cancelled = lambda: True
            with self.assertRaises(displays.DenoiseCancelled):
                self.display.poll_profile()
            self.assertFalse((self.directory / 'profile-confirmed').exists())

    def test_foreign_window_never_receives_posted_keys(self):
        with self.display:
            self.display.child = self.process
            with self.assertRaisesRegex(RuntimeError, 'no longer belongs'):
                self.display._send_key(444, 0x71)
            self.api.post_key.assert_not_called()

    def test_posted_key_uses_scan_and_release_bits_without_foreground_calls(self):
        api = windows.WindowsAPI.__new__(windows.WindowsAPI)
        api.user = mock.Mock()
        api.ready_for_key = mock.Mock(return_value=True)
        api.user.MapVirtualKeyW.return_value = 0x3C
        result = api.post_key(333, 0x71)
        down = 1 | (0x3C << 16)
        self.assertEqual(api.user.PostMessageW.call_args_list, [
            mock.call(333, 6, 1, 0), mock.call(333, 7, 0, 0),
            mock.call(333, 0x100, 0x71, down), mock.call(333, 0x101, 0x71, down | 0xC0000000)])
        self.assertEqual(result['events_posted'], 4)
        api.user.SendInput.assert_not_called()
        api.user.GetForegroundWindow.assert_not_called()
        api.user.SetForegroundWindow.assert_not_called()


class HiddenFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp'))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        script = self.directory / 'template.nk'
        script.write_text('template')
        self.request = self.directory / 'request.json'
        self.request.write_text(json.dumps({'workflow':'template', 'script':str(script),
                                           'windows_display':'hidden', 'sha256':'same',
                                           'output_pattern':str(self.directory / 'out_####.exr')}))
        self.events = []

    def run_with(self, handler):
        with mock.patch.object(supervisor.sys, 'platform', 'win32'), \
                mock.patch.object(supervisor, '_run_once', side_effect=handler) as run:
            result = supervisor.run(self.request, self.events.append)
        return result, run

    def test_hidden_success_never_launches_visible_and_retains_root_profile(self):
        def complete(path, notify):
            request = json.loads(path.read_text())
            self.assertEqual(request['windows_display'], 'hidden')
            self.assertEqual(request['cancel_path'], str(self.directory / 'cancel'))
            self.assertEqual(Path(request['script']).read_text(), 'template')
            (path.parent / 'profiled.nk').write_text('profile')
            return {'stage':'complete', 'profiled':True, 'directory':str(path.parent)}
        result, run = self.run_with(complete)
        run.assert_called_once()
        self.assertEqual(result['stage'], 'complete')
        self.assertEqual(result['directory'], str(self.directory))
        self.assertEqual((self.directory / 'profiled.nk').read_text(), 'profile')
        self.assertFalse((self.directory / 'visible-attempt').exists())

    def test_hidden_input_failure_retries_fresh_visible_job_once(self):
        modes = []
        def attempt(path, notify):
            request = json.loads(path.read_text()); modes.append(request['windows_display'])
            if modes[-1] == 'hidden':
                (path.parent / 'keyboard-apply-pending').touch()
                notify({'stage':'failed', 'error':'hidden attempt failed'})
                return {'stage':'failed', 'profiled':False, 'directory':str(path.parent),
                        'error':'Hidden denoise unavailable: no response'}
            self.assertFalse((path.parent / 'keyboard-apply-pending').exists())
            return {'stage':'complete', 'profiled':True, 'directory':str(path.parent)}
        result, run = self.run_with(attempt)
        self.assertEqual(modes, ['hidden','visible'])
        self.assertEqual(result['stage'], 'complete')
        self.assertEqual(sum(event['stage']=='complete' for event in self.events), 1)
        self.assertFalse(any(event['stage']=='failed' for event in self.events))
        self.assertTrue((self.directory / 'attempts.json').exists())

    def test_committed_profile_never_retries_even_if_later_failure(self):
        def fail(path, notify):
            (path.parent / 'profile-confirmed').touch()
            return {'stage':'failed', 'profiled':False, 'directory':str(path.parent),
                    'error':'Hidden denoise unavailable: failure after commit'}
        result, run = self.run_with(fail)
        run.assert_called_once()
        self.assertEqual(result['stage'], 'failed')

    def test_cancel_after_hidden_failure_prevents_visible_launch(self):
        def cancel(path, notify):
            (self.directory / 'cancel').touch()
            return {'stage':'failed', 'profiled':False, 'directory':str(path.parent),
                    'error':'Hidden denoise unavailable: no response'}
        result, run = self.run_with(cancel)
        run.assert_called_once()
        self.assertEqual(result['stage'], 'cancelled')
        self.assertFalse((self.directory / 'visible-attempt').exists())

    def test_source_error_does_not_restart_visibly(self):
        result, run = self.run_with(lambda path, notify: {
            'stage':'failed', 'profiled':False, 'error':'Source media is missing', 'directory':str(path.parent)})
        run.assert_called_once()
        self.assertEqual(result['stage'], 'failed')

    def test_visible_failure_is_final_and_never_restarts_again(self):
        def fail(path, notify):
            return {'stage':'failed', 'profiled':False, 'directory':str(path.parent),
                    'error':'Hidden denoise unavailable: no response'}
        result, run = self.run_with(fail)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result['stage'], 'failed')
        self.assertEqual(sum(event['stage']=='failed' for event in self.events), 1)

    def test_hidden_warning_reopens_visibly_without_dismissing_it(self):
        def attempt(path, notify):
            if path.parent.name == 'hidden-attempt':
                return {'stage':'failed', 'profiled':False, 'directory':str(path.parent),
                        'error':'Unexpected dialog during denoise: Information.'}
            return {'stage':'complete', 'profiled':True, 'directory':str(path.parent)}
        result, run = self.run_with(attempt)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result['stage'], 'complete')


if __name__ == '__main__':
    unittest.main()
