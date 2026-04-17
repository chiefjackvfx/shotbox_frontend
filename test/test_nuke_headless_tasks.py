from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import re
import unittest
from unittest import mock


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import nuke_headless_tasks as module


class _FakeNodeRef:
    def __init__(self, name="Node1"):
        self._name = name

    def name(self):
        return self._name


class _FakeKnob:
    def __init__(self, name, value=None, node=None):
        self._name = name
        self._value = value
        self._node = node or _FakeNodeRef("KnobNode")
        self.user_text = None

    def setValue(self, value, index=None):
        if index is None:
            self._value = value
            if self._name == "name" and hasattr(self._node, "_set_name"):
                self._node._set_name(str(value))
            return
        if not isinstance(self._value, list):
            self._value = []
        while len(self._value) <= index:
            self._value.append(None)
        self._value[index] = value

    def value(self):
        return self._value

    def name(self):
        return self._name

    def node(self):
        return self._node

    def fromUserText(self, value):
        self.user_text = value
        self._value = value


class _FakeReadFileKnob(_FakeKnob):
    def __init__(self, name, node, media_ranges):
        super().__init__(name, None, node)
        self._media_ranges = media_ranges

    def fromUserText(self, value):
        self.user_text = value
        lookup_value = value
        explicit_range = None
        if isinstance(value, str):
            parts = value.rsplit(" ", 1)
            if len(parts) == 2 and re.match(r"^\d+-\d+$", parts[1]):
                lookup_value = parts[0]
                start_text, end_text = parts[1].split("-", 1)
                explicit_range = (int(start_text), int(end_text))
        self._value = lookup_value
        first, last = explicit_range or self._media_ranges.get(value) or self._media_ranges.get(lookup_value, (1, 1))
        self._node["first"].setValue(first)
        self._node["last"].setValue(last)


class _FakeNode:
    DEFAULT_KNOBS = (
        "name",
        "file",
        "first",
        "last",
        "origfirst",
        "origlast",
        "origset",
        "message",
        "colorspace",
        "projecttext",
        "artisttext",
        "commenttext",
        "startfr",
        "tc1",
        "frame",
        "first_frame",
        "last_frame",
        "fps",
        "colorManagement",
        "OCIO_config",
        "workingSpaceLUT",
        "monitorOutLUT",
        "input.first",
        "input.first_lock",
        "input.last",
        "input.last_lock",
        "output.first",
        "output.first_lock",
        "output.last",
        "output.last_lock",
        "file_type",
        "create_directories",
        "checkHashOnRead",
        "mov64_quality",
        "ocioColorspace",
        "display",
        "view",
    )

    def __init__(self, name, *, media_ranges=None):
        self._name = name
        self._knobs = {}
        for knob_name in self.DEFAULT_KNOBS:
            knob = _FakeKnob(knob_name, None, self)
            if name == "Read1" and knob_name == "file":
                knob = _FakeReadFileKnob(knob_name, self, media_ranges or {})
            self._knobs[knob_name] = knob
        self._knobs["name"].setValue(name)

    def _set_name(self, name):
        self._name = name

    def __getitem__(self, key):
        return self._knobs[key]

    def knobs(self):
        return self._knobs

    def name(self):
        return self._name


class _FakeGroupNode(_FakeNode):
    def __init__(self, name, nuke, inner_nodes=None):
        super().__init__(name)
        self._nuke = nuke
        self.inner_nodes = inner_nodes or []

    def begin(self):
        self._nuke.current_group = self

    def end(self):
        self._nuke.current_group = None


