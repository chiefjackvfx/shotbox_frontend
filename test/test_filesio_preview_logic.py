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

import filesIO


class FilesIOPreviewLogicTests(unittest.TestCase):
    def test_latest_render_info_prefers_exr_when_versions_tie(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            renders_dir = shot_dir / "renders" / "comp"
            exr_dir = renders_dir / "sho010_v001"
            exr_dir.mkdir(parents=True)
            (renders_dir / "sho010_v001.mov").write_bytes(b"mov")
            (exr_dir / "sho010_v001_1001.exr").write_bytes(b"exr")

            info = filesIO.Folders().latest_render_info(str(shot_dir))

            self.assertIsNotNone(info)
            self.assertEqual(info["type"], "exr")
            self.assertEqual(info["version"], 1)
            self.assertEqual(info["sequence_path"], str(exr_dir / "sho010_v001_####.exr"))

    def test_latest_render_info_still_uses_highest_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            renders_dir = shot_dir / "renders" / "comp"
            exr_dir = renders_dir / "sho010_v001"
            exr_dir.mkdir(parents=True)
            (exr_dir / "sho010_v001_1001.exr").write_bytes(b"exr")
            (renders_dir / "sho010_v002.mov").write_bytes(b"mov")

            info = filesIO.Folders().latest_render_info(str(shot_dir))

            self.assertIsNotNone(info)
            self.assertEqual(info["type"], "mov")
            self.assertEqual(info["version"], 2)

    def test_latest_preview_accepts_legacy_preview_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            legacy_preview = preview_dir / "sho010_v01_preview.mp4"
            legacy_preview.write_bytes(b"legacy")

            preview_path, preview_name = filesIO.Folders().latest_preview(str(shot_dir))

            self.assertEqual(preview_path, str(legacy_preview))
            self.assertEqual(preview_name, legacy_preview.name)

    def test_latest_preview_prefers_new_name_on_same_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            legacy_preview = preview_dir / "sho010_v01_preview.mp4"
            new_preview = preview_dir / "sho010_v001.mp4"
            legacy_preview.write_bytes(b"legacy")
            new_preview.write_bytes(b"new")

            preview_path, preview_name = filesIO.Folders().latest_preview(str(shot_dir))

            self.assertEqual(preview_path, str(new_preview))
            self.assertEqual(preview_name, new_preview.name)

    def test_latest_preview_accepts_plate_specific_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            plate_preview = preview_dir / "sho010_plate_01_v001.mp4"
            plate_preview.write_bytes(b"preview")

            preview_path, preview_name = filesIO.Folders().latest_preview(str(shot_dir))

            self.assertEqual(preview_path, str(plate_preview))
            self.assertEqual(preview_name, plate_preview.name)

    def test_latest_preview_prefers_newer_plate_preview_when_versions_tie(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / "renders" / "precomp" / "previews"
            preview_dir.mkdir(parents=True)
            older_preview = preview_dir / "sho010_plate_01_v001.mp4"
            newer_preview = preview_dir / "sho010_plate_02_v001.mp4"
            older_preview.write_bytes(b"older")
            newer_preview.write_bytes(b"newer")
            os.utime(older_preview, (1000, 1000))
            os.utime(newer_preview, (2000, 2000))

            preview_path, preview_name = filesIO.Folders().latest_preview(str(shot_dir))

            self.assertEqual(preview_path, str(newer_preview))
            self.assertEqual(preview_name, newer_preview.name)


if __name__ == "__main__":
    unittest.main()
