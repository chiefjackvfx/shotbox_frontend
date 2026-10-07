from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import av
import numpy as np
import OpenEXR
import PyOpenColorIO as ocio
from PyQt6.QtCore import QPoint, QThreadPool, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget, QVBoxLayout, QLineEdit

import filesIO
import main
from settings import SettingsManager
from review_media import EXRSequenceReader, discover_renders, display_image
from review_ocio import OCIOPanel
from review_page import ReviewPage
from readable_combo_box import ReadableComboBox


def write_exr(path, value=0.25, **header):
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.full((4, 6), value, dtype=np.float32)
    OpenEXR.File(header, {c: pixels.copy() for c in "RGB"}).write(str(path))
    return path


def write_movie(path, value=0, *, fps=24, frame_count=12):
    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for _ in range(frame_count):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), value, np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def test_config():
    config = ocio.Config()
    config.setVersion(2, 0)
    config.setName("Test config")
    linear = ocio.ColorSpace(name="ACEScg")
    display = ocio.ColorSpace(name="Display gamma")
    display.setTransform(ocio.ExponentTransform(value=[0.5, 0.5, 0.5, 1]),
                         ocio.COLORSPACE_DIR_FROM_REFERENCE)
    config.addColorSpace(linear)
    config.addColorSpace(display)
    config.setRole(ocio.ROLE_DEFAULT, "ACEScg")
    config.setRole(ocio.ROLE_SCENE_LINEAR, "ACEScg")
    config.addDisplayView("Test display", "Gamma", "Display gamma")
    config.addDisplayView("Other display", "Raw", "ACEScg")
    config.addLook(ocio.Look(name="Boost", processSpace="ACEScg",
                            transform=ocio.MatrixTransform.Scale([2, 2, 2, 1])))
    config.validate()
    return config


class ReviewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.folders = filesIO.Folders()
        self.page = None

    def tearDown(self):
        if self.page:
            self.page.close()
        QThreadPool.globalInstance().waitForDone(5000)
        self.app.processEvents()
        if self.page:
            self.page.deleteLater()
        self.temporary.cleanup()

    def make_page(self):
        self.page = ReviewPage()
        self.page.resize(1440, 900)
        self.page.show()
        self.app.processEvents()
        return self.page

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "Background render did not settle")

    def select_media(self, page, path):
        combo = page.shot_info_header.combo_preview
        if not any((combo.itemData(i) or {}).get("value") == str(path) for i in range(combo.count())):
            was_playing = page.video_viewer.is_playing()
            page.shot_info_header.button_play_render.click()
            if not was_playing:
                page.video_viewer.pause()
        index = next(i for i in range(combo.count())
                     if combo.itemData(i).get("value") == str(path))
        combo.setCurrentIndex(index)
        return index

    def assert_media_types(self, page, expected):
        combo = page.shot_info_header.combo_preview
        types = {(combo.itemData(i) or {}).get("type") for i in range(combo.count())}
        self.assertEqual(types - {"thumbnail"}, {expected})

    def set_config(self, panel):
        config_path = self.root / "test.ocio"
        config_path.write_text(test_config().serialize())
        panel.set_source(True, "ACEScg")
        panel.edit_filename.setText(str(config_path))
        panel.combo_configuration.setCurrentIndex(panel.combo_configuration.findData("file"))
        self.assertIsNotNone(panel.processor, panel.label_status.text())


