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
from PyQt6.QtWidgets import QApplication, QDialog, QToolButton

import widgets
from colourspace_defaults import COLOURSPACE_LIST
from matchmove_helpers import SequenceInfo


class FakeFolders:
    def __init__(self):
        self.opened_locations: list[Path] = []
        self.pushed_to_dvr: list[tuple[str, bool, str | None]] = []

    def latest_nk(self, folder):
        return None

    def latest_render_info(self, folder):
        return None

    def openFileLocation(self, folder):
        self.opened_locations.append(Path(folder))

    def open_file(self, path):
        return None

    def convert_path(self, path):
        return path

    def push2dvr(self, clip, matte=False, shot_name=None):
        self.pushed_to_dvr.append((clip, matte, shot_name))
        return True


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
        self.enabled = True
        self.checkable = False
        self.checked = False

    def setEnabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def setCheckable(self, checkable: bool):
        self.checkable = bool(checkable)

    def setChecked(self, checked: bool):
        self.checked = bool(checked)


class FakeMenu:
    instances: list["FakeMenu"] = []

    def __init__(self, parent=None):
        self.parent = parent
        self.actions: list[FakeAction] = []
        self.submenus: list[tuple[str, FakeMenu]] = []
        self.separator_count = 0
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

    def addSeparator(self):
        self.separator_count += 1

    def clear(self):
        self.actions.clear()
        self.submenus.clear()
        self.separator_count = 0

    def exec(self, pos):
        self.exec_pos = pos

    def deleteLater(self):
        self.deleted = True


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

    def _make_card(self, base_path: str, data_overrides: dict | None = None):
        data = {
            "id": 7,
            "title": "sho010",
            "notes": "",
            "base_path": base_path,
            "tasks": [],
            "colourspace": "",
            "matchmove_path": None,
        }
        if data_overrides:
            data.update(data_overrides)
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

    def _find_action(self, menu: FakeMenu, text: str) -> FakeAction:
        for action in menu.actions:
            if action.text == text:
                return action
        raise AssertionError(f"Action not found: {text}")

    def test_colourspace_context_menu_uses_shared_presets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, _fake_folders = self._make_card(
                tmpdir,
                data_overrides={"colourspace": COLOURSPACE_LIST[0]},
            )

            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu):
                card._on_colourspace_context_menu(QPoint(1, 2))

            menu = FakeMenu.instances[0]
            self.assertEqual(
                [action.text for action in menu.actions],
                list(COLOURSPACE_LIST),
            )
            self.assertTrue(menu.actions[0].checked)

    def test_open_shot_assets_uses_shot_assets_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, fake_folders = self._make_card(str(shot_root))

            card._open_shot_assets()

            self.assertEqual(fake_folders.opened_locations[-1], shot_root / "shot_assets")
            self.assertTrue((shot_root / "shot_assets").is_dir())

    def test_assets_control_opens_folder_with_delayed_popup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, fake_folders = self._make_card(str(shot_root))

            self.assertIsInstance(card.btn_open_assets, QToolButton)
            self.assertEqual(
                card.btn_open_assets.popupMode(),
                QToolButton.ToolButtonPopupMode.DelayedPopup,
            )
            card.btn_open_assets.click()
            self.assertEqual(fake_folders.opened_locations, [shot_root / "shot_assets"])

    def test_assets_menu_first_action_opens_assets_in_file_browser(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, fake_folders = self._make_card(str(shot_root))

            menu = FakeMenu()
            with mock.patch.object(widgets, "list_matchmove_projects", return_value=[]), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[]):
                card._populate_assets_menu(menu)

            self.assertEqual(menu.actions[0].text, "Open Assets in File Browser")
            self.assertEqual(menu.separator_count, 1)
            menu.actions[0].triggered.fire()
            self.assertEqual(fake_folders.opened_locations[-1], shot_root / "shot_assets")

    def test_push_to_dvr_uses_shot_title_for_resolve_bin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "SQ010_SH020"
            shot_root.mkdir(parents=True)
            render_path = shot_root / "renders" / "comp" / "legacy_v001.mov"
            render_path.parent.mkdir(parents=True)
            render_path.write_bytes(b"mov")
            card, fake_folders = self._make_card(
                str(shot_root),
                data_overrides={"title": "SQ010_SH020"},
            )
            card.btn_latest_render.setProperty("file_path", str(render_path))
            card.btn_latest_render.setProperty("render_type", "mov")
            card.btn_latest_render.setProperty("render_display", render_path.name)

            card._on_push_to_dvr_clicked()

            self.assertEqual(
                fake_folders.pushed_to_dvr,
                [(str(render_path), False, "SQ010_SH020")],
            )

    def test_render_button_green_tracks_latest_render_and_backend_conform_updates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, _ = self._make_card(tmpdir, {"last_conform": "sho010_v001.mov"})
            card.setStyleSheet((FRONTEND_DIR / "ui" / "dark_v01.qss").read_text())
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v001.mov"),
                                        render_display="sho010_v001.mov", render_type="mov")
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "true")
            self.assertEqual(card.btn_latest_render.palette().button().color().name(), "#2f4237")
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v002.mov"),
                                        render_display="sho010_v002.mov", render_type="mov")
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "false")
            self.assertNotEqual(card.btn_latest_render.palette().button().color().name(), "#2f4237")
            card.update_from_data(dict(card.data, last_conform="sho010_v002.mov"))
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "true")
            card._set_render_file_state()
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "false")
            self.assertEqual(card.label_last_conform.styleSheet(), "")

    def test_render_conform_comparison_supports_exr_labels_paths_and_compact_cards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cases = [
                ("sho010_v003.####.exr 1001-1040 (EXR)", "sho010_v003.####.exr 1001-1060"),
                (r"R:\show\renders\sho010_v003.mov", "sho010_v003.mov"),
                ("sho010_v003.mov (MOV)", "sho010_v003.mov"),
            ]
            for last_conform, display in cases:
                with self.subTest(last_conform=last_conform):
                    card, _ = self._make_card(tmpdir, {"last_conform": last_conform})
                    card._set_render_file_state(render_path=str(Path(tmpdir) / "render"), render_display=display)
                    for grid in (True, False):
                        card.set_v02_grid(grid)
                        for compact in (True, False):
                            card.set_compact_mode(compact)
                            card._refresh_last_conform_indicator()
                            self.assertEqual(card.btn_latest_render.property("is_conformed"), "true")

    def test_render_without_last_conform_never_shows_conformed_green(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, _ = self._make_card(tmpdir)
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v001.mov"),
                                        render_display="sho010_v001.mov")
            for value in (None, "", "None", "sho010_v002.mov"):
                card.update_from_data(dict(card.data, last_conform=value))
                self.assertEqual(card.btn_latest_render.property("is_conformed"), "false")

    def test_successful_dvr_push_updates_green_even_when_api_save_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, _ = self._make_card(tmpdir)
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v001.mov"),
                                        render_display="sho010_v001.mov", render_type="mov")
            with mock.patch.object(card._api, "update_shot", side_effect=RuntimeError("offline")):
                card.btn_latest_render.click()
            self.assertEqual(card.data["last_conform"], "sho010_v001.mov")
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "true")

    def test_compact_dvr_push_uses_filename_when_render_display_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, _ = self._make_card(tmpdir)
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v001.mov"), render_type="mov")
            card.set_compact_mode(True)
            self.assertEqual(card.btn_latest_render.text(), "Render")
            card.btn_latest_render.click()
            self.assertEqual(card.data["last_conform"], "sho010_v001.mov")
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "true")

    def test_failed_dvr_push_does_not_mark_render_conformed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, folders = self._make_card(tmpdir)
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v001.mov"),
                                        render_display="sho010_v001.mov", render_type="mov")
            with mock.patch.object(folders, "push2dvr", return_value=False), \
                    mock.patch.object(card._api, "update_shot") as update:
                card.btn_latest_render.click()
                update.assert_not_called()
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "false")

    def test_polled_new_render_clears_green_without_flashing_conformed_colour(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, folders = self._make_card(tmpdir, {"last_conform": "sho010_v001.mov"})
            card._set_render_file_state(render_path=str(Path(tmpdir) / "sho010_v001.mov"),
                                        render_display="sho010_v001.mov", render_type="mov")
            new_render = {"render_path": str(Path(tmpdir) / "sho010_v002.mov"),
                          "display_name": "sho010_v002.mov", "render_relpath": "sho010_v002.mov",
                          "type": "mov", "version": 2, "mtime": 123}
            with mock.patch.object(folders, "latest_render_info", return_value=new_render), \
                    mock.patch.object(card, "_flash_button_green") as flash:
                card._check_for_new_render()
                flash.assert_not_called()
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "false")
            card.update_from_data(dict(card.data, last_conform="sho010_v002.mov"))
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "true")
            with mock.patch.object(folders, "latest_render_info", return_value=None):
                card._check_for_new_render()
            self.assertEqual(card.btn_latest_render.property("is_conformed"), "false")

    def test_existing_project_uses_direct_3de_button_and_leaves_assets_menu_plain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            work = shot_root / "Shot_Assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            (work / "sho010_matchmove_v001.3de").touch()
            latest = work / "sho010_matchmove_v003.3de"
            latest.touch()
            card, _ = self._make_card(str(shot_root))
            self.assertFalse(card.btn_open_matchmove.isHidden())
            self.assertEqual(card.btn_open_matchmove.text(), latest.name.replace("_matchmove", ""))
            with mock.patch.object(widgets, "open_3de_project") as open_project:
                card.btn_open_matchmove.click()
                open_project.assert_called_once_with(str(latest))
            menu = FakeMenu()
            card._populate_assets_menu(menu)
            self.assertEqual([action.text for action in menu.actions],
                             ["Open Assets in File Browser", "No renders/precomp folder found"])
            self.assertFalse(menu.actions[1].enabled)
            self.assertEqual(menu.submenus, [])
            self.assertEqual(menu.separator_count, 1)
            self.assertEqual(card.btn_open_assets.text(), "Assets")
            self.assertIsNone(card.btn_open_assets.property("has_matchmove"))
            self.assertNotIn("3DE", card.btn_open_assets.toolTip())

    def test_assets_right_click_allows_creation_with_existing_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            project = work / "sho010_matchmove_v003.3de"
            project.write_text("existing project")
            sequence = SimpleNamespace(folder_path=str(Path(tmpdir) / "renders" / "precomp" / "plateA"))
            card, _ = self._make_card(tmpdir)
            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                    mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[sequence]), \
                    mock.patch.object(card, "_create_matchmove_project") as create:
                card.btn_open_assets.customContextMenuRequested.emit(QPoint(1, 2))
                menu = FakeMenu.instances[0]
                self.assertEqual(menu.submenus[0][0], "Create Matchmove")
                menu.submenus[0][1].submenus[0][1].actions[0].triggered.fire()
                create.assert_called_once_with(sequence)
            self.assertEqual(project.read_text(), "existing project")
            self.assertFalse(card.btn_open_matchmove.isHidden())
            self.assertNotIn(f"Open {project.name}", [action.text for action in menu.actions])

    def test_3de_chooser_selects_latest_per_name_and_keeps_standalone_projects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            work = shot_root / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            for name in ("A_matchmove_v001.3de", "A_matchmove_v002.3de", "B.3DE"):
                (work / name).touch()
            card, _ = self._make_card(str(shot_root))
            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                    mock.patch.object(widgets, "open_3de_project") as open_project:
                card.btn_open_matchmove.click()
                open_project.assert_not_called()
                menu = FakeMenu.instances[0]
                self.assertEqual([action.text for action in menu.actions],
                                 ["Open A_matchmove_v002.3de", "Open B.3DE"])
                menu.actions[0].triggered.fire()
                menu.actions[1].triggered.fire()
                self.assertEqual(open_project.call_args_list,
                                 [mock.call(str(work / "A_matchmove_v002.3de")),
                                  mock.call(str(work / "B.3DE"))])
            self.assertIn("A_matchmove_v002.3de", card.btn_open_matchmove.toolTip())
            self.assertNotIn("A_matchmove_v001.3de", card.btn_open_matchmove.toolTip())
            self.assertIn("B.3DE", card.btn_open_matchmove.toolTip())
            self.assertEqual(card.btn_open_matchmove.text(), "A_v002.3de (+1)")

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
                mock.patch.object(widgets, "list_matchmove_projects", return_value=[]), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=sequence_infos):
                shown = card._show_assets_matchmove_menu(QPoint(1, 2))

            self.assertTrue(shown)
            self.assertEqual(FakeMenu.instances[0].submenus[0][0], "Create Matchmove")
            submenu = FakeMenu.instances[0].submenus[0][1]
            self.assertEqual([label for label, _ in submenu.submenus], ["plateA", "plateB"])
            self.assertTrue(FakeMenu.instances[0].deleted)
            self.assertEqual([action.text for action in submenu.submenus[0][1].actions], ["Create Matchmove"])

    def test_unavailable_sequence_preview_does_not_block_matchmove_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card, _ = self._make_card(tmpdir)
            sequence = SimpleNamespace(folder_path=str(Path(tmpdir) / "plateA"))
            menu = FakeMenu()
            with mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[sequence]), \
                    mock.patch.object(widgets, "find_exr_preview_frames", side_effect=OSError("offline")), \
                    mock.patch.object(widgets, "EXRMenuPreview") as preview, \
                    mock.patch.object(card, "_create_matchmove_project") as create:
                card._populate_assets_menu(menu)
                menu.submenus[0][1].submenus[0][1].actions[0].triggered.fire()
                preview.assert_not_called()
                create.assert_called_once_with(sequence)

    def test_assets_menu_shows_disabled_message_when_no_valid_sequences_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            (shot_root / "renders" / "precomp").mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                mock.patch.object(widgets, "list_matchmove_projects", return_value=[]), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[]):
                shown = card._show_assets_matchmove_menu(QPoint(3, 4))

            self.assertTrue(shown)
            self.assertEqual(FakeMenu.instances[0].actions[1].text, "No valid EXR precomp folders found")

    def test_assets_menu_scans_converted_windows_project_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            windows_like_root = r"/Volumes/projects/PROJECTS/show/VFX/timeline_A/sho010"
            resolved_root = Path(tmpdir) / "PROJECTS" / "show" / "VFX" / "timeline_A" / "sho010"
            (resolved_root / "renders" / "precomp" / "plateA").mkdir(parents=True)
            card, fake_folders = self._make_card(windows_like_root)
            fake_folders.convert_path = mock.Mock(return_value=str(resolved_root))

            sequence_infos = [SimpleNamespace(folder_path=str(resolved_root / "renders" / "precomp" / "plateA"))]

            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                mock.patch.object(widgets, "list_matchmove_projects", return_value=[]), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=sequence_infos) as list_sequences:
                shown = card._show_assets_matchmove_menu(QPoint(6, 7))

            self.assertTrue(shown)
            fake_folders.convert_path.assert_any_call(windows_like_root)
            list_sequences.assert_called_once_with(str(resolved_root))

    def test_assets_dropdown_handler_catches_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            with mock.patch.object(card, "_populate_assets_menu", side_effect=RuntimeError("boom")), \
                mock.patch.object(widgets.QMessageBox, "critical") as critical:
                card._on_assets_menu_about_to_show()

            critical.assert_called_once()

    def test_matchmove_indicator_tracks_valid_project_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            work_dir = shot_root / "Shot_Assets" / "matchmove" / "work"
            work_dir.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            self.assertEqual(card.btn_open_assets.text(), "Assets")
            self.assertEqual(card.btn_open_matchmove.property("has_matchmove"), "false")
            self.assertTrue(card.btn_open_matchmove.isHidden())

            invalid_project = work_dir / "different_name.txt"
            invalid_project.write_text("")
            card.apply_file_state_snapshot(None)
            self.assertEqual(card.btn_open_assets.text(), "Assets")

            valid_project = work_dir / "sho010_matchmove_v003.3de"
            valid_project.write_text("")
            card.apply_file_state_snapshot(None)
            self.assertEqual(card.btn_open_assets.text(), "Assets")
            self.assertFalse(card.btn_open_matchmove.isHidden())
            self.assertEqual(card.btn_open_matchmove.property("has_matchmove"), "true")
            self.assertIn(valid_project.name, card.btn_open_matchmove.toolTip())
            self.assertIn(str(valid_project), card.btn_open_matchmove.toolTip())

            valid_project.unlink()
            card.apply_file_state_snapshot(None)
            self.assertEqual(card.btn_open_assets.text(), "Assets")
            self.assertEqual(card.btn_open_matchmove.property("has_matchmove"), "false")
            self.assertTrue(card.btn_open_matchmove.isHidden())

    def test_matchmove_indicator_uses_configured_matchmove_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            configured_dir = Path(tmpdir) / "shared" / "sho010_matchmove"
            work_dir = configured_dir / "work"
            shot_root.mkdir(parents=True)
            work_dir.mkdir(parents=True)
            project = work_dir / "sho010_matchmove_v004.3de"
            project.write_text("")

            card, _fake_folders = self._make_card(
                str(shot_root),
                data_overrides={"matchmove_path": str(configured_dir)},
            )

            self.assertEqual(card.btn_open_assets.text(), "Assets")
            self.assertFalse(card.btn_open_matchmove.isHidden())
            self.assertIn(project.name, card.btn_open_matchmove.toolTip())

    def test_3de_visibility_follows_data_updates_and_compact_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "VFX" / "edit" / "sho010"
            root.mkdir(parents=True)
            custom = Path(tmpdir) / "custom" / "work"
            custom.mkdir(parents=True)
            project = custom / "solve.3de"
            project.touch()
            card, _ = self._make_card(str(root))
            self.assertTrue(card.btn_open_matchmove.isHidden())
            data = dict(card.data, matchmove_path=str(custom.parent))
            card.update_from_data(data)
            self.assertFalse(card.btn_open_matchmove.isHidden())
            card.set_compact_mode(True)
            self.assertTrue(card.btn_open_matchmove.isHidden())
            card.apply_file_state_snapshot(None)
            self.assertTrue(card.btn_open_matchmove.isHidden())
            card.update_from_data(data)
            self.assertTrue(card.btn_open_matchmove.isHidden())
            card.set_compact_mode(False)
            self.assertFalse(card.btn_open_matchmove.isHidden())
            project.unlink()
            card.apply_file_state_snapshot(None)
            self.assertTrue(card.btn_open_matchmove.isHidden())
            card.set_compact_mode(True)
            card.set_compact_mode(False)
            self.assertTrue(card.btn_open_matchmove.isHidden())

    def test_3de_click_rescans_and_opens_newer_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            (work / "solve_matchmove_v001.3de").touch()
            card, _ = self._make_card(tmpdir)
            latest = work / "solve_matchmove_v002.3de"
            latest.touch()
            with mock.patch.object(widgets, "open_3de_project") as launch:
                card.btn_open_matchmove.click()
                launch.assert_called_once_with(str(latest))
                self.assertEqual(card.btn_open_matchmove.text(), latest.name.replace("_matchmove", ""))

    def test_3de_click_handles_deleted_files_and_launch_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            project = work / "solve.3de"
            project.touch()
            card, _ = self._make_card(tmpdir)
            with mock.patch.object(widgets, "open_3de_project", side_effect=RuntimeError("launch failed")) as launch, \
                    mock.patch.object(widgets.QMessageBox, "warning") as warning:
                card.btn_open_matchmove.click()
                self.assertIn("launch failed", warning.call_args.args[2])
                project.unlink()
                launch.reset_mock()
                card.btn_open_matchmove.click()
                launch.assert_not_called()
                self.assertTrue(card.btn_open_matchmove.isHidden())
                self.assertIn("No 3DE projects", warning.call_args.args[2])

    def test_matchmove_right_click_opens_work_and_export_folders(self):
        from PyQt6.QtCore import Qt
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "sho010"
            root.mkdir()
            for relative, configured in (("shot_assets/matchmove", False),
                                         ("Shot_Assets/matchmove", False),
                                         ("custom_matchmove", True)):
                with self.subTest(relative=relative):
                    directory = root / relative
                    work = directory / "work"
                    work.mkdir(parents=True)
                    export = directory / "export"
                    export.mkdir()
                    project = work / "solve_matchmove_v003.3de"
                    project.touch()
                    overrides = {"matchmove_path": str(directory)} if configured else None
                    card, folders = self._make_card(str(root), overrides)
                    self.assertEqual(card.btn_open_matchmove.text(), project.name.replace("_matchmove", ""))
                    self.assertEqual(card.btn_open_matchmove.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
                    FakeMenu.instances = []
                    with mock.patch.object(widgets, "QMenu", FakeMenu):
                        card.btn_open_matchmove.customContextMenuRequested.emit(QPoint(3, 4))
                        menu = FakeMenu.instances[0]
                        self.assertEqual(menu.exec_pos, card.btn_open_matchmove.mapToGlobal(QPoint(3, 4)))
                        self.assertEqual([action.text for action in menu.actions],
                                         ["Open Work in Explorer", "Open Export in Explorer"])
                        menu.actions[0].triggered.fire()
                        menu.actions[1].triggered.fire()
                    self.assertEqual(folders.opened_locations, [work, export])
                    project.unlink()
                    work.rmdir()
                    export.rmdir()
                    directory.rmdir()

    def test_matchmove_label_shortens_name_without_renaming_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            project = work / "sho010_MATCHMOVE_v003.3DE"
            project.touch()
            card, _ = self._make_card(tmpdir)
            self.assertEqual(card.btn_open_matchmove.text(), "sho010_v003.3DE")
            self.assertIn(project.name, card.btn_open_matchmove.toolTip())
            with mock.patch.object(widgets, "open_3de_project") as launch:
                card.btn_open_matchmove.click()
                launch.assert_called_once_with(str(project))
            self.assertTrue(project.is_file())

    def test_matchmove_folder_errors_do_not_create_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "sho010"
            work = root / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            project = work / "solve.3de"
            project.touch()
            card, folders = self._make_card(str(root))
            with mock.patch.object(widgets.QMessageBox, "warning") as warning:
                with mock.patch.object(folders, "openFileLocation", side_effect=OSError("Explorer failed")):
                    card._open_matchmove_folder()
                    self.assertIn("Explorer failed", warning.call_args.args[2])
                project.unlink()
                work.rmdir()
                card._open_matchmove_folder()
                self.assertIn("unavailable", warning.call_args.args[2])
                self.assertFalse(work.exists())
                self.assertEqual(folders.opened_locations, [])
                card._open_matchmove_folder("export")
                self.assertIn("export folder is unavailable", warning.call_args.args[2])
                self.assertFalse((work.parent / "export").exists())

    def test_long_matchmove_filename_fits_grid_button_with_full_tooltip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            project = work / ("long_solve_name_" * 7 + "matchmove_v003.3de")
            project.touch()
            card, _ = self._make_card(tmpdir)
            card.setStyleSheet((FRONTEND_DIR / "ui" / "dark_v01.qss").read_text())
            card.set_v02_grid(True)
            card.prepare_layout_width(400)
            card.resize(400, 500)
            card.show()
            self.app.processEvents()
            self.assertEqual(card.btn_open_matchmove.text(), project.name.replace("_matchmove", ""))
            self.assertIn(str(project), card.btn_open_matchmove.toolTip())
            self.assertLessEqual(card.btn_open_matchmove.width(), card.btn_open_matchmove.preferred_width_limit)
            self.assertEqual(card.btn_open_matchmove.height(), card.btn_open_assets.height())

    def test_3de_click_reports_discovery_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            (work / "solve.3de").touch()
            card, _ = self._make_card(tmpdir)
            with mock.patch.object(widgets, "list_matchmove_projects", side_effect=OSError("access denied")), \
                    mock.patch.object(widgets.QMessageBox, "warning") as warning:
                card.btn_open_matchmove.click()
                self.assertIn("access denied", warning.call_args.args[2])
                self.assertTrue(card.btn_open_matchmove.isHidden())

    def test_3de_chooser_rechecks_file_before_launch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            first = work / "first.3de"
            first.touch()
            (work / "second.3de").touch()
            card, _ = self._make_card(tmpdir)
            FakeMenu.instances = []
            with mock.patch.object(widgets, "QMenu", FakeMenu), \
                    mock.patch.object(widgets, "open_3de_project") as launch, \
                    mock.patch.object(widgets.QMessageBox, "warning") as warning:
                card.btn_open_matchmove.click()
                first.unlink()
                FakeMenu.instances[0].actions[0].triggered.fire()
                launch.assert_not_called()
                self.assertIn("moved or been deleted", warning.call_args.args[2])

    def test_3de_button_stays_in_order_when_switching_grid_layouts(self):
        from v02_cards import arrange_shot_actions
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "shot_assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            (work / "solve.3de").touch()
            card, _ = self._make_card(tmpdir)
            for _ in range(2):
                self.assertEqual([card.horizontalLayout_3.itemAt(i).widget() for i in (1, 2, 3)],
                                 [card.btn_open_assets, card.btn_open_matchmove, card.btn_open_precomp])
                card.set_v02_grid(True)
                arrange_shot_actions(card)
                folders = card.horizontalLayout_3.itemAt(1).layout()
                self.assertEqual([folders.itemAt(i).widget() for i in range(3)],
                                 [card.btn_open_assets, card.btn_open_matchmove, card.btn_open_precomp])
                self.assertEqual(card.btn_open_matchmove.property("v02"), "true")
                self.assertFalse(card.btn_open_matchmove.isHidden())
                card.set_v02_grid(False)
            self.assertFalse(card.btn_open_matchmove.isHidden())

    def test_resolve_preview_project_name_prefers_job_title(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))
            card.data["job"] = {"title": "ProjectA"}

            self.assertEqual(card._resolve_preview_project_name(), "ProjectA")

    def test_resolve_preview_project_name_falls_back_to_project_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "ProjectB" / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, _fake_folders = self._make_card(str(shot_root))

            self.assertEqual(card._resolve_preview_project_name(), "ProjectB")

    def test_original_clip_label_shows_clip_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            original_clip = shot_root / "plates" / "very_long_clip_name_v001.mov"
            card, _fake_folders = self._make_card(
                str(shot_root),
                data_overrides={"original_clip": str(original_clip)},
            )

            self.assertEqual(
                card.label_original_clip.text(),
                original_clip.name,
            )

    def test_original_clip_menu_shows_disabled_state_without_clips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            card, _fake_folders = self._make_card(
                str(shot_root),
                data_overrides={"original_clip": "", "original_clips": []},
            )

            menu = FakeMenu()
            card._populate_original_clip_menu(menu)

            self.assertEqual(menu.actions[0].text, "No OG clips available")
            self.assertFalse(menu.actions[0].enabled)

    def test_original_clip_menu_falls_back_to_single_og_clip_submenu(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            original_clip = shot_root / "plates" / "V1.mov"
            card, _fake_folders = self._make_card(
                str(shot_root),
                data_overrides={"original_clip": str(original_clip), "original_clips": []},
            )

            menu = FakeMenu()
            card._populate_original_clip_menu(menu)

            self.assertEqual([label for label, _submenu in menu.submenus], ["OG Clip"])

    def test_original_clip_menu_uses_original_clips_filenames_and_selected_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            clip_a = shot_root / "plates" / "V1.mov"
            clip_b = shot_root / "plates" / "V2.mov"
            card, fake_folders = self._make_card(
                str(shot_root),
                data_overrides={
                    "original_clip": str(clip_a),
                    "original_clips": [str(clip_a), str(clip_b)],
                },
            )

            self.assertEqual(card.label_original_clip.text(), f'{clip_a.name} (+1)')

            menu = FakeMenu()
            with mock.patch.object(card, "_copy_to_clipboard") as copy_to_clipboard, \
                mock.patch.object(card, "_on_make_preview_clicked") as make_preview, \
                mock.patch.object(card, "_on_make_precomp_exr_clicked") as make_precomp:
                card._populate_original_clip_menu(menu)
                labels = [label for label, _submenu in menu.submenus]
                self.assertEqual(labels, [clip_a.name, clip_b.name])

                second_submenu = menu.submenus[1][1]
                self._find_action(second_submenu, "Open in Files").triggered.fire()
                self._find_action(second_submenu, "Copy Path").triggered.fire()
                self._find_action(second_submenu, "Copy Filename").triggered.fire()
                self._find_action(second_submenu, "Make Preview").triggered.fire()
                self._find_action(second_submenu, "Make Precomp EXR (ACEScg)").triggered.fire()

            self.assertEqual(fake_folders.opened_locations[-1], clip_b)
            copy_to_clipboard.assert_any_call(str(clip_b))
            copy_to_clipboard.assert_any_call(clip_b.name)
            make_preview.assert_called_once_with(clip_path=str(clip_b), plate_name="plate_02")
            make_precomp.assert_called_once_with(clip_path=str(clip_b), plate_name="plate_02")

    def test_create_matchmove_project_creates_work_and_export_and_patches_shot(self):
        self._assert_matchmove_creation()

    def test_assets_creates_next_version_in_existing_default_or_custom_directory(self):
        for custom in (False, True):
            with self.subTest(custom=custom):
                self._assert_matchmove_creation(custom=custom, existing_version=3)

    def _assert_matchmove_creation(self, *, custom=False, existing_version=0):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "VFX" / "timeline_A" / "sho010"
            shot_root.mkdir(parents=True)
            matchmove_dir = Path(tmpdir) / "shared_matchmove" if custom else shot_root / "shot_assets" / "matchmove"
            if existing_version:
                (matchmove_dir / "work").mkdir(parents=True)
                old_project = matchmove_dir / "work" / f"sho010_matchmove_v{existing_version:03d}.3de"
                old_project.write_text("preserve existing project")
            overrides = {"matchmove_path": str(matchmove_dir)} if custom else None
            card, _fake_folders = self._make_card(str(shot_root), overrides)

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
            updated_data["matchmove_path"] = str(matchmove_dir)

            def create_project_file(request, _log_callback):
                Path(request.project_path).touch()
                return fake_headless_result

            with mock.patch.object(widgets, "SingleCameraMatchmoveDialog", AcceptedMatchmoveDialog), \
                mock.patch.object(widgets, "run_headless_3de", side_effect=create_project_file) as run_headless, \
                mock.patch.object(widgets, "cleanup_headless_artifacts") as cleanup, \
                mock.patch.object(widgets, "open_3de_project", return_value=["run_3DE4", "-open", "dummy"]) as open_project, \
                mock.patch.object(widgets.QMessageBox, "information"), \
                mock.patch.object(card._api, "update_shot", return_value=updated_data) as update_shot, \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[sequence_info]):
                menu = FakeMenu()
                card._populate_assets_menu(menu)
                menu.submenus[0][1].submenus[0][1].actions[0].triggered.fire()

            self.assertTrue((matchmove_dir / "work").is_dir())
            self.assertTrue((matchmove_dir / "export").is_dir())
            update_shot.assert_called_once_with(card._shot_id, matchmove_path=str(matchmove_dir))
            run_headless.assert_called_once()
            request = run_headless.call_args.args[0]
            self.assertEqual(request.version, existing_version + 1)
            self.assertEqual(Path(request.project_path).parent, matchmove_dir / "work")
            if existing_version:
                self.assertEqual(old_project.read_text(), "preserve existing project")
            open_project.assert_called_once_with(request.project_path)
            cleanup.assert_called_once_with(fake_headless_result)
            self.assertEqual(card.btn_open_assets.text(), "Assets")
            self.assertFalse(card.btn_open_matchmove.isHidden())


if __name__ == "__main__":
    unittest.main()
