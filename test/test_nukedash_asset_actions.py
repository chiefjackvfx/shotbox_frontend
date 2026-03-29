from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget

import page_nukedash


class FakeFolders:
    def __init__(self):
        self.opened_paths: list[str] = []

    def convert_path(self, file_path: str) -> str:
        return str(file_path)

    def openFileLocation(self, file_path: str) -> None:
        self.opened_paths.append(str(file_path))


class TimelineWidget(QWidget):
    def __init__(self, timeline_data: dict, parent=None):
        super().__init__(parent)
        self._last_timeline = timeline_data


class AssetsHarness(QMainWindow):
    _active_job_data = page_nukedash.page_nukedash._active_job_data
    _active_timeline_data = page_nukedash.page_nukedash._active_timeline_data
    _path_from_base_path = page_nukedash.page_nukedash._path_from_base_path
    _first_shot_dir_for_timeline = page_nukedash.page_nukedash._first_shot_dir_for_timeline
    _first_shot_dir_for_job = page_nukedash.page_nukedash._first_shot_dir_for_job
    _infer_vfx_root_from_shot_dir = page_nukedash.page_nukedash._infer_vfx_root_from_shot_dir
    _job_uses_timeline_directories = page_nukedash.page_nukedash._job_uses_timeline_directories
    _resolve_job_assets_dir = page_nukedash.page_nukedash._resolve_job_assets_dir
    _resolve_timeline_assets_dir = page_nukedash.page_nukedash._resolve_timeline_assets_dir
    _set_assets_button_state = page_nukedash.page_nukedash._set_assets_button_state
    _update_assets_action_buttons = page_nukedash.page_nukedash._update_assets_action_buttons
    _open_assets_directory = page_nukedash.page_nukedash._open_assets_directory
    _on_open_timeline_assets_clicked = page_nukedash.page_nukedash._on_open_timeline_assets_clicked
    _on_open_job_assets_clicked = page_nukedash.page_nukedash._on_open_job_assets_clicked

    def __init__(self, job_data: dict | None = None, current_timeline_index: int = 0):
        super().__init__()

        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.comboBox_jobs = QComboBox(central)
        self.btn_open_timeline_assets = QPushButton("Timeline Assets", central)
        self.btn_open_job_assets = QPushButton("Job Assets", central)
        self.timelines_tabs = QTabWidget(central)

        layout.addWidget(self.comboBox_jobs)
        layout.addWidget(self.btn_open_timeline_assets)
        layout.addWidget(self.btn_open_job_assets)
        layout.addWidget(self.timelines_tabs)

        self.filesIO = FakeFolders()
        self._jobs_by_id = {}
        self._active_job_id = None
        self._pending_job_data = None

        if job_data:
            self._jobs_by_id[job_data["id"]] = job_data
            self._active_job_id = job_data["id"]
            self.comboBox_jobs.addItem(job_data.get("title", "Job"), job_data["id"])
            for timeline in job_data.get("timelines", []) or []:
                self.timelines_tabs.addTab(
                    TimelineWidget(timeline, self.timelines_tabs),
                    timeline.get("title", "Timeline"),
                )
            if 0 <= current_timeline_index < self.timelines_tabs.count():
                self.timelines_tabs.setCurrentIndex(current_timeline_index)


class NukeDashAssetActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_basic_ui_includes_asset_buttons_row(self):
        tree = ET.parse(PYQT_FRONTEND_DIR / "basic.ui")
        widget_names = {element.get("name") for element in tree.iter() if element.get("name")}

        self.assertIn("assets_actions_row", widget_names)
        self.assertIn("btn_open_timeline_assets", widget_names)
        self.assertIn("btn_open_job_assets", widget_names)

    def test_resolves_job_and_timeline_assets_from_active_timeline_shot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_root = Path(temp_dir) / "PROJECT" / "VFX"
            job_data = {
                "id": 1,
                "title": "PROJECT",
                "timelines": [
                    {
                        "id": 10,
                        "title": "Edit_A",
                        "shots": [{"id": 100, "base_path": str(base_root / "Edit_A" / "SHOT010")}],
                    },
                    {
                        "id": 11,
                        "title": "Edit_B",
                        "shots": [{"id": 101, "base_path": str(base_root / "Edit_B" / "SHOT020")}],
                    },
                ],
            }

            harness = AssetsHarness(job_data, current_timeline_index=0)

            self.assertEqual(
                harness._resolve_timeline_assets_dir(),
                base_root / "Edit_A" / "Timeline_Assets",
            )
            self.assertEqual(harness._resolve_job_assets_dir(), base_root / "Job_Assets")

    def test_resolves_timeline_assets_for_empty_timeline_from_job_structure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_root = Path(temp_dir) / "PROJECT" / "VFX"
            job_data = {
                "id": 1,
                "title": "PROJECT",
                "timelines": [
                    {
                        "id": 10,
                        "title": "Edit_A",
                        "shots": [{"id": 100, "base_path": str(base_root / "Edit_A" / "SHOT010")}],
                    },
                    {
                        "id": 11,
                        "title": "Edit_B",
                        "shots": [],
                    },
                ],
            }

            harness = AssetsHarness(job_data, current_timeline_index=1)

            self.assertEqual(
                harness._resolve_timeline_assets_dir(),
                base_root / "Edit_B" / "Timeline_Assets",
            )

    def test_disables_timeline_button_when_job_has_no_timeline_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_root = Path(temp_dir) / "PROJECT" / "VFX"
            job_data = {
                "id": 1,
                "title": "PROJECT",
                "timelines": [
                    {
                        "id": 10,
                        "title": "Main",
                        "shots": [{"id": 100, "base_path": str(base_root / "SHOT010")}],
                    }
                ],
            }

            harness = AssetsHarness(job_data, current_timeline_index=0)
            harness._update_assets_action_buttons()

            self.assertFalse(harness.btn_open_timeline_assets.isEnabled())
            self.assertTrue(harness.btn_open_job_assets.isEnabled())
            self.assertEqual(
                harness.btn_open_job_assets.property("folder_path"),
                str(base_root / "Job_Assets"),
            )

    def test_open_handlers_create_and_open_asset_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_root = Path(temp_dir) / "PROJECT" / "VFX"
            shot_dir = base_root / "Edit_A" / "SHOT010"
            shot_dir.mkdir(parents=True, exist_ok=True)
            job_data = {
                "id": 1,
                "title": "PROJECT",
                "timelines": [
                    {
                        "id": 10,
                        "title": "Edit_A",
                        "shots": [{"id": 100, "base_path": str(shot_dir)}],
                    }
                ],
            }

            harness = AssetsHarness(job_data, current_timeline_index=0)

            timeline_assets_dir = base_root / "Edit_A" / "Timeline_Assets"
            job_assets_dir = base_root / "Job_Assets"

            harness._on_open_timeline_assets_clicked()
            harness._on_open_job_assets_clicked()

            self.assertTrue(timeline_assets_dir.is_dir())
            self.assertTrue(job_assets_dir.is_dir())
            self.assertEqual(
                harness.filesIO.opened_paths,
                [str(timeline_assets_dir), str(job_assets_dir)],
            )


if __name__ == "__main__":
    unittest.main()