class ReviewRenderTests(ReviewTestCase):
    def test_discovery_orders_versions_and_prefers_exr_at_equal_version(self):
        comp = self.root / "renders" / "comp"
        write_movie(comp / "shot_v002.mov")
        write_movie(comp / "shot_v010.mov")
        write_exr(comp / "shot_v010" / "shot_v010.1001.exr")
        write_exr(comp / "shot_v010" / "shot_v010.1002.exr")
        renders = discover_renders(self.root, self.folders)
        self.assertEqual([(r["version"], r["type"]) for r in renders],
                         [(10, "exr"), (10, "mov"), (2, "mov")])
        self.assertEqual(renders[0]["frame_range"], "1001-1002")

    def test_reader_keeps_hdr_values_and_reports_missing_frame_numbers(self):
        first = write_exr(self.root / "shot_v002.1001.exr", 4.0)
        write_exr(self.root / "shot_v002.1003.exr", 0.5)
        write_exr(self.root / "different.1002.exr")
        reader = EXRSequenceReader(first)
        self.assertEqual(reader.total_frames, 3)
        self.assertEqual(reader.first_frame, 1001)
        np.testing.assert_allclose(reader.read_frame(0), 4.0)
        np.testing.assert_allclose(reader.read_frame(2), 0.5)
        with self.assertRaisesRegex(FileNotFoundError, "1002"):
            reader.read_frame(1)

    def test_reader_respects_sequence_template_next_to_version_digits(self):
        first = write_exr(self.root / "shot_v0021001.exr")
        write_exr(self.root / "shot_v0021002.exr")
        reader = EXRSequenceReader(first, sequence_path=self.root / "shot_v002####.exr")
        self.assertEqual((reader.first_frame, reader.last_frame), (1001, 1002))

    def test_reader_places_cropped_data_in_display_window(self):
        first = write_exr(self.root / "crop.1001.exr",
                          dataWindow=(np.array([2, 1], np.int32), np.array([7, 4], np.int32)),
                          displayWindow=(np.array([0, 0], np.int32), np.array([9, 5], np.int32)))
        pixels = EXRSequenceReader(first).read_frame(0)
        self.assertEqual(pixels.shape, (6, 10, 3))
        np.testing.assert_allclose(pixels[1:5, 2:8], 0.25)
        np.testing.assert_allclose(pixels[0], 0)

    def test_layered_exr_reads_rgb_together_without_loading_other_passes(self):
        path = self.root / "layered.1001.exr"
        planes = {"beauty." + channel: np.full((4, 6), value, np.float32)
                  for channel, value in zip("RGB", (4.0, 0.25, -0.5))}
        planes["depth.Z"] = np.full((4, 6), 999, np.float32)
        OpenEXR.File({}, planes).write(str(path))
        original = OpenEXR.InputFile
        calls = []
        class TrackedFile:
            def __init__(self, filename):
                self.file = original(filename)
            def header(self):
                return self.file.header()
            def channels(self, names, pixel_type):
                calls.append(names)
                return self.file.channels(names, pixel_type)
            def close(self):
                self.file.close()
        with mock.patch.object(OpenEXR, "InputFile", TrackedFile), mock.patch.object(
                OpenEXR, "File", side_effect=AssertionError("Must not load unrelated AOVs")):
            pixels = EXRSequenceReader(path).read_frame(0)
        self.assertEqual(calls, [["beauty.R", "beauty.G", "beauty.B"]])
        np.testing.assert_allclose(pixels[0, 0], [4.0, 0.25, -0.5])

    def test_rgba_half_exr_keeps_float_hdr_rgb_without_alpha(self):
        path = self.root / "rgba.1001.exr"
        rgba = np.full((4, 6, 4), 4.0, np.float16)
        rgba[..., 3] = 0.1
        OpenEXR.File({}, {"RGBA": rgba}).write(str(path))
        pixels = EXRSequenceReader(path).read_frame(0)
        self.assertEqual(pixels.shape, (4, 6, 3))
        self.assertEqual(pixels.dtype, np.float32)
        np.testing.assert_allclose(pixels, 4.0)

    def test_play_render_chooses_latest_mov_and_can_return_to_preview(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v010.mp4", 64)
        write_movie(self.root / "renders" / "comp" / "shot_v002.mov", 32)
        latest = write_movie(self.root / "renders" / "comp" / "shot_v010.mov", 128)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "video_path": str(preview), "tasks": []}])
        page.video_viewer.seek_to_frame(7)
        self.assert_media_types(page, "video")
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Render")
        page.shot_info_header.button_play_render.click()
        self.assertEqual(page.video_viewer.current_video_path, str(latest))
        self.assert_media_types(page, "render")
        self.assertEqual(page.shot_info_header.label_preview.text(), "Renders:")
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.assertTrue(page.video_viewer.is_playing())
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Preview")
        page.video_viewer.pause()
        page.shot_info_header.button_play_render.click()
        self.assertEqual(page.video_viewer.current_video_path, str(preview))
        self.assert_media_types(page, "video")
        self.assertEqual(page.shot_info_header.label_preview.text(), "Previews:")
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.assertTrue(page.video_viewer.is_playing())
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Render")

    def test_preview_only_shot_has_working_play_preview_button(self):
        preview = write_movie(self.root / "preview.mp4")
        page = self.make_page()
        page.set_shots([{"id": 1, "video_path": str(preview)}])
        button = page.shot_info_header.button_play_render
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.text(), "Play Preview")
        page.video_viewer.seek_to_frame(7)
        button.click()
        self.assertEqual(page.video_viewer.current_video_path, str(preview))
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.assertTrue(page.video_viewer.is_playing())

    def test_render_only_button_plays_selected_version(self):
        older = write_movie(self.root / "renders" / "comp" / "shot_v001.mov")
        write_movie(self.root / "renders" / "comp" / "shot_v002.mov")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        self.select_media(page, older)
        page.video_viewer.seek_to_frame(7)
        button = page.shot_info_header.button_play_render
        self.assertEqual(button.text(), "Play Render")
        button.click()
        self.assertEqual(page.video_viewer.current_video_path, str(older))
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.assertTrue(page.video_viewer.is_playing())

    def test_thumbnail_only_shot_disables_play_media_button(self):
        thumbnail = self.root / "thumbnail.png"
        image = QImage(6, 4, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.assertTrue(image.save(str(thumbnail)))
        page = self.make_page()
        page.set_shots([{"id": 1, "thumbnail": str(thumbnail)}])
        self.assertTrue(page.video_viewer.is_showing_still())
        self.assertFalse(page.shot_info_header.button_play_render.isEnabled())

    def test_paused_version_switch_preserves_frame_in_dropdown_and_shortcuts(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        next_preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v002.mp4")
        render = write_movie(self.root / "renders" / "comp" / "shot_v002.mov", 128)
        latest = write_movie(self.root / "renders" / "comp" / "shot_v003.mov", 64)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "video_path": str(preview)}])
        viewer = page.video_viewer
        viewer.seek_to_frame(7)
        self.select_media(page, next_preview)
        self.assertEqual(viewer.current_frame, 7)
        self.assertFalse(viewer.is_playing())
        self.select_media(page, render)
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Preview")
        self.assertEqual(viewer.current_frame, 7)
        self.assertFalse(viewer.is_playing())
        self.assertFalse(page.playback_bar.button_play_pause.isChecked())
        page.activateWindow()
        page.setFocus()
        self.app.processEvents()
        QTest.keyClick(page, Qt.Key.Key_Up)
        self.assertEqual(viewer.current_video_path, str(latest))
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Preview")
        self.assert_media_types(page, "render")
        self.assertEqual(viewer.current_frame, 7)
        QTest.keyClick(page, Qt.Key.Key_Down)
        self.assertEqual(viewer.current_video_path, str(render))
        self.assertEqual(viewer.current_frame, 7)
        self.assertFalse(viewer.is_playing())

    def test_render_mode_and_thumbnail_keep_filtered_versions_after_refresh(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        render = write_movie(self.root / "renders" / "comp" / "shot_v002.mov")
        thumbnail = self.root / "thumbnail.png"
        image = QImage(6, 4, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.assertTrue(image.save(str(thumbnail)))
        shot = {"id": 1, "base_path": str(self.root), "video_path": str(preview),
                "thumbnail": str(thumbnail)}
        page = self.make_page()
        page.set_shots([shot.copy()])
        self.select_media(page, render)
        page.video_viewer.seek_to_frame(7)
        page.set_shots([shot.copy()])
        self.assert_media_types(page, "render")
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.select_media(page, thumbnail)
        page.set_shots([shot.copy()])
        self.assertTrue(page.video_viewer.is_showing_still())
        self.assert_media_types(page, "render")
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Preview")
        page.shot_info_header.button_play_render.click()
        self.assert_media_types(page, "video")
        self.assertEqual(page.video_viewer.current_video_path, str(preview))
        self.assertEqual(page.video_viewer.current_frame, 7)

    def test_versions_with_different_fps_preserve_playback_time(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        render = write_movie(self.root / "renders" / "comp" / "shot_v002.mov", fps=48, frame_count=24)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "video_path": str(preview)}])
        page.video_viewer.seek_to_frame(7)
        self.select_media(page, render)
        self.assertEqual(page.video_viewer.current_frame, 14)
        self.assertEqual(page.video_viewer.frame_rate, 48)
        self.assertFalse(page.video_viewer.is_playing())
        self.select_media(page, preview)
        self.assertEqual(page.video_viewer.current_frame, 7)

    def test_shorter_version_clamps_paused_position_to_last_frame(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        render = write_movie(self.root / "renders" / "comp" / "shot_v002.mov", frame_count=4)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "video_path": str(preview)}])
        page.video_viewer.seek_to_frame(7)
        self.select_media(page, render)
        self.assertEqual(page.video_viewer.current_frame, 3)
        self.assertFalse(page.video_viewer.is_playing())

    def test_thumbnail_preserves_paused_comparison_time(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        render = write_movie(self.root / "renders" / "comp" / "shot_v002.mov")
        thumbnail = self.root / "thumbnail.png"
        image = QImage(6, 4, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.assertTrue(image.save(str(thumbnail)))
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "video_path": str(preview),
                         "thumbnail": str(thumbnail)}])
        self.select_media(page, preview)
        page.video_viewer.seek_to_frame(7)
        self.select_media(page, thumbnail)
        self.assertTrue(page.video_viewer.is_showing_still())
        self.select_media(page, render)
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.assertFalse(page.video_viewer.is_playing())

    def test_rapid_exr_version_switch_preserves_pending_paused_seek(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        first_paths = []
        for version in (2, 3):
            directory = self.root / "renders" / "comp" / f"shot_v{version:03d}"
            for frame in range(12):
                path = write_exr(directory / f"shot_v{version:03d}.{1001 + frame}.exr",
                                 0.1 + frame * 0.05)
                if frame == 0:
                    first_paths.append(path)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "video_path": str(preview)}])
        viewer = page.video_viewer
        viewer.seek_to_frame(7)
        self.select_media(page, first_paths[0])
        self.assertEqual(viewer._sequence_target, 7)
        self.select_media(page, first_paths[1])
        self.wait_for(lambda: viewer.current_frame == 7 and not viewer._sequence_busy)
        self.assertEqual(viewer.current_video_path, str(first_paths[1]))
        self.assertAlmostEqual(viewer.video_widget.current_frame_image().pixelColor(0, 0).red(), 115, delta=1)
        self.assertFalse(viewer.is_playing())
        self.select_media(page, preview)
        self.assertEqual(viewer.current_frame, 7)

    def test_refresh_keeps_position_but_next_shot_starts_at_zero(self):
        preview = write_movie(self.root / "renders" / "precomp" / "previews" / "shot_v001.mp4")
        other = write_movie(self.root / "other" / "shot_v001.mp4")
        page = self.make_page()
        shots = [{"id": 1, "base_path": str(self.root), "video_path": str(preview)},
                 {"id": 2, "video_path": str(other)}]
        page.set_shots(shots)
        page.video_viewer.seek_to_frame(7)
        page.set_shots([shot.copy() for shot in shots])
        self.assertEqual(page.video_viewer.current_frame, 7)
        page.logic._go_to_next_shot()
        self.assertEqual(page.video_viewer.current_video_path, str(other))
        self.assertEqual(page.video_viewer.current_frame, 0)
        self.assertFalse(page.video_viewer.is_playing())

    def test_render_only_shot_plays_and_scrubs_native_exr_frames(self):
        comp = self.root / "renders" / "comp" / "shot_v002"
        first = write_exr(comp / "shot_v002.1001.exr", 0.25)
        write_exr(comp / "shot_v002.1002.exr", 0.75)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "tasks": []}])
        self.assertTrue(page.shot_info_header.button_play_render.isEnabled())
        self.wait_for(lambda: not page.video_viewer.video_widget.current_frame_image().isNull())
        self.assertEqual(page.video_viewer.current_video_path, str(first))
        self.assertEqual(page.video_viewer.total_frames, 2)
        page.shot_info_header.button_play_render.click()
        self.assertTrue(page.video_viewer.is_playing())
        page.video_viewer.pause()
        page.video_viewer.seek_to_frame(1)
        self.wait_for(lambda: page.video_viewer.current_frame == 1)
        self.assertEqual(page.video_viewer.video_widget.current_frame_image().pixelColor(0, 0).red(), 191)

    def test_ocio_changes_reprocess_current_frame_and_cached_frames(self):
        comp = self.root / "renders" / "comp" / "shot_v002"
        write_exr(comp / "shot_v002.1001.exr", 0.25)
        write_exr(comp / "shot_v002.1002.exr", 0.36)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root), "tasks": []}])
        viewer = page.video_viewer
        self.wait_for(lambda: not viewer.video_widget.current_frame_image().isNull())
        self.set_config(page.ocio_panel)
        self.wait_for(lambda: viewer.video_widget.current_frame_image().pixelColor(0, 0).red() == 127
                      or viewer.video_widget.current_frame_image().pixelColor(0, 0).red() == 128)
        viewer.seek_to_frame(1)
        self.wait_for(lambda: viewer.current_frame == 1)
        page.ocio_panel.combo_configuration.setCurrentIndex(0)
        self.wait_for(lambda: viewer.video_widget.current_frame_image().pixelColor(0, 0).red() == 92)
        viewer.seek_to_frame(0)
        self.wait_for(lambda: viewer.current_frame == 0)
        self.assertEqual(viewer.video_widget.current_frame_image().pixelColor(0, 0).red(), 64)

    def test_old_background_frame_cannot_replace_new_thumbnail(self):
        first = write_exr(self.root / "slow.1001.exr")
        page = self.make_page()
        with mock.patch.object(EXRSequenceReader, "read_frame", side_effect=lambda _: (
                time.sleep(0.05) or np.ones((4, 6, 3), dtype=np.float32))):
            page.video_viewer.load_video(str(first))
            image = QImage(6, 4, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.red)
            page.video_viewer.show_still(image)
            self.wait_for(lambda: not page.video_viewer._sequence_busy)
        self.assertEqual(page.video_viewer.video_widget.current_frame_image(), image)
        self.assertTrue(page.video_viewer.is_showing_still())

    def test_missing_render_frame_pauses_and_shows_error(self):
        directory = self.root / "renders" / "comp" / "shot_v002"
        write_exr(directory / "shot_v002.1001.exr")
        write_exr(directory / "shot_v002.1003.exr")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        self.wait_for(lambda: not page.video_viewer._sequence_busy)
        page.video_viewer.play()
        page.playback_bar.spinbox_current_frame.setValue(1002)
        self.wait_for(lambda: "1002 is missing" in page.review_status.text())
        self.assertFalse(page.video_viewer.is_playing())
        self.assertEqual(page.playback_bar.spinbox_current_frame.value(), 1001)

    def test_native_sequence_uses_play_range_in_both_directions(self):
        directory = self.root / "renders" / "comp" / "shot_v002"
        for frame in range(1001, 1005):
            write_exr(directory / f"shot_v002.{frame}.exr")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        viewer = page.video_viewer
        self.wait_for(lambda: not viewer._sequence_busy)
        viewer.set_play_range_enabled(True)
        viewer.set_out_point(2)
        viewer.set_in_point(1)
        viewer.seek_to_frame(2)
        self.wait_for(lambda: viewer.current_frame == 2)
        viewer._on_playback_tick()
        self.wait_for(lambda: viewer.current_frame == 1)
        viewer._play_direction = -1
        viewer._on_playback_tick()
        self.wait_for(lambda: viewer.current_frame == 2)

    def test_playback_rate_changes_keep_native_frame_count(self):
        directory = self.root / "renders" / "comp" / "shot_v002"
        for frame in range(1001, 1004):
            write_exr(directory / f"shot_v002.{frame}.exr")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        page.playback_bar.spinbox_fps.setValue(25)
        self.assertEqual(page.video_viewer.frame_rate, 25)
        self.assertEqual(page.video_viewer.total_frames, 3)
        self.assertEqual(page.video_viewer.duration_ms, 120)

    def test_ocio_processor_is_applied_to_movies(self):
        path = write_movie(self.root / "preview.mp4", 64)
        page = self.make_page()
        page.set_shots([{"id": 1, "video_path": str(path)}])
        self.set_config(page.ocio_panel)
        self.assertAlmostEqual(page.video_viewer.video_widget.current_frame_image()
                               .pixelColor(0, 0).red(), 128, delta=4)

    def test_timeline_keeps_render_only_and_missing_preview_shots(self):
        window = SimpleNamespace(_review_files_io=self.folders)
        shots = main.MainWindow._build_review_shots_from_timeline(window, {"shots": [
            {"id": 1, "base_path": str(self.root)},
            {"id": 2, "base_path": str(self.root), "preview_video": "missing.mov"},
        ]})
        self.assertEqual([shot["id"] for shot in shots], [1, 2])
        self.assertEqual([shot["video_path"] for shot in shots], [None, None])

    def test_empty_shot_or_timeline_clears_previous_media(self):
        preview = write_movie(self.root / "preview.mp4")
        page = self.make_page()
        page.set_shots([{"id": 1, "video_path": str(preview)}])
        page.set_shots([{"id": 2}])
        self.assertTrue(page.video_viewer.video_widget.current_frame_image().isNull())
        self.assertFalse(page.shot_info_header.button_play_render.isEnabled())
        page.set_shots([])
        self.assertIsNone(page.logic.current_shot)


