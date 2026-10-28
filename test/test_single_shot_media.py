"""Real EXR coverage for standalone shot creation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import OpenEXR
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
import import_xml_v2 as module
from single_shot_media import exr_source, probe_single_media


class SingleShotMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def frames(self, root, numbers=(1041, 1042, 1043), prefix='plate'):
        paths = []
        for frame in numbers:
            path = root / f'{prefix}.{frame:04}.exr'
            header = OpenEXR.Header(8, 4)
            header.update(cameraMake=b'ARRI', cameraModel=b'ALEXA Mini LF', lensModel=b'Example Lens',
                          focalLength=b'35 mm', colorspace=b'ACEScg')
            image = OpenEXR.OutputFile(str(path), header)
            try:
                pixels = np.full((4, 8), 0.18, dtype=np.float32).tobytes()
                image.writePixels({channel: pixels for channel in 'RGB'})
            finally:
                image.close()
            paths.append(path)
        return paths

    def test_exr_thumbnail_probing_copy_and_request_keep_full_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = self.frames(root)
            source, timing = probe_single_media(str(frames[1]))
            self.assertEqual(source, str(frames[0]))
            self.assertEqual((timing['source_first'], timing['source_last'], timing['frames']), (1041, 1043, 3))
            self.assertEqual(timing['camera_model'], 'ALEXA Mini LF')
            self.assertEqual(timing['input_colourspace'], 'ACES - ACEScg')
            thumb = root / 'thumbnail.jpeg'
            self.assertEqual(module.ThumbnailGenerator.generate(str(frames[1]), str(thumb), frame=0,
                                                               input_colourspace=timing['input_colourspace']), str(thumb))
            image = QImage(str(thumb))
            self.assertFalse(image.isNull())
            self.assertGreater(image.pixelColor(0, 0).red(), 0)
            page = module.XMLImportPage.__new__(module.XMLImportPage)
            shot = page._build_parsed_shot_from_clip_files('sho010', [str(frames[1])])
            shot.notes = 'Remove the marker.'
            self.assertEqual((shot.duration, shot.edit_inpoint, shot.edit_outpoint), (3, 1001, 1003))
            folder = root / 'shot'
            generator = module.NukeScriptGenerator()
            generator._copy_clips_to_plates(shot, str(folder))
            self.assertEqual(len(list((folder / 'plates/plate_01').glob('*.exr'))), 3)
            request = generator._build_create_nk_request(shot, str(folder), str(folder/'scripts/shot.nk'), 'project')
            self.assertEqual(request.plate_timings[0]['source_first'], 1041)
            self.assertEqual(request.denoise_plates[0]['source_last'], 1043)
            self.assertIn('Remove the marker.', request.shot_metadata_lines)
            self.assertTrue(any('Camera: ARRI ALEXA Mini LF' in line for line in request.shot_metadata_lines))
            self.assertTrue(any('Lens: Example Lens' in line for line in request.shot_metadata_lines))
            self.assertEqual((request.format_width, request.format_height), (8, 4))

    def test_gapped_sequence_fails_before_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory), (1001, 1003))
            with self.assertRaisesRegex(RuntimeError, 'missing frames'):
                probe_single_media(str(frames[0]))

    def test_dialog_groups_exr_frames_and_keeps_editable_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = self.frames(root)
            dialog = module.SingleShotCreationDialog(add_to_db=False)
            try:
                dialog._add_clip_paths([str(path) for path in frames])
                self.assertEqual(len(dialog.selected_clip_file_paths), 1)
                dialog.output_directory_path_line_edit.setText(str(root))
                dialog.notes_edit.setPlainText('Clean the sky.\nKeep the grain.')
                dialog.auto_colourspace_checkbox.setChecked(False)
                dialog.fps_spinbox.setValue(24)
                dialog._validate_and_accept()
                self.assertEqual(dialog.notes, 'Clean the sky.\nKeep the grain.')
                self.assertFalse(dialog.automatic_colourspace)
                self.assertEqual(dialog.fps, 24)
            finally:
                dialog.close()

    def test_movie_duration_uses_real_frame_count_and_rate(self):
        import av
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.mp4'
            with av.open(str(path), 'w') as container:
                stream = container.add_stream('mpeg4', rate=24)
                stream.width, stream.height, stream.pix_fmt = 64, 32, 'yuv420p'
                for _ in range(12):
                    frame = av.VideoFrame.from_ndarray(np.full((32, 64, 3), 100, dtype=np.uint8), format='rgb24')
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            _, timing = probe_single_media(str(path), automatic=False, fallback_colourspace='ACES - ACEScg')
            self.assertEqual((timing['frames'], timing['fps']), (12, 24))
            page = module.XMLImportPage.__new__(module.XMLImportPage)
            shot = page._build_parsed_shot_from_clip_files('sho010', [str(path)])
            self.assertEqual((shot.duration, shot.fps, shot.edit_outpoint), (12, 24, 1012))
            dialog = module.SingleShotCreationDialog(add_to_db=False)
            try:
                dialog._add_clip_paths([str(path)])
                dialog._media_inspected(dialog._review_generation, [timing])
                self.assertIn('12 frames', dialog.clip_slots[0].duration_label.text())
                self.assertIn('0.500 seconds', dialog.clip_slots[0].duration_label.text())
                self.assertIn('24 fps', dialog.clip_slots[0].duration_label.text())
                dialog._media_inspected(dialog._review_generation - 1, [{'error': 'stale'}])
                self.assertIn('12 frames', dialog.clip_slots[0].duration_label.text())
            finally:
                dialog.close()

    def test_notes_preserve_existing_text_and_include_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = self.frames(Path(directory))
            page = module.XMLImportPage.__new__(module.XMLImportPage)
            shot = page._build_parsed_shot_from_clip_files('sho010', [str(frames[0])])
            shot.notes = 'Remove the marker.'
            api = mock.Mock()
            api.get_or_create_job.return_value = ({'id': 1}, False)
            api.get_or_create_timeline.return_value = ({'id': 2}, False)
            api.get_or_create_shot.return_value = ({'id': 3}, False)
            api.get_shot.return_value = {'notes': 'Existing artist note.'}
            importer = module.DjangoImporter(api=api)
            importer.import_shot(shot, 'Project', 'Timeline', 'ACES - ACEScg', 25)
            notes = api.update_shot.call_args.kwargs['notes']
            self.assertIn('Existing artist note.', notes)
            self.assertIn('Remove the marker.', notes)
            self.assertIn('Source frames: 1041-1043 at 25 fps', notes)
            self.assertIn('Input transform: ACES - ACEScg', notes)
            self.assertEqual(api.update_shot.call_args.kwargs['handles'], '0')

    def test_exr_folder_drop_groups_sequences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.frames(root)
            self.frames(root, prefix='second')
            dialog = module.SingleShotCreationDialog(add_to_db=False)
            try:
                dialog._add_clip_paths([str(root)])
                self.assertEqual(len(dialog.selected_clip_file_paths), 2)
            finally:
                dialog.close()

    def test_worker_reports_failures(self):
        worker = module.SingleShotWorker(mock.Mock(side_effect=ValueError('Bad media')))
        failures = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, ['Bad media'])
