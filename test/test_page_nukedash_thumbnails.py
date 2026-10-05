from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
FRONTEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND_DIR))

from PyQt6.QtCore import QThread
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget

import page_nukedash
import thumbnail_updates


class ThumbnailCard(QWidget):
    def __init__(self, data):
        super().__init__()
        self.data = dict(data)
        self.updates = []

    def apply_thumbnail_update(self, path, image):
        self.data["thumbnail"] = path
        self.updates.append((path, image, QThread.currentThread()))


class ThumbnailPageHarness(QMainWindow):
    _active_job_data = page_nukedash.page_nukedash._active_job_data
    _iter_timeline_shot_cards = page_nukedash.page_nukedash._iter_timeline_shot_cards
    _on_update_all_thumbnails_clicked = page_nukedash.page_nukedash._on_update_all_thumbnails_clicked
    _on_thumbnail_batch_progress = page_nukedash.page_nukedash._on_thumbnail_batch_progress
    _on_thumbnail_batch_updated = page_nukedash.page_nukedash._on_thumbnail_batch_updated
    _on_thumbnail_batch_finished = page_nukedash.page_nukedash._on_thumbnail_batch_finished
    _on_data = page_nukedash.page_nukedash._on_data

    def __init__(self):
        super().__init__()
        shots = [{"id": 1, "title": "Visible shot"}, {"id": 2, "title": "Hidden shot", "hidden": True}]
        self._active_job_id = 10
        self._jobs_by_id = {10: {"timelines": [{"shots": [shot]} for shot in shots]}}
        self._thumbnail_batch_worker = None
        self._thumbnail_batch_progress = None
        self.btn_update_thumbnails = QPushButton("thumbs", self)
        self.btn_make_previews = QPushButton("mp4s", self)
        self.btn_update_thumbnails.clicked.connect(self._on_update_all_thumbnails_clicked)
        self.set_auto_refresh_paused = mock.Mock()
        self.timelines_tabs = QTabWidget(self)
        self.cards = []
        for index, shot in enumerate(shots):
            timeline = QWidget()
            timeline.shots_layout = QVBoxLayout(timeline)
            card = ThumbnailCard(shot)
            timeline.shots_layout.addWidget(card)
            if shot.get("hidden"):
                card.hide()
            self.cards.append(card)
            self.timelines_tabs.addTab(timeline, f"Timeline {index}")


class NukeDashThumbnailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_thumbs_button_is_immediately_after_mp4s(self):
        tree = ET.parse(FRONTEND_DIR / "basic.ui")
        for layout in tree.findall(".//layout"):
            names = [item.find("widget").get("name") for item in layout.findall("item") if item.find("widget") is not None]
            if "btn_make_previews" in names:
                self.assertEqual(names[names.index("btn_make_previews") + 1], "btn_update_thumbnails")
                button = tree.find(".//widget[@name='btn_update_thumbnails']")
                self.assertEqual(button.find("property[@name='text']/string").text, "thumbs")
                break
        else:
            self.fail("MP4s button was not found")

    def test_batch_updates_hidden_shots_and_all_timelines_then_restores_controls(self):
        page = ThumbnailPageHarness()
        self.addCleanup(page.close)
        api = mock.Mock()
        api.upload_shot_thumbnail.side_effect = lambda shot_id, _: {"thumbnail": f"/media/{shot_id}.png"}
        image = QImage(64, 32, QImage.Format.Format_RGB888)
        image.fill(0x0000FF)
        with mock.patch.object(page_nukedash.http_help, "DjangoAPI", return_value=api), \
                mock.patch.object(page_nukedash.QThreadPool, "globalInstance") as pool, \
                mock.patch.object(page_nukedash.QMessageBox, "information") as message, \
                mock.patch.object(thumbnail_updates, "latest_thumbnail_preview", return_value="preview.mp4"), \
                mock.patch.object(thumbnail_updates, "extract_preview_thumbnail", return_value=image):
            page.btn_update_thumbnails.click()
            self.assertFalse(page.btn_update_thumbnails.isEnabled())
            self.assertFalse(page.btn_make_previews.isEnabled())
            worker = pool.return_value.start.call_args.args[0]
            self.assertEqual([shot["id"] for shot in worker.shots], [1, 2])
            page._on_data([])
            self.assertIn(10, page._jobs_by_id)
            worker.run()
            self.assertIn("Updated: 2", message.call_args.args[2])
        self.assertTrue(page.btn_update_thumbnails.isEnabled())
        self.assertTrue(page.btn_make_previews.isEnabled())
        self.assertIsNone(page._thumbnail_batch_worker)
        self.assertEqual(page.set_auto_refresh_paused.call_args_list, [mock.call(True), mock.call(False)])
        for index, card in enumerate(page.cards, start=1):
            self.assertEqual(card.data["thumbnail"], f"/media/{index}.png")
            cached = page._jobs_by_id[10]["timelines"][index - 1]["shots"][0]
            self.assertEqual(cached["thumbnail"], card.data["thumbnail"])

    def test_background_batch_keeps_card_updates_on_ui_thread(self):
        page = ThumbnailPageHarness()
        self.addCleanup(page.close)
        api = mock.Mock()
        threads = []

        def upload(shot_id, _):
            threads.append(QThread.currentThread())
            return {"thumbnail": f"/media/{shot_id}.png"}

        api.upload_shot_thumbnail.side_effect = upload
        image = QImage(64, 32, QImage.Format.Format_RGB888)
        image.fill(0x0000FF)
        with mock.patch.object(page_nukedash.http_help, "DjangoAPI", return_value=api), \
                mock.patch.object(page_nukedash.QMessageBox, "information"), \
                mock.patch.object(thumbnail_updates, "latest_thumbnail_preview", return_value="preview.mp4"), \
                mock.patch.object(thumbnail_updates, "extract_preview_thumbnail", return_value=image):
            page.btn_update_thumbnails.click()
            for _ in range(200):
                if page._thumbnail_batch_worker is None:
                    break
                QTest.qWait(10)
        self.assertIsNone(page._thumbnail_batch_worker)
        self.assertEqual(len(threads), 2)
        self.assertTrue(all(thread is not self.app.thread() for thread in threads))
        for card in page.cards:
            self.assertEqual(card.updates[0][2], self.app.thread())

    def test_no_selected_job_does_not_start_worker(self):
        page = ThumbnailPageHarness()
        self.addCleanup(page.close)
        page._active_job_id = None
        with mock.patch.object(page_nukedash.QMessageBox, "information") as message, \
                mock.patch.object(page_nukedash.QThreadPool, "globalInstance") as pool:
            page.btn_update_thumbnails.click()
        pool.assert_not_called()
        self.assertEqual(message.call_args.args[2], "No job selected.")


if __name__ == "__main__":
    unittest.main()
