import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import app_cache
import denoise_template as template


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source with spaces.custom"
        self.source.write_bytes(b"Reader support is tested by Nuke, not an extension whitelist")
        self.media = {"source": str(self.source), "shot_dir": str(self.root / "shot"), "shot_name": "sho010"}

    def test_width_limit_never_upscales_and_preserves_portrait_dimensions(self):
        for size, expected in [((8192, 4320), (4096, 2160)), ((4096, 2160), (4096, 2160)),
                               ((1920, 1080), (1920, 1080)), ((2160, 8192), (2160, 8192)),
                               ((6000, 4001), (4096, 2731))]:
            self.assertEqual(template.output_size(*size), expected)

    def test_requests_copy_template_and_preserve_source_and_template(self):
        directory = self.root / "worker"
        directory.mkdir()
        original = template.digest(template.TEMPLATE)
        request = json.loads(template.create_template_request(directory, self.media, "/nuke").read_text())
        self.assertEqual(request["workflow"], "template")
        self.assertEqual(request["write_node"], "Write1")
        self.assertEqual(template.digest(template.TEMPLATE), original)
        self.assertEqual(template.digest(request["script"]), original)
        self.assertNotEqual(request["script"], str(template.TEMPLATE))
        self.assertTrue(request["output_pattern"].endswith("sho010_DN_v001_####.exr"))
        self.assertNotIn("output_script", request)
        self.assertNotIn("output_sha256", request)

    def test_shot_idt_reaches_request_and_is_applied_before_probe_validation(self):
        colourspace = "ARRI LogC3 (EI800)"
        media = {**self.media, "colourspace": colourspace}
        directory = self.root / "worker"
        directory.mkdir()
        request = json.loads(template.create_template_request(directory, media, "/nuke").read_text())
        self.assertEqual(request["colourspace"], colourspace)
        nuke = mock.Mock()
        read, reformat, noise, write, probe, root = [mock.MagicMock() for _ in range(6)]
        for node in (read, probe):
            knobs = {name: mock.Mock() for name in ("file", "colorspace", "first", "last", "origfirst", "origlast", "origset")}
            node.__getitem__.side_effect = knobs.__getitem__
        for node, cls in ((read, "Read"), (reformat, "Reformat"), (write, "Write"), (noise, "OFXcom.absoft.NeatVideo6_v6")):
            node.Class.return_value = cls
        nuke.toNode.side_effect = {"Read1": read, "Reformat1": reformat, "Write1": write}.__getitem__
        nuke.allNodes.return_value = [read, reformat, noise, write]
        reformat.input.return_value, noise.input.return_value, write.input.return_value = read, reformat, noise
        nuke.nodes.Read.return_value = probe
        nuke.root.return_value = root
        probe["first"].value.return_value = 1
        probe["last"].value.return_value = 2
        probe.width.return_value, probe.height.return_value, probe.pixelAspect.return_value = 1920, 1080, 1
        probe.metadata.return_value = {}
        # Reproduce the movie reader's invalid legacy default. Validation must
        # see the shot IDT, not rec709; fromUserText may reset the colourspace.
        probe_colourspace = "rec709"
        def set_colourspace(value):
            nonlocal probe_colourspace
            probe_colourspace = value
        probe["colorspace"].setValue.side_effect = set_colourspace
        def validate():
            self.assertEqual(probe_colourspace, colourspace)
            return False
        probe.error.side_effect = validate
        read["colorspace"].value.return_value = colourspace
        result = template.prepare(nuke, request)
        probe.error.assert_called_once()
        read["colorspace"].setValue.assert_called_once_with(colourspace)
        self.assertEqual(result["read_colorspace"], colourspace)
        nuke.delete.assert_called_once_with(probe)

    def test_windows_requests_full_gui_while_linux_keeps_qt_terminal(self):
        for platform, qt_terminal in (("win32", False), ("linux", True)):
            with self.subTest(platform=platform), mock.patch.object(template.sys, "platform", platform):
                directory = self.root / platform
                directory.mkdir()
                request = json.loads(template.create_template_request(directory, self.media, "/nuke").read_text())
                self.assertEqual(request["qt_terminal"], qt_terminal)
                self.assertTrue(request["topdown"])
                self.assertEqual(request.get("windows_display"), "hidden" if platform == "win32" else None)

    def test_windows_display_can_be_forced_visible(self):
        with mock.patch.object(template.sys, "platform", "win32"), \
                mock.patch.dict(template.os.environ, {"SHOTBOX_DENOISE_WINDOWS_DISPLAY": "visible"}):
            request = json.loads(template.create_template_request(self.root, self.media, "/nuke").read_text())
        self.assertEqual(request["windows_display"], "visible")

    def test_overwrite_defaults_to_latest_and_new_versions_are_distinct(self):
        first = template.output_directory(self.media)
        (first / "keep.txt").write_text("unrelated output")
        self.assertEqual(first, template.output_directory(self.media))
        second = template.output_directory({**self.media, "mode": "new"})
        third = template.output_directory({**self.media, "mode": "new"})
        self.assertEqual(second.name, "sho010_DN_v002")
        self.assertEqual(third.name, "sho010_DN_v003")
        self.assertEqual(template.output_directory(self.media), third)
        self.assertEqual((first / "keep.txt").read_text(), "unrelated output")

    def test_multiple_plates_have_separate_versions(self):
        a = template.output_directory({**self.media, "plate": "plate_01"})
        b = template.output_directory({**self.media, "plate": "plate_02"})
        self.assertNotEqual(a, b)
        self.assertTrue(a.name.endswith("plate_01_DN_v001"))
        self.assertTrue(b.name.endswith("plate_02_DN_v001"))

    def test_successful_shorter_overwrite_removes_only_stale_matching_frames(self):
        directory = template.output_directory(self.media)
        script = directory / (directory.name + ".nk")
        script.touch()
        for name in (script.stem + '_1001.exr', script.stem + '_1002.exr', 'other_1002.exr'):
            (directory / name).touch()
        template.remove_stale_frames(directory / (script.stem + "_####.exr"), 1001, 1001)
        self.assertTrue(script.exists())
        self.assertTrue((directory / (script.stem + '_1001.exr')).exists())
        self.assertFalse((directory / (script.stem + '_1002.exr')).exists())
        self.assertTrue((directory / 'other_1002.exr').exists())

    def test_collects_media_without_nuke_scripts_and_deduplicates_shots(self):
        files = mock.Mock()
        files.convert_path.side_effect = lambda path: path
        shot = {"id": 1, "base_path": str(self.root / "shot"), "title": "sho010",
                "original_clips": [str(self.source), str(self.source)], "hidden": True,
                "colourspace": "ARRI LogC3 (EI800)"}
        missing = {"id": 2, "base_path": str(self.root / "other"), "original_clip": str(self.root / "absent")}
        tasks, skipped = template.collect_tasks({"timelines": [{"shots": [shot, missing]}, {"shots": [shot]}]}, files)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(skipped, 1)
        self.assertEqual(tasks[0]["media"]["source"], str(self.source))
        self.assertIsNone(tasks[0]["media"]["plate"])
        self.assertEqual(tasks[0]["media"]["colourspace"], shot["colourspace"])
        files.latest_nk.assert_not_called()

    def test_non_exr_sequence_patterns_and_missing_frames(self):
        for number in (42, 43, 44):
            (self.root / f"image.{number:04d}.dpx").touch()
        for pattern in ("image.####.dpx", "image.%04d.dpx"):
            source, first, last = template.resolve_source(str(self.root / pattern))
            self.assertEqual((first, last), (42, 44))
        (self.root / "image.0043.dpx").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing frames"):
            template.resolve_source(str(self.root / "image.####.dpx"))

    def test_selected_secondary_keeps_batch_identity_and_ignores_other_missing_sources(self):
        files = mock.Mock(convert_path=lambda path: path)
        shot = {"id": 1, "title": "sho010", "base_path": str(self.root / "shot"),
                "original_clips": [str(self.root / "missing.mov"), str(self.source), str(self.root / "also-missing.mov")]}
        job = {"timelines": [{"shots": [shot]}]}
        batch, skipped = template.collect_tasks(job, files)
        self.assertEqual(skipped, 2)
        selected, skipped = template.collect_tasks(job, files, source_path=str(self.source))
        self.assertEqual(skipped, 0)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0], batch[0])
        self.assertEqual(selected[0]["media"]["plate"], "plate_02")
        output = template.output_directory(selected[0]["media"])
        self.assertEqual(output.name, "sho010_plate_02_DN_v001")
        self.assertEqual(template.output_directory(batch[0]["media"]), output)

    def test_selected_clip_must_belong_to_current_shot_and_exist(self):
        files = mock.Mock(convert_path=lambda path: path)
        missing = str(self.root / "missing.mov")
        shot = {"base_path": str(self.root / "shot"), "original_clip": str(self.source),
                "original_clips": [missing]}
        job = {"timelines": [{"shots": [shot]}]}
        for path in (missing, str(self.source), str(self.root / "stale.mov")):
            with self.subTest(source=path):
                self.assertEqual(template.collect_tasks(job, files, source_path=path), ([], 1))

    def test_selected_path_alias_uses_same_deduplicated_batch_output(self):
        files = mock.Mock(convert_path=lambda path: path)
        (self.root / 'nested').mkdir()
        alias = str(self.root / 'nested' / '..' / self.source.name)
        shot = {'base_path': str(self.root / 'shot'), 'original_clips': [str(self.source), alias]}
        job = {'timelines': [{'shots': [shot]}]}
        batch, _ = template.collect_tasks(job, files)
        selected, skipped = template.collect_tasks(job, files, source_path=alias)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected, batch)
        self.assertEqual(selected[0]['media']['plate'], 'plate_01')

    def test_legacy_selected_clip_and_shared_sources_keep_separate_shot_outputs(self):
        files = mock.Mock(convert_path=lambda path: path)
        first = {"id": 1, "base_path": str(self.root / "one"),
                 "original_clips": [], "original_clip": str(self.source)}
        second = {**first, "id": 2, "base_path": str(self.root / "two")}
        alias = {**first, "id": 3}
        tasks, skipped = template.collect_tasks({"timelines": [{"shots": [first, second, alias, first]}]},
                                               files, source_path=str(self.source))
        self.assertEqual(skipped, 0)
        self.assertEqual([task["shot"]["id"] for task in tasks], [1, 2])
        self.assertTrue(all(task["media"]["plate"] is None for task in tasks))


if __name__ == "__main__":
    unittest.main()
