from __future__ import annotations

import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class AppCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / ".cache"
        patcher = mock.patch.object(app_cache, "CACHE_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_creates_cache_categories_in_frontend_cache_root(self):
        self.assertEqual(app_cache.cache_directory("temp"), self.root / "temp")
        self.assertTrue((self.root / "temp").is_dir())

    def test_clears_only_cache_contents_and_keeps_the_root(self):
        outside = Path(self.temporary.name) / "settings.yaml"
        outside.write_text("settings", encoding="utf-8")
        (app_cache.cache_directory("exr-previews-v1") / "frame.jpg").touch()
        (app_cache.cache_directory("temp") / "request.json").touch()
        result = app_cache.clear_cache_files()
        self.assertEqual(result["errors"], [])
        self.assertEqual([path for path in self.root.iterdir() if path.name != ".lease.lock"], [])
        self.assertEqual(outside.read_text(encoding="utf-8"), "settings")

    def test_clear_does_not_follow_symlinks_outside_cache(self):
        outside = Path(self.temporary.name) / "project"
        outside.mkdir()
        sentinel = outside / "plate.exr"
        sentinel.touch()
        app_cache.cache_directory()
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        app_cache.clear_cache_files()
        self.assertTrue(sentinel.exists())
        self.assertFalse((self.root / "linked").exists())

    def test_running_task_temporary_files_survive_until_task_finishes(self):
        started = threading.Event()
        release = threading.Event()
        path = app_cache.cache_directory("temp") / "active.json"

        @app_cache.temporary_cache_task
        def operation():
            path.touch()
            started.set()
            release.wait(3)

        thread = threading.Thread(target=operation)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            (app_cache.cache_directory("exr-previews-v1") / "proxy.jpg").touch()
            result = app_cache.clear_cache_files()
            self.assertTrue(result["active_temp"])
            self.assertTrue(path.exists())
            self.assertFalse((self.root / "exr-previews-v1").exists())
        finally:
            release.set()
            thread.join(3)
        result = app_cache.clear_cache_files()
        self.assertFalse(result["active_temp"])
        self.assertFalse(path.exists())

    def test_failed_tasks_release_temporary_file_protection(self):
        @app_cache.temporary_cache_task
        def operation():
            (app_cache.cache_directory("temp") / "failed.json").touch()
            raise RuntimeError("failed")

        with self.assertRaises(RuntimeError):
            operation()
        result = app_cache.clear_cache_files()
        self.assertFalse(result["active_temp"])
        self.assertEqual([path for path in self.root.iterdir() if path.name != ".lease.lock"], [])

    def test_reports_files_that_cannot_be_removed(self):
        category = app_cache.cache_directory("exr-previews-v1")
        with mock.patch.object(app_cache.shutil, "rmtree", side_effect=PermissionError("locked")):
            result = app_cache.clear_cache_files()
        self.assertTrue(category.exists())
        self.assertIn("locked", result["errors"][0])


class ImageCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_late_download_does_not_refill_cleared_thumbnail_cache(self):
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QImage, QPixmap
        from PyQt6.QtNetwork import QNetworkReply
        from image_loader import ImageLoader

        loader = ImageLoader()
        image = QImage(16, 16, QImage.Format.Format_RGB888)
        image.fill(0xFF0000)
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        callback = mock.Mock()
        loader._cache_set("old", QPixmap.fromImage(image))
        loader._in_flight["late"] = [callback]
        reply = mock.Mock()
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        reply.readAll.return_value = payload
        generation = loader._generation
        loader.clear_cache()
        loader._on_reply("late", reply, generation)
        self.assertFalse(loader._cache)
        callback.assert_called_once()
        self.assertFalse(callback.call_args.args[0].isNull())
        loader.deleteLater()

    def test_default_proxy_cache_uses_frontend_cache_root(self):
        from exr_preview_proxy import EXRProxyLoader

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(app_cache, "CACHE_ROOT", Path(directory) / ".cache"):
            loader = EXRProxyLoader()
            self.assertEqual(loader.directory, Path(directory) / ".cache" / "exr-previews-v1")
            self.assertTrue(loader.directory.is_dir())
            loader.deleteLater()