class _FakeNuke:
    def __init__(self, media_ranges):
        self.media_ranges = media_ranges
        self.current_group = None
        self.opened = None
        self.executed = None
        self.root_node = _FakeNode("Root")
        self.top_nodes = [
            _FakeNode("Read1", media_ranges=media_ranges),
            _FakeNode("Retime1"),
            _FakeNode("WriteCompMP4"),
            _FakeNode("Text11"),
        ]
        readtime = _FakeNode("readtime")
        self.top_nodes.append(_FakeGroupNode("MS_slate_overlay", self, inner_nodes=[readtime]))

    def root(self):
        return self.root_node

    def scriptOpen(self, path):
        self.opened = path

    def toNode(self, name):
        pool = self.current_group.inner_nodes if self.current_group is not None else self.top_nodes
        for node in pool:
            if node.name() == name:
                return node
        return None

    def execute(self, node, first, last):
        self.executed = (node.name(), first, last)


class FindNukeExecutableTests(unittest.TestCase):
    def test_custom_directory_returns_nuke_binary_inside_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir) / "Nuke16.0v8"
            install_dir.mkdir()
            nuke_binary = install_dir / "Nuke16.0"
            nuke_binary.write_text("", encoding="utf-8")

            with mock.patch.object(module.platform, "system", return_value="Linux"):
                with mock.patch.object(module.shutil, "which", return_value=None):
                    found = module.find_nuke_executable([str(install_dir)])

            self.assertEqual(found, str(nuke_binary))

    def test_custom_file_path_is_returned_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nuke_binary = Path(tmpdir) / "Nuke16.0"
            nuke_binary.write_text("", encoding="utf-8")

            with mock.patch.object(module.shutil, "which", return_value=None):
                found = module.find_nuke_executable([str(nuke_binary)])

            self.assertEqual(found, str(nuke_binary))


