from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow, QTabWidget, QVBoxLayout, QWidget

import page_nukedash
import widgets


class FakeThumbnailShot(widgets.ShotCard):
    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.enabled_calls = []
        self.width_calls = []

    def set_thumbnails_enabled(self, enabled: bool) -> None:
        self.enabled_calls.append(bool(enabled))

    def set_thumbnail_width(self, width: int) -> None:
        self.width_calls.append(int(width))


class PreviewSizeHarness(QMainWindow):
    _on_preview_size_changed = page_nukedash.page_nukedash._on_preview_size_changed
    apply_ui_density_settings = page_nukedash.page_nukedash.apply_ui_density_settings

    def __init__(self):
        super().__init__()
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.comboBox_preview_size = QComboBox(central)
        self.comboBox_preview_size.addItems(["NoThumb", "Tiny", "Small", "Medium", "Large"])
        self.timelines_tabs = QTabWidget(central)
        self._preview_size_map = {
            "NoThumb": 240,
            "Tiny": 80,
            "Small": 160,
            "Medium": 240,
            "Large": 360,
        }
        self._card_spacing = 8
        self._row_height = 0
        self._compact_view_enabled = False

        layout.addWidget(self.comboBox_preview_size)
        layout.addWidget(self.timelines_tabs)

        container = QWidget(self.timelines_tabs)
        container_layout = QVBoxLayout(container)
        self.shot = FakeThumbnailShot(container)
        container_layout.addWidget(self.shot)
        self.timelines_tabs.addTab(container, "Timeline 1")

    def _apply_density_to_timeline(self, timeline_widget):
        return


class FakeFolders:
    def latest_nk(self, folder):
        return None

    def latest_render_info(self, folder):
        return None

    def convert_path(self, folder):
        return folder


class ThumbnailDensityNoThumbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_nothumb_disables_existing_shot_thumbnails(self):
        harness = PreviewSizeHarness()
        try:
            with mock.patch.object(widgets, "THUMBNAILS_ENABLED", True), \
                mock.patch.object(widgets, "THUMBNAIL_TARGET_WIDTH", 240):
                harness.apply_ui_density_settings(preview_size="NoThumb")
                self.assertEqual(harness.shot.enabled_calls[-1], False)
                self.assertEqual(harness.shot.width_calls, [])
                self.assertFalse(widgets.THUMBNAILS_ENABLED)

                harness.apply_ui_density_settings(preview_size="Small")
                self.assertEqual(harness.shot.enabled_calls[-1], True)
                self.assertEqual(harness.shot.width_calls[-1], 160)
                self.assertTrue(widgets.THUMBNAILS_ENABLED)
        finally:
            harness.close()
            harness.deleteLater()
            self.app.processEvents()

    def test_shot_card_skips_thumbnail_loader_when_nothumb_is_enabled(self):
        data = {
            "id": 1,
            "title": "Shot 001",
            "notes": "",
            "base_path": "/tmp",
            "tasks": [],
            "thumbnail": "/thumb.jpg",
        }
        loader = mock.Mock()

        with mock.patch.object(widgets, "THUMBNAILS_ENABLED", False), \
            mock.patch.object(widgets, "THUMBNAIL_TARGET_WIDTH", 240), \
            mock.patch.object(widgets, "HAS_MULTIMEDIA", False), \
            mock.patch.object(widgets.http_help, "DjangoAPI", return_value=mock.Mock()), \
            mock.patch.object(widgets.filesIO, "Folders", return_value=FakeFolders()), \
            mock.patch.object(widgets.ShotCard, "_setup_nk_polling", lambda self: None), \
            mock.patch.object(widgets.ShotCard, "_setup_render_polling", lambda self: None), \
            mock.patch.object(widgets.ShotCard, "_setup_preview_polling", lambda self: None), \
            mock.patch.object(widgets.ImageLoader, "instance", return_value=loader):
            card = widgets.ShotCard(data)

            try:
                loader.load.assert_not_called()
                self.assertTrue(card.label_thumbnail.isHidden())

                card.set_thumbnails_enabled(True)

                loader.load.assert_called_once()
                self.assertFalse(card.label_thumbnail.isHidden())
            finally:
                card.close()
                card.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
