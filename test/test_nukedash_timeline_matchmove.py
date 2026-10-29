from __future__ import annotations

import os
import struct
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
from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow, QTabWidget, QToolButton, QVBoxLayout, QWidget

import matchmove_helpers
import page_nukedash
import timeline_matchmove_dialog
import widgets


def write_minimal_exr(file_path: Path, width: int = 2048, height: int = 1152) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray()
    payload.extend((20000630).to_bytes(4, "little"))
    payload.extend((2).to_bytes(4, "little"))

    def add_attribute(name: str, attr_type: str, value: bytes) -> None:
        payload.extend(name.encode("utf-8") + b"\x00")
        payload.extend(attr_type.encode("utf-8") + b"\x00")
        payload.extend(len(value).to_bytes(4, "little"))
        payload.extend(value)

    add_attribute(
        "dataWindow",
        "box2i",
        struct.pack("<iiii", 0, 0, width - 1, height - 1),
    )
    add_attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    payload.extend(b"\x00")
    file_path.write_bytes(payload)


class FakeFolders:
    def __init__(self):
        self.opened_paths: list[Path] = []

    def convert_path(self, file_path: str) -> str:
        return str(file_path)

    def openFileLocation(self, file_path) -> None:
        self.opened_paths.append(Path(file_path))


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
        self.enabled = True
        self.triggered = FakeSignal()

    def setEnabled(self, enabled: bool):
        self.enabled = bool(enabled)


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
        return None

    def clear(self):
        self.actions.clear()
        self.submenus.clear()
        self.separator_count = 0

    def exec(self, pos):
        self.exec_pos = pos


class TimelineWidget(QWidget):
    def __init__(self, timeline_data: dict, parent=None):
        super().__init__(parent)
        self._last_timeline = timeline_data


class FakeApi:
    def __init__(self):
        self.update_calls: list[tuple[int, dict]] = []
        self.response = None
        self.raise_error: Exception | None = None

    def update_job(self, timeline_id: int, **fields):
        self.update_calls.append((timeline_id, fields))
        if self.raise_error is not None:
            raise self.raise_error
        return self.response