class PreviewPathLogicTests(unittest.TestCase):
    def test_build_preview_output_path_uses_v001_and_detects_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / module.PreviewConfig.PREVIEW_SUBDIR
            preview_dir.mkdir(parents=True)
            (preview_dir / "sho010_v01_preview.mp4").write_bytes(b"legacy")

            output_path, version, file_exists = module.build_preview_output_path(
                str(shot_dir),
                "sho010",
                version=1,
            )

            self.assertEqual(output_path.name, "sho010_v001.mp4")
            self.assertEqual(version, 1)
            self.assertTrue(file_exists)
            self.assertTrue(module.preview_version_exists(str(shot_dir), "sho010", 1))

    def test_build_preview_output_path_auto_increments_across_new_and_legacy_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / module.PreviewConfig.PREVIEW_SUBDIR
            preview_dir.mkdir(parents=True)
            (preview_dir / "sho010_v001.mp4").write_bytes(b"new")
            (preview_dir / "sho010_v02_preview.mp4").write_bytes(b"legacy")

            output_path, version, file_exists = module.build_preview_output_path(
                str(shot_dir),
                "sho010",
            )

            self.assertEqual(output_path.name, "sho010_v003.mp4")
            self.assertEqual(version, 3)
            self.assertFalse(file_exists)

    def test_build_preview_output_path_uses_plate_name_and_detects_existing_plate_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / module.PreviewConfig.PREVIEW_SUBDIR
            preview_dir.mkdir(parents=True)
            (preview_dir / "sho010_plate_01_v001.mp4").write_bytes(b"preview")

            output_path, version, file_exists = module.build_preview_output_path(
                str(shot_dir),
                "sho010",
                version=1,
                plate_name="plate_01",
            )

            self.assertEqual(output_path.name, "sho010_plate_01_v001.mp4")
            self.assertEqual(version, 1)
            self.assertTrue(file_exists)
            self.assertTrue(module.preview_version_exists(str(shot_dir), "sho010", 1))
            self.assertTrue(
                module.preview_version_exists(str(shot_dir), "sho010", 1, plate_name="plate_01")
            )

    def test_build_precomp_exr_output_path_uses_plate_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)

            sequence_path, output_dir, file_exists = module.build_precomp_exr_output_path(
                str(shot_dir),
                "sho010",
                version=1,
                plate_name="plate_02",
            )

            self.assertEqual(output_dir.name, "sho010_plate_02_v01")
            self.assertEqual(sequence_path.name, "sho010_plate_02_v01_####.exr")
            self.assertFalse(file_exists)

    def test_preview_input_exists_accepts_exr_sequence_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            (shot_dir / "render.1001.exr").write_bytes(b"frame1")
            (shot_dir / "render.1002.exr").write_bytes(b"frame2")

            self.assertTrue(module.preview_input_exists(str(shot_dir / "render.####.exr")))

    def test_preview_input_exists_accepts_missing_explicit_frame_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            (shot_dir / "sho010_v001_1001.exr").write_bytes(b"frame1")
            (shot_dir / "sho010_v001_1002.exr").write_bytes(b"frame2")

            self.assertTrue(module.preview_input_exists(str(shot_dir / "sho010_v001_0001.exr")))

    def test_resolve_preview_sequence_input_rebuilds_missing_frame_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            (shot_dir / "sho010_v001_1001.exr").write_bytes(b"frame1")
            (shot_dir / "sho010_v001_1002.exr").write_bytes(b"frame2")

            sequence_path, first, last = module.resolve_preview_sequence_input(
                str(shot_dir / "sho010_v001_0001.exr")
            )

            self.assertEqual(Path(sequence_path).name, "sho010_v001_####.exr")
            self.assertEqual((first, last), (1001, 1002))

    def test_resolve_preview_input_colourspace_uses_render_media_type(self):
        self.assertEqual(
            module.resolve_preview_input_colourspace(
                source_type="render",
                media_type="exr",
                input_path="/tmp/render.####.exr",
                requested_colourspace="AlexaV3LogC",
            ),
            "ACES - ACEScg",
        )
        self.assertEqual(
            module.resolve_preview_input_colourspace(
                source_type="render",
                media_type="mov",
                input_path="/tmp/render.mov",
                requested_colourspace="AlexaV3LogC",
            ),
            "sRGB",
        )
        self.assertEqual(
            module.resolve_preview_input_colourspace(
                source_type="original_clip",
                media_type="mov",
                input_path="/tmp/plate.mov",
                requested_colourspace="AlexaV3LogC",
            ),
            "AlexaV3LogC",
        )

    def test_generate_preview_with_overwrite_removes_legacy_version_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_dir = Path(tmpdir)
            preview_dir = shot_dir / module.PreviewConfig.PREVIEW_SUBDIR
            preview_dir.mkdir(parents=True)
            legacy_preview = preview_dir / "sho010_v01_preview.mp4"
            legacy_preview.write_bytes(b"legacy")
            generator = module.PreviewGenerator()

            with mock.patch.object(
                generator,
                "generate_preview",
                return_value=module.PreviewResult(success=True, output_path=preview_dir / "sho010_v001.mp4"),
            ) as mocked_generate:
                result = generator.generate_preview_with_overwrite(
                    input_path="/tmp/input.mov",
                    shot_dir=str(shot_dir),
                    shot_name="sho010",
                    version=1,
                )

            self.assertTrue(result.success)
            self.assertFalse(legacy_preview.exists())
            mocked_generate.assert_called_once()