class ReviewSessionTests(ReviewTestCase):
    def test_cached_exr_loops_read_and_colour_convert_each_frame_once(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1041):
            write_exr(directory / f"shot_v001.{frame}.exr", 0.25)
        class Processor:
            def __init__(self):
                self.calls = []
            def applyRGB(self, pixels):
                self.calls.append(threading.get_ident())
                pixels *= 2
        processor = Processor()
        page = self.make_page()
        page.video_viewer.set_ocio_processor(processor)
        original = EXRSequenceReader.read_frame
        with mock.patch.object(EXRSequenceReader, "read_frame", autospec=True, side_effect=original) as reads:
            page.video_viewer.load_video(str(directory / "shot_v001.1001.exr"))
            viewer = page.video_viewer
            self.wait_for(lambda: not viewer._sequence_busy)
            self.assertEqual(len(viewer._frame_cache), 40)
            self.assertEqual(reads.call_count, 40)
            self.assertEqual(len(processor.calls), 40)
            self.assertNotIn(threading.get_ident(), processor.calls)
            presented = []
            viewer.frame_changed.connect(presented.append)
            viewer.play()
            viewer.play_timer.stop()
            for _ in range(80):
                viewer._on_playback_tick()
            self.assertEqual(presented, list(range(1, 40)) + [0] + list(range(1, 40)) + [0])
            self.assertEqual(reads.call_count, 40)
            self.assertEqual(len(processor.calls), 40)
            self.assertEqual(viewer.video_widget.current_frame_image().pixelColor(0, 0).red(), 128)

    def test_cached_movie_loops_do_not_decode_or_reapply_ocio(self):
        path = write_movie(self.root / "render.mov", 64, frame_count=36)
        class Processor:
            def __init__(self):
                self.calls = []
            def applyRGB(self, pixels):
                self.calls.append(threading.get_ident())
                pixels *= 0.5
        processor = Processor()
        page = self.make_page()
        viewer = page.video_viewer
        viewer.set_ocio_processor(processor)
        viewer.load_video(str(path))
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(len(viewer._frame_cache), 36)
        self.assertEqual(len(processor.calls), 36)
        self.assertEqual(processor.calls.count(threading.get_ident()), 1)
        viewer.play()
        viewer.play_timer.stop()
        with mock.patch.object(viewer.reader, "decode_next", side_effect=AssertionError("Cached playback must not decode")), \
                mock.patch.object(viewer.reader, "seek_to_frame", side_effect=AssertionError("Cached playback must not seek")):
            for _ in range(72):
                viewer._on_playback_tick()
            viewer.seek_to_frame(12)
            viewer.step_backward()
            viewer.step_forward()
        self.assertEqual(viewer.current_frame, 12)
        self.assertEqual(len(processor.calls), 36)
        self.assertAlmostEqual(viewer.video_widget.current_frame_image().pixelColor(0, 0).red(), 32, delta=1)

    def test_pausing_during_movie_loading_cancels_automatic_playback(self):
        path = write_movie(self.root / "render.mov", 64)
        gate = threading.Event()
        gui_thread = threading.get_ident()
        class Processor:
            def applyRGB(self, pixels):
                if threading.get_ident() != gui_thread:
                    gate.wait(3)
        page = self.make_page()
        viewer = page.video_viewer
        viewer.set_ocio_processor(Processor())
        try:
            viewer.load_video(str(path))
            viewer.play()
            self.assertTrue(viewer._buffering)
            self.assertFalse(viewer.play_timer.isActive())
            viewer.pause()
            gate.set()
            self.wait_for(lambda: not viewer._sequence_busy)
            self.assertEqual(len(viewer._frame_cache), 12)
            self.assertEqual(viewer.current_frame, 0)
            self.assertFalse(viewer.is_playing())
            self.assertFalse(viewer.play_timer.isActive())
        finally:
            gate.set()

    def test_cache_budget_loads_the_next_section_without_skipping_frames(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1013):
            write_exr(directory / f"shot_v001.{frame}.exr")
        page = self.make_page()
        viewer = page.video_viewer
        viewer._cache_byte_limit = 4 * display_image(np.zeros((4, 6, 3), np.float32)).sizeInBytes()
        viewer.load_video(str(directory / "shot_v001.1001.exr"))
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(set(viewer._frame_cache), {0, 1, 2, 3})
        viewer.play()
        viewer.play_timer.stop()
        for _ in range(3):
            viewer._on_playback_tick()
        self.assertEqual(viewer.current_frame, 3)
        viewer._on_playback_tick()
        self.assertTrue(viewer._buffering)
        self.assertEqual(viewer.current_frame, 3)
        self.wait_for(lambda: not viewer._buffering)
        viewer.play_timer.stop()
        viewer._on_playback_tick()
        self.assertEqual(viewer.current_frame, 4)
        self.assertLessEqual(viewer._cache_bytes, viewer._cache_byte_limit)

    def test_cache_ram_preference_survives_settings_reload(self):
        path = self.root / "settings.yaml"
        settings = SettingsManager(str(path))
        page = self.make_page()
        page.set_settings_manager(settings)
        page.combo_cache_memory.setCurrentText("2 GB")
        reloaded = SettingsManager(str(path))
        self.assertEqual(reloaded.get("review_cache_mb"), 2048)
        page.set_settings_manager(reloaded)
        self.assertEqual(page.video_viewer._cache_byte_limit, 2048 * 1024**2)

    def test_load_frames_retries_a_missing_frame_that_has_arrived(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        write_exr(directory / "shot_v001.1001.exr")
        write_exr(directory / "shot_v001.1003.exr")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        viewer = page.video_viewer
        self.wait_for(lambda: not viewer._sequence_busy)
        viewer.play()
        self.assertFalse(viewer.is_playing())
        self.assertIn("1002 is missing", page.review_status.text())
        write_exr(directory / "shot_v001.1002.exr")
        page.button_load_frames.click()
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(len(viewer._frame_cache), 3)
        self.assertEqual(page.cache_progress.value(), 3)
        self.assertNotIn("missing", page.review_status.text())
        viewer.play()
        self.assertFalse(viewer._buffering)

    def test_long_media_labels_use_spare_row_width_and_keep_versions(self):
        page = self.make_page()
        combo = page.shot_info_header.combo_preview
        name = "Render: " + "a_long_shot_name_" * 12 + "_v014.1001.exr"
        selection = {"type": "render", "value": "/renders/the_source.exr"}
        combo.addItem(name, selection)
        combo.setCurrentIndex(0)
        self.app.processEvents()
        self.assertGreater(combo.width(), 220)
        displayed = combo._label_option().currentText
        self.assertIn("…", displayed)
        self.assertTrue(displayed.endswith("_v014.1001.exr"))
        self.assertEqual(combo.currentText(), name)
        self.assertEqual(combo.currentData(), selection)

    def test_compact_combo_values_fit_with_shotbox_stylesheet(self):
        original = self.app.styleSheet()
        try:
            self.app.setStyleSheet((FRONTEND_DIR / "ui" / "dark_v01.qss").read_text())
            page = self.make_page()
            page.resize(1200, 900)
            self.app.processEvents()
            for combo in (page.annotation_toolbar.combo_thickness, page.annotation_toolbar.combo_scope,
                          page.combo_resolution, page.combo_cache_memory):
                for index in range(combo.count()):
                    combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(False)
                    self.assertEqual(combo._label_option().currentText, combo.currentText())
        finally:
            self.app.setStyleSheet(original)

    def test_long_combo_popup_fits_screen_and_retains_selection_data(self):
        page = self.make_page()
        combo = page.review_selection_bar.combo_job
        combo.addItem("A long production name " * 20, 73)
        combo.addItem("Second production", 91)
        combo.showPopup()
        self.app.processEvents()
        popup = combo.view().window().geometry()
        screen = combo.screen().availableGeometry()
        self.assertGreater(popup.width(), combo.width())
        self.assertGreaterEqual(popup.left(), screen.left())
        self.assertLessEqual(popup.right(), screen.right())
        self.assertEqual(combo.itemData(0, Qt.ItemDataRole.ToolTipRole), combo.itemText(0))
        QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        self.assertEqual(combo.currentData(), 91)

    def test_movie_resolution_changes_keep_paused_position_and_range(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        viewer = page.video_viewer
        viewer.seek_to_frame(7)
        viewer.set_in_point(2)
        viewer.set_out_point(9)
        viewer.set_play_range_enabled(True)
        page.combo_resolution.setCurrentText("Quarter")
        self.assertEqual(viewer.video_widget.current_frame_image().size().width(), 16)
        self.assertEqual(viewer.current_frame, 7)
        self.assertFalse(viewer.is_playing())
        self.assertEqual((viewer.in_point, viewer.out_point), (2, 9))
        page.combo_resolution.setCurrentText("Full")
        self.assertEqual(viewer.video_widget.current_frame_image().size().width(), 64)
        self.assertEqual(viewer.current_frame, 7)
        viewer.play()
        viewer.play_timer.stop()
        page.combo_resolution.setCurrentText("Half")
        self.assertTrue(viewer.is_playing())
        self.assertEqual(viewer.current_frame, 7)
        viewer.pause()

    def test_exr_resolution_replaces_cached_pixels_without_changing_position(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1006):
            write_exr(directory / f"shot_v001.{frame}.exr")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        viewer = page.video_viewer
        self.wait_for(lambda: not viewer._sequence_busy)
        viewer.seek_to_frame(2)
        page.combo_resolution.setCurrentText("Half")
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(viewer.current_frame, 2)
        self.assertFalse(viewer.is_playing())
        self.assertTrue(all(image.width() == 3 for image in viewer._frame_cache.values()))
        page.combo_resolution.setCurrentText("Full")
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(viewer.current_frame, 2)
        self.assertTrue(all(image.width() == 6 for image in viewer._frame_cache.values()))

    def two_shots(self, *, second_render=True):
        shots = []
        for index, title in enumerate(("010 — Arrival", "020 — Landing"), 1):
            base = self.root / f"shot{index}"
            preview = write_movie(base / "renders" / "precomp" / "previews" / "shot_v001.mp4")
            if index == 1 or second_render:
                write_movie(base / "renders" / "comp" / "shot_v001.mov")
                write_movie(base / "renders" / "comp" / "shot_v002.mov", 128)
            shots.append({"id": index, "title": title, "base_path": str(base), "video_path": str(preview)})
        return shots

    def test_render_mode_follows_shot_queue_and_pauses_for_discussion(self):
        shots = self.two_shots()
        page = self.make_page()
        page.set_shots(shots)
        page.shot_info_header.button_play_render.click()
        page.video_viewer.seek_to_frame(7)
        page.session_bar.combo_shot.setCurrentIndex(1)
        self.assertEqual(page.video_viewer.current_video_path,
                         str(Path(shots[1]["base_path"]) / "renders" / "comp" / "shot_v002.mov"))
        self.assertEqual(page.video_viewer.current_frame, 0)
        self.assertFalse(page.video_viewer.is_playing())
        self.assert_media_types(page, "render")
        page.center_widget.setFocus()
        QTest.keyClick(page.center_widget, Qt.Key.Key_PageUp)
        self.assertEqual(page.logic.current_shot_index, 0)
        self.assertEqual(page.session_bar.combo_shot.currentIndex(), 0)
        self.assert_media_types(page, "render")

    def test_missing_render_is_visible_and_preview_requires_explicit_switch(self):
        shots = self.two_shots(second_render=False)
        page = self.make_page()
        page.set_shots(shots)
        page.shot_info_header.button_play_render.click()
        page.session_bar.button_next.click()
        self.assertEqual(page.video_viewer.total_frames, 0)
        self.assertFalse(page.playback_bar.isEnabled())
        self.assertIn("No renders", page.review_status.text())
        self.assertEqual(page.shot_info_header.combo_preview.currentText(), "No renders")
        self.assertEqual(page.shot_info_header.button_play_render.text(), "Play Preview")
        page.shot_info_header.button_play_render.click()
        self.assertEqual(page.video_viewer.current_video_path, shots[1]["video_path"])
        self.assertTrue(page.video_viewer.is_playing())

    def test_play_timeline_advances_renders_and_restores_loop_at_end(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        page.shot_info_header.button_play_render.click()
        page.session_bar.button_play_all.click()
        self.assertFalse(page.video_viewer.loop_playback)
        self.assertFalse(page.playback_bar.button_loop.isEnabled())
        page.video_viewer.seek_to_frame(page.video_viewer.total_frames - 1)
        page.video_viewer._on_playback_tick()
        self.assertEqual(page.logic.current_shot_index, 1)
        self.assertTrue(page.video_viewer.is_playing())
        self.assert_media_types(page, "render")
        page.video_viewer.seek_to_frame(page.video_viewer.total_frames - 1)
        page.video_viewer._on_playback_tick()
        self.assertFalse(page.video_viewer.is_playing())
        self.assertFalse(page.session_bar.button_play_all.isChecked())
        self.assertTrue(page.video_viewer.loop_playback)
        self.assertTrue(page.playback_bar.button_loop.isEnabled())

    def test_play_timeline_stops_at_missing_render(self):
        page = self.make_page()
        page.set_shots(self.two_shots(second_render=False))
        page.shot_info_header.button_play_render.click()
        page.session_bar.button_play_all.click()
        page.video_viewer.seek_to_frame(page.video_viewer.total_frames - 1)
        page.video_viewer._on_playback_tick()
        self.assertEqual(page.logic.current_shot_index, 1)
        self.assertFalse(page.session_bar.button_play_all.isChecked())
        self.assertFalse(page.video_viewer.is_playing())
        self.assertEqual(page.video_viewer.total_frames, 0)
        self.assertIn("No renders", page.review_status.text())

    def test_manual_shot_navigation_stops_timeline_playthrough(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        page.session_bar.button_play_all.click()
        page.session_bar.button_next.click()
        self.assertFalse(page.session_bar.button_play_all.isChecked())
        self.assertFalse(page.video_viewer.is_playing())
        self.assertTrue(page.video_viewer.loop_playback)

    def test_presentation_preserves_media_and_keyboard_controls_then_restores_layout(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        page.shot_info_header.button_play_render.click()
        page.video_viewer.pause()
        page.video_viewer.seek_to_frame(7)
        image = page.video_viewer.video_widget.current_frame_image()
        path = page.video_viewer.current_video_path
        page.review_selection_bar.button_ocio.setChecked(True)
        page.center_widget.setFocus()
        QTest.keyClick(page.center_widget, Qt.Key.Key_F11)
        self.app.processEvents()
        self.assertIsNotNone(page._presentation_window)
        self.assertTrue(page._presentation_window.isFullScreen())
        page._presentation_window.resize(1440, 900)
        self.app.processEvents()
        next_button = page.shot_navigation.button_next_shot
        position = next_button.mapTo(page.viewer_frame, next_button.rect().center())
        self.assertIs(page.viewer_frame.childAt(position), next_button)
        self.assertFalse(page.review_selection_bar.isVisible())
        self.assertFalse(page.annotation_toolbar.isVisible())
        self.assertEqual(page.video_viewer.current_video_path, path)
        self.assertEqual(page.video_viewer.video_widget.current_frame_image(), image)
        QTest.keyClick(page.center_widget, Qt.Key.Key_Space)
        self.assertTrue(page.video_viewer.is_playing())
        QTest.keyClick(page.center_widget, Qt.Key.Key_Space)
        self.assertFalse(page.video_viewer.is_playing())
        QTest.keyClick(page.center_widget, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertIsNone(page._presentation_window)
        self.assertIs(page.center_widget.parentWidget(), page)
        self.assertTrue(page.annotation_toolbar.isVisible())
        self.assertTrue(page.ocio_panel.isVisible())
        self.assertFalse(page.session_bar.button_present.isChecked())
        self.assertEqual(page.video_viewer.current_frame, 7)

    def test_presentation_keeps_drawing_aligned_to_the_image(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        page.annotation_toolbar.button_rectangle.click()
        canvas = page.video_viewer.annotation_canvas
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(canvas.width() // 4, canvas.height() // 4))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(canvas.width() // 2, canvas.height() // 2))
        page.session_bar.button_present.click()
        self.app.processEvents()
        overlay = canvas.get_composite_image().toImage()
        self.assertGreater(overlay.pixelColor(canvas.width() // 4, canvas.height() // 4).alpha(), 0)
        page.session_bar.button_present.click()
        self.app.processEvents()
        overlay = canvas.get_composite_image().toImage()
        self.assertGreater(overlay.pixelColor(canvas.width() // 4, canvas.height() // 4).alpha(), 0)

    def test_exr_source_numbers_drive_seek_ranges_and_annotations(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(2001, 2006):
            write_exr(directory / f"shot_v001.{frame}.exr")
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        self.wait_for(lambda: not page.video_viewer._sequence_busy)
        bar = page.playback_bar
        self.assertEqual(bar.spinbox_current_frame.value(), 2001)
        self.assertEqual(bar.spinbox_out_point.value(), 2005)
        bar.spinbox_current_frame.setValue(2003)
        self.wait_for(lambda: page.video_viewer.current_frame == 2)
        bar.spinbox_in_point.setValue(2002)
        bar.spinbox_out_point.setValue(2004)
        self.assertEqual((page.video_viewer.in_point, page.video_viewer.out_point), (1, 3))
        page.annotation_toolbar.spin_range_start.setValue(2002)
        page.annotation_toolbar.spin_range_end.setValue(2004)
        canvas = page.video_viewer.annotation_canvas
        self.assertEqual((canvas.scope_range_start, canvas.scope_range_end), (1, 3))
        bar._on_slider_moved(3)
        self.wait_for(lambda: page.video_viewer.current_frame == 3)
        self.assertEqual(bar.spinbox_current_frame.value(), 2004)

    def test_exr_loads_the_whole_range_without_advancing_paused_image(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1010):
            write_exr(directory / f"shot_v001.{frame}.exr", (frame - 1000) / 10)
        page = self.make_page()
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        self.wait_for(lambda: not page.video_viewer._sequence_busy)
        self.assertEqual(page.video_viewer.current_frame, 0)
        self.assertEqual(set(page.video_viewer._frame_cache), set(range(9)))
        self.assertEqual(page.cache_progress.value(), 9)
        self.assertEqual(page.cache_progress.maximum(), 9)
        self.assertIn("cached", page.playback_status.text())
        self.assertFalse(page.video_viewer.is_playing())

    def test_playback_waits_for_range_loading_before_advancing(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1006):
            write_exr(directory / f"shot_v001.{frame}.exr")
        original = EXRSequenceReader.read_frame
        def slow_read(reader, index):
            if index == 2:
                time.sleep(0.08)
            return original(reader, index)
        page = self.make_page()
        with mock.patch.object(EXRSequenceReader, "read_frame", slow_read):
            page.set_shots([{"id": 1, "base_path": str(self.root)}])
            viewer = page.video_viewer
            self.wait_for(lambda: 1 in viewer._frame_cache and viewer._sequence_busy)
            viewer.play()
            viewer.play_timer.stop()
            viewer._on_playback_tick()
            self.assertEqual(viewer.current_frame, 0)
            self.assertTrue(viewer._buffering)
            self.assertTrue(viewer._sequence_busy)
            self.wait_for(lambda: not viewer._sequence_busy)
            self.assertFalse(viewer._buffering)
            viewer.play_timer.stop()
            viewer._on_playback_tick()
            self.assertEqual(viewer.current_frame, 1)

    def test_parallel_exr_reads_present_only_the_requested_frame_out_of_order(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1005):
            write_exr(directory / f"shot_v001.{frame}.exr")
        gates = {index: threading.Event() for index in (1, 2, 3)}
        def controlled_read(reader, index):
            if index in gates:
                gates[index].wait(3)
            return np.full((4, 6, 3), (index + 1) / 10, np.float32)
        page = self.make_page()
        with mock.patch.object(EXRSequenceReader, "read_frame", controlled_read):
            try:
                page.set_shots([{"id": 1, "base_path": str(self.root)}])
                viewer = page.video_viewer
                self.wait_for(lambda: len(viewer._sequence_tasks) == 3)
                viewer.seek_to_frame(2)
                gates[3].set()
                self.wait_for(lambda: 3 in viewer._frame_cache)
                self.assertEqual(viewer.current_frame, 0)
                gates[2].set()
                self.wait_for(lambda: viewer.current_frame == 2)
                gates[1].set()
                self.wait_for(lambda: not viewer._sequence_busy)
                self.assertEqual(viewer.current_frame, 2)
                self.assertAlmostEqual(viewer.video_widget.current_frame_image().pixelColor(0, 0).red(), 77, delta=1)
                self.assertFalse(viewer.is_playing())
            finally:
                for gate in gates.values():
                    gate.set()

    def test_exr_lookahead_respects_memory_limit_and_reclaims_consumed_frames(self):
        directory = self.root / "renders" / "comp" / "shot_v001"
        for frame in range(1001, 1013):
            write_exr(directory / f"shot_v001.{frame}.exr")
        page = self.make_page()
        viewer = page.video_viewer
        viewer._cache_byte_limit = 128
        page.set_shots([{"id": 1, "base_path": str(self.root)}])
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(len(viewer._frame_cache), 1)
        self.assertLessEqual(viewer._cache_bytes, viewer._cache_byte_limit)
        viewer._cache_byte_limit = 4 * viewer._cache_bytes
        viewer.seek_to_frame(0)
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(set(viewer._frame_cache), {0, 1, 2, 3})
        viewer.seek_to_frame(3)
        self.wait_for(lambda: not viewer._sequence_busy)
        self.assertEqual(set(viewer._frame_cache), {3, 4, 5, 6})
        self.assertLessEqual(viewer._cache_bytes, viewer._cache_byte_limit)

    def test_version_comparison_keeps_loop_range_and_next_shot_resets_it(self):
        shots = self.two_shots()
        page = self.make_page()
        page.set_shots(shots)
        bar = page.playback_bar
        bar.spinbox_in_point.setValue(1003)
        bar.spinbox_out_point.setValue(1009)
        bar.button_play_range.setChecked(True)
        page.video_viewer.seek_to_frame(7)
        page.shot_info_header.button_play_render.click()
        self.assertEqual((page.video_viewer.in_point, page.video_viewer.out_point), (2, 8))
        self.assertTrue(page.video_viewer.use_play_range)
        self.assertEqual(page.video_viewer.current_frame, 7)
        page.session_bar.button_next.click()
        self.assertFalse(page.video_viewer.use_play_range)
        self.assertEqual(page.video_viewer.current_frame, 0)

    def test_drawing_pauses_playback_and_scrubbing_restores_play_state(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        page.video_viewer.play()
        page.annotation_toolbar.button_rectangle.click()
        self.assertFalse(page.video_viewer.is_playing())
        page.video_viewer.play()
        page.logic._on_scrub_started()
        self.assertFalse(page.video_viewer.is_playing())
        page.playback_bar.slider_timeline.setValue(7)
        page.logic._on_scrub_finished()
        self.assertEqual(page.video_viewer.current_frame, 7)
        self.assertTrue(page.video_viewer.is_playing())

    def test_movie_resume_after_cached_backward_steps_does_not_skip_frames(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        viewer = page.video_viewer
        viewer.seek_to_frame(7)
        viewer.step_backward()
        viewer.step_backward()
        self.assertEqual(viewer.current_frame, 5)
        viewer.play()
        self.wait_for(lambda: not viewer._buffering)
        viewer.play_timer.stop()
        viewer._on_playback_tick()
        self.assertEqual(viewer.current_frame, 6)

    def test_review_shortcuts_do_not_capture_keys_on_other_tabs_or_editors(self):
        page = self.make_page()
        page.set_shots(self.two_shots())
        stack = QStackedWidget()
        stack.addWidget(page)
        other = QWidget()
        layout = QVBoxLayout(other)
        edit = QLineEdit()
        layout.addWidget(edit)
        stack.addWidget(other)
        stack.setCurrentWidget(other)
        stack.show()
        stack.activateWindow()
        edit.setFocus()
        self.app.processEvents()
        edit.setText("hello")
        edit.setCursorPosition(5)
        QTest.keyClick(edit, Qt.Key.Key_Left)
        QTest.keyClick(edit, Qt.Key.Key_M)
        self.assertEqual(edit.text(), "hellmo")
        self.assertEqual(page.video_viewer.current_frame, 0)
        stack.setCurrentWidget(page)
        page.playback_bar.spinbox_current_frame.setFocus()
        self.app.processEvents()
        self.assertTrue(page._shortcut_blocked())
        stack.removeWidget(page)
        page.setParent(None)
        stack.close()


class OCIOPanelTests(ReviewTestCase):
    def test_screen_conversion_sanitizes_nan_and_infinity_without_mutating_hdr(self):
        pixels = np.array([[[np.nan, np.inf, -np.inf], [4.0, 0.5, -1.0]]], np.float32)
        original = pixels.copy()
        image = display_image(pixels)
        self.assertEqual(image.pixelColor(0, 0).getRgb()[:3], (0, 255, 0))
        self.assertEqual(image.pixelColor(1, 0).getRgb()[:3], (255, 128, 0))
        np.testing.assert_equal(pixels, original)

    def test_preferences_survive_application_settings_reload(self):
        path = self.root / "settings.yaml"
        settings = SettingsManager(str(path))
        panel = OCIOPanel()
        panel.set_settings_manager(settings)
        self.set_config(panel)
        panel.combo_look.setCurrentText("Boost")
        panel.combo_input.setCurrentText("Display gamma")
        reloaded = SettingsManager(str(path))
        restored = OCIOPanel()
        restored.set_source(True)
        restored.set_settings_manager(reloaded)
        self.assertEqual(restored.combo_configuration.currentData(), "file")
        self.assertEqual(restored.combo_input.currentText(), "Display gamma")
        self.assertEqual(restored.combo_look.currentText(), "Boost")
        self.assertIsNotNone(restored.processor)

    def test_hdr_pixels_are_transformed_before_screen_clipping(self):
        processor = ocio.Config.CreateRaw().getProcessor(
            ocio.MatrixTransform.Scale([0.1, 0.1, 0.1, 1])).getDefaultCPUProcessor()
        raw = np.full((2, 3, 3), 4.0, np.float32)
        self.assertEqual(display_image(raw, processor).pixelColor(0, 0).red(), 102)
        np.testing.assert_allclose(raw, 4.0)

    def test_environment_and_builtin_configuration_create_display_processors(self):
        path = self.root / "env.ocio"
        path.write_text(test_config().serialize())
        panel = OCIOPanel()
        with mock.patch.dict(os.environ, {"OCIO": str(path)}):
            panel.combo_configuration.setCurrentIndex(panel.combo_configuration.findData("environment"))
            self.assertEqual(panel.label_name.text(), "Test config")
            self.assertIsNotNone(panel.processor)
        panel.set_source(True)
        panel.combo_configuration.setCurrentIndex(panel.combo_configuration.findData("builtin"))
        self.assertEqual(panel.combo_input.currentText(), "ACEScg")
        self.assertIsNotNone(panel.processor)

    def test_controls_and_look_change_actual_pixel_values(self):
        panel = OCIOPanel()
        self.set_config(panel)
        raw = np.full((2, 3, 3), 0.25, np.float32)
        normal = display_image(raw, panel.processor).pixelColor(0, 0).red()
        self.assertAlmostEqual(normal, 128, delta=1)
        panel.combo_look.setCurrentText("Boost")
        boosted = display_image(raw, panel.processor).pixelColor(0, 0).red()
        self.assertAlmostEqual(boosted, 180, delta=1)
        np.testing.assert_allclose(raw, 0.25)
        panel.combo_display.setCurrentText("Other display")
        self.assertEqual(panel.combo_view.currentText(), "Raw")
        self.assertIsNotNone(panel.processor)

    def test_invalid_config_and_missing_library_have_visible_errors(self):
        panel = OCIOPanel()
        panel.edit_filename.setText(str(self.root / "missing.ocio"))
        panel.combo_configuration.setCurrentIndex(panel.combo_configuration.findData("file"))
        self.assertIsNone(panel.processor)
        self.assertEqual(panel.label_status.property("error"), "true")
        with mock.patch("review_ocio.ocio", None):
            panel.combo_configuration.setCurrentIndex(panel.combo_configuration.findData("builtin"))
            self.assertIn("unavailable", panel.label_status.text())

    def test_saved_config_selections_are_restored_without_overwriting_preferences(self):
        panel = OCIOPanel()
        self.set_config(panel)
        values = {"review_ocio": {"mode": "file", "file": panel.edit_filename.text(),
                  "display": "Other display", "view": "Raw", "look": "Boost",
                  "inputs": {"exr": "ACEScg", "video": "Display gamma"}}}
        settings = SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                                   set=lambda key, value, **_: values.__setitem__(key, value))
        restored = OCIOPanel()
        restored.set_settings_manager(settings)
        self.assertEqual(restored.combo_display.currentText(), "Other display")
        self.assertEqual(restored.combo_view.currentText(), "Raw")
        self.assertEqual(restored.combo_look.currentText(), "Boost")
        self.assertEqual(restored.combo_input.currentText(), "Display gamma")
        restored.set_source(True)
        self.assertEqual(restored.combo_input.currentText(), "ACEScg")
        self.assertEqual(values["review_ocio"]["inputs"]["video"], "Display gamma")


if __name__ == "__main__":
    unittest.main()
