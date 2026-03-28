from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import import_xml_v2 as module


class ImportXmlV2GeneratorTests(unittest.TestCase):
    def test_create_shot_folder_copies_clips_to_vfx_plates_and_uses_v001_script(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clip_dir_a = root / "source_a"
            clip_dir_b = root / "source_b"
            clip_dir_a.mkdir()
            clip_dir_b.mkdir()
            clip_a = clip_dir_a / "plate.mov"
            clip_b = clip_dir_b / "plate.mov"
            clip_a.write_bytes(b"clip-a")
            clip_b.write_bytes(b"clip-b")

            shot = module.ParsedShot(
                index=0,
                name="seq010",
                clips=[
                    module.ParsedClip(
                        name="plate-a",
                        filepath=str(clip_a),
                        in_point=0,
                        out_point=1,
                        duration=10,
                        start_frame=1001,
                        end_frame=1010,
                        track=1,
                    ),
                    module.ParsedClip(
                        name="plate-b",
                        filepath=str(clip_b),
                        in_point=0,
                        out_point=1,
                        duration=10,
                        start_frame=1001,
                        end_frame=1010,
                        track=2,
                    ),
                ],
                duration=10,
                edit_inpoint=1001,
                edit_outpoint=1010,
            )

            generator = module.NukeScriptGenerator()
            shot_folder = Path(generator.create_shot_folder(shot, str(root), "timeline_A"))

            self.assertEqual(shot_folder, root / "VFX" / "timeline_A" / "seq010")
            self.assertTrue((shot_folder / "plates").is_dir())
            self.assertFalse((shot_folder / "seq010.txt").exists())

            copied_paths = shot.original_clip_paths
            self.assertEqual(len(copied_paths), 2)
            self.assertEqual(Path(copied_paths[0]).name, "plate.mov")
            self.assertEqual(Path(copied_paths[1]).name, "plate_2.mov")
            self.assertTrue(Path(copied_paths[0]).exists())
            self.assertTrue(Path(copied_paths[1]).exists())
            self.assertEqual(shot.original_clip_name, copied_paths[0])

            script_path = Path(generator.create_nuke_script(shot, str(shot_folder), str(root), template_path=None))
            self.assertEqual(script_path.name, "seq010_v001.nk")
            contents = script_path.read_text(encoding="utf-8")
            self.assertIn('file "../plates/plate.mov"', contents)
            self.assertIn('file "../plates/plate_2.mov"', contents)
            self.assertIn("Copied plate paths:", contents)

    def test_shot_name_supports_longer_prefix_with_existing_010_style(self):
        page = module.XMLImportPage.__new__(module.XMLImportPage)
        page.shot_prefix = "longprefix"
        page.shot_padding = 3

        self.assertEqual(module.XMLImportPage._shot_name(page, 1), "longprefix010")
        self.assertEqual(module.XMLImportPage._shot_name(page, 12), "longprefix120")


if __name__ == "__main__":
    unittest.main()
