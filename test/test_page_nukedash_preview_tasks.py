from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import page_nukedash


class _PreviewTaskHarness:
    _collect_preview_tasks = page_nukedash.page_nukedash._collect_preview_tasks


class FakeFolders:
    def __init__(self, render_info_map=None):
        self._render_info_map = render_info_map or {}

    def convert_path(self, path):
        return str(path)

    def latest_render_info(self, folder):
        return self._render_info_map.get(str(folder))


class PreviewTaskCollectionTests(unittest.TestCase):
    def test_overwrite_queues_existing_plate_and_comp_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            previews = Path(directory) / "renders/precomp/previews"
            previews.mkdir(parents=True)
            (previews / "sho010_plate_01_v001.mp4").touch()
            (previews / "sho010_v003.mp4").touch()
            shot = {"id": 7, "title": "sho010", "base_path": directory, "original_clip": "/plate.mov"}
            folders = FakeFolders({directory: {"version": 3, "type": "mov", "render_path": "/comp.mov"}})
            harness = _PreviewTaskHarness()
            self.assertEqual(harness._collect_preview_tasks(self._job_payload(shot), folders), [])
            tasks = harness._collect_preview_tasks(self._job_payload(shot), folders, overwrite=True)
            self.assertEqual([task["source_type"] for task in tasks], ["original_clip", "render"])

    def test_plate_preview_does_not_block_same_version_comp(self):
        with tempfile.TemporaryDirectory() as directory:
            previews = Path(directory) / "renders/precomp/previews"
            previews.mkdir(parents=True)
            (previews / "sho010_plate_01_v001.mp4").touch()
            shot = {"id": 7, "title": "sho010", "base_path": directory, "original_clip": "/plate.mov"}
            folders = FakeFolders({directory: {"version": 1, "type": "mov", "render_path": "/comp.mov"}})
            tasks = _PreviewTaskHarness()._collect_preview_tasks(self._job_payload(shot), folders)
            self.assertEqual([task["source_type"] for task in tasks], ["render"])

    def test_multiple_plates_are_queued_and_existing_plates_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            previews = Path(directory) / "renders/precomp/previews"
            previews.mkdir(parents=True)
            (previews / "sho010_plate_01_v001.mp4").touch()
            shot = {"id": 7, "title": "sho010", "base_path": directory,
                    "original_clip": "/plateA.mov", "original_clips": ["/plateA.mov", "/plateB.mov"]}
            tasks = _PreviewTaskHarness()._collect_preview_tasks(self._job_payload(shot), FakeFolders())
            self.assertEqual([(task["source_path"], task["plate_name"]) for task in tasks],
                             [("/plateB.mov", "plate_02")])

    def _job_payload(self, shot: dict) -> dict:
        return {
            "id": 1,
            "title": "JobA",
            "timelines": [
                {
                    "id": 10,
                    "title": "TimelineA",
                    "shots": [shot],
                }
            ],
        }

    def test_collect_preview_tasks_skips_existing_v001_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "sho010"
            preview_dir = shot_root / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            (preview_dir / "sho010_v001.mp4").write_bytes(b"preview")

            shot = {
                "id": 7,
                "title": "sho010",
                "base_path": str(shot_root),
                "original_clip": str(shot_root / "plates" / "V1.mov"),
                "preview_video": "",
            }

            tasks = _PreviewTaskHarness()._collect_preview_tasks(
                self._job_payload(shot),
                FakeFolders(),
            )

            self.assertEqual(tasks, [])

    def test_collect_preview_tasks_skips_existing_legacy_v01_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "sho010"
            preview_dir = shot_root / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            (preview_dir / "sho010_v01_preview.mp4").write_bytes(b"preview")

            shot = {
                "id": 7,
                "title": "sho010",
                "base_path": str(shot_root),
                "original_clip": str(shot_root / "plates" / "V1.mov"),
                "preview_video": "",
            }

            tasks = _PreviewTaskHarness()._collect_preview_tasks(
                self._job_payload(shot),
                FakeFolders(),
            )

            self.assertEqual(tasks, [])

    def test_collect_preview_tasks_skips_existing_plate_specific_v001_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "sho010"
            preview_dir = shot_root / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            (preview_dir / "sho010_plate_01_v001.mp4").write_bytes(b"preview")

            shot = {
                "id": 7,
                "title": "sho010",
                "base_path": str(shot_root),
                "original_clip": str(shot_root / "plates" / "V1.mov"),
                "preview_video": "",
            }

            tasks = _PreviewTaskHarness()._collect_preview_tasks(
                self._job_payload(shot),
                FakeFolders(),
            )

            self.assertEqual(tasks, [])

    def test_collect_preview_tasks_uses_exr_sequence_pattern_for_render(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_root = Path(tmpdir) / "sho010"
            shot_root.mkdir(parents=True)
            sequence_path = shot_root / "renders" / "comp" / "sho010_v005" / "sho010_v005_####.exr"

            shot = {
                "id": 7,
                "title": "sho010",
                "base_path": str(shot_root),
                "original_clip": "",
                "preview_video": "",
            }

            tasks = _PreviewTaskHarness()._collect_preview_tasks(
                self._job_payload(shot),
                FakeFolders(
                    {
                        str(shot_root): {
                            "type": "exr",
                            "version": 5,
                            "render_path": str(sequence_path).replace("####", "1001"),
                            "sequence_path": str(sequence_path),
                        }
                    }
                ),
            )

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["source_type"], "render")
            self.assertEqual(tasks[0]["version"], 5)
            self.assertEqual(tasks[0]["source_path"], str(sequence_path))

    def test_batch_preview_progress_combines_completed_mp4s_and_current_frames(self):
        value, maximum = page_nukedash._batch_preview_progress_value(
            task_index=1,
            task_count=4,
            current_frame=25,
            total_frames=100,
        )

        self.assertEqual(maximum, 4000)
        self.assertEqual(value, 1250)

        completed_value, completed_maximum = (
            page_nukedash._batch_preview_progress_value(
                task_index=4,
                task_count=4,
            )
        )
        self.assertEqual(completed_maximum, 4000)
        self.assertEqual(completed_value, 4000)


if __name__ == "__main__":
    unittest.main()
