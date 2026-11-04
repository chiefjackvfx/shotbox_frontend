from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_detection import RenderReporter, scan_outputs


class RenderDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'comp'
        self.api = Mock()
        self.reporter = RenderReporter(lambda **kwargs: self.api, start_worker=False)
        self.scope = ('http://backend/api', 1, self.root)

    def scan(self):
        self.reporter.scan(*self.scope)

    def frame(self, version=13, frame=1001):
        directory = self.root / f'bam010_v{version:03d}'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'bam010_v{version:03d}_{frame}.exr').write_bytes(b'frame')

    def test_baseline_and_missing_directory(self):
        self.scan()
        self.frame()
        self.scan()
        self.assertEqual(len(self.reporter.pending), 1)
        self.frame(frame=1002)
        self.scan()
        self.assertEqual(len(self.reporter.pending), 1)
        self.reporter.deliver()
        self.frame(frame=1001)
        self.scan()
        self.assertFalse(self.reporter.pending)
        self.assertEqual(self.api.report_render_detected.call_count, 1)
        for frame in (self.root / 'bam010_v013').iterdir():
            frame.unlink()
        (self.root / 'bam010_v013').rmdir()
        self.scan()
        self.frame()
        self.scan()
        self.assertFalse(self.reporter.pending)

    def test_existing_outputs_baselined_and_formats_independent(self):
        self.frame()
        self.scan()
        self.assertFalse(self.reporter.pending)
        (self.root / 'bam010_v013.mov').write_bytes(b'movie')
        self.scan()
        self.assertEqual(next(iter(self.reporter.pending.values()))['payload']['output_type'], 'mov')
        self.frame(version=14)
        self.scan()
        self.assertEqual(len(self.reporter.pending), 2)

    def test_inaccessible_is_not_empty_baseline(self):
        with patch('render_detection.scan_outputs', side_effect=PermissionError('denied')):
            self.scan()
        self.assertFalse(self.reporter.seen)
        self.frame()
        self.scan()
        self.assertFalse(self.reporter.pending)

    def test_empty_or_unrelated_files_ignored(self):
        self.root.mkdir()
        (self.root / 'bam010_v013.mov').touch()
        (self.root / 'bam010_v014').mkdir()
        (self.root / 'bam010_v014' / 'unrelated.exr').write_bytes(b'x')
        self.assertEqual(scan_outputs(self.root), {})

    def test_retry_keeps_timestamp_and_card_reload_state(self):
        self.scan()
        self.frame()
        self.scan()
        event = next(iter(self.reporter.pending.values()))
        original = dict(event['payload'])
        self.api.report_render_detected.side_effect = ConnectionError('offline')
        self.reporter.deliver()
        self.assertGreater(event['retry_at'], time.monotonic())
        self.reporter.deliver()
        self.assertEqual(self.api.report_render_detected.call_count, 1)
        # New card requests a scan against the same application-lifetime reporter.
        self.scan()
        self.assertEqual(len(self.reporter.pending), 1)
        event['retry_at'] = 0
        self.api.report_render_detected.side_effect = None
        self.reporter.deliver()
        self.api.report_render_detected.assert_called_with(1, **original)
        self.assertFalse(self.reporter.pending)
        self.scan()
        self.assertFalse(self.reporter.pending)

    def test_permanent_failure_logged_and_not_retried(self):
        self.scan()
        self.frame()
        self.scan()
        error = RuntimeError('bad request')
        error.status_code = 400
        self.api.report_render_detected.side_effect = error
        with self.assertLogs('render_detection', level='ERROR'):
            self.reporter.deliver()
        self.assertFalse(self.reporter.pending)

    def test_http_submission_uses_explicit_backend_and_payload(self):
        from http_help import DjangoAPI
        api = DjangoAPI(base_url="http://backend/api/")
        api._request = Mock()
        payload = {'output_path': 'bam010_v013', 'output_type': 'exr',
                   'version': 13, 'observed_at': '2026-11-04T12:00:00+00:00'}
        api.report_render_detected(12, **payload)
        api._request.assert_called_once_with(
            'POST', 'http://backend/api/shots/12/render-detected',
            json=payload, operation='report_render_detected')

    def test_backend_and_shot_scopes_separate(self):
        self.scan()
        self.reporter.scan('http://other/api', 1, self.root)
        self.reporter.scan(self.scope[0], 2, self.root)
        self.frame()
        self.scan()
        self.reporter.scan('http://other/api', 1, self.root)
        self.reporter.scan(self.scope[0], 2, self.root)
        self.assertEqual(len(self.reporter.pending), 3)

    def test_worker_is_async_and_single_flight(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def report(*args, **kwargs):
            calls.append(threading.get_ident())
            entered.set()
            release.wait(3)
        self.api.report_render_detected.side_effect = report
        reporter = RenderReporter(lambda **kwargs: self.api)
        try:
            # Queue a delivery to test worker isolation without filesystem timing races.
            with reporter._condition:
                reporter.pending[('http://backend/api', 1, 'bam010_v013')] = {
                    'payload': {}, 'retry_at': 0,
                }
                reporter._condition.notify()
            self.assertTrue(entered.wait(2))
            for _ in range(5):
                reporter.request_scan(*self.scope)
            self.assertEqual(len(calls), 1)
            self.assertNotEqual(calls[0], threading.get_ident())
        finally:
            release.set()
            reporter.stop()
            reporter._thread.join(3)
        self.assertFalse(reporter._thread.is_alive())


if __name__ == '__main__':
    unittest.main()
