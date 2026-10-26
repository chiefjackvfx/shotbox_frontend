from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import app_cache
from nuke_headless_tasks import PreviewGenerator
import preview_batch


FAKE_PROCESS = r'''
import json, os, pathlib, sys, time
for line in sys.stdin:
    request = json.loads(line)
    arguments = request['arguments']
    action = arguments.get('action')
    if action == 'sleep':
        time.sleep(10)
    if action == 'crash':
        os._exit(7)
    success = action != 'fail'
    if success:
        pathlib.Path(arguments['output_path']).write_bytes(b'test output')
    print('SHOTBOX_PREVIEW_PROGRESS 1/1', flush=True)
    print('SHOTBOX_PREVIEW_RESULT ' + json.dumps({'id': request['id'], 'success': success, 'error': '' if success else 'bad media'}), flush=True)
'''


class PreviewBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        cache = mock.patch.object(app_cache, "CACHE_ROOT", self.directory / "cache")
        cache.start()
        self.addCleanup(cache.stop)
        self.launch_commands = []
        real_popen = subprocess.Popen

        def launch(command, **kwargs):
            self.launch_commands.append(command)
            return real_popen([sys.executable, "-u", "-c", FAKE_PROCESS], **kwargs)

        popen = mock.patch.object(preview_batch.subprocess, "Popen", side_effect=launch)
        popen.start()
        self.addCleanup(popen.stop)
        self.worker = preview_batch.PreviewBatchWorker("/nuke with spaces", "/headless script.py")
        self.addCleanup(self.worker.close)

    def arguments(self, name="one.mp4", **extra):
        return {"output_path": str(self.directory / name), **extra}

    def test_reuses_one_process_and_returns_frame_progress_for_multiple_jobs(self):
        lines = []
        for name in ("one.mp4", "two.mp4"):
            self.assertEqual(self.worker.render(self.arguments(name), on_output=lines.append), (True, ""))
        self.assertEqual(self.worker.launches, 1)
        self.assertEqual(len(self.launch_commands), 1)
        self.assertEqual(lines.count("SHOTBOX_PREVIEW_PROGRESS 1/1"), 2)
        self.assertEqual(len(set(line for line in lines if line.startswith("PREVIEW_WORKER_PID:"))), 1)
        process = self.worker.process
        self.worker.close()
        self.assertIsNotNone(process.poll())

    def test_media_failure_does_not_contaminate_the_next_job(self):
        self.assertEqual(self.worker.render(self.arguments(action="fail")), (False, "bad media"))
        self.assertEqual(self.worker.render(self.arguments("next.mp4")), (True, ""))
        self.assertEqual(self.worker.launches, 1)

    def test_crashed_worker_reports_failure_and_next_job_can_start_fresh(self):
        success, error = self.worker.render(self.arguments(action="crash"))
        self.assertFalse(success)
        self.assertIn("closed its output", error)
        self.assertEqual(self.worker.render(self.arguments("next.mp4")), (True, ""))
        self.assertEqual(self.worker.launches, 2)

    def test_cancel_while_output_is_silent_stops_process_without_retrying(self):
        started = time.monotonic()
        success, error = self.worker.render(
            self.arguments(action="sleep"), check_cancelled=lambda: time.monotonic() - started > .2,
        )
        self.assertFalse(success)
        self.assertIn("cancelled", error)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(self.worker.process.poll())
        self.assertEqual(self.worker.launches, 1)

    def test_cancel_before_start_does_not_launch_nuke(self):
        success, error = self.worker.render(self.arguments(), check_cancelled=lambda: True)
        self.assertFalse(success)
        self.assertIn("cancelled", error)
        self.assertFalse(self.launch_commands)

    def test_callback_failure_stops_worker_and_context_cleanup_removes_working_files(self):
        def fail(_):
            raise ValueError("closed dialog")
        with self.assertRaisesRegex(ValueError, "closed dialog"):
            self.worker.render(self.arguments(), on_output=fail)
        self.assertIsNotNone(self.worker.process.poll())
        temporary = Path(self.worker._temporary.name)
        self.worker.close()
        self.assertFalse(temporary.exists())

    def test_cache_clear_preserves_working_files_until_worker_has_stopped(self):
        self.worker.render(self.arguments())
        temporary = Path(self.worker._temporary.name)
        working_file = temporary / "in-flight"
        working_file.touch()
        result = app_cache.clear_cache_files()
        self.assertTrue(result["active_temp"])
        self.assertTrue(working_file.exists())
        self.worker.close()
        self.assertFalse(temporary.exists())
        self.assertFalse(app_cache.clear_cache_files()["active_temp"])

    def test_native_worker_clears_comp_and_isolates_errors_and_arguments(self):
        nuke = SimpleNamespace(scriptClear=mock.Mock())
        received = []
        class Task:
            def setup_nuke(self):
                return nuke

            def run(self, **arguments):
                received.append(arguments)
                if arguments.get("fail"):
                    raise ValueError("bad input")
                return True

        requests = [{"id": 1, "arguments": {"fps": 25, "source": "movie"}},
                    {"id": 2, "arguments": {"fail": True}},
                    {"id": 3, "arguments": {"fps": 30, "source": "sequence"}}]
        stdout = io.StringIO()
        stdin = io.StringIO("".join(json.dumps(request) + "\n" for request in requests))
        with mock.patch.object(sys, "stdin", stdin), redirect_stdout(stdout):
            preview_batch.run_worker(Task)
        results = [json.loads(line[len(preview_batch.RESULT_PREFIX):]) for line in stdout.getvalue().splitlines()
                   if line.startswith(preview_batch.RESULT_PREFIX)]
        self.assertEqual([result["success"] for result in results], [True, False, True])
        self.assertEqual([result["id"] for result in results], [1, 2, 3])
        self.assertEqual(received[-1], {"fps": 30, "source": "sequence"})
        self.assertEqual(nuke.scriptClear.call_count, 3)

    def test_generator_preserves_versioning_and_does_not_render_existing_output(self):
        source = self.directory / "source.mov"
        source.touch()
        executable = self.directory / "Nuke"
        executable.touch()
        generator = PreviewGenerator(str(executable))
        worker = mock.Mock(command=[str(executable), "preview_worker"])

        def render(arguments, **kwargs):
            Path(arguments["output_path"]).write_bytes(b"test MP4")
            return True, ""

        worker.render.side_effect = render
        generator._preview_worker = worker
        request = {"input_path": str(source), "shot_dir": str(self.directory), "shot_name": "sho010",
                   "version": 3, "plate_name": "plate_02", "fps": 24, "colourspace": "ACEScg", "quality": "high"}
        result = generator.generate_preview(**request)
        self.assertTrue(result.success)
        self.assertEqual(result.relative_path, "renders/precomp/previews/sho010_plate_02_v003.mp4")
        arguments = worker.render.call_args.args[0]
        self.assertEqual((arguments["fps"], arguments["colourspace"], arguments["quality"]), (24, "ACEScg", "high"))
        self.assertEqual(generator.generate_preview(**request).error, "FILE_EXISTS")
        worker.render.assert_called_once()
        generator.end_preview_batch()
        worker.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
