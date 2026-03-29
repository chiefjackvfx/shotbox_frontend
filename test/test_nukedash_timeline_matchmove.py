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
from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget

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

    def update_timeline(self, timeline_id: int, **fields):
        self.update_calls.append((timeline_id, fields))
        if self.raise_error is not None:
            raise self.raise_error
        return self.response


class TimelineMatchmoveHarness(QMainWindow):
    _active_job_data = page_nukedash.page_nukedash._active_job_data
    _active_timeline_data = page_nukedash.page_nukedash._active_timeline_data
    _path_from_base_path = page_nukedash.page_nukedash._path_from_base_path
    _first_shot_dir_for_timeline = page_nukedash.page_nukedash._first_shot_dir_for_timeline
    _first_shot_dir_for_job = page_nukedash.page_nukedash._first_shot_dir_for_job
    _infer_vfx_root_from_shot_dir = page_nukedash.page_nukedash._infer_vfx_root_from_shot_dir
    _job_uses_timeline_directories = page_nukedash.page_nukedash._job_uses_timeline_directories
    _resolve_timeline_assets_dir = page_nukedash.page_nukedash._resolve_timeline_assets_dir
    _resolved_timeline_matchmove_dir = page_nukedash.page_nukedash._resolved_timeline_matchmove_dir
    _discover_timeline_matchmove_candidates = page_nukedash.page_nukedash._discover_timeline_matchmove_candidates
    _apply_timeline_payload = page_nukedash.page_nukedash._apply_timeline_payload
    _open_timeline_matchmove_project = page_nukedash.page_nukedash._open_timeline_matchmove_project
    _show_timeline_assets_matchmove_menu = page_nukedash.page_nukedash._show_timeline_assets_matchmove_menu
    _on_timeline_assets_context_menu = page_nukedash.page_nukedash._on_timeline_assets_context_menu
    _create_timeline_matchmove_project = page_nukedash.page_nukedash._create_timeline_matchmove_project
    _open_assets_directory = page_nukedash.page_nukedash._open_assets_directory
    _on_open_timeline_assets_clicked = page_nukedash.page_nukedash._on_open_timeline_assets_clicked

    def __init__(self, job_data: dict):
        super().__init__()
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.comboBox_jobs = QComboBox(central)
        self.btn_open_timeline_assets = QPushButton("Timeline Assets", central)
        self.timelines_tabs = QTabWidget(central)

        layout.addWidget(self.comboBox_jobs)
        layout.addWidget(self.btn_open_timeline_assets)
        layout.addWidget(self.timelines_tabs)

        self.filesIO = FakeFolders()
        self._jobs_by_id = {job_data["id"]: job_data}
        self._active_job_id = job_data["id"]
        self._worker = SimpleNamespace(api=FakeApi())

        self.comboBox_jobs.addItem(job_data.get("title", "Job"), job_data["id"])
        for timeline in job_data.get("timelines", []) or []:
            self.timelines_tabs.addTab(TimelineWidget(timeline, self.timelines_tabs), timeline.get("title", "Timeline"))


class AcceptedTimelineDialog:
    project_name = "Edit_A_Solve"

    def __init__(self, timeline_title: str, candidates, matchmove_dir: str, parent=None):
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

            harness._on_open_timeline_assets_clicked()

            self.assertEqual(
                harness.filesIO.opened_paths[-1],
                shot_root.parent / "Timeline_Assets",
            )
            self.assertTrue((shot_root.parent / "Timeline_Assets").is_dir())

    def test_timeline_assets_menu_lists_existing_projects_under_matchmove_submenu(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            work_dir = shot_root.parent / "Timeline_Assets" / "matchmove" / "work"
            work_dir.mkdir(parents=True)
            (work_dir / "Edit_A_matchmove_v001.3de").write_text("")
            (work_dir / "Edit_A_matchmove_v003.3de").write_text("")

            FakeMenu.instances = []
            with mock.patch.object(page_nukedash, "QMenu", FakeMenu), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[]):
                shown = harness._show_timeline_assets_matchmove_menu(QPoint(10, 20))

            self.assertTrue(shown)
            self.assertEqual(FakeMenu.instances[0].submenus[0][0], "Matchmove")
            submenu = FakeMenu.instances[0].submenus[0][1]
            self.assertEqual(
                [action.text for action in submenu.actions[:2]],
                [
                    "Open Edit_A_matchmove_v003.3de",
                    "Open Edit_A_matchmove_v001.3de",
                ],
            )
            self.assertEqual(submenu.actions[-1].text, "No valid EXR precomp folders found")
            self.assertFalse(submenu.actions[-1].enabled)

    def test_timeline_assets_menu_includes_create_action_when_candidates_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            shot_root.mkdir(parents=True)
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            sequence_info = self._make_sequence_info(shot_root / "renders" / "precomp" / "plateA")

            FakeMenu.instances = []
            with mock.patch.object(page_nukedash, "QMenu", FakeMenu), \
                mock.patch.object(widgets, "list_matchmove_projects", return_value=[]), \
                mock.patch.object(widgets, "list_valid_precomp_sequences", return_value=[sequence_info]), \
                mock.patch.object(harness, "_create_timeline_matchmove_project") as create_project:
                shown = harness._show_timeline_assets_matchmove_menu(QPoint(3, 4))
                submenu = FakeMenu.instances[0].submenus[0][1]
                self.assertEqual(submenu.actions[0].text, "Create New 3DE Project")
                submenu.actions[0].triggered.fire()

            self.assertTrue(shown)
            create_project.assert_called_once()

    def test_discover_timeline_matchmove_candidates_scans_all_shots_and_excludes_previews(self):
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

    def test_create_timeline_matchmove_project_runs_headless_and_patches_timeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "PROJECT" / "VFX" / "Edit_A" / "sho001"
            write_minimal_exr(shot_root / "renders" / "precomp" / "plateA" / "plateA.1001.exr")
            harness = TimelineMatchmoveHarness(self._make_job_data([shot_root]))
            harness._worker.api.response = {"id": 10, "matchmove_path": str(shot_root.parent / "Timeline_Assets" / "matchmove")}
            candidate = timeline_matchmove_dialog.TimelineMatchmoveCandidate(
                shot_id=1,
                shot_title="sho001",
                shot_root=str(shot_root),
                sequence_info=matchmove_helpers.detect_exr_sequence(str(shot_root / "renders" / "precomp" / "plateA")),
            )
            fake_headless_result = SimpleNamespace(runtime_script_path="/tmp/runtime.py", status_path="/tmp/status.json")

            with mock.patch.object(page_nukedash, "TimelineMatchmoveDialog", AcceptedTimelineDialog), \
                mock.patch.object(widgets, "run_headless_3de", return_value=fake_headless_result) as run_headless, \
                mock.patch.object(widgets, "cleanup_headless_artifacts") as cleanup, \
                mock.patch.object(widgets, "open_3de_project", return_value=["run_3DE4", "-open", "dummy"]) as open_project, \
                mock.patch.object(page_nukedash.QMessageBox, "information") as information:
                harness._create_timeline_matchmove_project([candidate])

            matchmove_dir = shot_root.parent / "Timeline_Assets" / "matchmove"
            self.assertTrue((matchmove_dir / "work").is_dir())
            self.assertTrue((matchmove_dir / "export").is_dir())
            self.assertEqual(harness._worker.api.update_calls[0][0], 10)
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

    def test_create_timeline_matchmove_project_reports_patch_warning_after_success(self):
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
                harness._create_timeline_matchmove_project([candidate])

            message_text = information.call_args.args[2]
            self.assertIn("saving matchmove_path to ShotBox failed", message_text)

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
