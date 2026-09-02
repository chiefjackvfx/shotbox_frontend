from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
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

    def __init__(self):
        super().__init__()
        self.visible = False
        self.position = 0
        self.shown = []

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

    def position(self):
        return self.current_position

    def duration(self):
        return self.current_duration

    def pause(self):
        self.pause_calls += 1

    def play(self):
        self.play_calls += 1

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
        self.assertTrue(popup.controls.isHidden())
        self.assertFalse(popup.image_label.pixmap().isNull())
        dismissed = []
        popup.dismissed.connect(lambda: dismissed.append(True))
        popup.hide()
        self.app.processEvents()
        self.assertEqual(dismissed, [True])

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

    def test_shot_card_handoff_pauses_and_resumes_at_popup_position(self):
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
            self.assertEqual(card._video_player.pause_calls, 1)
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

    def test_nukedash_space_handler_toggles_and_ignores_auto_repeat(self):
        page = page_nukedash.page_nukedash.__new__(page_nukedash.page_nukedash)
        QWidget.__init__(page)
        controller = mock.Mock()
        controller.is_open = False
        controller.toggle.return_value = True
        page._quick_view_controller = controller
        page._quick_view_shortcut_available = mock.Mock(return_value=True)

        self.assertTrue(page.eventFilter(page, FakeKeyEvent()))
        controller.toggle.assert_called_once_with()

        controller.toggle.reset_mock()
        self.assertFalse(page.eventFilter(page, FakeKeyEvent(auto_repeat=True)))
        controller.toggle.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
