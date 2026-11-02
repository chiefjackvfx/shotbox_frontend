import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import app_cache
import deadline_client
import preview_deadline as module


class PreviewDeadlineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp'))
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.source = self.directory / 'source.mov'
        self.source.touch()
        self.task = {'shot': {'title': 'SH010', 'base_path': str(self.directory)},
                     'source_path': str(self.source), 'source_type': 'original_clip',
                     'job_name': 'Project', 'version': 1, 'plate_name': 'plate_01'}
        self.options = {'batch_name': 'Batch', 'pool': 'none', 'group': 'none', 'submission_id': 'test',
                        'chunked': True, 'chunk_size': 5, 'machine_list': 'renderhost'}
        self.info = {'executables': {'RenderExecutable17_1': '/opt/Nuke17.1v2/Nuke17.1'}}

    def test_movie_submission_is_one_complete_task_with_auxiliary_scene(self):
        scene = self.directory / 'preview.nk'
        files = module.submission_files(self.directory, scene, self.directory / 'movie.mp4',
                                       {'first': 1001, 'last': 1250, 'nuke_version': '17.1v2'}, self.options, self.info)
        job, plugin = Path(files[0]).read_text(), Path(files[1]).read_text()
        self.assertIn('ChunkSize=250\n', job)
        self.assertIn('Frames=1001-1250\n', job)
        self.assertIn('Whitelist=renderhost\n', job)
        self.assertIn('WriteNode=WriteCompMP4\n', plugin)
        self.assertIn('BatchMode=False\n', plugin)
        self.assertNotIn('SceneFile=', plugin)
        self.assertEqual(files[2], str(scene))

    def test_submission_prepares_overwrite_without_local_render_and_reports_queued(self):
        output, _, _ = module.build_preview_output_path(str(self.directory), 'SH010', version=1, plate_name='plate_01')
        output.write_bytes(b'existing movie')
        calls = []
        def command(executable, args, **kwargs):
            calls.append((executable, args))
            if executable == '/nuke':
                request = json.loads(Path(args[-1]).read_text())
                Path(request['scene']).touch()
                Path(request['result']).write_text(json.dumps({'first': 1001, 'last': 1024, 'nuke_version': '17.1v2'}))
                return ''
            return 'JobID=' + 'a'*24
        with mock.patch.object(module.client, 'discover_client', return_value='/deadline'), \
                mock.patch.object(module.client, 'repository_info', return_value=self.info), \
                mock.patch.object(module.client, 'validate_destination'), \
                mock.patch.object(module.client, 'validate_options'), \
                mock.patch.object(module.client, 'command', side_effect=command):
            result = module.submit_preview(self.task, mock.Mock(nuke_exe='/nuke'), self.options,
                                           mock.Mock(convert_path=lambda p: p), quality='medium', overwrite=True)
        self.assertEqual(output.read_bytes(), b'existing movie')
        self.assertEqual(result, {'stage': 'submitted', 'job_id': 'a'*24})
        self.assertEqual(len(calls), 2)
        self.assertIn('preview_deadline_prepare.py', calls[0][1][1])

    def test_existing_output_is_not_submitted_without_overwrite(self):
        output, _, _ = module.build_preview_output_path(str(self.directory), 'SH010', version=1, plate_name='plate_01')
        output.touch()
        with mock.patch.object(module.client, 'discover_client') as discover:
            result = module.submit_preview(self.task, mock.Mock(), self.options,
                                           mock.Mock(convert_path=lambda p: p), quality='medium', overwrite=False)
        self.assertEqual(result['stage'], 'skipped')
        discover.assert_not_called()

    def test_uncertain_submission_returns_reference_without_retry(self):
        def command(executable, args, **kwargs):
            if executable == '/nuke':
                request = json.loads(Path(args[-1]).read_text())
                Path(request['scene']).touch()
                Path(request['result']).write_text(json.dumps({'first': 1001, 'last': 1024, 'nuke_version': '17.1v2'}))
                return ''
            raise deadline_client.CommandInterrupted('Submission timed out; check Deadline Monitor.')
        with mock.patch.object(module.client, 'discover_client', return_value='/deadline'), \
                mock.patch.object(module.client, 'repository_info', return_value=self.info), \
                mock.patch.object(module.client, 'validate_destination'), \
                mock.patch.object(module.client, 'validate_options'), \
                mock.patch.object(module.client, 'command', side_effect=command) as run:
            result = module.submit_preview(self.task, mock.Mock(nuke_exe='/nuke'), self.options,
                                           mock.Mock(convert_path=lambda p: p), quality='medium', overwrite=False)
        self.assertEqual(result['stage'], 'unknown')
        self.assertIn('Submission reference:', result['error'])
        self.assertEqual(run.call_count, 2)

    def test_cancel_before_start_does_not_contact_deadline(self):
        with mock.patch.object(module.client, 'discover_client') as discover:
            with self.assertRaises(module.DenoiseCancelled):
                module.submit_preview(self.task, mock.Mock(), self.options, mock.Mock(),
                                      quality='medium', overwrite=False, cancelled=lambda: True)
        discover.assert_not_called()
