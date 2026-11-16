"""Editorial compatibility, native interactions and unchanged-API integration."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import av
import numpy as np
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app_cache import cache_directory, temporary_cache_task
from timeline_review_model import Edit, prepare_timeline, preview_versions, timecode, parse_timecode
from timeline_review_media import MovieDecoder
from timeline_review_page import TimelineReviewPage


def shot(identifier=1, start=100, end=110, **extra):
    return {"id": identifier, "title": f"shot{identifier}", "edit_inpoint": str(start),
            "edit_outpoint": str(end), "handles": "2", "duration": "14", "tasks": [],
            "preview": {"path": "unused.mp4", "version": 2, "fps": 25., "frame_count": 14}, **extra}


class TimingTests(unittest.TestCase):
    def test_handles_trimmed_previews_and_task_numbering(self):
        edit = Edit({"shots": [shot()]})
        cut = edit.cuts[0]
        self.assertEqual(cut.source_frame(100), 2)
        self.assertEqual(cut.source_frame(109), 11)
        self.assertEqual(cut.task_frame({"frame_reference": 1005}), (102, False))
        self.assertEqual(cut.task_frame({"frame_reference": [999, 1005]}), (100, True))
        self.assertEqual(cut.task_frame({"frame_reference": 2004, "frameio_metadata": {"mapping": {"working_start": 2001}}}), (101, False))
        cut.media["frame_count"] = 10
        self.assertEqual(cut.offset, 0)
        self.assertEqual(cut.source_frame(109), 9)

    def test_gap_and_legacy_sequential_assembly(self):
        edit = Edit({"shots": [shot(2, 120, 130), shot()]})
        self.assertEqual([c.shot["id"] for c in edit.cuts], [1, 2])
        self.assertIsNone(edit.at(115))
        self.assertFalse(edit.sequential)
        edit = Edit({"shots": [shot(2, 105, 115), shot()]})
        self.assertTrue(edit.sequential)
        self.assertEqual([(c.start, c.end) for c in edit.cuts], [(100, 110), (110, 120)])

    def test_missing_positions_and_media_keep_shots_available(self):
        edit = Edit({"shots": [shot(10, "", "", preview=None), shot(2, "", "", preview=None)]})
        self.assertEqual([c.shot["id"] for c in edit.cuts], [2, 10])
        self.assertEqual(edit.cuts[0].duration, 10)
        self.assertEqual(edit.end, 20)

    def test_mixed_media_rates_map_to_source_time(self):
        edit = Edit({"shots": [shot(), shot(2, 110, 120), shot(3, 120, 130,
                     preview={"fps": 50., "frame_count": 24, "path": "x", "version": 1})]})
        self.assertEqual(edit.fps, 25)
        self.assertEqual(edit.cuts[-1].source_frame(125), 12)

    def test_timecode_roundtrip_and_validation(self):
        for fps in (25, 24, 24000 / 1001, 30000 / 1001):
            self.assertEqual(parse_timecode(timecode(12345, fps), fps), 12345)
        for value in ("abc", "00:60:00:00", "00:00:00:25"):
            with self.assertRaises(ValueError):
                parse_timecode(value, 25)


class FakeAPI:
    _default_base_url = "http://test/api/"

    def __init__(self, *args, **kwargs):
        pass

    def get_jobs(self):
        return [{"id": 1, "title": "Test job", "timelines": [deepcopy(self.timeline)]}]

    def get_users(self):
        return [{"id": 5, "username": "artist", "first_name": "Artist"}]

    def get_timeline(self, identifier):
        return deepcopy(self.timeline)

    def create_task(self, shot_id, **payload):
        saved = {"id": 40, "shot": shot_id, **deepcopy(payload)}
        self.timeline["shots"][0]["tasks"].append(saved)
        return saved

    def update_task(self, identifier, **fields):
        task = next(t for s in self.timeline["shots"] for t in s["tasks"] if t["id"] == identifier)
        task.update(deepcopy(fields))
        return deepcopy(task)

    def update_shot(self, identifier, **fields):
        data = next(s for s in self.timeline["shots"] if s["id"] == identifier)
        data.update(fields)
        return deepcopy(data)

    def delete_task(self, identifier):
        for data in self.timeline["shots"]:
            data["tasks"] = [t for t in data["tasks"] if t["id"] != identifier]
        return True


class NativeReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patch = mock.patch("timeline_review_page.http_help.DjangoAPI", FakeAPI)
        self.patch.start()
        self.page = TimelineReviewPage()
        self.page._started = True
        self.page._poll_timer.stop()
        self.page.resize(1500, 950)
        self.page.show()
        FakeAPI.timeline = {"id": 7, "shots": [shot(preview=None), shot(2, 120, 130, preview=None)]}
        self.page.edit = Edit(deepcopy(FakeAPI.timeline))
        self.page.strip.edit = self.page.edit
        self.page.frame = 99
        self.page.seek(100)
        self.app.processEvents()

    def tearDown(self):
        self.page.shutdown()
        self.page._pool.waitForDone(10000)
        self.page._media_pool.waitForDone(10000)
        self.page.close()
        self.app.processEvents()
        self.page.deleteLater()
        self.app.processEvents()
        self.patch.stop()

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_draft_pins_shot_version_and_frame_across_navigation(self):
        self.page.title.setText("Paint")
        self.page.scope.setCurrentIndex(1)
        self.page.scrub(125)
        self.assertEqual(self.page.draft["cut"].shot["id"], 1)
        self.page.save_task()
        self.wait(lambda: not self.page._saving)
        saved = FakeAPI.timeline["shots"][0]["tasks"][0]
        self.assertEqual(saved["frame_reference"], 1003)
        self.assertEqual(saved["shot"], 1)
        self.assertEqual(self.page.frame, 125)

    def test_edit_preserves_suspended_draft_and_annotation_format(self):
        self.page.title.setText("Unfinished original")
        annotation = {"schema_version": 1, "coordinate_space": "normalized", "aspect_ratio": 16 / 9,
                      "shapes": [{"tool": "line", "color": "#ff6b35", "size": 3, "x": .1, "y": .2, "w": .4, "h": .3}]}
        task = {"id": 40, "title": "Existing", "frame_reference": [1004, 1006], "annotations": annotation,
                "status": "in_progress", "artist": None, "priority": 5}
        FakeAPI.timeline["shots"][0]["tasks"] = [deepcopy(task)]
        self.page.edit_task(self.page.edit.cuts[0], task)
        self.assertEqual(self.page.draft["annotations"], annotation)
        self.page.title.setText("Updated")
        self.page.save_task()
        self.wait(lambda: not self.page._saving)
        self.assertEqual(FakeAPI.timeline["shots"][0]["tasks"][0]["annotations"], annotation)
        self.assertEqual(self.page.title.text(), "Unfinished original")

    def test_failed_save_retains_form_and_drawing(self):
        self.page.title.setText("Keep me")
        self.page.draft["annotations"] = {"shapes": [{"tool": "pen"}]}
        with mock.patch.object(FakeAPI, "create_task", side_effect=RuntimeError("Offline")):
            self.page.save_task()
            self.wait(lambda: not self.page._saving)
        self.assertEqual(self.page.title.text(), "Keep me")
        self.assertTrue(self.page.draft["annotations"])
        self.assertIn("retained", self.page.status.text())

    def test_drawing_pauses_blocks_play_and_clear_unlocks(self):
        image = QImage(320, 180, QImage.Format.Format_RGB888)
        image.fill(Qt.GlobalColor.black)
        QTest.mouseClick(self.page.draw_buttons['pen'], Qt.MouseButton.LeftButton)
        self.page.picture.set_image(image)
        target = self.page.picture.target().center().toPoint()
        QTest.mousePress(self.page.picture, Qt.MouseButton.LeftButton, pos=target)
        QTest.mouseMove(self.page.picture, target + QPoint(20, 10))
        QTest.mouseRelease(self.page.picture, Qt.MouseButton.LeftButton, pos=target + QPoint(20, 10))
        self.page.done_drawing()
        self.assertTrue(self.page.draft["annotations"])
        self.assertEqual(self.page.draft["annotations"]["coordinate_space"], "normalized")
        self.page.toggle_play()
        self.assertFalse(self.page.playing)
        self.page.clear_drawing()
        self.page.toggle_play()
        self.assertTrue(self.page.playing)
        self.page.pause()
        self.page.picture.redo()
        self.assertFalse(self.page.playing)
        self.assertTrue(self.page.draft["annotations"])

    def test_refresh_preserves_position_draft_and_local_shot_notes(self):
        self.page.title.setText("Draft")
        self.page.shot_notes.setPlainText("Unsaved shot notes")
        self.page.scrub(104)
        FakeAPI.timeline["shots"][0]["tasks"] = [{"id": 41, "title": "Incoming", "status": "unassigned"}]
        self.page.refresh_tasks()
        self.wait(lambda: not self.page._polling)
        self.assertEqual(self.page.frame, 104)
        self.assertEqual(self.page.title.text(), "Draft")
        self.assertEqual(self.page.shot_notes.toPlainText(), "Unsaved shot notes")
        self.assertEqual(self.page.edit.cuts[0].shot["tasks"][0]["id"], 41)

    def test_repeat_pingpong_and_gap(self):
        self.page.repeat.setChecked(True)
        self.page.repeat_mode.setCurrentIndex(1)
        self.page.seek(109)
        self.page.playing = True
        self.page._tick()
        self.assertEqual(self.page.frame, 108)
        self.assertEqual(self.page.direction, -1)
        self.page.repeat.setChecked(False)
        self.page.pause()
        self.page.seek(115)
        self.assertEqual(self.page.picture.message, "Editorial gap")

    def test_jkl_shortcuts_play_both_directions_at_normal_and_double_speed(self):
        self.page.activateWindow()
        self.page.strip.setFocus()
        self.app.processEvents()
        with mock.patch.object(self.page, "_request_picture"):
            self.page.seek(105)
            QTest.keyClick(self.page.strip, Qt.Key.Key_L)
            self.assertTrue(self.page.playing)
            self.assertEqual((self.page.direction, self.page.playback_speed), (1, 1))
            QTest.keyClick(self.page.strip, Qt.Key.Key_L)
            self.assertEqual(self.page.playback_speed, 2)
            self.assertEqual(self.page._timer.interval(), 20)
            self.page._tick()
            self.assertEqual(self.page.frame, 106)
            QTest.keyClick(self.page.strip, Qt.Key.Key_J)
            self.assertEqual((self.page.direction, self.page.playback_speed), (-1, 1))
            QTest.keyClick(self.page.strip, Qt.Key.Key_J)
            self.assertEqual(self.page.playback_speed, 2)
            self.page._tick()
            self.assertEqual(self.page.frame, 105)
            QTest.keyClick(self.page.strip, Qt.Key.Key_K)
            self.assertFalse(self.page.playing)
            QTest.keyClick(self.page.strip, Qt.Key.Key_Space)
            self.assertEqual((self.page.direction, self.page.playback_speed), (1, 1))
            QTest.keyClick(self.page.strip, Qt.Key.Key_Space)
            self.assertFalse(self.page.playing)

    def test_shuttle_does_not_consume_typing_or_resume_unsaved_drawings(self):
        self.page.activateWindow()
        self.page.title.setFocus()
        self.app.processEvents()
        QTest.keyClicks(self.page.title, "jkl H, .")
        self.assertEqual(self.page.title.text(), "jkl H, .")
        self.assertFalse(self.page.playing)
        self.page.draft["annotations"] = {"shapes": [{"tool": "pen"}]}
        self.page.shuttle(-1)
        self.assertFalse(self.page.playing)
        self.page.shuttle(1)
        self.assertFalse(self.page.playing)

    def test_reverse_repeat_wraps_to_last_frame_and_stops_at_edit_start(self):
        with mock.patch.object(self.page, "_request_picture"):
            self.page.seek(100)
            self.page.repeat.setChecked(True)
            self.page.shuttle(-1)
            self.page._tick()
            self.assertEqual(self.page.frame, 109)
            self.page.repeat.setChecked(False)
            self.page.seek(100)
            self.page._tick()
            self.assertFalse(self.page.playing)
            self.assertEqual(self.page.frame, 100)

    def test_picture_and_timeline_can_zoom_out_below_fit(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QWheelEvent
        picture = self.page.picture
        for _ in range(40):
            position = QPointF(picture.rect().center())
            wheel = QWheelEvent(position, QPointF(picture.mapToGlobal(position.toPoint())), QPoint(), QPoint(0, -120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(picture, wheel)
        self.assertAlmostEqual(picture.zoom, .1)
        self.assertEqual(self.page.zoom_label.text(), "10%")
        self.page.timeline_zoom.setValue(1)
        self.assertAlmostEqual(self.page.strip.width(), self.page.timeline_scroll.viewport().width() / 4, delta=1)
        picture.fit()
        self.assertEqual(picture.zoom, 1.)

    def test_shot_updates_and_task_deletion_use_existing_api(self):
        self.page.shot_notes.setPlainText("Saved notes")
        self.page.save_shot()
        self.wait(lambda: FakeAPI.timeline["shots"][0].get("notes") == "Saved notes")
        self.page.toggle_hidden()
        self.wait(lambda: FakeAPI.timeline["shots"][0].get("hidden") is True)
        task = {"id": 40, "title": "Remove", "status": "unassigned"}
        FakeAPI.timeline["shots"][0]["tasks"] = [task]
        self.page.edit.cuts[0].shot["tasks"] = [deepcopy(task)]
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page.delete_task(self.page.edit.cuts[0], task)
        self.wait(lambda: not self.page._pending_tasks)
        self.assertFalse(FakeAPI.timeline["shots"][0]["tasks"])

    def test_scrolling_over_inputs_does_not_change_values(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QWheelEvent
        self.page.priority.setFocus()
        wheel = QWheelEvent(QPointF(5, 5), QPointF(self.page.priority.mapToGlobal(QPoint(5, 5))), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(self.page.priority, wheel)
        self.assertEqual(self.page.priority.value(), 5)

    def test_unchanged_task_refresh_preserves_cards_and_local_editor_choices(self):
        task = {"id": 40, "title": "Existing", "status": "unassigned", "artist": None}
        FakeAPI.timeline["shots"][0]["tasks"] = [deepcopy(task)]
        self.page.edit.cuts[0].shot["tasks"] = [deepcopy(task)]
        self.page._render_comments()
        cards = self.page.comments.widget()
        self.page.refresh_tasks()
        self.wait(lambda: not self.page._polling)
        self.assertIs(self.page.comments.widget(), cards)
        self.page.artist.addItem("Artist", 5)
        self.page.edit_task(self.page.edit.cuts[0], task)
        self.page.artist.setCurrentIndex(self.page.artist.findData(5))
        FakeAPI.timeline["shots"][0]["tasks"][0].update(status="approved", artist=9)
        self.page.refresh_tasks()
        self.wait(lambda: not self.page._polling)
        self.assertEqual(self.page.task_status.currentData(), "approved")
        self.assertEqual(self.page.artist.currentData(), 5)

    def test_card_body_seeks_highlights_and_preserves_note_selection(self):
        from PyQt6.QtWidgets import QLabel
        from timeline_review_sidebar import CommentCard
        task = {"id": 40, "title": "Paint", "notes": "Paint out marker", "frame_reference": 1007,
                "status": "unassigned", "artist": None}
        self.page.edit.cuts[0].shot["tasks"] = [task]
        self.page._render_comments()
        self.app.processEvents()
        card = self.page.comments.widget().findChild(CommentCard)
        self.assertFalse(card.property("selected"))
        self.assertIsNone(card.findChild(QLabel, "review_comment_title"))
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=card.rect().bottomRight() - QPoint(4, 4))
        self.assertEqual(self.page.frame, 104)
        card = self.page.comments.widget().findChild(CommentCard)
        self.assertTrue(card.property("selected"))
        notes = card.findChild(QLabel, "review_comment_notes")
        notes.setSelection(0, 5)
        self.page.seek(100)
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=card.rect().bottomRight() - QPoint(4, 4))
        self.assertEqual(self.page.frame, 100)

    def test_stale_poll_cannot_replace_a_newly_saved_task(self):
        captured = []
        with mock.patch.object(self.page, "_submit", side_effect=lambda fn, done, **kw: captured.append(done)):
            self.page.refresh_tasks()
        self.page._revision += 1
        saved = {"id": 41, "title": "Newly saved", "status": "unassigned"}
        self.page.edit.cuts[0].shot["tasks"] = [saved]
        captured[0](deepcopy(FakeAPI.timeline))
        self.assertEqual(self.page.edit.cuts[0].shot["tasks"], [saved])

    def test_fullscreen_keeps_transport_and_restores_center_panel(self):
        self.page.fullscreen()
        self.app.processEvents()
        self.assertIs(self.page.center.parentWidget(), self.page._presentation)
        self.assertTrue(self.page.play_button.isVisible())
        self.page._presentation.activateWindow()
        self.page.strip.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.page.strip, Qt.Key.Key_J)
        self.assertEqual(self.page.direction, -1)
        self.assertTrue(self.page.playing)
        QTest.keyClick(self.page.strip, Qt.Key.Key_J)
        self.assertEqual(self.page.playback_speed, 2)
        QTest.keyClick(self.page.strip, Qt.Key.Key_K)
        self.assertFalse(self.page.playing)
        self.page.fullscreen()
        self.app.processEvents()
        self.assertIsNone(self.page._presentation)
        self.assertIs(self.page.splitter.widget(1), self.page.center)


class LocalMediaTests(unittest.TestCase):
    @temporary_cache_task
    def test_discovery_probe_decode_eviction_and_cancellation(self):
        with tempfile.TemporaryDirectory(dir=cache_directory("temp"), prefix="timeline-review-test-") as directory:
            root = Path(directory) / "renders/precomp/previews"
            root.mkdir(parents=True)
            path = root / "shot_v002.mp4"
            with av.open(str(path), "w") as output:
                stream = output.add_stream("mpeg4", rate=25)
                stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
                for index in range(12):
                    frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 15, dtype=np.uint8), format="rgb24")
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            (root / "unversioned.mp4").touch()
            self.assertEqual(len(preview_versions({"base_path": directory})), 1)
            data = prepare_timeline({"shots": [shot(base_path=directory, preview=None)]})
            media = data["shots"][0]["preview"]
            self.assertEqual(media["frame_count"], 12)
            self.assertEqual(media["fps"], 25)
            decoder = MovieDecoder()
            decoder.limit = 64 * 48 * 3 + 100
            try:
                for index in (0, 1, 8, 3, 11):
                    image = decoder.read(media, index)
                    self.assertFalse(image.isNull())
                    self.assertLess(abs(image.pixelColor(0, 0).red() - index * 15), 12)
                self.assertLessEqual(len(decoder.cache), 1)
                decoder.cancel()
                self.assertIsNone(decoder.read(media, 0))
            finally:
                decoder.close()

            # Exercise the native controller and actual asynchronous decoding
            # through two cuts and an editorial gap, without a running server.
            app = QApplication.instance() or QApplication([])
            with mock.patch("timeline_review_page.http_help.DjangoAPI", FakeAPI):
                page = TimelineReviewPage()
                page._started = True
                page._poll_timer.stop()
                second = deepcopy(data["shots"][0])
                second.update(id=2, title="Second shot", edit_inpoint="112", edit_outpoint="122")
                data.update(id=7)
                data["shots"].append(second)
                page.edit = Edit(data)
                page.strip.edit = page.edit
                page.frame = 99
                page.show()
                page.seek(100)
                deadline = time.monotonic() + 3
                while page._display_key is None and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertIsNotNone(page._display_key)
                page.toggle_play()
                deadline = time.monotonic() + 4
                while page.playing and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertFalse(page.playing)
                self.assertEqual(page.frame, 121)
                self.assertFalse(page.picture.image.isNull())
                page.shutdown()
                page._pool.waitForDone(5000)
                page._media_pool.waitForDone(5000)
                page.close()
                page.deleteLater()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