class TimelineMatchmoveHarness(QMainWindow):
    _legacy_timeline_assets_dir = page_nukedash.page_nukedash._legacy_timeline_assets_dir
    _resolved_job_matchmove_dir = page_nukedash.page_nukedash._resolved_job_matchmove_dir
    _set_job_matchmove_state = page_nukedash.page_nukedash._set_job_matchmove_state
    _refresh_job_matchmove_state = page_nukedash.page_nukedash._refresh_job_matchmove_state
    _discover_job_matchmove_candidates = page_nukedash.page_nukedash._discover_job_matchmove_candidates
    _apply_job_payload = page_nukedash.page_nukedash._apply_job_payload
    _populate_job_assets_menu = page_nukedash.page_nukedash._populate_job_assets_menu
    _show_job_assets_matchmove_menu = page_nukedash.page_nukedash._show_job_assets_matchmove_menu
    _on_open_job_assets_clicked = page_nukedash.page_nukedash._on_open_job_assets_clicked
    _setup_assets_menus = page_nukedash.page_nukedash._setup_assets_menus
    _active_job_data = page_nukedash.page_nukedash._active_job_data
    _active_timeline_data = page_nukedash.page_nukedash._active_timeline_data
    _path_from_base_path = page_nukedash.page_nukedash._path_from_base_path
    _first_shot_dir_for_timeline = page_nukedash.page_nukedash._first_shot_dir_for_timeline
    _first_shot_dir_for_job = page_nukedash.page_nukedash._first_shot_dir_for_job
    _infer_vfx_root_from_shot_dir = page_nukedash.page_nukedash._infer_vfx_root_from_shot_dir
    _job_uses_timeline_directories = page_nukedash.page_nukedash._job_uses_timeline_directories
    _resolve_job_assets_dir = page_nukedash.page_nukedash._resolve_job_assets_dir
    _resolve_timeline_assets_dir = page_nukedash.page_nukedash._resolve_timeline_assets_dir
    _resolved_timeline_matchmove_dir = page_nukedash.page_nukedash._resolved_timeline_matchmove_dir
    _discover_timeline_matchmove_candidates = page_nukedash.page_nukedash._discover_timeline_matchmove_candidates
    _apply_timeline_payload = page_nukedash.page_nukedash._apply_timeline_payload
    _open_timeline_matchmove_project = page_nukedash.page_nukedash._open_timeline_matchmove_project
    _set_timeline_matchmove_state = page_nukedash.page_nukedash._set_timeline_matchmove_state
    _refresh_timeline_matchmove_state = page_nukedash.page_nukedash._refresh_timeline_matchmove_state
    _populate_timeline_assets_menu = page_nukedash.page_nukedash._populate_timeline_assets_menu
    _on_timeline_assets_menu_about_to_show = page_nukedash.page_nukedash._on_timeline_assets_menu_about_to_show
    _show_timeline_assets_matchmove_menu = page_nukedash.page_nukedash._show_timeline_assets_matchmove_menu
    _create_job_matchmove_project = page_nukedash.page_nukedash._create_job_matchmove_project
    _set_assets_button_state = page_nukedash.page_nukedash._set_assets_button_state
    _update_assets_action_buttons = page_nukedash.page_nukedash._update_assets_action_buttons
    _open_assets_directory = page_nukedash.page_nukedash._open_assets_directory
    _on_open_timeline_assets_clicked = page_nukedash.page_nukedash._on_open_timeline_assets_clicked

    def __init__(self, job_data: dict):
        super().__init__()
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.comboBox_jobs = QComboBox(central)
        self.btn_open_timeline_assets = QToolButton(central)
        self.btn_open_timeline_assets.setText("Timeline Assets")
        self.btn_open_job_assets = QToolButton(central)
        self.btn_open_job_assets.setText("Job Assets")
        self.timelines_tabs = QTabWidget(central)

        layout.addWidget(self.comboBox_jobs)
        layout.addWidget(self.btn_open_timeline_assets)
        layout.addWidget(self.timelines_tabs)

        self.filesIO = FakeFolders()
        self._jobs_by_id = {job_data["id"]: job_data}
        self._active_job_id = job_data["id"]
        self._worker = SimpleNamespace(api=FakeApi())

        self._setup_assets_menus()

        self.comboBox_jobs.addItem(job_data.get("title", "Job"), job_data["id"])
        for timeline in job_data.get("timelines", []) or []:
            self.timelines_tabs.addTab(TimelineWidget(timeline, self.timelines_tabs), timeline.get("title", "Timeline"))


class AcceptedTimelineDialog:
    project_name = "Edit_A_Solve"

    def __init__(self, timeline_title: str, candidates, matchmove_dir: str, parent=None, scope_label="Timeline"):
        self.timeline_title = timeline_title
        self.candidates = list(candidates)
        self.matchmove_dir = matchmove_dir
        project_dir, export_dir, project_path, version = matchmove_helpers.resolve_project_path(
            matchmove_dir,
            self.project_name,
        )
        clip_requests = []
        for index, candidate in enumerate(self.candidates):
            clip_requests.append(
                matchmove_helpers.MatchmoveClipRequest(
                    slot_index=index,
                    clip_name=Path(candidate.sequence_info.folder_path).name,
                    camera_name=f"cam_{index + 1}",
                    lens_name=matchmove_helpers.format_focal_length_label(35.0 + index),
                    sequence_info=candidate.sequence_info,
                    focal_length_mm=35.0 + index,
                    sequence_start_frame=candidate.sequence_info.first_frame,
                    sequence_end_frame=candidate.sequence_info.last_frame,
                )
            )
        self.built_request = matchmove_helpers.MatchmoveProjectRequest(
            project_name=self.project_name,
            shot_name=self.project_name,
            clips=tuple(clip_requests),
            camera_preset_name="Alexa 35",
            matchmove_dir=matchmove_dir,
            export_dir=export_dir,
            fps=24.0,
            project_dir=project_dir,
            project_path=project_path,
            version=version,
        )

    def exec(self):
        return page_nukedash.QDialog.DialogCode.Accepted


class TimelineMatchmoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_job_data(self, shot_roots: list[Path], *, timeline_title: str = "Edit_A") -> dict:
        shots = []
        for index, shot_root in enumerate(shot_roots, start=1):
            shots.append(
                {
                    "id": index,
                    "title": f"sho{index:03d}",
                    "base_path": str(shot_root),
                }
            )
        return {
            "id": 1,
            "title": "PROJECT",
            "timelines": [
                {
                    "id": 10,
                    "title": timeline_title,
                    "shots": shots,
                    "matchmove_path": None,
                }
            ],
        }

    def _make_sequence_info(self, folder_path: Path):
        example_file = folder_path / "plate.1001.exr"
        return matchmove_helpers.detect_exr_sequence(str(folder_path)) if example_file.exists() else matchmove_helpers.SequenceInfo(
            folder_path=str(folder_path),
            example_file=str(folder_path / "plate.1001.exr"),
            sequence_path_pattern=str(folder_path / "plate.####.exr"),
            display_pattern="plate.####.exr",
            prefix="plate.",
            suffix=".exr",
            padding=4,
            first_frame=1001,
            last_frame=1001,
            frames=(1001,),
            width=2048,
            height=1152,
            header_pixel_aspect=1.0,
        )

    def test_timeline_assets_left_click_opens_timeline_assets_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))

            (shot_root.parent / "timeline_assets").mkdir()
            harness._on_open_timeline_assets_clicked()

            self.assertEqual(
                harness.filesIO.opened_paths[-1],
                shot_root.parent / "timeline_assets",
            )
            self.assertTrue((shot_root.parent / "timeline_assets").is_dir())

    def test_timeline_assets_control_is_an_instant_popup_tool_button(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))

            self.assertIsInstance(harness.btn_open_timeline_assets, QToolButton)
            self.assertEqual(
                harness.btn_open_timeline_assets.popupMode(),
                QToolButton.ToolButtonPopupMode.InstantPopup,
            )

    def test_timeline_assets_menu_first_action_opens_assets_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            menu = FakeMenu()

            with mock.patch.object(widgets, "list_matchmove_projects", return_value=[]), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[]):
                harness._populate_timeline_assets_menu(menu)

            self.assertEqual(menu.actions[0].text, "Open Timeline Assets in File Browser")
            self.assertEqual(menu.separator_count, 1)
            (shot_root.parent / "timeline_assets").mkdir()
            menu.actions[0].triggered.fire()
            self.assertEqual(
                harness.filesIO.opened_paths[-1],
                shot_root.parent / "timeline_assets",
            )

    def test_timeline_matchmove_indicator_tracks_configured_project_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            configured_dir = Path(tmpdir) / "shared" / "timeline_matchmove"
            work_dir = configured_dir / "work"
            shot_root.mkdir(parents=True)
            work_dir.mkdir(parents=True)
            job_data = self._make_job_data([shot_root])
            job_data["timelines"][0]["matchmove_path"] = str(configured_dir)
            (shot_root.parent / "timeline_assets").mkdir()
            harness = TimelineMatchmoveHarness(job_data)
            harness._update_assets_action_buttons()

            self.assertEqual(harness.btn_open_timeline_assets.text(), "Timeline Assets")
            self.assertEqual(
                harness.btn_open_timeline_assets.property("has_matchmove"),
                "false",
            )

            outside_project = configured_dir / "Edit_A_matchmove_v001.3de"
            outside_project.write_text("")
            harness._refresh_timeline_matchmove_state()
            self.assertEqual(harness.btn_open_timeline_assets.text(), "Timeline Assets")

            project = work_dir / "Edit_A_matchmove_v002.3de"
            project.write_text("")
            harness._refresh_timeline_matchmove_state()
            self.assertEqual(
                harness.btn_open_timeline_assets.text(),
                "Timeline Assets · 3DE",
            )
            self.assertEqual(
                harness.btn_open_timeline_assets.property("has_matchmove"),
                "true",
            )
            self.assertIn("1 timeline matchmove project", harness.btn_open_timeline_assets.toolTip())
            self.assertIn(str(configured_dir), harness.btn_open_timeline_assets.toolTip())

            project.unlink()
            harness._refresh_timeline_matchmove_state()
            self.assertEqual(harness.btn_open_timeline_assets.text(), "Timeline Assets")

    def test_timeline_assets_menu_lists_existing_projects_under_matchmove_submenu(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            work_dir = shot_root.parent / "Timeline_Assets" / "matchmove" / "work"
            work_dir.mkdir(parents=True)
            (work_dir / "Edit_A_matchmove_v001.3de").write_text("")
            (work_dir / "Edit_A_matchmove_v003.3de").write_text("")
            (work_dir / "standalone.3DE").touch()

            FakeMenu.instances = []
            with mock.patch.object(page_nukedash, "QMenu", FakeMenu):
                shown = harness._show_timeline_assets_matchmove_menu(QPoint(10, 20))

            self.assertTrue(shown)
            self.assertEqual(FakeMenu.instances[0].submenus[0][0], "Matchmove")
            submenu = FakeMenu.instances[0].submenus[0][1]
            self.assertEqual(
                [action.text for action in submenu.actions[:2]],
                [
                    "Open Edit_A_matchmove_v003.3de",
                    "Open standalone.3DE",
                ],
            )
            self.assertEqual(len(submenu.actions), 2)

    def test_legacy_timeline_menu_never_offers_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            menu = FakeMenu()
            with mock.patch.object(harness, "_discover_timeline_matchmove_candidates") as discover:
                self.assertTrue(harness._populate_timeline_assets_menu(menu))
            discover.assert_not_called()
            self.assertEqual(menu.submenus[0][1].actions, [])

    def test_discover_timeline_matchmove_candidates_scans_all_shots_and_excludes_previews(self):
        print("____________test discover")
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root_a = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root_b = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho002"
            write_minimal_exr(shot_root_a / "renders" / "precomp" / "plateA" / "plateA.1001.exr")
            write_minimal_exr(shot_root_a / "renders" / "precomp" / "previews" / "preview.1001.exr")
            write_minimal_exr(shot_root_b / "renders" / "precomp" / "plateB" / "plateB.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root_a, shot_root_b]))

            candidates = harness._discover_timeline_matchmove_candidates()

            self.assertEqual(len(candidates), 2)
            self.assertEqual([candidate.shot_title for candidate in candidates], ["sho001", "sho002"])
            self.assertEqual(
                [Path(candidate.sequence_info.folder_path).name for candidate in candidates],
                ["plateA", "plateB"],
            )

    def test_create_job_matchmove_project_runs_headless_and_patches_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            write_minimal_exr(shot_root / "renders" / "precomp" / "plateA" / "plateA.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            harness._worker.api.response = {"id": 1, "matchmove_path": str(shot_root.parent.parent / "job_assets" / "matchmove")}
            candidate = timeline_matchmove_dialog.TimelineMatchmoveCandidate(
                shot_id=1,
                shot_title="sho001",
                shot_root=str(shot_root),
                sequence_info=matchmove_helpers.detect_exr_sequence(str(shot_root / "renders" / "precomp" / "plateA")),
            )
            fake_headless_result = SimpleNamespace(runtime_script_path="/tmp/runtime.py", status_path="/tmp/status.json")

            def create_project_file(request, _log_callback):
                Path(request.project_path).touch()
                return fake_headless_result

            with mock.patch.object(page_nukedash, "TimelineMatchmoveDialog", AcceptedTimelineDialog), \
                mock.patch.object(widgets, "run_headless_3de", side_effect=create_project_file) as run_headless, \
                mock.patch.object(widgets, "cleanup_headless_artifacts") as cleanup, \
                mock.patch.object(widgets, "open_3de_project", return_value=["run_3DE4", "-open", "dummy"]) as open_project, \
                mock.patch.object(page_nukedash.QMessageBox, "information") as information:
                harness._create_job_matchmove_project([candidate])

            matchmove_dir = shot_root.parent.parent / "job_assets" / "matchmove"
            self.assertTrue((matchmove_dir / "work").is_dir())
            self.assertTrue((matchmove_dir / "export").is_dir())
            self.assertEqual(harness._worker.api.update_calls[0][0], 1)
            self.assertEqual(
                harness._worker.api.update_calls[0][1]["matchmove_path"],
                str(matchmove_dir),
            )
            request = run_headless.call_args.args[0]
            self.assertEqual(request.clip_count, 1)
            self.assertEqual(request.project_name, "Edit_A_Solve")
            open_project.assert_called_once()
            cleanup.assert_called_once_with(fake_headless_result)
            information.assert_called_once()
            self.assertEqual(
                harness.btn_open_job_assets.text(),
                "Job Assets · 3DE",
            )
            self.assertIsNone(harness._jobs_by_id[1]["timelines"][0]["matchmove_path"])

    def test_create_job_matchmove_project_reports_patch_warning_after_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            write_minimal_exr(shot_root / "renders" / "precomp" / "plateA" / "plateA.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            harness._worker.api.raise_error = RuntimeError("api down")
            candidate = timeline_matchmove_dialog.TimelineMatchmoveCandidate(
                shot_id=1,
                shot_title="sho001",
                shot_root=str(shot_root),
                sequence_info=matchmove_helpers.detect_exr_sequence(str(shot_root / "renders" / "precomp" / "plateA")),
            )
            fake_headless_result = SimpleNamespace(runtime_script_path="/tmp/runtime.py", status_path="/tmp/status.json")

            with mock.patch.object(page_nukedash, "TimelineMatchmoveDialog", AcceptedTimelineDialog), \
                mock.patch.object(widgets, "run_headless_3de", return_value=fake_headless_result), \
                mock.patch.object(widgets, "cleanup_headless_artifacts"), \
                mock.patch.object(widgets, "open_3de_project", return_value=["run_3DE4", "-open", "dummy"]), \
                mock.patch.object(page_nukedash.QMessageBox, "information") as information:
                harness._create_job_matchmove_project([candidate])

            message_text = information.call_args.args[2]
            self.assertIn("saving matchmove_path to ShotBox failed", message_text)

    def test_assets_buttons_wire_right_click_to_context_menus(self):
        from PyQt6.QtCore import Qt
        with mock.patch.object(TimelineMatchmoveHarness, "_show_job_assets_matchmove_menu") as job_menu, \
                mock.patch.object(TimelineMatchmoveHarness, "_show_timeline_assets_matchmove_menu") as timeline_menu:
            harness = TimelineMatchmoveHarness(self._make_job_data([]))
            for button, callback in ((harness.btn_open_job_assets, job_menu),
                                     (harness.btn_open_timeline_assets, timeline_menu)):
                self.assertEqual(button.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
                button.customContextMenuRequested.emit(QPoint(2, 3))
                callback.assert_called_once_with(button.mapToGlobal(QPoint(2, 3)))
            self.assertEqual(harness.btn_open_job_assets.popupMode(),
                             QToolButton.ToolButtonPopupMode.InstantPopup)

    def test_job_candidates_cover_timelines_label_duplicate_names_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            roots = [Path(tmpdir) / "VFX" / edit / "sho001" for edit in ("Edit_A", "Edit_B")]
            for root in roots:
                write_minimal_exr(root / "renders" / "precomp" / "plate" / "plate.1001.exr")
            job = self._make_job_data([roots[0]])
            other = self._make_job_data([roots[1]], timeline_title="Edit_B")["timelines"][0]
            other["id"] = 11
            other["shots"].append(dict(job["timelines"][0]["shots"][0]))
            job["timelines"].append(other)
            harness = TimelineMatchmoveHarness(job)
            candidates = harness._discover_job_matchmove_candidates()
            self.assertEqual([candidate.display_title for candidate in candidates],
                             ["Edit_A / sho001", "Edit_B / sho001"])
            dialog = timeline_matchmove_dialog.TimelineMatchmoveDialog(
                timeline_title=job["title"], candidates=candidates,
                matchmove_dir=str(harness._resolved_job_matchmove_dir()), scope_label="Job",
            )
            self.assertEqual(dialog.project_name_edit.text(), job["title"])
            self.assertEqual([label.text() for label in dialog._group_labels],
                             ["Edit_A / sho001", "Edit_B / sho001"])
            for row in dialog._candidate_rows:
                row.check_box.setChecked(True)
            request = dialog.build_request()
            self.assertEqual(request.clip_count, 2)
            self.assertEqual(len({clip.camera_name for clip in request.clips}), 2)

    def test_job_menu_lists_latest_per_name_and_disables_empty_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "VFX" / "Edit_A" / "sho001"
            root.mkdir(parents=True)
            work = root.parent.parent / "Job_Assets" / "matchmove" / "work"
            work.mkdir(parents=True)
            for name in ("A_matchmove_v001.3de", "A_matchmove_v002.3de", "B.3DE"):
                (work / name).touch()
            harness = TimelineMatchmoveHarness(self._make_job_data([root]))
            self.assertEqual(harness._resolved_job_matchmove_dir(), work.parent)
            menu = FakeMenu()
            self.assertTrue(harness._populate_job_assets_menu(menu))
            submenu = menu.submenus[0][1]
            self.assertEqual([action.text for action in submenu.actions[:2]],
                             ["Open A_matchmove_v002.3de", "Open B.3DE"])
            self.assertEqual(submenu.actions[2].text, "Create New 3DE Project")
            self.assertFalse(submenu.actions[2].enabled)
            self.assertIn("No valid EXR", submenu.actions[3].text)
            self.assertEqual(harness.btn_open_job_assets.text(), "Job Assets · 3DE")

    def test_job_menu_creation_available_with_valid_precomps_and_right_click(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "VFX" / "Edit_A" / "sho001"
            write_minimal_exr(root / "renders" / "precomp" / "plate" / "plate.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([root]))
            FakeMenu.instances = []
            with mock.patch.object(page_nukedash, "QMenu", FakeMenu), \
                    mock.patch.object(harness, "_create_job_matchmove_project") as create:
                self.assertTrue(harness._show_job_assets_matchmove_menu(QPoint(5, 6)))
                menu = FakeMenu.instances[0]
                self.assertEqual(menu.exec_pos, QPoint(5, 6))
                action = menu.submenus[0][1].actions[0]
                self.assertTrue(action.enabled)
                action.triggered.fire()
                create.assert_called_once_with()

    def test_legacy_visibility_and_missing_saved_paths_never_create_folders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "VFX" / "Edit_A" / "sho001"
            root.mkdir(parents=True)
            job = self._make_job_data([root])
            harness = TimelineMatchmoveHarness(job)
            harness._update_assets_action_buttons()
            self.assertTrue(harness.btn_open_timeline_assets.isHidden())
            legacy = root.parent / "Timeline_Assets"
            legacy.mkdir()
            harness._update_assets_action_buttons()
            self.assertFalse(harness.btn_open_timeline_assets.isHidden())
            legacy.rmdir()
            custom = Path(tmpdir) / "unavailable" / "custom_matchmove"
            job["timelines"][0]["matchmove_path"] = str(custom)
            harness._update_assets_action_buttons()
            self.assertTrue(harness.btn_open_timeline_assets.isHidden())
            self.assertFalse(harness.btn_open_timeline_assets.isEnabled())
            custom.mkdir(parents=True)
            harness._update_assets_action_buttons()
            self.assertTrue(harness.btn_open_timeline_assets.isHidden())
            custom.rmdir()
            self.assertEqual(harness._resolved_timeline_matchmove_dir(), custom)
            with mock.patch.object(page_nukedash.QMessageBox, "information") as info:
                harness._on_open_timeline_assets_clicked()
                info.assert_called_once()
            self.assertFalse(custom.exists())
            self.assertFalse((root.parent / "timeline_assets").exists())
            lowercase = root.parent / "timeline_assets"
            lowercase.touch()
            harness._update_assets_action_buttons()
            self.assertTrue(harness.btn_open_timeline_assets.isHidden())
            lowercase.unlink()
            lowercase.mkdir()
            harness._update_assets_action_buttons()
            self.assertFalse(harness.btn_open_timeline_assets.isHidden())
            harness._update_assets_action_buttons(loading=True)
            self.assertTrue(harness.btn_open_timeline_assets.isHidden())

    def test_job_configured_paths_and_indicators_follow_job_switching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom = Path(tmpdir) / "custom"
            (custom / "work").mkdir(parents=True)
            (custom / "work" / "solve.3de").touch()
            job = self._make_job_data([])
            job["matchmove_path"] = str(custom)
            harness = TimelineMatchmoveHarness(job)
            harness._update_assets_action_buttons()
            self.assertEqual(harness._resolved_job_matchmove_dir(), custom)
            self.assertEqual(harness.btn_open_job_assets.text(), "Job Assets · 3DE")
            other = {"id": 2, "title": "Other", "timelines": [],
                     "matchmove_path": str(Path(tmpdir) / "new_custom")}
            harness._jobs_by_id[2] = other
            harness._active_job_id = 2
            harness._update_assets_action_buttons()
            self.assertEqual(harness._resolved_job_matchmove_dir(), Path(other["matchmove_path"]))
            self.assertEqual(harness.btn_open_job_assets.text(), "Job Assets")
            self.assertTrue(harness.btn_open_job_assets.isEnabled())
            harness._apply_job_payload({"id": 1, "matchmove_path": str(custom / "changed")})
            self.assertEqual(other["matchmove_path"], str(Path(tmpdir) / "new_custom"))

    def test_job_menu_reports_unresolvable_directory(self):
        harness = TimelineMatchmoveHarness(self._make_job_data([]))
        menu = FakeMenu()
        harness._populate_job_assets_menu(menu)
        submenu = menu.submenus[0][1]
        self.assertFalse(submenu.actions[0].enabled)
        self.assertIn("No job matchmove folder", submenu.actions[1].text)

    def test_job_creation_failure_does_not_patch_or_open(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "VFX" / "Edit_A" / "sho001"
            write_minimal_exr(root / "renders" / "precomp" / "plate" / "plate.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([root]))
            with mock.patch.object(page_nukedash, "TimelineMatchmoveDialog", AcceptedTimelineDialog), \
                    mock.patch.object(widgets, "run_headless_3de", side_effect=RuntimeError("3DE failed")), \
                    mock.patch.object(widgets, "open_3de_project") as open_project, \
                    mock.patch.object(page_nukedash.QMessageBox, "critical") as critical:
                harness._create_job_matchmove_project()
                self.assertIn("3DE failed", critical.call_args.args[2])
                open_project.assert_not_called()
            self.assertEqual(harness._worker.api.update_calls, [])

    def test_job_open_failure_still_persists_created_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "VFX" / "Edit_A" / "sho001"
            write_minimal_exr(root / "renders" / "precomp" / "plate" / "plate.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([root]))
            with mock.patch.object(page_nukedash, "TimelineMatchmoveDialog", AcceptedTimelineDialog), \
                    mock.patch.object(widgets, "run_headless_3de"), \
                    mock.patch.object(widgets, "cleanup_headless_artifacts"), \
                    mock.patch.object(widgets, "open_3de_project", side_effect=RuntimeError("launch failed")), \
                    mock.patch.object(page_nukedash.QMessageBox, "information") as info:
                harness._create_job_matchmove_project()
                self.assertIn("opening it in 3DE failed", info.call_args.args[2])
            self.assertEqual(len(harness._worker.api.update_calls), 1)
            self.assertEqual(harness._jobs_by_id[1]["matchmove_path"],
                             str(root.parent.parent / "job_assets" / "matchmove"))

    def test_dialog_groups_by_shot_and_builds_unlimited_multi_clip_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            candidates = []
            for index in range(7):
                shot_root = Path(tmpdir) / f"sho{index:03d}"
                sequence_folder = shot_root / "renders" / "precomp" / f"plate{index:02d}"
                write_minimal_exr(sequence_folder / f"plate{index:02d}.1001.exr")
                candidates.append(
                    timeline_matchmove_dialog.TimelineMatchmoveCandidate(
                        shot_id=index,
                        shot_title=f"sho{index:03d}",
                        shot_root=str(shot_root),
                        sequence_info=matchmove_helpers.detect_exr_sequence(str(sequence_folder)),
                    )
                )

            dialog = timeline_matchmove_dialog.TimelineMatchmoveDialog(
                timeline_title="Edit_A",
                candidates=candidates,
                matchmove_dir=str(Path(tmpdir) / "Timeline_Assets" / "matchmove"),
            )
            self.addCleanup(dialog.close)
            self.addCleanup(dialog.deleteLater)

            self.assertEqual(
                [label.text() for label in dialog._group_labels],
                [f"sho{index:03d}" for index in range(7)],
            )

            dialog.project_name_edit.setText("Edit_A_Solve")
            for index, row in enumerate(dialog._candidate_rows):
                row.check_box.setChecked(True)
                row.focal_spin_box.setValue(35.0 + index)

            request = dialog.build_request()

            self.assertEqual(request.project_name, "Edit_A_Solve")
            self.assertEqual(request.shot_name, "Edit_A_Solve")
            self.assertEqual(request.clip_count, 7)
            self.assertTrue(Path(request.project_path).name.startswith("Edit_A_Solve_matchmove_v001"))
            self.assertEqual(
                [clip.focal_length_mm for clip in request.clips],
                [35.0 + index for index in range(7)],
            )


if __name__ == "__main__":
    unittest.main()
