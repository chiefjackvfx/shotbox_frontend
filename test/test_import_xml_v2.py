from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import import_xml_v2 as module


class ImportXmlV2GeneratorTests(unittest.TestCase):
    def test_create_shot_folder_copies_clips_and_builds_template_request(self):
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

            captured_request = {}

            def fake_generate_from_template(request, nuke_path=None, on_output=None):
                captured_request["request"] = request
                Path(request.script_path).write_text("# template nk", encoding="utf-8")
                return module.create_nk.GenerateNkResult(
                    success=True,
                    script_path=request.script_path,
                    stdout_lines=["Created script"],
                )

            generator = module.NukeScriptGenerator(nuke_path="/configured/Nuke")
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

            with mock.patch.object(
                module.create_nk,
                "generate_from_template",
                side_effect=fake_generate_from_template,
            ):
                script_path = Path(generator.create_nuke_script(shot, str(shot_folder), str(root)))

            self.assertEqual(script_path.name, "seq010_v001.nk")
            self.assertTrue(script_path.is_file())

            request = captured_request["request"]
            self.assertEqual(request.script_path, str(script_path))
            self.assertEqual(request.shot_dir, str(shot_folder))
            self.assertEqual(request.shot_name, "seq010")
            self.assertEqual(request.frame_first, 1001)
            self.assertEqual(request.frame_last, 1010)
            self.assertEqual(request.edit_inpoint, 1001)
            self.assertEqual(request.edit_outpoint, 1010)
            self.assertEqual(request.project_name, root.name)
            self.assertEqual(request.artist_name, "Jack")
            self.assertEqual(Path(request.template_path).name, "template_current.nk")
            self.assertEqual(Path(request.primary_plate_path).name, "plate.mov")
            self.assertEqual([Path(path).name for path in request.extra_plate_paths], ["plate_2.mov"])
            self.assertEqual(request.dn_exr_file, "../renders/precomp/seq010_DN_v001/seq010_DN_v001_####.exr")
            self.assertEqual(request.comp_mov_file, "../renders/comp/seq010_v001.mov")
            self.assertEqual(request.comp_exr_file, "../renders/comp/seq010_v001/seq010_v001_####.exr")
            self.assertEqual(request.preview_mp4_file, "../renders/precomp/previews/seq010_v001.mp4")

    def test_create_nuke_script_surfaces_create_nk_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clip_dir = root / "source"
            clip_dir.mkdir()
            clip_path = clip_dir / "plate.mov"
            clip_path.write_bytes(b"clip")

            shot = module.ParsedShot(
                index=0,
                name="seq010",
                clips=[
                    module.ParsedClip(
                        name="plate",
                        filepath=str(clip_path),
                        in_point=0,
                        out_point=1,
                        duration=10,
                        start_frame=1001,
                        end_frame=1010,
                        track=1,
                    ),
                ],
                duration=10,
                edit_inpoint=1001,
                edit_outpoint=1010,
            )

            generator = module.NukeScriptGenerator()
            shot_folder = Path(generator.create_shot_folder(shot, str(root), "timeline_A"))

            with mock.patch.object(
                module.create_nk,
                "generate_from_template",
                return_value=module.create_nk.GenerateNkResult(
                    success=False,
                    script_path=str(shot_folder / "scripts" / "seq010_v001.nk"),
                    error="Template is missing required nodes: WriteCompMP4",
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Template is missing required nodes: WriteCompMP4"):
                    generator.create_nuke_script(shot, str(shot_folder), str(root))

    def test_shot_name_supports_longer_prefix_with_existing_010_style(self):
        page = module.XMLImportPage.__new__(module.XMLImportPage)
        page.shot_prefix = "longprefix"
        page.shot_padding = 3

        self.assertEqual(module.XMLImportPage._shot_name(page, 1), "longprefix010")
        self.assertEqual(module.XMLImportPage._shot_name(page, 12), "longprefix120")


if __name__ == "__main__":
    unittest.main()
