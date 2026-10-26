from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import OpenEXR
from PyQt6 import sip
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QPoint, QThreadPool
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMenu

import exr_thumbnails
import exr_preview_proxy


class EXRThumbnailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "plate.1001.exr"
        self.path.touch()
        self.loader = exr_thumbnails.EXRThumbnailLoader(
            max_items=2, proxy_cache_dir=Path(self.temporary.name) / "proxy-cache",
        )

    def tearDown(self):
        self.loader._proxies.pool.waitForDone(5000)
        self.loader._pool.waitForDone(5000)
        self.app.processEvents()
        self.loader.deleteLater()
        self.temporary.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "Thumbnail loading did not settle")

    def image(self):
        image = QImage(640, 320, QImage.Format.Format_RGB888)
        image.fill(0x3388FF)
        return image

    def test_decodes_real_exr_with_display_gamma_and_preserves_aspect(self):
        pixels = np.full((80, 160), 0.25, dtype=np.float32)
        # Sequence detection also accepts text after the frame number.
        path = self.path.with_name("plate.1001_beauty.EXR")
        OpenEXR.File({}, {channel: pixels.copy() for channel in "RGB"}).write(str(path))
        image = exr_thumbnails.read_thumbnail(path)
        self.assertEqual((image.width(), image.height()), (256, 128))
        self.assertAlmostEqual(image.pixelColor(0, 0).red(), 137, delta=1)

    def test_unsupported_fast_decode_falls_back_to_native_exr_reader(self):
        pixels = np.full((80, 160), 0.25, dtype=np.float32)
        OpenEXR.File({}, {channel: pixels.copy() for channel in "RGB"}).write(str(self.path))
        with mock.patch("av.open", side_effect=RuntimeError("unsupported")):
            image = exr_thumbnails.read_thumbnail(self.path)
        self.assertEqual((image.width(), image.height()), (256, 128))
        self.assertAlmostEqual(image.pixelColor(0, 0).red(), 137, delta=1)

    def test_disk_proxy_survives_reloads_and_invalidates_changed_frames(self):
        directory = Path(self.temporary.name) / "proxy-cache"
        with mock.patch.object(exr_thumbnails, "read_thumbnail", return_value=self.image()) as read:
            first = exr_preview_proxy.cached_proxy_frame(self.path, directory)
            second = exr_preview_proxy.cached_proxy_frame(self.path, directory)
            self.assertFalse(first.isNull())
            self.assertFalse(second.isNull())
            read.assert_called_once()
            self.path.write_bytes(b"changed")
            exr_preview_proxy.cached_proxy_frame(self.path, directory)
            self.assertEqual(read.call_count, 2)
            cached_files = list(directory.rglob("*.jpg"))
            self.assertEqual(len(cached_files), 1)
            self.assertRegex(cached_files[0].name, r"^plate\.1001_[0-9a-f]{16}\.jpg$")
            self.assertEqual(cached_files[0].parent, directory / "Other")
            metadata = json.loads(QImage(str(cached_files[0])).text("shotbox-source"))
            self.assertEqual(metadata["size"], len(b"changed"))

    def test_proxy_paths_use_only_job_folders_and_unique_names_across_platforms(self):
        directory = Path(self.temporary.name) / "proxy-cache"
        targets = []
        for root in ("/Volumes/projects/PROJECTS", "/mnt/projects/PROJECTS", "Z:/PROJECTS"):
            source = root + "/C2255_Bamboo_HR/VFX/BAMBOO_HR/bam050/renders/precomp/bam050_DN_v001/bam050_DN_v001_1001.exr"
            target = exr_preview_proxy.proxy_frame_path(source, directory)
            self.assertEqual(target.parent, directory / "C2255_Bamboo_HR")
            self.assertRegex(target.name, r"^bam050_DN_v001_1001_[0-9a-f]{16}\.jpg$")
            targets.append(target)
        windows_source = r"Z:\PROJECTS\C2255_Bamboo_HR\VFX\BAMBOO_HR\bam050\renders\precomp\bam050_DN_v001\bam050_DN_v001_1001.exr"
        targets.append(exr_preview_proxy.proxy_frame_path(windows_source, directory))
        self.assertEqual(len(set(targets)), 1)

    def test_matching_frame_names_from_jobs_shots_and_sequences_stay_separate(self):
        directory = Path(self.temporary.name) / "proxy-cache"
        targets = set()
        for job, timeline, shot, sequence in (("JobA", "EditA", "sh010", "plate"),
                                            ("JobB", "EditA", "sh010", "plate"),
                                            ("JobA", "EditB", "sh010", "plate"),
                                            ("JobA", "EditA", "sh020", "plate"),
                                            ("JobA", "EditA", "sh010", "other_plate")):
            source = Path(self.temporary.name) / "PROJECTS" / job / "VFX" / timeline / shot / "renders" / "precomp" / sequence / "plate.1001.exr"
            source.parent.mkdir(parents=True)
            source.touch()
            with mock.patch.object(exr_thumbnails, "read_thumbnail", return_value=self.image()) as read:
                exr_preview_proxy.cached_proxy_frame(source, directory)
                exr_preview_proxy.cached_proxy_frame(source, directory)
                read.assert_called_once()
            target = exr_preview_proxy.proxy_frame_path(source, directory)
            self.assertTrue(target.is_file())
            self.assertEqual(target.parent, directory / job)
            targets.add(target)
        self.assertEqual(len(targets), 5)

    def test_nested_proxy_moves_into_job_folder_without_decoding_exr(self):
        directory = Path(self.temporary.name) / "proxy-cache"
        nested = exr_preview_proxy._nested_proxy_frame_path(self.path, directory)
        with mock.patch.object(exr_preview_proxy, "proxy_frame_path", return_value=nested), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", return_value=self.image()):
            exr_preview_proxy.cached_proxy_frame(self.path, directory)
        self.assertTrue(nested.exists())
        with mock.patch.object(exr_thumbnails, "read_thumbnail") as decode:
            image = exr_preview_proxy.cached_proxy_frame(self.path, directory)
            self.assertFalse(image.isNull())
            target = exr_preview_proxy.proxy_frame_path(self.path, directory)
            self.assertTrue(target.exists())
            self.assertFalse(nested.exists())
            self.assertEqual(list(target.parent.iterdir()), [target])
            decode.assert_not_called()

    def test_existing_hashed_proxy_migrates_without_decoding_exr(self):
        directory = Path(self.temporary.name) / "proxy-cache"
        directory.mkdir()
        stat = self.path.stat()
        signature = f"{self.path}\0{stat.st_mtime_ns}\0{stat.st_size}"
        legacy = directory / (hashlib.sha256(signature.encode()).hexdigest() + ".jpg")
        self.image().save(str(legacy), "JPG", 50)
        with mock.patch.object(exr_thumbnails, "read_thumbnail") as read:
            image = exr_preview_proxy.cached_proxy_frame(self.path, directory)
            self.assertFalse(image.isNull())
            target = exr_preview_proxy.proxy_frame_path(self.path, directory)
            self.assertTrue(target.exists())
            self.assertFalse(legacy.exists())
            exr_preview_proxy.cached_proxy_frame(self.path, directory)
            read.assert_not_called()

    def test_cache_clear_waits_for_cancelled_proxy_writes_before_deleting(self):
        import app_cache
        import settings

        root = Path(self.temporary.name) / "local-cache"
        self.loader._proxies.directory = root / "exr-previews-v1"
        release, started = threading.Event(), threading.Event()
        image = self.image()

        def decode(path):
            started.set()
            release.wait(3)
            return image

        label = QLabel()
        ready = mock.Mock()
        self.loader.proxy_ready.connect(ready)
        results = []
        with mock.patch.object(app_cache, "CACHE_ROOT", root), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=decode):
            try:
                self.loader.prepare((str(self.path),), label)
                self.assertTrue(started.wait(1))
                self.loader.begin_cache_clear()
                task = settings._CacheClearTask(self.loader)
                task.signals.finished.connect(results.append)
                QThreadPool.globalInstance().start(task)
                QTest.qWait(30)
                self.assertFalse(results)
            finally:
                release.set()
            self.wait_for(lambda: bool(results))
            self.loader.finish_cache_clear()
            self.assertEqual(results[0]["errors"], [])
            self.assertEqual([path for path in root.iterdir() if path.name != ".lease.lock"], [])
            self.assertFalse(self.loader._proxies.suspended)
            ready.assert_not_called()
            self.assertTrue(label.pixmap().isNull())

    def test_unwritable_proxy_cache_still_returns_image(self):
        directory = Path(self.temporary.name) / "blocked-cache"
        directory.touch()
        with mock.patch.object(exr_thumbnails, "read_thumbnail", return_value=self.image()):
            image = exr_preview_proxy.cached_proxy_frame(self.path, directory)
        self.assertFalse(image.isNull())

    def test_disk_proxy_cache_removes_oldest_files_to_stay_bounded(self):
        directory = Path(self.temporary.name) / "proxy-cache"
        directory.mkdir()
        paths = [directory / f"Job{index}" / "shot" / f"{index}.jpg" for index in range(3)]
        for index, path in enumerate(paths):
            path.parent.mkdir(parents=True)
            path.write_bytes(b"0123456789")
            os.utime(path, ns=(index + 1, index + 1))
        exr_preview_proxy.prune_proxy_cache(directory, max_bytes=15)
        self.assertEqual(list(directory.rglob("*.jpg")), paths[-1:])

    def test_long_sequence_proxy_samples_frames_and_preserves_source_duration(self):
        frames = tuple(f"plate.{index}.exr" for index in range(1001))
        task = exr_preview_proxy._ProxyTask(frames, Path(self.temporary.name))
        results = []
        task.signals.finished.connect(lambda worker, result: results.append(result))
        with mock.patch.object(exr_preview_proxy, "cached_proxy_frame", return_value=self.image()) as read:
            task.run()
        self.assertEqual(results[0]["source_count"], 1001)
        self.assertEqual(results[0]["indices"][:3], (0, 4, 9))
        self.assertEqual(len(results[0]["images"]), 209)
        self.assertEqual(read.call_count, 209)

    def test_playback_uses_elapsed_time_and_skips_frames_after_delays(self):
        menu = QMenu()
        menu.addAction("Open Folder")
        preview = exr_thumbnails.EXRMenuPreview(menu, tuple(str(index) for index in range(6)))
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance", return_value=mock.Mock()):
            try:
                menu.popup(QPoint(100, 100))
                self.wait_for(preview.isVisible)
                preview._on_proxy_ready(preview, {"images": [self.image()] * 3, "indices": (0, 2, 4)})
                self.assertEqual(preview._playback_timer.interval(), 100)
                preview._playback_timer.stop()
                preview._playback_elapsed = mock.Mock()
                preview._playback_elapsed.elapsed.return_value = 167
                preview._next_frame()
                self.assertEqual(preview._frame_index, 4)
                preview._playback_elapsed.elapsed.return_value = 250
                preview._next_frame()
                self.assertEqual(preview._frame_index, 0)
            finally:
                menu.hide()

    def test_deduplicates_background_reads_and_updates_both_previews(self):
        release = threading.Event()
        started = threading.Event()
        threads = []
        image = self.image()

        def decode(path):
            threads.append(threading.get_ident())
            started.set()
            release.wait(3)
            return image

        labels = [QLabel("one"), QLabel("two")]
        with mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=decode) as read:
            try:
                for label in labels:
                    self.loader.load(self.path, label)
                self.assertTrue(started.wait(1))
                self.assertTrue(all(label.pixmap().isNull() for label in labels))
                self.assertNotEqual(threads[0], threading.get_ident())
            finally:
                release.set()
            self.wait_for(lambda: all(not label.pixmap().isNull() for label in labels))
            cached = QLabel("cached")
            self.loader.load(self.path, cached)
            self.assertFalse(cached.pixmap().isNull())
            self.assertEqual(cached.pixmap().deviceIndependentSize().width(), 320)
            read.assert_called_once()

    def test_reloads_when_frame_changes_and_limits_cache(self):
        labels = []
        with mock.patch.object(exr_thumbnails, "read_thumbnail", return_value=self.image()) as read:
            for index in range(3):
                self.path.write_bytes(b"x" * (index + 1))
                label = QLabel(str(index))
                labels.append(label)
                self.loader.load(self.path, label)
                self.wait_for(lambda: not label.pixmap().isNull())
            self.assertEqual(read.call_count, 3)
            self.assertEqual(len(self.loader._cache), 2)

    def test_deleted_preview_labels_are_safe_during_loading(self):
        release = threading.Event()
        image = self.image()

        def decode(path):
            release.wait(3)
            return image

        label = QLabel("deleted")
        with mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=decode):
            try:
                self.loader.load(self.path, label)
                sip.delete(label)
            finally:
                release.set()
            self.wait_for(lambda: not self.loader._pending)
        self.assertEqual(len(self.loader._cache), 1)

    def test_decode_failure_shows_unavailable_and_caches_failure(self):
        label = QLabel("Loading preview…")
        with mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=RuntimeError("bad EXR")) as read:
            self.loader.load(self.path, label)
            self.wait_for(lambda: not self.loader._pending)
            self.loader.load(self.path, label)
            read.assert_called_once()
        self.assertTrue(label.pixmap().isNull())
        self.assertEqual(label.text(), "Preview unavailable")

    def test_missing_frame_does_not_schedule_work(self):
        self.path.unlink()
        label = QLabel("missing")
        self.loader.load(self.path, label)
        self.assertFalse(self.loader._pending)
        self.assertTrue(label.pixmap().isNull())
        self.assertEqual(label.text(), "Preview unavailable")

    def test_preview_prefers_folder_version_over_larger_mixed_sequence(self):
        folder = Path(self.temporary.name) / "bam050_DN_v001"
        folder.mkdir()
        for version, frames in ((1, (1001, 1003)), (2, (1001, 1002, 1003))):
            for frame in frames:
                (folder / f"bam050_DN_v{version:03d}_{frame}.exr").touch()
        self.assertEqual(
            exr_thumbnails.find_exr_preview_frame(folder),
            str(folder / "bam050_DN_v001_1001.exr"),
        )
        self.assertEqual(
            exr_thumbnails.find_exr_preview_frames(folder),
            tuple(str(folder / f"bam050_DN_v001_{frame}.exr") for frame in (1001, 1003)),
        )

    def test_preview_uses_largest_group_when_folder_name_does_not_match(self):
        folder = Path(self.temporary.name)
        self.path.unlink()
        for name in ("small.0001.exr", "small.0002.exr", "large.0002.exr", "large.0003.EXR"):
            (folder / name).touch()
        # EXR extension case alone must not split an otherwise matching group.
        self.assertEqual(exr_thumbnails.find_exr_preview_frame(folder), str(folder / "large.0002.exr"))

    def test_preview_ignores_mac_sidecars_non_exrs_and_directories(self):
        folder = Path(self.temporary.name)
        self.path.unlink()
        for name in ("._plate.0001.exr", "preview.0001.mp4", "still.exr"):
            (folder / name).touch()
        (folder / "directory.0001.exr").mkdir()
        self.assertIsNone(exr_thumbnails.find_exr_preview_frame(folder))
        (folder / "plate.1001.EXR").touch()
        self.assertEqual(exr_thumbnails.find_exr_preview_frame(folder), str(folder / "plate.1001.EXR"))

    def test_folder_submenu_shows_separate_preview_panel(self):
        from test.test_shotcard_matchmove import ShotCardMatchmoveTests
        import widgets

        fixture = ShotCardMatchmoveTests()
        self.addCleanup(fixture.doCleanups)
        folder = Path(self.temporary.name) / "renders" / "precomp" / "plate"
        folder.mkdir(parents=True)
        pixels = np.full((80, 160), 0.25, dtype=np.float32)
        frame = folder / "plate.1001.exr"
        OpenEXR.File({}, {channel: pixels.copy() for channel in "RGB"}).write(str(frame))
        # Preview browsing accepts gaps and other versions in the same folder.
        (folder / "plate.1003.exr").write_bytes(frame.read_bytes())
        for name in ("plate_v002.1001.exr", "plate_v002.1002.exr"):
            (folder / name).touch()
        (folder.parent / "empty").mkdir()
        card, _ = fixture._make_card(self.temporary.name)
        menu = QMenu()
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance", return_value=self.loader), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", wraps=exr_thumbnails.read_thumbnail) as read:
            card._populate_precomp_menu(menu)
            self.assertTrue(all(action.icon().isNull() for action in menu.actions()))
            folder_menu = next(action.menu() for action in menu.actions() if action.text() == "plate")
            actions = [action for action in folder_menu.actions() if not action.isSeparator()]
            self.assertEqual([a.text() for a in actions], ["Open Folder", "Copy Path", "Roto"])
            self.assertTrue(all(action.menu() is None for action in actions))
            empty_menu = next(action.menu() for action in menu.actions() if action.text() == "empty")
            self.assertEqual([a.text() for a in empty_menu.actions()], ["Open Folder", "Copy Path"])
            read.assert_not_called()
            label = folder_menu.findChild(exr_thumbnails.EXRMenuPreview)
            self.assertEqual(label._path, str(frame))
            self.assertFalse(label.isVisible())
            folder_menu.popup(QPoint(100, 100))
            self.wait_for(lambda: bool(label._proxy_pixmaps))
            self.assertTrue(label.isVisible())
            self.assertFalse(label.geometry().intersects(folder_menu.geometry()))
            self.assertEqual(label.size().width(), 336)
            self.assertEqual(label.pixmap().deviceIndependentSize().width(), 320)
            folder_menu.hide()
            self.assertFalse(label.isVisible())
            folder_menu.popup(QPoint(100, 100))
            self.wait_for(lambda: label.isVisible() and bool(label._proxy_pixmaps))
            self.assertEqual(read.call_count, 1)
            folder_menu.hide()

    def test_preview_uses_left_side_when_right_side_has_no_space(self):
        menu = QMenu()
        menu.addAction("Open Folder")
        preview = exr_thumbnails.EXRMenuPreview(menu, str(self.path))
        available = menu.screen().availableGeometry()
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance", return_value=self.loader), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", return_value=self.image()):
            menu.popup(QPoint(available.right() - menu.sizeHint().width(), available.top() + 20))
            self.wait_for(preview.isVisible)
            self.assertLess(preview.geometry().right(), menu.geometry().left())
            self.assertTrue(available.contains(preview.geometry()))
            menu.hide()
            self.assertFalse(preview.isVisible())

    def test_preview_stays_hidden_when_menu_closes_before_deferred_show(self):
        menu = QMenu()
        menu.addAction("Open Folder")
        preview = exr_thumbnails.EXRMenuPreview(menu, str(self.path))
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance") as loader:
            menu.popup(QPoint(100, 100))
            menu.hide()
            self.app.processEvents()
            self.assertFalse(preview.isVisible())
            loader.assert_not_called()

    def test_plays_real_exr_frames_in_order_loops_and_stops_when_closed(self):
        frames = []
        for index, color in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1)) * 2, start=1001):
            path = self.path.with_name(f"plate.{index}.exr")
            OpenEXR.File({}, {c: np.full((16, 32), value, np.float32)
                             for c, value in zip("RGB", color)}).write(str(path))
            frames.append(str(path))
        menu = QMenu()
        menu.addAction("Open Folder")
        preview = exr_thumbnails.EXRMenuPreview(menu, frames)
        colors = []
        preview.frame_changed.connect(
            lambda index: colors.append(int(np.argmax(preview.pixmap().toImage().pixelColor(0, 0).getRgb()[:3])))
        )
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance", return_value=self.loader), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", wraps=exr_thumbnails.read_thumbnail) as read:
            try:
                menu.popup(QPoint(100, 100))
                self.wait_for(lambda: len(colors) >= 4)
                self.assertFalse(preview._loading)
                self.assertFalse(preview._spinner_timer.isActive())
                self.assertEqual(colors[:4], [0, 2, 1, 0])
                self.assertEqual(read.call_count, 3)
            finally:
                menu.hide()
            frames_shown = len(colors)
            QTest.qWait(100)
            self.assertEqual(len(colors), frames_shown)
            self.assertFalse(preview._playback_timer.isActive())

    def test_playback_waits_for_decode_and_ignores_completion_after_closing(self):
        release = threading.Event()
        started = threading.Event()
        frames = [self.path.with_name(f"plate.{index}.exr") for index in range(1001, 1006)]
        for path in frames:
            path.touch()
        image = self.image()

        def decode(path):
            started.set()
            release.wait(3)
            return image

        menu = QMenu()
        menu.addAction("Open Folder")
        preview = exr_thumbnails.EXRMenuPreview(menu, tuple(str(path) for path in frames))
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance", return_value=self.loader), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=decode) as read:
            try:
                menu.popup(QPoint(100, 100))
                self.wait_for(started.is_set)
                self.assertTrue(preview._loading)
                self.assertTrue(preview._spinner_timer.isActive())
                QTest.qWait(120)
                self.assertEqual(read.call_count, 2)
                menu.hide()
            finally:
                release.set()
                menu.hide()
            self.wait_for(lambda: not self.loader._proxies._jobs)
            self.assertTrue(preview.pixmap().isNull())
            self.assertFalse(preview._loading)
            self.assertFalse(preview._spinner_timer.isActive())
            self.assertFalse(preview._playback_timer.isActive())
            self.assertEqual(read.call_count, 2)

    def test_late_frame_cannot_replace_a_newer_frame_request(self):
        release = threading.Event()
        second = self.path.with_name("plate.1002.exr")
        second.touch()
        red, blue = self.image(), self.image()
        red.fill(0xFF0000)
        blue.fill(0x0000FF)

        def decode(path):
            if path == str(self.path):
                release.wait(3)
                return red
            return blue

        label = QLabel()
        with mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=decode):
            try:
                self.loader.load(self.path, label)
                self.loader.load(second, label)
                self.wait_for(lambda: not label.pixmap().isNull())
                self.assertEqual(label.pixmap().toImage().pixelColor(0, 0).name(), "#0000ff")
            finally:
                release.set()
            self.wait_for(lambda: not self.loader._pending)
            self.assertEqual(label.pixmap().toImage().pixelColor(0, 0).name(), "#0000ff")

    def test_unreadable_sequence_stops_after_trying_each_frame(self):
        second = self.path.with_name("plate.1002.exr")
        second.touch()
        menu = QMenu()
        menu.addAction("Open Folder")
        preview = exr_thumbnails.EXRMenuPreview(menu, (str(self.path), str(second)))
        with mock.patch.object(exr_thumbnails.EXRThumbnailLoader, "instance", return_value=self.loader), \
             mock.patch.object(exr_thumbnails, "read_thumbnail", side_effect=RuntimeError("invalid")) as read:
            try:
                menu.popup(QPoint(100, 100))
                self.wait_for(lambda: preview.text() == "Preview unavailable")
                self.assertFalse(preview._loading)
                self.assertFalse(preview._spinner_timer.isActive())
                self.assertFalse(preview._playback_timer.isActive())
                self.assertEqual(preview.text(), "Preview unavailable")
                QTest.qWait(100)
                self.assertEqual(read.call_count, 1)
            finally:
                menu.hide()
