from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication

import import_xml_v2 as module


class ImportXmlV2GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
            self.assertTrue((root / "VFX" / "Job_Assets").is_dir())
            self.assertTrue((root / "VFX" / "timeline_A" / "Timeline_Assets").is_dir())
            self.assertTrue((shot_folder / "Shot_Assets").is_dir())
            self.assertFalse((shot_folder / "assets").exists())
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
            self.assertEqual(request.colourspace, generator.colourspace)
            self.assertEqual(Path(request.template_path).name, "template_current.nk")
            self.assertEqual(Path(request.primary_plate_path).name, "plate.mov")
            self.assertEqual([Path(path).name for path in request.extra_plate_paths], ["plate_2.mov"])
            self.assertEqual(request.dn_exr_file, "../renders/precomp/seq010_DN_v001/seq010_DN_v001_####.exr")
            self.assertEqual(request.comp_mov_file, "../renders/comp/seq010_v001.mov")
            self.assertEqual(request.comp_exr_file, "../renders/comp/seq010_v001/seq010_v001_####.exr")
            self.assertEqual(request.preview_mp4_file, "../renders/precomp/previews/seq010_v001.mp4")

    def test_single_shot_folder_structure_uses_new_assets_scaffold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            timeline_dir = root / "VFX" / "timeline_A"
            timeline_dir.mkdir(parents=True)
            page = module.XMLImportPage.__new__(module.XMLImportPage)

            shot_folder = Path(
                module.XMLImportPage._create_single_shot_folder_structure(
                    page,
                    str(timeline_dir),
                    "sho010",
                    None,
                )
            )

            self.assertEqual(shot_folder, timeline_dir / "sho010")
            self.assertTrue((root / "VFX" / "Job_Assets").is_dir())
            self.assertTrue((timeline_dir / "Timeline_Assets").is_dir())
            self.assertTrue((shot_folder / "Shot_Assets").is_dir())
            self.assertFalse((shot_folder / "assets").exists())

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

    def test_build_parsed_shot_from_clip_files_orders_tracks_and_uses_primary_range(self):
        page = module.XMLImportPage.__new__(module.XMLImportPage)

        with mock.patch.object(
            module.ThumbnailGenerator,
            "get_video_duration",
            side_effect=[12, 12, 8, 15],
        ):
            shot = module.XMLImportPage._build_parsed_shot_from_clip_files(
                page,
                shot_name="seq010",
                clip_file_paths=[
                    "/tmp/V1.mov",
                    "/tmp/V2.mov",
                    "/tmp/V3.mov",
                ],
            )

        self.assertEqual(shot.name, "seq010")
        self.assertEqual(shot.duration, 12)
        self.assertEqual(shot.edit_inpoint, 1001)
        self.assertEqual(shot.edit_outpoint, 1012)
        self.assertEqual([clip.track for clip in shot.clips], [1, 2, 3])
        self.assertEqual([Path(clip.filepath).name for clip in shot.clips], ["V1.mov", "V2.mov", "V3.mov"])
        self.assertEqual(shot.primary_clip.filepath, "/tmp/V1.mov")
        self.assertEqual(shot.clips[1].duration, 8)
        self.assertEqual(shot.clips[2].duration, 15)

    def test_single_shot_dialog_infers_job_name_above_vfx_or_nuke(self):
        dialog = module.SingleShotCreationDialog(add_to_db=True)
        try:
            job_name, timeline_name = dialog._infer_job_and_timeline_from_output_dir("/jobs/C000_Job/VFX/timeline_A")
            self.assertEqual(job_name, "C000_Job")
            self.assertEqual(timeline_name, "timeline_A")

            job_name, timeline_name = dialog._infer_job_and_timeline_from_output_dir("/jobs/C000_Job/Nuke/timeline_B")
            self.assertEqual(job_name, "C000_Job")
            self.assertEqual(timeline_name, "timeline_B")

            job_name, timeline_name = dialog._infer_job_and_timeline_from_output_dir("/jobs/C000_Job/VFX")
            self.assertEqual(job_name, "C000_Job")
            self.assertIsNone(timeline_name)

            dialog._on_folder_path_changed("/jobs/C000_Job/VFX/timeline_A")
            self.assertEqual(dialog.job_name_line_edit.text(), "C000_Job")
            self.assertEqual(dialog.timeline_name_line_edit.text(), "timeline_A")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_single_shot_dialog_accepts_selected_input_transform(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clip_path = root / "primary.mov"
            clip_path.write_bytes(b"clip")

            default_colourspace = module.COLOURSPACE_LIST[0]
            selected_colourspace = (
                module.COLOURSPACE_LIST[1]
                if len(module.COLOURSPACE_LIST) > 1
                else default_colourspace
            )

            dialog = module.SingleShotCreationDialog(
                add_to_db=False,
                colourspace=default_colourspace,
            )
            try:
                self.assertEqual(dialog.colourspace_combo_box.currentText(), default_colourspace)

                dialog._add_clip_paths([str(clip_path)], start_index=0)
                dialog.output_directory_path_line_edit.setText(str(root))
                dialog.shot_name_line_edit.setText("sho010")
                dialog.colourspace_combo_box.setCurrentText(selected_colourspace)

                dialog._validate_and_accept()

                self.assertEqual(dialog.colourspace, selected_colourspace)
                self.assertEqual(dialog.selected_clip_file_paths, [str(clip_path)])
            finally:
                dialog.close()
                dialog.deleteLater()
                self.app.processEvents()

    def test_single_shot_dialog_clip_slots_expand_compact_and_cap_at_five(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dialog = module.SingleShotCreationDialog(add_to_db=False)
            try:
                clips = []
                for index in range(6):
                    clip_path = root / f"clip_{index + 1}.mov"
                    clip_path.write_bytes(b"clip")
                    clips.append(str(clip_path))

                self.assertEqual(sum(1 for slot in dialog.clip_slots if not slot.isHidden()), 1)

                dialog._add_clip_paths(clips[:2], start_index=0)
                self.assertEqual(dialog.selected_clip_file_paths, clips[:2])
                self.assertEqual(sum(1 for slot in dialog.clip_slots if not slot.isHidden()), 3)
                self.assertEqual(dialog.clip_slots[0].role_label.text(), "Primary")
                self.assertEqual(dialog.clip_slots[1].role_label.text(), "V2")
                self.assertEqual(dialog.clip_slots[2].role_label.text(), "V3")

                dialog._add_clip_paths(clips[2:], start_index=2)
                self.assertEqual(dialog.selected_clip_file_paths, clips[:5])
                self.assertEqual(sum(1 for slot in dialog.clip_slots if not slot.isHidden()), 5)
                self.assertEqual(dialog.clip_slots[4].role_label.text(), "V5")

                dialog._clear_clip_slot(1)
                self.assertEqual(dialog.selected_clip_file_paths, [clips[0], clips[2], clips[3], clips[4]])
                self.assertEqual(dialog.clip_slots[1].path_line_edit.text(), clips[2])
                self.assertEqual(dialog.clip_slots[2].path_line_edit.text(), clips[3])
                self.assertEqual(dialog.clip_slots[3].path_line_edit.text(), clips[4])
                self.assertEqual(sum(1 for slot in dialog.clip_slots if not slot.isHidden()), 5)
                self.assertEqual(dialog.clip_slots[4].path_line_edit.text(), "")
            finally:
                dialog.close()
                dialog.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
