from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QDialog

import widgets
from matchmove_helpers import SequenceInfo


class FakeFolders:
    def __init__(self):
        self.opened_locations: list[Path] = []

    def latest_nk(self, folder):
        return None

    def latest_render_info(self, folder):
        return None

    def openFileLocation(self, folder):
        self.opened_locations.append(Path(folder))

    def open_file(self, path):
        return None


class FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def fire(self):
        for callback in list(self._callbacks):
            callback()


class FakeAction:
    def __init__(self, text: str):
        self.text = text
        self.triggered = FakeSignal()


class FakeMenu:
    instances: list["FakeMenu"] = []

    def __init__(self, parent=None):
        self.parent = parent
        self.actions: list[FakeAction] = []
        self.submenus: list[tuple[str, FakeMenu]] = []
        self.exec_pos = None
        FakeMenu.instances.append(self)

    def addAction(self, text: str):
        action = FakeAction(text)
        self.actions.append(action)
        return action

    def addMenu(self, text: str):
        menu = FakeMenu(self.parent)
        self.submenus.append((text, menu))
        return menu

    def exec(self, pos):
        self.exec_pos = pos


class AcceptedMatchmoveDialog:
    camera_preset_name = "Alexa 35"
    focal_length_mm = 50.0
    fps = 24.0

    def __init__(self, sequence_folder: str, parent=None):
        self.sequence_folder = sequence_folder

    def exec(self):
        return QDialog.DialogCode.Accepted


class ShotCardMatchmoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_card(self, base_path: str):
        data = {
            "id": 7,
            "title": "sho010",
            "notes": "",
            "base_path": base_path,
            "tasks": [],
            "colourspace": "",
            "matchmove_path": None,
        }
        fake_folders = FakeFolders()
        patches = [
            mock.patch.object(widgets, "HAS_MULTIMEDIA", False),
            mock.patch.object(widgets.http_help, "DjangoAPI", return_value=mock.Mock()),
            mock.patch.object(widgets.filesIO, "Folders", return_value=fake_folders),
            mock.patch.object(widgets.ShotCard, "_setup_nk_polling", lambda self: None),
            mock.patch.object(widgets.ShotCard, "_setup_render_polling", lambda self: None),
            mock.patch.object(widgets.ShotCard, "_setup_preview_polling", lambda self: None),
            mock.patch.object(widgets.ImageLoader, "instance", return_value=mock.Mock()),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        card = widgets.ShotCard(data)
        self.addCleanup(card.close)
        self.addCleanup(card.deleteLater)
        return card, fake_folders

    def test_open_shot_assets_uses_shot_assets_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, fake_folders = self._make_card(str(shot_root))

            card._open_shot_assets()

            self.assertEqual(fake_folders.opened_locations[-1], shot_root / "Shot_Assets")
            self.assertTrue((shot_root / "Shot_Assets").is_dir())

    def test_assets_menu_opens_latest_matchmove_when_project_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                mock.patch.object(widgets, "find_latest_matchmove_project", return_value=shot_root / "Shot_Assets" / "matchmove" / "work" / "sho010_matchmove_v003.3de"), \
                mock.patch.object(card, "_open_matchmove_project") as open_project:
                shown = card._show_assets_matchmove_menu(QPoint(10, 20))
                self.assertTrue(shown)
                self.assertEqual(FakeMenu.instances[0].actions[0].text, "Open sho010_matchmove_v003.3de")
                FakeMenu.instances[0].actions[0].triggered.fire()
                open_project.assert_called_once()

    def test_assets_menu_lists_multiple_precomp_sequences_under_create_submenu(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            seq_a = shot_root / "renders" / "precomp" / "plateA"
            seq_b = shot_root / "renders" / "precomp" / "plateB"
            seq_a.mkdir(parents=True)
            seq_b.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            sequence_infos = [
                SimpleNamespace(folder_path=str(seq_a)),
                SimpleNamespace(folder_path=str(seq_b)),
            ]

            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                mock.patch.object(widgets, "find_latest_matchmove_project", return_value=None), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=sequence_infos):
                shown = card._show_assets_matchmove_menu(QPoint(1, 2))

            self.assertTrue(shown)
            self.assertEqual(FakeMenu.instances[0].submenus[0][0], "Create Matchmove")
            submenu = FakeMenu.instances[0].submenus[0][1]
            self.assertEqual([action.text for action in submenu.actions], ["plateA", "plateB"])

    def test_create_matchmove_project_creates_work_and_export_and_patches_shot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            sequence_info = SequenceInfo(
                folder_path=str(shot_root / "renders" / "precomp" / "plateA"),
                example_file=str(shot_root / "renders" / "precomp" / "plateA" / "plateA.1001.exr"),
                sequence_path_pattern=str(shot_root / "renders" / "precomp" / "plateA" / "plateA.####.exr"),
                display_pattern="plateA.####.exr",
                prefix="plateA.",
                suffix=".exr",
                padding=4,
                first_frame=1001,
                last_frame=1010,
                frames=tuple(range(1001, 1011)),
                width=6048,
                height=4032,
                header_pixel_aspect=1.0,
            )

            fake_headless_result = SimpleNamespace(
                runtime_script_path="/tmp/runtime.py",
                status_path="/tmp/status.json",
            )
            updated_data = dict(card.data)
            updated_data["matchmove_path"] = str(shot_root / "Shot_Assets" / "matchmove")

            with mock.patch.object(widgets, "SingleCameraMatchmoveDialog", AcceptedMatchmoveDialog), \
                mock.patch.object(widgets, "run_headless_3de", return_value=fake_headless_result) as run_headless, \
                mock.patch.object(widgets, "cleanup_headless_artifacts") as cleanup, \
                mock.patch.object(widgets, "open_3de_project", return_value=["run_3DE4", "-open", "dummy"]) as open_project, \
                mock.patch.object(widgets.QMessageBox, "information"), \
                mock.patch.object(card._api, "update_shot", return_value=updated_data) as update_shot:
                card._create_matchmove_project(sequence_info)

            matchmove_dir = shot_root / "Shot_Assets" / "matchmove"
            self.assertTrue((matchmove_dir / "work").is_dir())
            self.assertTrue((matchmove_dir / "export").is_dir())
            update_shot.assert_called_once_with(card._shot_id, matchmove_path=str(matchmove_dir))
            run_headless.assert_called_once()
            open_project.assert_called_once()
            cleanup.assert_called_once_with(fake_headless_result)


if __name__ == "__main__":
    unittest.main()
