from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import av
import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from review_page import AnnotationCanvas, ReviewPage


class ReviewPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.video_path = str(Path(cls.fixture_dir.name) / "review.mp4")
        with av.open(cls.video_path, "w") as output:
            stream = output.add_stream("mpeg4", rate=24)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            for index in range(12):
                pixels = np.full((48, 64, 3), index * 15, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        with mock.patch("review_page.filesIO.Folders"):
            self.page = ReviewPage()
        self.page.resize(1280, 800)
        self.page.show()
        QTest.qWait(20)
        self.viewer = self.page.video_viewer
        self.bar = self.page.playback_bar

    def tearDown(self):
        self.viewer.pause()
        self.viewer.reader.close()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def _click_viewer(self):
        position = self.viewer.rect().center()
        target = self.viewer.childAt(position)
        QTest.mouseClick(target, Qt.MouseButton.LeftButton,
                         pos=target.mapFrom(self.viewer, position))

    def test_video_click_toggles_playback_and_updates_transport(self):
        self.viewer.load_video(self.video_path)
        self.assertIs(self.viewer.childAt(self.viewer.rect().center()),
                      self.viewer.video_widget)
        self._click_viewer()
        self.assertTrue(self.viewer.is_playing())
        self.assertTrue(self.bar.button_play_pause.isChecked())
        self._click_viewer()
        self.assertFalse(self.viewer.is_playing())
        self.assertFalse(self.bar.button_play_pause.isChecked())

    def test_drawing_captures_clicks_until_select_tool_is_restored(self):
        self.viewer.load_video(self.video_path)
        self.page.annotation_toolbar.button_rectangle.click()
        canvas = self.viewer.annotation_canvas
        self.assertIs(self.viewer.childAt(self.viewer.rect().center()), canvas)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        QTest.mouseMove(canvas, QPoint(100, 80))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
        self.assertFalse(self.viewer.is_playing())
        self.assertEqual(len(canvas.annotations_all), 1)
        self.assertEqual(canvas.annotations_all[0]["type"], AnnotationCanvas.MODE_RECTANGLE)
        self.page.annotation_toolbar.button_select.click()
        self._click_viewer()
        self.assertTrue(self.viewer.is_playing())

    def test_thumbnail_click_keeps_still_image_and_playback_paused(self):
        self.viewer.load_video(self.video_path)
        image = QImage(64, 48, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.viewer.show_still(image)
        self._click_viewer()
        self.assertFalse(self.viewer.is_playing())
        self.assertTrue(self.viewer.is_showing_still())
        self.assertEqual(self.viewer.video_widget.current_frame_image(), image)

    def test_empty_viewer_does_not_activate_playback(self):
        self._click_viewer()
        self.bar.button_play_pause.click()
        self.assertFalse(self.viewer.is_playing())
        self.assertFalse(self.bar.button_play_pause.isChecked())

    def test_drag_and_right_click_do_not_toggle_playback(self):
        self.viewer.load_video(self.video_path)
        video = self.viewer.video_widget
        QTest.mousePress(video, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
        QTest.mouseMove(video, QPoint(100, 80))
        QTest.mouseRelease(video, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
        QTest.mouseClick(video, Qt.MouseButton.RightButton, pos=video.rect().center())
        self.assertFalse(self.viewer.is_playing())


if __name__ == "__main__":
    unittest.main()
