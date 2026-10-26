import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from shotbox_matte.core import CancelledError, JobOptions, pad_frames, padded_frame_count, plan_job, run_job, trim_frames
from shotbox_matte.imaging import read_exr, write_exr
from shotbox_matte.sequences import SequenceInfo


class CorePlanningTests(unittest.TestCase):
    def sequence(self, count=20):
        return SequenceInfo("shot.#.exr", Path("/tmp/source_v001"),
                            tuple(Path(f"/tmp/source_v001/shot.{i}.exr") for i in range(1001, 1001 + count)),
                            tuple(range(1001, 1001 + count)), 1920, 1080)

    def test_twenty_frames_pad_to_twenty_five_and_trim(self):
        frames = np.arange(20, dtype=np.float32).reshape(20, 1, 1, 1)
        padded = pad_frames(frames)
        self.assertEqual(padded.shape[0], 25)
        np.testing.assert_array_equal(padded[-6:], 19)
        np.testing.assert_array_equal(trim_frames(padded, 20), frames)
        self.assertEqual(padded_frame_count(1), 1)
        self.assertEqual(padded_frame_count(121), 121)
        with self.assertRaises(ValueError):
            trim_frames(padded, 26)

    def test_dry_plan_has_no_output_side_effect_or_native_import(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "shot_v001"
            folder.mkdir()
            seq = self.sequence()
            seq = SequenceInfo(seq.id, folder, seq.files, seq.frame_numbers, seq.width, seq.height)
            with patch("shotbox_matte.core.discover_sequences", return_value=[seq]):
                before = set(sys.modules)
                plan = plan_job(JobOptions(str(folder)))
                self.assertNotIn("shotbox_matte.native", set(sys.modules) - before)
                self.assertEqual(plan.padded_count, 25)
                self.assertEqual(plan.first_frame, 1001)
                self.assertEqual(plan.added_copies, 5)
                self.assertFalse(plan.output_preview.exists())
                json.dumps(plan.to_dict())

    def test_long_shots_are_segmented_with_native_frame_and_duration_limits(self):
        with patch("shotbox_matte.core.discover_sequences", return_value=[self.sequence(146)]):
            plan = plan_job(JobOptions("unused"))
            self.assertEqual(plan.selected_count, 146)
            self.assertEqual([(chunk.start, chunk.stop, chunk.padded_count) for chunk in plan.chunks],
                             [(0, 121, 121), (104, 146, 49)])
        with patch("shotbox_matte.core.discover_sequences", return_value=[self.sequence(25)]):
            plan = plan_job(JobOptions("unused", fps=2))
            self.assertTrue(all((chunk.padded_count - 1) / 2 <= 6 for chunk in plan.chunks))
        with patch("shotbox_matte.core.discover_sequences", return_value=[self.sequence(251)]):
            with self.assertRaisesRegex(ValueError, "250-frame shot limit"):
                plan_job(JobOptions("unused"))

    def test_range_plan_preserves_absolute_indices_numbering_and_provenance(self):
        with patch("shotbox_matte.core.discover_sequences", return_value=[self.sequence(20)]):
            plan = plan_job(JobOptions("unused", source_start=1004, source_end=1017, frame_skip=2, frame_cap=3, fps=25))
        self.assertEqual(plan.selected_indices, (3, 6, 9))
        self.assertEqual(plan.source_numbers, (1004, 1007, 1010))
        self.assertEqual((plan.selected_count, plan.padded_count, plan.first_frame), (3, 9, 1004))
        data = plan.to_dict()
        self.assertEqual((data["source_start"], data["source_end"]), (1004, 1017))
        self.assertEqual((data["selected_source_start"], data["selected_source_end"]), (1004, 1010))
        self.assertEqual(data["source_range_mode"], "frame-number")
        self.assertEqual((data["fps"], data["frame_skip"]), (25, 2))

    def test_older_job_payload_defaults_to_all_source_frames(self):
        options = JobOptions(**{"input_path": "unused"})
        with patch("shotbox_matte.core.discover_sequences", return_value=[self.sequence(20)]):
            plan = plan_job(options)
        self.assertEqual(plan.selected_indices, tuple(range(20)))
        self.assertEqual((plan.to_dict()["source_start"], plan.to_dict()["source_end"]), (1001, 1020))
        self.assertIsNone(plan.to_dict()["requested_source_start"])

    def test_alpha_gen_prompt_is_a_fixed_recipe_record_not_a_job_option(self):
        from dataclasses import asdict

        options = JobOptions("unused")
        self.assertNotIn("prompt", asdict(options))
        with patch("shotbox_matte.core.discover_sequences", return_value=[self.sequence(20)]):
            self.assertEqual(plan_job(options).to_dict()["prompt"], "")
        with self.assertRaisesRegex(TypeError, "unexpected keyword argument 'prompt'"):
            JobOptions("unused", prompt="Foreground")

    def test_ambiguous_sequences_require_selection(self):
        sequences = [self.sequence(), self.sequence()]
        with patch("shotbox_matte.core.discover_sequences", return_value=sequences):
            with self.assertRaisesRegex(ValueError, "Multiple image"):
                plan_job(JobOptions("unused"))


@unittest.skipUnless(importlib.util.find_spec("OpenImageIO"), "OpenImageIO is not installed")
class CoreExecutionTests(unittest.TestCase):
    def make_source(self, folder, count=20):
        folder.mkdir()
        for number in range(1001, 1001 + count):
            write_exr(folder / f"source.{number}.exr", np.full((5, 8, 3), .18, dtype=np.float32), "float")

    def fake_native(self, engine):
        return SimpleNamespace(ModelConfig=SimpleNamespace(from_dict=lambda values: values), NativeAlphaEngine=engine)

    def test_real_exr_preprocess_and_export_with_mocked_model(self):
        captured = {}

        class Engine:
            def __init__(self, config, **kwargs):
                pass

            def infer(self, frames, **kwargs):
                captured["frames"] = frames.copy()
                for _ in frames:
                    result = np.full(frames.shape[1:], -8, dtype=np.float32)
                    result[12:52, :, 0] = 1.25  # 8x5 -> 64x40, padded vertically to 64.
                    yield result

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "shot_v001"
            self.make_source(source)
            with patch.dict(sys.modules, {"shotbox_matte.native": self.fake_native(Engine)}):
                result = run_job(JobOptions(str(source), generation_width=64, generation_height=64, precision="float"))
            self.assertEqual(captured["frames"].shape, (25, 64, 64, 3))
            np.testing.assert_allclose(captured["frames"], .46135613, atol=1e-6)
            self.assertEqual(result["frame_count"], 20)
            output = Path(result["output_dir"])
            self.assertEqual(len(list(output.glob("*.exr"))), 20)
            matte = read_exr(result["first_output"])
            self.assertEqual(matte.shape, (5, 8, 3))
            np.testing.assert_array_equal(matte, 1.25)
            self.assertEqual(json.loads(Path(result["manifest"]).read_text())["status"], "complete")
            self.assertEqual({path.suffix for path in output.iterdir()}, {".exr"})
            self.assertTrue(all(path.is_file() for path in output.iterdir()))
            self.assertNotEqual(Path(result["job_dir"]), output)
            self.assertTrue(Path(result["source_preview"]).is_file())
            self.assertTrue(Path(result["matte_preview"]).is_file())
            self.assertEqual(Path(result["source_preview"]).parent, Path(result["manifest"]).parent)

    def test_cancel_marks_partial_and_preserves_overwrite_sequence(self):
        state = {"cancel": False, "closed": False}

        class Engine:
            def __init__(self, config, **kwargs):
                pass

            def infer(self, frames, **kwargs):
                try:
                    yield frames[0]
                    state["cancel"] = True
                    yield frames[0]
                finally:
                    state["closed"] = True

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "shot_v001"
            self.make_source(source, 2)
            output = source.with_name("shot_roto_v001")
            output.mkdir()
            old = output / "shot_roto_v001.1001.exr"
            old.write_bytes(b"original")
            (output / "_shotbox_job.json").write_text('{"status":"complete"}')
            options = JobOptions(str(source), generation_width=64, generation_height=64, existing="overwrite")
            with patch.dict(sys.modules, {"shotbox_matte.native": self.fake_native(Engine)}):
                with self.assertRaises(CancelledError):
                    run_job(options, cancelled=lambda: state["cancel"])
            self.assertTrue(state["closed"])
            self.assertEqual(old.read_bytes(), b"original")
            self.assertEqual(json.loads((output / "_shotbox_job.json").read_text())["status"], "complete")
            partials = list(output.parent.glob(".shotbox-partial-*"))
            self.assertEqual(len(partials), 1)
            manifests = list(Path(os.environ["SHOTBOX_JOB_DIR"]).glob("*/_shotbox_job.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text())
            self.assertEqual(manifest["status"], "cancelled")
            self.assertEqual(Path(manifest["stage_dir"]), partials[0])
            self.assertEqual({path.name for path in output.iterdir()}, {old.name, "_shotbox_job.json"})
            self.assertFalse(output.with_name(f".{output.name}.shotbox-lock").exists())


if __name__ == "__main__":
    unittest.main()
