from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

import page_nukedash
import quick_view
import widgets


class FakePopup(QObject):
    dismissed = pyqtSignal()
    navigation_requested = pyqtSignal(int)
    thumbnail_update_requested = pyqtSignal(QImage)

    def __init__(self):
        super().__init__()
        self.visible = False
        self.position = 0
        self.shown = []
        self.thumbnail_states = []
        self.thumbnail_updates = []

    def set_thumbnail_upload_state(self, saving, message=""):
        self.thumbnail_states.append((saving, message))

    def apply_updated_thumbnail(self, image, filename):
        self.thumbnail_updates.append((image, filename))

    def isVisible(self):
        return self.visible

    def show_media(self, media, position_ms=0, screen=None):
        self.visible = True
        self.position = int(position_ms)
        self.shown.append((media, int(position_ms), screen))

    def current_position(self):
        return self.position

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.dismissed.emit()


class FakeCard(QObject):
    def __init__(self, title: str, position: int = 0, previewable: bool = True):
        super().__init__()
        self.title = title
        self.position = position
        self.begin_calls = 0
        self.end_calls = []
        pixmap = QPixmap(12, 8) if previewable else None
        if pixmap is not None:
            pixmap.fill(Qt.GlobalColor.darkGray)
        self.media = quick_view.QuickViewMedia(
            title=title,
            filename=f"{title}.jpg",
            thumbnail=pixmap,
        )

    def quick_view_media(self):
        return self.media

    def begin_quick_view(self):
        self.begin_calls += 1
        return self.position

    def end_quick_view(self, position_ms, *, resume=False):
        self.end_calls.append((int(position_ms), bool(resume)))

    def screen(self):
        return None


class FakePlayer:
    def __init__(self, position=0, duration=10_000):
        self.current_position = position
        self.current_duration = duration
        self.pause_calls = 0
        self.play_calls = 0
        self.source = None
        self.state = quick_view.QMediaPlayer.PlaybackState.PausedState

    def position(self):
        return self.current_position

    def duration(self):
        return self.current_duration

    def pause(self):
        self.pause_calls += 1
        self.state = quick_view.QMediaPlayer.PlaybackState.PausedState

    def play(self):
        self.play_calls += 1
        self.state = quick_view.QMediaPlayer.PlaybackState.PlayingState

    def playbackState(self):
        return self.state

    def stop(self):
        self.current_position = 0

    def setSource(self, source):
        self.source = source

    def setPosition(self, position):
        self.current_position = int(position)


class FakeStack:
    def __init__(self):
        self.index = None

    def setCurrentIndex(self, index):
        self.index = index


class FakeKeyEvent:
    def __init__(self, *, auto_repeat=False):
        self._auto_repeat = auto_repeat

    def type(self):
        return QEvent.Type.KeyPress

    def key(self):
        return Qt.Key.Key_Space

    def isAutoRepeat(self):
        return self._auto_repeat


class QuickViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_media_payload_is_previewable_with_still_or_video(self):
        still = QPixmap(4, 4)
        self.assertTrue(
            quick_view.QuickViewMedia("Shot", "thumb.jpg", thumbnail=still).is_previewable
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "preview.mp4"
            video_path.write_bytes(b"preview")
            self.assertTrue(
                quick_view.QuickViewMedia(
                    "Shot",
                    "preview.mp4",
                    video_path=str(video_path),
                ).is_previewable
            )
        self.assertFalse(
            quick_view.QuickViewMedia(
                "Shot",
                "missing.mp4",
                video_path="/missing/preview.mp4",
            ).is_previewable
        )
        self.assertFalse(quick_view.QuickViewMedia("Shot", "Thumbnail").is_previewable)

    def test_controller_toggles_and_transfers_position_back_to_hovered_card(self):
        popup = FakePopup()
        card = FakeCard("sho010", position=1250)
        controller = quick_view.QuickViewController(
            cards_provider=lambda: [card],
            popup_factory=lambda: popup,
        )
        controller.set_hovered_card(card)

        self.assertTrue(controller.toggle())
        self.assertEqual(card.begin_calls, 1)
        self.assertEqual(popup.shown[-1][1], 1250)

        popup.position = 4800
        self.assertTrue(controller.toggle())
        self.assertEqual(card.end_calls, [(4800, True)])
        self.assertFalse(controller.is_open)

    def test_navigation_skips_unpreviewable_cards_and_does_not_wrap(self):
        popup = FakePopup()
        first = FakeCard("sho010", position=100)
        blank = FakeCard("sho020", previewable=False)
        last = FakeCard("sho030", position=300)
        controller = quick_view.QuickViewController(
            cards_provider=lambda: [first, blank, last],
            popup_factory=lambda: popup,
        )
        controller.set_hovered_card(first)
        self.assertTrue(controller.toggle())

        popup.position = 900
        self.assertTrue(controller.navigate(1))
        self.assertIs(controller.active_card(), last)
        self.assertEqual(last.begin_calls, 1)
        self.assertFalse(controller.navigate(1))

        popup.position = 1200
        self.assertTrue(controller.navigate(-1))
        self.assertIs(controller.active_card(), first)
        self.assertEqual(popup.shown[-1][1], 900)
        self.assertFalse(controller.navigate(-1))

    def test_popup_shows_still_without_multimedia_and_hides_controls(self):
        pixmap = QPixmap(64, 36)
        pixmap.fill(Qt.GlobalColor.blue)
        media = quick_view.QuickViewMedia("sho010", "thumb.jpg", thumbnail=pixmap)
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)

        popup.show_media(media, screen=self.app.primaryScreen())
        self.app.processEvents()

        self.assertEqual(popup.title_label.text(), "sho010")
        self.assertEqual(popup.filename_label.text(), "thumb.jpg")
        self.assertIn("←/→ Shot", popup.shortcut_label.text())
        self.assertIn("↑/↓ Ver", popup.shortcut_label.text())
        self.assertIn("J/K/L", popup.shortcut_label.text())
        self.assertIn("Scroll to zoom", popup.shortcut_label.text())
        self.assertIn("Double-click to reset", popup.shortcut_label.text())
        self.assertIn("Space/Esc", popup.shortcut_label.text())
        self.assertTrue(popup.controls.isHidden())
        self.assertFalse(popup.image_label.pixmap().isNull())
        dismissed = []
        popup.dismissed.connect(lambda: dismissed.append(True))
        popup.hide()
        self.app.processEvents()
        self.assertEqual(dismissed, [True])

    def test_up_down_navigates_thumbnail_then_preview_versions(self):
        pixmap = QPixmap(64, 36)
        pixmap.fill(Qt.GlobalColor.blue)
        with tempfile.TemporaryDirectory() as tmpdir:
            first_video = Path(tmpdir) / "sho010_v001.mp4"
            latest_video = Path(tmpdir) / "sho010_v002.mp4"
            first_video.write_bytes(b"first")
            latest_video.write_bytes(b"latest")
            media = quick_view.QuickViewMedia(
                "sho010",
                latest_video.name,
                video_path=str(latest_video),
                thumbnail=pixmap,
                thumbnail_filename="sho010_thumb.jpg",
                video_versions=(str(first_video), str(latest_video)),
            )
            with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
                popup = quick_view.QuickViewPopup()
            self.addCleanup(popup.close)

            popup.show_media(media, screen=self.app.primaryScreen())
            self.app.processEvents()

            self.assertEqual(
                [entry.filename for entry in popup._version_entries],
                ["sho010_thumb.jpg", "sho010_v001.mp4", "sho010_v002.mp4"],
            )
            self.assertEqual(popup._version_index, 2)
            self.assertTrue(popup.navigate_version(-1))
            self.assertEqual(popup._version_index, 1)
            self.assertTrue(popup.navigate_version(-1))
            self.assertEqual(popup._version_index, 0)
            self.assertEqual(popup.filename_label.toolTip(), "Original thumbnail")
            self.assertFalse(popup.navigate_version(-1))
            self.assertTrue(popup.navigate_version(1))
            self.assertEqual(popup._version_index, 1)

    @unittest.skipUnless(quick_view.HAS_MULTIMEDIA, "Qt multimedia is unavailable")
    def test_version_navigation_preserves_position_and_playback_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_video = Path(tmpdir) / "sho010_v001.mp4"
            current_video = Path(tmpdir) / "sho010_v002.mp4"
            previous_video.write_bytes(b"previous")
            current_video.write_bytes(b"current")

            for playing in (False, True):
                with self.subTest(playing=playing):
                    with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
                        popup = quick_view.QuickViewPopup()
                    self.addCleanup(popup.close)
                    popup.video_widget = QLabel()
                    popup.media_stack.addWidget(popup.video_widget)
                    player = FakePlayer(position=2_000)
                    if playing:
                        player.state = quick_view.QMediaPlayer.PlaybackState.PlayingState
                    popup.player = player
                    popup._is_video = True
                    popup._version_entries = (
                        quick_view.QuickViewEntry(
                            filename=previous_video.name,
                            video_path=str(previous_video),
                        ),
                        quick_view.QuickViewEntry(
                            filename=current_video.name,
                            video_path=str(current_video),
                        ),
                    )
                    popup._version_index = 1

                    with mock.patch.object(quick_view, "HAS_MULTIMEDIA", True):
                        self.assertTrue(popup.navigate_version(-1))

                    self.assertEqual(popup._version_index, 0)
                    self.assertEqual(player.position(), 2_000)
                    self.assertEqual(player.source.toLocalFile(), str(previous_video))
                    self.assertEqual(player.play_calls, 1 if playing else 0)
                    self.assertEqual(player.pause_calls, 0 if playing else 1)

    def test_pending_version_position_survives_zero_duration_signal(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        popup.player = FakePlayer(position=0, duration=0)
        popup._pending_position_ms = 2_000

        popup._on_duration_changed(0)
        self.assertEqual(popup._pending_position_ms, 2_000)
        self.assertEqual(popup.player.position(), 0)

        popup._on_duration_changed(8_000)
        self.assertEqual(popup._pending_position_ms, 2_000)
        self.assertEqual(popup.player.position(), 2_000)

        popup.player.current_duration = 8_000
        popup._waiting_for_media_load = True
        popup._saw_loading_media = False
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", True):
            popup._on_media_status_changed(
                quick_view.QMediaPlayer.MediaStatus.LoadedMedia
            )
            self.assertEqual(popup._pending_position_ms, 2_000)
            popup._on_media_status_changed(
                quick_view.QMediaPlayer.MediaStatus.LoadingMedia
            )
            popup._on_media_status_changed(
                quick_view.QMediaPlayer.MediaStatus.LoadedMedia
            )
        self.assertEqual(popup._pending_position_ms, 0)
        self.assertEqual(popup.player.position(), 2_000)

    def test_popup_resize_grip_resizes_and_size_survives_navigation(self):
        pixmap = QPixmap(64, 36)
        pixmap.fill(Qt.GlobalColor.blue)
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        first = quick_view.QuickViewMedia("sho010", "one.jpg", thumbnail=pixmap)
        second = quick_view.QuickViewMedia("sho020", "two.jpg", thumbnail=pixmap)
        popup.show_media(first, screen=self.app.primaryScreen())
        self.app.processEvents()
        initial_size = popup.size()
        initial_center = popup.geometry().center()

        QTest.mousePress(
            popup.resize_grip,
            Qt.MouseButton.LeftButton,
            pos=QPoint(10, 10),
        )
        QTest.mouseMove(popup.resize_grip, QPoint(90, 70))
        QTest.mouseRelease(
            popup.resize_grip,
            Qt.MouseButton.LeftButton,
            pos=QPoint(90, 70),
        )
        self.app.processEvents()
        resized = popup.size()

        self.assertGreater(resized.width(), initial_size.width())
        self.assertGreater(resized.height(), initial_size.height())
        resized_center = popup.geometry().center()
        self.assertLessEqual(abs(resized_center.x() - initial_center.x()), 1)
        self.assertLessEqual(abs(resized_center.y() - initial_center.y()), 1)
        popup.show_media(second, screen=self.app.primaryScreen())
        self.app.processEvents()
        self.assertEqual(popup.size(), resized)

    def test_popup_uses_configured_percentage_of_available_screen(self):
        class FakeScreen:
            @staticmethod
            def availableGeometry():
                return QRect(100, 50, 2000, 1000)

        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup(screen_percentage=50)
        self.addCleanup(popup.close)

        popup._position_on_screen(FakeScreen())

        self.assertEqual(popup.width(), 1000)
        self.assertEqual(popup.height(), 500)
        self.assertEqual(popup.geometry().center(), QRect(100, 50, 2000, 1000).center())

        popup.set_screen_percentage(10)
        self.assertEqual(popup._screen_percentage, 25)
        popup.set_screen_percentage(120)
        self.assertEqual(popup._screen_percentage, 100)

    def test_shot_card_registers_hover_for_still_quick_view(self):
        card = widgets.ShotCard.__new__(widgets.ShotCard)
        QWidget.__init__(card)
        target = QLabel(card)
        controller = mock.Mock()
        card._thumbnail_hover_target = target
        card._thumbnail_hovered = False
        card._quick_view_controller = controller
        card._quick_view_active = False
        card._has_preview = False
        card._preview_video_path = None
        card._video_player = None

        card.eventFilter(target, QEvent(QEvent.Type.Enter))
        self.assertTrue(card._thumbnail_hovered)
        controller.set_hovered_card.assert_called_once_with(card)

        card.eventFilter(target, QEvent(QEvent.Type.Leave))
        self.assertFalse(card._thumbnail_hovered)
        controller.clear_hovered_card.assert_called_once_with(card)

    @unittest.skipUnless(quick_view.HAS_MULTIMEDIA, "Qt multimedia is unavailable")
    def test_update_thumbnail_captures_full_video_frame_and_disables_during_upload(self):
        from PyQt6.QtMultimedia import QVideoFrame, QVideoSink

        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.assertFalse(popup.update_thumbnail_button.isEnabled())
        image = QImage(640, 360, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        sink = QVideoSink()
        frame = QVideoFrame(image)
        sink.setVideoFrame(frame)
        popup.video_widget = SimpleNamespace(videoSink=lambda: sink)
        popup._is_video = True
        popup._on_thumbnail_frame_changed(frame)
        captured = []
        popup.thumbnail_update_requested.connect(captured.append)
        popup.update_thumbnail_button.click()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].size(), image.size())
        self.assertEqual(captured[0].pixelColor(0, 0), image.pixelColor(0, 0))

        popup.set_thumbnail_upload_state(True)
        self.assertFalse(popup.update_thumbnail_button.isEnabled())
        popup._request_thumbnail_update()
        self.assertEqual(len(captured), 1)
        popup.set_thumbnail_upload_state(False)
        self.assertTrue(popup.update_thumbnail_button.isEnabled())
        sink.setVideoFrame(QVideoFrame())
        popup._request_thumbnail_update()
        self.assertEqual(len(captured), 1)
        self.assertIn("No frame available", popup.thumbnail_status_label.text())
        popup.deleteLater()

    def _thumbnail_upload_fixture(self):
        popup = FakePopup()
        card = FakeCard("sho010")
        card._shot_id = 10
        card._api = mock.Mock()
        card._api.upload_shot_thumbnail.return_value = {"thumbnail": "/media/new.png"}
        card.apply_thumbnail_update = mock.Mock()
        controller = quick_view.QuickViewController(popup_factory=lambda: popup)
        self.addCleanup(controller.dismiss)
        controller.open_card(card)
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        return controller, popup, card, image

    def test_thumbnail_upload_saves_png_refreshes_card_and_cleans_temporary_file(self):
        controller, popup, card, image = self._thumbnail_upload_fixture()
        uploaded_paths = []

        def upload(shot_id, path):
            self.assertEqual(shot_id, 10)
            self.assertEqual(QImage(path).size(), image.size())
            uploaded_paths.append(Path(path))
            return {"thumbnail": "/media/new.png"}

        card._api.upload_shot_thumbnail.side_effect = upload
        with mock.patch.object(quick_view.QThreadPool, "globalInstance") as pool:
            popup.thumbnail_update_requested.emit(image)
            self.assertEqual(popup.thumbnail_states[-1][0], True)
            popup.thumbnail_update_requested.emit(image)
            pool.return_value.start.assert_called_once()
            worker = pool.return_value.start.call_args.args[0]
            worker.run()

        card.apply_thumbnail_update.assert_called_once()
        self.assertEqual(card.apply_thumbnail_update.call_args.args[0], "/media/new.png")
        self.assertFalse(uploaded_paths[0].exists())
        self.assertEqual(popup.thumbnail_states[-1], (False, "Thumbnail updated."))
        self.assertEqual(popup.thumbnail_updates[-1][1], "new.png")
        self.assertEqual(controller._thumbnail_uploads, {})

    def test_failed_thumbnail_upload_preserves_card_and_allows_retry(self):
        controller, popup, card, image = self._thumbnail_upload_fixture()
        card._api.upload_shot_thumbnail.side_effect = RuntimeError("Server unavailable")
        with mock.patch.object(quick_view.QThreadPool, "globalInstance") as pool:
            popup.thumbnail_update_requested.emit(image)
            pool.return_value.start.call_args.args[0].run()
        card.apply_thumbnail_update.assert_not_called()
        self.assertEqual(popup.thumbnail_updates, [])
        self.assertEqual(popup.thumbnail_states[-1][0], False)
        self.assertIn("Server unavailable", popup.thumbnail_states[-1][1])
        self.assertEqual(controller._thumbnail_uploads, {})

    def test_thumbnail_upload_runs_off_ui_thread_and_applies_result_on_ui_thread(self):
        controller, popup, card, image = self._thumbnail_upload_fixture()
        upload_threads = []
        apply_threads = []

        def upload(shot_id, path):
            upload_threads.append(QThread.currentThread())
            return {"thumbnail": "/media/new.png"}

        card._api.upload_shot_thumbnail.side_effect = upload
        card.apply_thumbnail_update.side_effect = lambda *_: apply_threads.append(QThread.currentThread())
        popup.thumbnail_update_requested.emit(image)
        for _ in range(200):
            if not controller._thumbnail_uploads:
                break
            QTest.qWait(10)
        self.assertEqual(controller._thumbnail_uploads, {})
        self.assertEqual(len(upload_threads), 1)
        self.assertIsNot(upload_threads[0], self.app.thread())
        self.assertEqual(apply_threads, [self.app.thread()])

    def test_thumbnail_upload_completion_does_not_change_another_shot(self):
        controller, popup, card, image = self._thumbnail_upload_fixture()
        other = FakeCard("sho020")
        other._shot_id = 20
        with mock.patch.object(quick_view.QThreadPool, "globalInstance") as pool:
            popup.thumbnail_update_requested.emit(image)
            worker = pool.return_value.start.call_args.args[0]
            controller.open_card(other)
            states = list(popup.thumbnail_states)
            worker.run()
        card.apply_thumbnail_update.assert_called_once()
        self.assertIs(controller.active_card(), other)
        self.assertEqual(popup.thumbnail_states, states)
        self.assertEqual(popup.thumbnail_updates, [])

    def test_updated_thumbnail_preserves_preview_and_adds_missing_original_entry(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        popup._version_entries = (quick_view.QuickViewEntry("preview.mov", video_path="preview.mov"),)
        popup._version_index = 0
        popup._is_video = True
        popup.player = FakePlayer(position=7300)
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        popup.apply_updated_thumbnail(image, "new.png")
        self.assertEqual(popup.player.position(), 7300)
        self.assertTrue(popup._is_video)
        self.assertEqual(popup._version_index, 1)
        self.assertEqual(popup._version_entries[0].thumbnail.size(), image.size())
        self.assertEqual(popup._version_entries[1].video_path, "preview.mov")
        popup.apply_updated_thumbnail(image, "newer.png")
        self.assertEqual(len(popup._version_entries), 2)
        self.assertEqual(popup._version_entries[0].filename, "newer.png")
        popup.deleteLater()

    def test_shot_card_applies_thumbnail_immediately_and_ignores_old_download(self):
        card = widgets.ShotCard.__new__(widgets.ShotCard)
        QWidget.__init__(card)
        card.data = {"thumbnail": "/media/old.png"}
        card._api = SimpleNamespace(base_url="http://server:8000/api/")
        card.label_thumbnail = QLabel(card)
        card._thumb_orig = None
        card._thumb_sig = None
        card._thumbnails_enabled = True
        card._thumb_target_width = 80
        card._base_thumb_target_width = 80
        card._compact_mode = False
        with mock.patch.object(widgets.ImageLoader, "instance") as loader:
            card.set_thumbnail("http://server:8000/media/old.png")
            old_callback = loader.return_value.load.call_args.args[1]
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        card.apply_thumbnail_update("/media/new.png", image)
        old_callback(QPixmap(10, 10))
        self.assertEqual(card.data["thumbnail"], "/media/new.png")
        self.assertEqual(card._current_thumbnail_url, "http://server:8000/media/new.png")
        self.assertEqual(card._thumb_orig.size(), image.size())
        self.assertEqual(card.label_thumbnail.pixmap().width(), 80)
        card.deleteLater()

    def test_hover_leave_releases_preview_file(self):
        card = widgets.ShotCard.__new__(widgets.ShotCard)
        QWidget.__init__(card)
        card._video_player = FakePlayer(position=2100)
        card._preview_stack = FakeStack()
        card._quick_view_active = False
        with mock.patch.object(widgets, "HAS_MULTIMEDIA", True):
            card._on_preview_hover_leave()
        self.assertTrue(card._video_player.source.isEmpty())
        self.assertEqual(card._preview_stack.index, 0)

    def test_popup_hide_releases_file_and_preserves_handoff_position(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        player = FakePlayer(position=4200)
        popup.player = player
        popup._is_video = True
        popup._version_index = 0
        popup._version_entries = [SimpleNamespace(key="preview")]
        popup._session_visible = True
        handoff_positions = []
        popup.dismissed.connect(lambda: handoff_positions.append(popup.handoff_position()))
        from PyQt6.QtGui import QHideEvent
        popup.hideEvent(QHideEvent())
        self.assertTrue(player.source.isEmpty())
        self.assertEqual(handoff_positions, [4200])
        self.assertEqual(popup.handoff_position(), 4200)
        popup.deleteLater()

    def test_shot_card_handoff_releases_file_and_resumes_at_popup_position(self):
        card = widgets.ShotCard.__new__(widgets.ShotCard)
        QWidget.__init__(card)
        card._video_player = FakePlayer(position=2100)
        card._preview_stack = FakeStack()
        card._preview_video_path = "/tmp/preview.mp4"
        card._has_preview = True
        card._thumbnail_hovered = True
        card._quick_view_active = False
        card._quick_view_resume_position = 0

        with mock.patch.object(widgets, "HAS_MULTIMEDIA", True):
            self.assertEqual(card.begin_quick_view(), 2100)
            self.assertTrue(card._video_player.source.isEmpty())
            card.end_quick_view(7300, resume=True)
            self.app.processEvents()

        self.assertEqual(card._video_player.current_position, 7300)
        self.assertEqual(card._video_player.play_calls, 1)
        self.assertEqual(card._preview_stack.index, 1)

        card._thumbnail_hovered = False
        card.end_quick_view(9100, resume=False)
        self.app.processEvents()
        self.assertEqual(card._quick_view_resume_position, 0)
        self.assertEqual(card._video_player.play_calls, 1)

    def test_shot_card_payload_includes_all_filesystem_preview_versions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            first = preview_dir / "sho010_v001.mp4"
            latest = preview_dir / "sho010_v002.mp4"
            first.write_bytes(b"first")
            latest.write_bytes(b"latest")

            card = widgets.ShotCard.__new__(widgets.ShotCard)
            QWidget.__init__(card)
            card._preview_video_path = str(latest)
            card._thumb_orig = QPixmap(64, 36)
            card._thumb_orig.fill(Qt.GlobalColor.darkGray)
            card.label_thumbnail = QLabel(card)
            card._current_thumbnail_url = str(shot_dir / "sho010_thumb.jpg")
            card.filesIO = widgets.filesIO.Folders()
            card.shot_dir = str(shot_dir)
            card.data = {"title": "sho010"}

            media = card.quick_view_media()

            self.assertEqual(media.video_path, str(latest))
            self.assertEqual(media.video_versions, (str(first), str(latest)))
            self.assertEqual(media.thumbnail_filename, "sho010_thumb.jpg")

    def test_nukedash_space_handler_toggles_and_ignores_auto_repeat(self):
        page = page_nukedash.page_nukedash.__new__(page_nukedash.page_nukedash)
        QWidget.__init__(page)
        controller = mock.Mock()
        controller.is_open = False
        controller.toggle.return_value = True
        page._quick_view_controller = controller
        page._quick_view_shortcut_available = mock.Mock(return_value=True)
        page._open_hovered_render_in_default_app = mock.Mock(return_value=False)

        self.assertTrue(page.eventFilter(page, FakeKeyEvent()))
        controller.toggle.assert_called_once_with()

        controller.toggle.reset_mock()
        self.assertFalse(page.eventFilter(page, FakeKeyEvent(auto_repeat=True)))
        controller.toggle.assert_not_called()

    def test_nukedash_space_opens_hovered_render_instead_of_quick_view(self):
        page = page_nukedash.page_nukedash.__new__(page_nukedash.page_nukedash)
        QWidget.__init__(page)
        controller = mock.Mock()
        controller.is_open = False
        page._quick_view_controller = controller
        page._quick_view_shortcut_available = mock.Mock(return_value=True)
        page._open_hovered_render_in_default_app = mock.Mock(return_value=True)

        self.assertTrue(page.eventFilter(page, FakeKeyEvent()))

        page._open_hovered_render_in_default_app.assert_called_once_with()
        controller.toggle.assert_not_called()

    def test_hovered_render_opens_with_card_default_file_handler(self):
        page = page_nukedash.page_nukedash.__new__(page_nukedash.page_nukedash)
        QWidget.__init__(page)
        button = mock.Mock()
        button.underMouse.return_value = True
        button.property.return_value = "/show/shot/render_v003.mov"
        folders = mock.Mock()
        card = SimpleNamespace(btn_latest_render=button, filesIO=folders)
        page._iter_timeline_shot_cards = mock.Mock(return_value=[card])

        self.assertTrue(page._open_hovered_render_in_default_app())

        folders.open_file.assert_called_once_with("/show/shot/render_v003.mov")

    def test_text_entry_widgets_block_nukedash_space_shortcut(self):
        blockers = [
            QLineEdit(),
            QTextEdit(),
            QPlainTextEdit(),
        ]
        combo = QComboBox()
        combo.setEditable(True)
        blockers.append(combo)
        for widget in blockers:
            with self.subTest(widget=type(widget).__name__):
                self.assertTrue(
                    page_nukedash.page_nukedash._quick_view_focus_blocks_shortcut(widget)
                )
                widget.deleteLater()

        self.assertFalse(
            page_nukedash.page_nukedash._quick_view_focus_blocks_shortcut(QLabel())
        )

    def test_time_format_supports_minutes_and_hours(self):
        self.assertEqual(quick_view.QuickViewPopup._format_time(65_000), "01:05")
        self.assertEqual(quick_view.QuickViewPopup._format_time(3_665_000), "1:01:05")

    def test_seek_slider_clicks_jump_directly_to_pointer_position(self):
        slider = quick_view.SeekSlider(Qt.Orientation.Horizontal)
        self.addCleanup(slider.close)
        slider.setRange(0, 1_000)
        slider.resize(200, 24)
        slider.show()
        self.app.processEvents()
        moved_values = []
        slider.sliderMoved.connect(moved_values.append)

        QTest.mouseClick(
            slider,
            Qt.MouseButton.LeftButton,
            pos=QPoint(150, 12),
        )

        self.assertGreaterEqual(slider.value(), 740)
        self.assertLessEqual(slider.value(), 760)
        self.assertEqual(len(moved_values), 1)

    def test_dragging_seek_bar_seeks_immediately_and_keeps_dragged_time(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        popup.player = FakePlayer(position=1_000, duration=20_000)
        popup.position_slider.setRange(0, 20_000)
        popup._slider_dragging = True

        popup._on_slider_moved(7_500)
        popup._on_position_changed(1_100)

        self.assertEqual(popup.player.position(), 7_500)
        self.assertEqual(popup.time_label.text(), "00:07 / 00:20")

    def test_jkl_and_frame_step_shortcuts_control_video_transport(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        popup.player = FakePlayer(position=1_000, duration=2_000)
        popup._is_video = True
        popup.position_slider.setRange(0, 2_000)

        popup._forward_shortcut.activated.emit()
        self.assertEqual(popup.player.play_calls, 1)
        self.assertEqual(
            popup.player.playbackState(),
            quick_view.QMediaPlayer.PlaybackState.PlayingState,
        )

        popup._stop_shortcut.activated.emit()
        self.assertEqual(
            popup.player.playbackState(),
            quick_view.QMediaPlayer.PlaybackState.PausedState,
        )

        popup._previous_frame_shortcut.activated.emit()
        self.assertEqual(popup.player.position(), 960)
        popup._next_frame_shortcut.activated.emit()
        self.assertEqual(popup.player.position(), 1_000)

        popup._reverse_shortcut.activated.emit()
        self.assertTrue(popup._reverse_timer.isActive())
        self.assertEqual(popup.player.position(), 960)
        popup._stop_shortcut.activated.emit()
        self.assertFalse(popup._reverse_timer.isActive())

    def test_media_view_zooms_and_resets_around_pointer(self):
        view = quick_view.PanZoomViewport()
        self.addCleanup(view.close)
        content = QLabel("preview")
        view.setWidget(content)
        view.resize(300, 200)
        view.show()
        self.app.processEvents()
        fitted_size = content.size()

        view.set_zoom(2.0, QPoint(225, 100))
        self.app.processEvents()

        self.assertEqual(view.zoom_factor, 2.0)
        self.assertEqual(content.width(), fitted_size.width() * 2)
        self.assertEqual(content.height(), fitted_size.height() * 2)
        self.assertGreater(view.horizontalScrollBar().value(), 0)

        horizontal = view.horizontalScrollBar()
        vertical = view.verticalScrollBar()
        horizontal.setValue(horizontal.maximum() // 2)
        vertical.setValue(vertical.maximum() // 2)
        before_pan = (horizontal.value(), vertical.value())
        QTest.mousePress(
            content,
            Qt.MouseButton.LeftButton,
            pos=QPoint(150, 100),
        )
        QTest.mouseMove(content, QPoint(180, 120))
        QTest.mouseRelease(
            content,
            Qt.MouseButton.LeftButton,
            pos=QPoint(180, 120),
        )
        self.assertLess(horizontal.value(), before_pan[0])
        self.assertLess(vertical.value(), before_pan[1])

        view.reset_zoom()
        self.app.processEvents()
        self.assertEqual(view.zoom_factor, 1.0)
        self.assertEqual(content.size(), view.viewport().size())
        self.assertEqual(view.horizontalScrollBar().value(), 0)
        self.assertEqual(view.verticalScrollBar().value(), 0)

        view.set_zoom(0.5)
        self.app.processEvents()
        self.assertEqual(view.zoom_factor, 0.5)
        self.assertEqual(content.width(), round(view.viewport().width() * 0.5))
        self.assertEqual(content.height(), round(view.viewport().height() * 0.5))

        view.set_zoom(0.1)
        self.assertEqual(view.zoom_factor, 0.25)

        view.reset_zoom()
        self.assertEqual(view.zoom_factor, 1.0)

    def test_clicking_video_toggles_playback_at_fit_and_zoomed_sizes(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        popup.player = FakePlayer()
        popup._is_video = True
        video = QLabel("video frame")
        popup.media_stack.addWidget(video)
        popup.media_stack.setCurrentWidget(video)
        popup.media_view.add_interaction_target(video)
        popup.show()
        self.app.processEvents()

        for zoom in (1.0, 2.0):
            with self.subTest(zoom=zoom):
                popup.media_view.set_zoom(zoom)
                self.app.processEvents()
                point = video.mapFrom(popup.media_view.viewport(), popup.media_view.viewport().rect().center())
                QTest.mouseClick(video, Qt.MouseButton.LeftButton, pos=point)
                QTest.qWait(self.app.doubleClickInterval() + 100)
                self.assertEqual(popup.player.playbackState(), quick_view.QMediaPlayer.PlaybackState.PlayingState)
                QTest.mouseClick(video, Qt.MouseButton.LeftButton, pos=point)
                QTest.qWait(self.app.doubleClickInterval() + 100)
                self.assertEqual(popup.player.playbackState(), quick_view.QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(popup.player.play_calls, 2)
        self.assertEqual(popup.player.pause_calls, 2)

    def test_dragging_and_double_clicking_video_do_not_toggle_playback(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        popup.player = FakePlayer()
        popup._is_video = True
        video = QLabel("video frame")
        popup.media_stack.addWidget(video)
        popup.media_stack.setCurrentWidget(video)
        popup.media_view.add_interaction_target(video)
        popup.show()
        self.app.processEvents()
        popup.media_view.set_zoom(2.0)
        self.app.processEvents()
        point = video.mapFrom(popup.media_view.viewport(), QPoint(120, 80))
        QTest.mousePress(video, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(video, point + QPoint(30, 20))
        QTest.mouseRelease(video, Qt.MouseButton.LeftButton, pos=point + QPoint(30, 20))
        QTest.qWait(self.app.doubleClickInterval() + 100)
        self.assertEqual(popup.player.play_calls, 0)

        QTest.mouseClick(video, Qt.MouseButton.LeftButton)
        QTest.mouseDClick(video, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(video, Qt.MouseButton.LeftButton)
        QTest.qWait(self.app.doubleClickInterval() + 100)
        self.assertEqual(popup.media_view.zoom_factor, 1.0)
        self.assertEqual(popup.player.play_calls, 0)
        self.assertEqual(popup.player.pause_calls, 0)

        QTest.mouseClick(video, Qt.MouseButton.RightButton)
        QTest.qWait(self.app.doubleClickInterval() + 100)
        self.assertEqual(popup.player.play_calls, 0)

    def test_switching_media_cancels_pending_playback_click(self):
        with mock.patch.object(quick_view, "HAS_MULTIMEDIA", False):
            popup = quick_view.QuickViewPopup()
        self.addCleanup(popup.close)
        popup.player = FakePlayer()
        popup._is_video = True
        popup.show()
        self.app.processEvents()
        QTest.mouseClick(popup.image_label, Qt.MouseButton.LeftButton)
        self.assertTrue(popup.media_view._click_timer.isActive())
        popup._show_version(0)
        popup._is_video = True
        QTest.qWait(self.app.doubleClickInterval() + 100)
        self.assertEqual(popup.player.play_calls, 0)


if __name__ == "__main__":
    unittest.main()
