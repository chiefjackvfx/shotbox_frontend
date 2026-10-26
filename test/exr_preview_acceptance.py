"""Opt-in preview lifecycle check using real EXRs without changing source files.

Run with either frontend interpreter and --folder /path/to/an/exr/sequence.
Use QT_QPA_PLATFORM=offscreen for headless checks, or an isolated X display to
exercise native submenu hovering. Reports and proxy files stay in .cache/temp.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))


def check(folder, rounds=12):
    if rounds < 6:
        raise ValueError("Use at least six rounds to include cache-clearing and reopening checks")
    import av
    import OpenEXR
    import numpy as np
    from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt, qVersion, PYQT_VERSION_STR
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu
    import app_cache
    from exr_thumbnails import EXRMenuPreview, EXRThumbnailLoader, find_exr_preview_frames

    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    owner = QMainWindow()
    owner.resize(900, 600)
    owner.show()
    owner.activateWindow()
    QTest.qWait(150)
    pointer_display = None
    if application.platformName() == "xcb":
        # Route genuine X11 motion through popup grabs. QTest.mouseMove(widget)
        # can target a parent that no longer owns the active popup's mouse grab.
        import ctypes
        x11 = ctypes.CDLL("libX11.so.6")
        xtest = ctypes.CDLL("libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtest.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_ulong]
        xtest.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                             ctypes.c_int, ctypes.c_ulong]
        pointer_display = x11.XOpenDisplay(None)
        if not pointer_display:
            raise RuntimeError("Could not connect the acceptance test to its X display")

    def move_pointer(point):
        xtest.XTestFakeMotionEvent(pointer_display, -1, point.x(), point.y(), 0)
        x11.XFlush(pointer_display)
        QTest.qWait(20)

    frames = find_exr_preview_frames(folder)
    if not frames:
        raise ValueError(f"No numbered EXRs found in {folder}")

    def wait_until(predicate, description):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        if not predicate():
            diagnostic = {"menu_visible": menu.isVisible(),
                          "active_action": menu.activeAction().text() if menu.activeAction() else None,
                          "submenus_visible": [child.isVisible() for child in submenus],
                          "owner_active": owner.isActiveWindow()}
            print("PREVIEW_TIMEOUT " + json.dumps(diagnostic), flush=True)
            menu.grab().save(str(directory / "failure-menu.png"))
            raise AssertionError(description)

    with app_cache.temporary_cache_lease():
        directory = Path(tempfile.mkdtemp(prefix="exr-preview-acceptance-", dir=app_cache.cache_directory("temp")))
        print("EXR_PREVIEW_ACCEPTANCE " + str(directory), flush=True)
        loader = EXRThumbnailLoader(proxy_cache_dir=directory / "proxies")
        previous = EXRThumbnailLoader._instance
        EXRThumbnailLoader._instance = loader
        shown_frames = []

        def build_menu():
            parent = QMenu(owner)
            children, panels = [], []
            for name, source in (("First folder", frames), ("Second folder", tuple(reversed(frames)))):
                child = parent.addMenu(name)
                child.addAction("Open Folder")
                panel = EXRMenuPreview(child, source)
                panel.frame_changed.connect(shown_frames.append)
                children.append(child)
                panels.append(panel)
            return parent, children, panels

        menu, submenus, previews = build_menu()
        owner.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        owner.customContextMenuRequested.connect(lambda pos: menu.popup(owner.mapToGlobal(pos)))
        started = time.monotonic()
        report = {"python": sys.version, "qt": qVersion(), "pyqt": PYQT_VERSION_STR,
                  "av": av.__version__, "openexr": OpenEXR.__version__, "numpy": np.__version__,
                  "platform": application.platformName(), "source_count": len(frames),
                  "source": str(folder), "rounds": rounds}

        def hover(index):
            if not menu.isVisible():
                menu.setActiveAction(None)
                if pointer_display:
                    move_pointer(owner.mapToGlobal(QPoint(80, 80)))
                    xtest.XTestFakeButtonEvent(pointer_display, 3, 1, 0)
                    xtest.XTestFakeButtonEvent(pointer_display, 3, 0, 0)
                    x11.XFlush(pointer_display)
                else:
                    menu.popup(QPoint(100, 100))
                QTest.qWait(30)
                wait_until(menu.isVisible, "The right-click menu did not open")
            if pointer_display:
                move_pointer(menu.mapToGlobal(menu.actionGeometry(menu.actions()[index]).center()))
            else:
                # Offscreen Qt has no native input grabs; test popup lifecycle.
                for child in submenus:
                    if child is not submenus[index]:
                        child.hide()
                menu.setActiveAction(menu.actions()[index])
                wait_until(previews[index].isVisible, "Folder hover did not reveal its preview")
                submenus[index].popup(menu.mapToGlobal(QPoint(menu.width(), 0)))
            wait_until(submenus[index].isVisible, "The hovered folder submenu did not open")
            wait_until(lambda: previews[index].isVisible() and not previews[index].pixmap().isNull(),
                       "The current folder never showed a still")

        try:
            for index in range(rounds):
                current = index % 2
                hover(current)
                if index == 0:
                    report["first_still_seconds"] = round(time.monotonic() - started, 3)
                    previews[current].grab().save(str(directory / "first-still.png"))
                if index % 4 == 3:
                    menu.close()
                    QTest.qWait(20)
                    assert all(not preview.isVisible() for preview in previews)
                    menu.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    menu, submenus, previews = build_menu()
                    hover(current)
                if index == 5:
                    loader.begin_cache_clear()
                    loader._pool.waitForDone(10000)
                    loader._proxies.pool.waitForDone(10000)
                    application.processEvents()
                    shutil.rmtree(directory / "proxies", ignore_errors=True)
                    loader.finish_cache_clear()
                    wait_until(lambda: previews[current].isVisible() and bool(previews[current]._proxy_pixmaps),
                               "Preview did not recover after clearing its test cache")

            # Rapid movement should leave the last hovered folder usable.
            for index in range(30):
                if pointer_display:
                    move_pointer(menu.mapToGlobal(menu.actionGeometry(menu.actions()[index % 2]).center()))
                else:
                    hover(index % 2)
            hover(1)
            wait_until(lambda: bool(previews[1]._proxy_pixmaps), "Playback did not settle after rapid hovering")
            count = len(shown_frames)
            if len(previews[1]._proxy_pixmaps) > 1:
                wait_until(lambda: len(shown_frames) > count, "The playback timer stopped advancing")
            previews[1].grab().save(str(directory / "playback.png"))
            menu.hide()
            application.processEvents()
            assert all(not preview.isVisible() and not preview._playback_timer.isActive() for preview in previews)
            report.update(passed=True, elapsed_seconds=round(time.monotonic() - started, 3),
                          reopen=True, context_menu_recreated=True,
                          cache_clear=True, rapid_hover=True, playback=True)
        finally:
            menu.hide()
            loader.begin_cache_clear()
            loader._pool.waitForDone(10000)
            loader._proxies.pool.waitForDone(10000)
            application.processEvents()
            EXRThumbnailLoader._instance = previous
            menu.deleteLater()
            loader.deleteLater()
            owner.close()
            if pointer_display:
                x11.XCloseDisplay(pointer_display)
        (directory / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=12)
    arguments = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    check(arguments.folder, arguments.rounds)
