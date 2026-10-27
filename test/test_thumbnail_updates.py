from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import av
import numpy as np
from PyQt6.QtGui import QImage

import filesIO
import thumbnail_updates


class ThumbnailUpdateTests(unittest.TestCase):
    def image(self):
        image = QImage(64, 32, QImage.Format.Format_RGB888)
        image.fill(0x0000FF)
        return image

    def make_preview(self, path: Path):
        with av.open(str(path), mode="w") as output:
            stream = output.add_stream("mpeg4", rate=10)
            stream.width, stream.height = 64, 32
            stream.pix_fmt = "yuv420p"
            for index in range(10):
                array = np.zeros((32, 64, 3), dtype=np.uint8)
                array[:, :, 0 if index < 5 else 2] = 255
                for packet in stream.encode(av.VideoFrame.from_ndarray(array, format="rgb24")):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)

    def test_decodes_middle_frame_instead_of_first_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.mp4"
            self.make_preview(path)
            image = thumbnail_updates.extract_preview_thumbnail(str(path))
        self.assertEqual((image.width(), image.height()), (64, 32))
        color = image.pixelColor(0, 0)
        self.assertGreater(color.blue(), 240)
        self.assertLess(color.red(), 10)

    def test_exact_source_frame_preserves_occurrence_cut_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preview.mp4'
            self.make_preview(path)
            image = thumbnail_updates.extract_preview_thumbnail(str(path), frame_index=2)
        self.assertGreater(image.pixelColor(0, 0).red(), 240)
        self.assertLess(image.pixelColor(0, 0).blue(), 10)

    def test_reserved_colour_tags_on_twelve_bit_yuv_do_not_produce_black_thumbnail(self):
        source = np.full((32, 64, 3), 100, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(source, format='rgb24').reformat(format='yuv444p12le')
        frame.colorspace = frame.color_primaries = frame.color_trc = 0
        container = mock.Mock()
        container.duration = None
        container.streams.video = [SimpleNamespace(start_time=0, duration=None, time_base=None, frames=1)]
        container.decode.return_value = iter([frame])
        with mock.patch.object(av, 'open') as open_video:
            open_video.return_value.__enter__.return_value = container
            image = thumbnail_updates.extract_preview_thumbnail('camera.mxf', frame_index=0)
        self.assertFalse(image.isNull())
        self.assertGreater(image.pixelColor(0, 0).red(), 70)
        self.assertEqual((frame.colorspace, frame.color_primaries, frame.color_trc), (1, 1, 1))

    def test_valid_colour_metadata_is_preserved(self):
        frame = av.VideoFrame.from_ndarray(np.full((32, 64, 3), 100, dtype=np.uint8), format='rgb24').reformat(format='yuv420p')
        frame.colorspace, frame.color_primaries, frame.color_trc = 9, 9, 16
        container = mock.Mock()
        container.duration = None
        container.streams.video = [SimpleNamespace(start_time=0, duration=None, time_base=None, frames=1)]
        container.decode.return_value = iter([frame])
        with mock.patch.object(av, 'open') as open_video:
            open_video.return_value.__enter__.return_value = container
            thumbnail_updates.extract_preview_thumbnail('valid.mxf', frame_index=0)
        self.assertEqual((frame.colorspace, frame.color_primaries, frame.color_trc), (9, 9, 16))

    def test_cancellation_interrupts_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.mp4"
            self.make_preview(path)
            with self.assertRaises(thumbnail_updates.ThumbnailUpdateCancelled):
                thumbnail_updates.extract_preview_thumbnail(str(path), check_cancelled=lambda: True)

    def test_missing_duration_still_selects_middle_frame(self):
        frames = []
        for index in range(10):
            array = np.zeros((32, 64, 3), dtype=np.uint8)
            array[:, :, 0 if index < 5 else 2] = 255
            frames.append(av.VideoFrame.from_ndarray(array, format="rgb24"))
        for frame_count in (10, 0):
            with self.subTest(frame_count=frame_count):
                container = mock.Mock()
                container.duration = None
                container.streams.video = [SimpleNamespace(start_time=0, duration=None, time_base=None, frames=frame_count)]
                container.decode.side_effect = [iter(frames), iter(frames)]
                with mock.patch.object(av, "open") as open_video:
                    open_video.return_value.__enter__.return_value = container
                    image = thumbnail_updates.extract_preview_thumbnail("preview.mp4")
                self.assertGreater(image.pixelColor(0, 0).blue(), 240)
                self.assertLess(image.pixelColor(0, 0).red(), 10)

    def test_latest_filesystem_version_overrides_stored_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            previews = Path(directory) / "renders" / "precomp" / "previews"
            previews.mkdir(parents=True)
            old = previews / "sho010_v001.mp4"
            latest = previews / "sho010_v003.mp4"
            old.touch()
            latest.touch()
            shot = {"base_path": directory, "preview_video": str(old)}
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()), str(latest))

    def test_stored_preview_fallback_and_missing_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            stored = Path(directory) / "custom.mp4"
            stored.touch()
            shot = {"base_path": directory, "preview_video": "custom.mp4"}
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()), str(stored))
            stored.unlink()
            self.assertIsNone(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()))
        self.assertIsNone(thumbnail_updates.latest_thumbnail_preview({}, filesIO.Folders()))

    def test_uses_primary_mxf_when_no_preview_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            plates = Path(directory, 'plates')
            plates.mkdir()
            primary = plates / 'camera.MXF'
            secondary = plates / 'layer.mxf'
            primary.touch()
            secondary.touch()
            shot = {'base_path': directory, 'original_clip': str(primary), 'original_clips': [str(primary), str(secondary)]}
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()), str(primary))
            primary.unlink()
            self.assertIsNone(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()))

    def test_primary_mxf_does_not_override_rendered_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            previews = Path(directory, 'renders', 'precomp', 'previews')
            previews.mkdir(parents=True)
            preview = previews / 'sho010_v001.mp4'
            preview.touch()
            primary = Path(directory, 'camera.mxf')
            primary.touch()
            shot = {'base_path': directory, 'original_clip': str(primary)}
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()), str(preview))

    def test_stored_mxf_and_unambiguous_legacy_plate_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            plates = Path(directory, 'plates')
            plates.mkdir()
            video = plates / 'primary.mxf'
            video.touch()
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview({'base_path': directory, 'preview_video': 'plates/primary.mxf'}, filesIO.Folders()), str(video))
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview({'base_path': directory}, filesIO.Folders()), str(video))
            (plates / 'another.mxf').touch()
            self.assertIsNone(thumbnail_updates.latest_thumbnail_preview({'base_path': directory}, filesIO.Folders()))

    def test_sidecar_identifies_primary_and_the_middle_of_its_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            plates = Path(directory, 'plates')
            plates.mkdir()
            primary = plates / 'shared.mxf'
            primary.touch()
            (plates / 'secondary.mxf').touch()
            assets = Path(directory, 'Shot_Assets')
            assets.mkdir()
            metadata = {'cut_duration': 11, 'plates': [{'track': 1, 'copied_path': '/old/shot/plates/shared.mxf', 'cut_start': 42.221967, 'speed': 0.6361556}]}
            (assets / 'timeline_import.json').write_text(json.dumps(metadata))
            shot = {'base_path': directory}
            self.assertEqual(thumbnail_updates.latest_thumbnail_preview(shot, filesIO.Folders()), str(primary))
            self.assertEqual(thumbnail_updates._source_thumbnail_frame(shot, filesIO.Folders(), str(primary)), 45)
            self.assertIsNone(thumbnail_updates._source_thumbnail_frame(shot, filesIO.Folders(), str(plates / 'render.mp4')))

    def test_import_thumbnail_uses_pyav_for_mxf_and_without_opencv(self):
        from import_xml_v2 import ThumbnailGenerator
        import import_xml_v2

        with tempfile.TemporaryDirectory() as directory:
            for extension, installed in (('.mxf', True), ('.mp4', False)):
                with self.subTest(extension=extension):
                    video = Path(directory, 'camera' + extension)
                    video.touch()
                    output = Path(directory, 'thumbnail' + extension + '.jpeg')
                    with mock.patch.object(import_xml_v2, 'HAS_CV2', installed), \
                            mock.patch.object(thumbnail_updates, 'extract_preview_thumbnail', return_value=self.image()) as extract:
                        result = ThumbnailGenerator.generate(str(video), str(output), frame=10)
                    self.assertEqual(result, str(output))
                    self.assertEqual((QImage(str(output)).width(), QImage(str(output)).height()), (32, 16))
                    extract.assert_called_once_with(str(video), frame_index=10)

    def test_batch_continues_after_failed_upload_and_skips_missing_previews(self):
        api = mock.Mock()
        paths = []

        def upload(shot_id, path):
            paths.append(Path(path))
            self.assertEqual(QImage(path).size(), self.image().size())
            if shot_id == 1:
                raise RuntimeError("Upload unavailable")
            return {"thumbnail": "/media/new.png"}

        api.upload_shot_thumbnail.side_effect = upload
        worker = thumbnail_updates.BatchThumbnailWorker(
            [{"id": i, "title": f"Shot {i}"} for i in (1, 2, 3)], api, mock.Mock(),
        )
        summaries, updates = [], []
        worker.signals.finished.connect(summaries.append)
        worker.signals.updated.connect(lambda shot_id, *_: updates.append(shot_id))
        with mock.patch.object(thumbnail_updates, "latest_thumbnail_preview", side_effect=["one.mp4", None, "three.mp4"]), \
                mock.patch.object(thumbnail_updates, "extract_preview_thumbnail", return_value=self.image()):
            worker.run()
        self.assertEqual(updates, [3])
        self.assertEqual(api.upload_shot_thumbnail.call_count, 2)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(summaries[0]["updated"], 1)
        self.assertEqual(summaries[0]["failed"], 1)
        self.assertEqual(summaries[0]["skipped"], 1)
        self.assertEqual(summaries[0]["remaining"], 0)
        self.assertIn("Upload unavailable", summaries[0]["errors"][0])

    def test_cancel_after_current_upload_keeps_completed_result_and_stops_next_shot(self):
        api = mock.Mock()
        worker = thumbnail_updates.BatchThumbnailWorker([{"id": 1}, {"id": 2}], api, mock.Mock())
        summaries = []
        worker.signals.finished.connect(summaries.append)

        def upload(*_):
            worker.cancel()
            return {"thumbnail": "/media/new.png"}

        api.upload_shot_thumbnail.side_effect = upload
        with mock.patch.object(thumbnail_updates, "latest_thumbnail_preview", return_value="one.mp4"), \
                mock.patch.object(thumbnail_updates, "extract_preview_thumbnail", return_value=self.image()):
            worker.run()
        api.upload_shot_thumbnail.assert_called_once()
        self.assertTrue(summaries[0]["cancelled"])
        self.assertEqual(summaries[0]["updated"], 1)
        self.assertEqual(summaries[0]["remaining"], 1)

    def test_cancel_after_decode_does_not_upload(self):
        api = mock.Mock()
        worker = thumbnail_updates.BatchThumbnailWorker([{"id": 1}], api, mock.Mock())
        summaries = []
        worker.signals.finished.connect(summaries.append)

        def capture(*_, **__):
            worker.cancel()
            return self.image()

        with mock.patch.object(thumbnail_updates, "latest_thumbnail_preview", return_value="one.mp4"), \
                mock.patch.object(thumbnail_updates, "extract_preview_thumbnail", side_effect=capture):
            worker.run()
        api.upload_shot_thumbnail.assert_not_called()
        self.assertTrue(summaries[0]["cancelled"])
        self.assertEqual(summaries[0]["remaining"], 1)


if __name__ == "__main__":
    unittest.main()
