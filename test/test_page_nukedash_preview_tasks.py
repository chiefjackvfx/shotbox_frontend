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


if __name__ == "__main__":
    unittest.main()