class PreviewTemplateEditorTests(unittest.TestCase):
    def test_template_editor_updates_template_for_mov_input(self):
        template = module.get_preview_template_path()
        media_ranges = {"/tmp/clip.mov": (1, 24)}
        nuke = _FakeNuke(media_ranges)

        first, last = module._PreviewTemplateEditor(nuke).render(
            template_path=str(template),
            input_path="/tmp/clip.mov",
            output_path="/tmp/sho010_v001.mp4",
            shot_name="sho010",
            project="ProjectA",
            artist="Jack",
            colourspace="sRGB",
            fps=24,
            quality="medium",
        )

        self.assertEqual(nuke.opened, str(template))
        self.assertEqual((first, last), (1001, 1024))
        self.assertEqual(nuke.executed, ("WriteCompMP4", 1001, 1024))
        self.assertEqual(nuke.toNode("Read1")["file"].value(), "/tmp/clip.mov")
        self.assertEqual(nuke.toNode("Read1")["colorspace"].value(), "Utility - sRGB - Texture")
        self.assertEqual(nuke.toNode("WriteCompMP4")["file"].value(), "/tmp/sho010_v001.mp4")
        self.assertEqual(nuke.toNode("WriteCompMP4")["mov64_quality"].value(), "Medium")
        self.assertEqual(nuke.toNode("Text11")["message"].value(), "")
        self.assertEqual(nuke.root()["frame"].value(), 1001)
        self.assertEqual(nuke.root()["first_frame"].value(), 1001)
        self.assertEqual(nuke.root()["last_frame"].value(), 1024)
        self.assertEqual(nuke.root()["colorManagement"].value(), "OCIO")
        self.assertEqual(
            nuke.root()["OCIO_config"].value(),
            module.PreviewConfig.OCIO_CONFIG_NAME,
        )
        self.assertEqual(nuke.root()["workingSpaceLUT"].value(), "ACES - ACEScg")
        self.assertEqual(nuke.root()["name"].value(), "sho010_v001.nk")
        self.assertEqual(nuke.toNode("WriteCompMP4")["colorspace"].value(), "color_picking")
        self.assertEqual(nuke.toNode("WriteCompMP4")["ocioColorspace"].value(), "scene_linear")
        self.assertEqual(nuke.toNode("WriteCompMP4")["display"].value(), "ACES")
        self.assertEqual(nuke.toNode("WriteCompMP4")["view"].value(), "sRGB")
        retime = nuke.toNode("Retime1")
        self.assertEqual(retime["input.first"].value(), 1)
        self.assertEqual(retime["input.last"].value(), 24)
        self.assertEqual(retime["output.first"].value(), 1001)
        self.assertEqual(retime["output.last"].value(), 1024)

        slate = nuke.toNode("MS_slate_overlay")
        self.assertEqual(slate["projecttext"].value(), "ProjectA")
        self.assertEqual(slate["artisttext"].value(), "Jack")
        self.assertEqual(slate["commenttext"].value(), "")
        self.assertEqual(slate["startfr"].value(), 1001)
        slate.begin()
        try:
            self.assertEqual(nuke.toNode("readtime")["tc1"].value(), "00:00:00:23")
        finally:
            slate.end()

    def test_template_editor_keeps_native_exr_frame_range(self):
        template = module.get_preview_template_path()
        media_ranges = {"/tmp/render.####.exr 1001-1100": (1001, 1100), "/tmp/render.####.exr": (1001, 1100)}
        nuke = _FakeNuke(media_ranges)

        first, last = module._PreviewTemplateEditor(nuke).render(
            template_path=str(template),
            input_path="/tmp/render.####.exr",
            output_path="/tmp/sho010_v005.mp4",
            shot_name="sho010",
            project="ProjectA",
            artist="Jack",
            colourspace="ACES - ACEScg",
            fps=25,
            quality="high",
        )

        self.assertEqual((first, last), (1001, 1100))
        self.assertEqual(nuke.executed, ("WriteCompMP4", 1001, 1100))
        self.assertEqual(nuke.toNode("Text11")["message"].value(), "")
        self.assertEqual(nuke.root()["first_frame"].value(), 1001)
        self.assertEqual(nuke.root()["last_frame"].value(), 1100)
        self.assertEqual(
            nuke.root()["OCIO_config"].value(),
            module.PreviewConfig.OCIO_CONFIG_NAME,
        )
        self.assertEqual(nuke.toNode("Read1")["colorspace"].value(), "ACES - ACEScg")
        self.assertEqual(nuke.toNode("WriteCompMP4")["mov64_quality"].value(), "High")


if __name__ == "__main__":
    unittest.main()
