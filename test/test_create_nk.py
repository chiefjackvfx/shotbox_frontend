from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import create_nk as module


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [f"{line}\n" for line in lines]
        self._index = 0

    def readline(self):
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        return ""


class _FakeProcess:
    def __init__(self, lines, returncode):
        self.stdout = _FakeStdout(lines)
        self.returncode = None
        self._final_returncode = returncode

    def poll(self):
        if self.stdout._index >= len(self.stdout._lines):
            return self._final_returncode
        return None

    def wait(self):
        self.returncode = self._final_returncode
        return self.returncode


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
        self.script_value = None
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

    def fromScript(self, value):
        self.script_value = value
        self._value = value


class _FakeNode:
    DEFAULT_KNOBS = (
        "name",
        "file",
        "colorspace",
        "label",
        "message",
        "projecttext",
        "artisttext",
        "startfr",
        "tc1",
        "frame",
        "first_frame",
        "last_frame",
        "fps",
        "format",
        "project_directory",
        "last",
        "origlast",
        "origset",
        "first",
        "origfirst",
        "input.last",
        "output.first",
        "output.first_lock",
        "output.last",
        "time",
        "xpos",
        "ypos",
        "selected",
        "file_type",
        "create_directories",
        "checkHashOnRead",
        "ocioColorspace",
        "display",
        "view",
    )

    def __init__(self, name, xpos=0, ypos=0):
        self._name = name
        self._knobs = {}
        self.inputs = {}
        for knob_name in self.DEFAULT_KNOBS:
            self._knobs[knob_name] = _FakeKnob(knob_name, None, self)
        self._knobs["name"].setValue(name)
        self._knobs["xpos"].setValue(xpos)
        self._knobs["ypos"].setValue(ypos)

    def _set_name(self, name):
        self._name = name

    def __getitem__(self, key):
        return self._knobs[key]

    def knobs(self):
        return self._knobs

    def setInput(self, index, node):
        self.inputs[index] = node

    def input(self, index):
        return self.inputs.get(index)

    def name(self):
        return self._name


class _FakeGroupNode(_FakeNode):
    def __init__(self, name, nuke, inner_nodes=None, xpos=0, ypos=0):
        super().__init__(name, xpos=xpos, ypos=ypos)
        self._nuke = nuke
        self.inner_nodes = inner_nodes or []

    def begin(self):
        self._nuke.current_group = self

    def end(self):
        self._nuke.current_group = None


class _FakeNodeFactory:
    def __init__(self, nuke):
        self._nuke = nuke

    def __getattr__(self, class_name):
        def creator():
            node = _FakeNode(f"{class_name}Generated")
            self._nuke.top_nodes.append(node)
            return node

        return creator


COMP_MOV_EXPR = "../renders/comp/[string range [basename [value root.name]] 0 end-3].mov"
COMP_EXR_EXPR = (
    "../renders/comp/[string range [basename [value root.name]] 0 end-3]/"
    "[string range [basename [value root.name]] 0 end-3]_####.exr"
)
PREVIEW_MP4_EXPR = "../renders/precomp/previews/[string range [basename [value root.name]] 0 end-3].mp4"


class _FakeNuke:
    def __init__(self, include_text10=False, include_text12=False, use_dynamic_output_paths=True):
        self.top_nodes = []
        self.current_group = None
        self.opened = None
        self.saved = None
        self.format_specs = []
        self.root_node = _FakeNode("Root")
        self.nodes = _FakeNodeFactory(self)

        self.top_nodes.extend(
            [
                _FakeNode("BackdropINFO", xpos=-771, ypos=68),
                _FakeNode("Read1", xpos=282, ypos=87),
                _FakeNode("Retime1", xpos=282, ypos=255),
                _FakeNode("WriteDN1", xpos=180, ypos=993),
                _FakeNode("ReadDN1", xpos=180, ypos=1071),
                _FakeNode("WriteCompMov", xpos=400, ypos=2667),
                _FakeNode("WriteCompEXR", xpos=620, ypos=2667),
                _FakeNode("ReadCompMov", xpos=400, ypos=2751),
                _FakeNode("RetimePreview1", xpos=400, ypos=2895),
                _FakeNode("ReadCompEXR", xpos=620, ypos=3111),
                _FakeNode("WriteCompMP4", xpos=620, ypos=3339),
            ]
        )

        if include_text10:
            self.top_nodes.append(_FakeNode("Text10", xpos=620, ypos=3270))
        if include_text12:
            self.top_nodes.append(_FakeNode("Text12", xpos=620, ypos=3420))

        readtime = _FakeNode("readtime")
        slate = _FakeGroupNode("MS_slate_overlay", self, inner_nodes=[readtime], xpos=620, ypos=3274)
        self.top_nodes.append(slate)

        if use_dynamic_output_paths:
            self.toNode("WriteCompMov")["file"].setValue(COMP_MOV_EXPR)
            self.toNode("ReadCompMov")["file"].setValue(COMP_MOV_EXPR)
            self.toNode("WriteCompEXR")["file"].setValue(COMP_EXR_EXPR)
            self.toNode("ReadCompEXR")["file"].setValue(COMP_EXR_EXPR)
            self.toNode("WriteCompMP4")["file"].setValue(PREVIEW_MP4_EXPR)

    def root(self):
        return self.root_node

    def addFormat(self, spec):
        self.format_specs.append(spec)

    def scriptOpen(self, path):
        self.opened = path

    def scriptSaveAs(self, path, overwrite=0):
        self.saved = (path, overwrite)

    def toNode(self, name):
        pool = self.current_group.inner_nodes if self.current_group is not None else self.top_nodes
        for node in pool:
            if node.name() == name:
                return node
        return None

    def allNodes(self):
        nodes = list(self.top_nodes)
        for node in self.top_nodes:
            if isinstance(node, _FakeGroupNode):
                nodes.extend(node.inner_nodes)
        return nodes

    def createNode(self, class_name, inpanel=False):
        return getattr(self.nodes, class_name)()


class CreateNkTests(unittest.TestCase):
    def _request(self, root: Path, template_path: Path | None = None) -> module.GenerateNkRequest:
        template = template_path or root / "template_current.nk"
        primary_plate = root / "shot" / "plates" / "V1.mov"
        primary_plate.parent.mkdir(parents=True, exist_ok=True)
        primary_plate.write_bytes(b"primary")
        return module.GenerateNkRequest(
            template_path=str(template),
            script_path=str(root / "shot" / "scripts" / "seq010_v001.nk"),
            shot_dir=str(root / "shot"),
            shot_name="seq010",
            project_name="ProjectA",
            artist_name="Jack",
            fps=25.0,
            colourspace="Input - ARRI - V3 LogC (EI800) - Wide Gamut",
            duration=10,
            edit_inpoint=1001,
            edit_outpoint=1010,
            frame_first=1001,
            frame_last=1010,
            format_width=1920,
            format_height=1080,
            primary_plate_path=str(primary_plate),
            extra_plate_paths=[],
            dn_exr_file="../renders/precomp/seq010_DN_v001/seq010_DN_v001_####.exr",
            comp_mov_file="../renders/comp/seq010_v001.mov",
            comp_exr_file="../renders/comp/seq010_v001/seq010_v001_####.exr",
            preview_mp4_file="../renders/precomp/previews/seq010_v001.mp4",
        )

    def test_build_generate_command(self):
        command = module.build_generate_from_template_command("/opt/Nuke16.0", "/tmp/request.json")

        self.assertEqual(command[0], "/opt/Nuke16.0")
        self.assertEqual(command[1], "-t")
        self.assertEqual(command[3], "make_nk")
        self.assertEqual(command[4], "--request")
        self.assertEqual(command[5], "/tmp/request.json")
        self.assertTrue(command[2].endswith("create_nk.py"))

    def test_request_round_trip_preserves_values(self):
        request = module.GenerateNkRequest(
            template_path="/tmp/template_current.nk",
            script_path="/tmp/shot/scripts/seq010_v001.nk",
            shot_dir="/tmp/shot",
            shot_name="seq010",
            project_name="ProjectA",
            artist_name="Jack",
            fps=25.0,
            colourspace="ACES - ACEScg",
            duration=10,
            edit_inpoint=1001,
            edit_outpoint=1010,
            frame_first=1001,
            frame_last=1010,
            format_width=1920,
            format_height=1080,
            primary_plate_path="/tmp/shot/plates/V1.mov",
            extra_plate_paths=["/tmp/shot/plates/V2.mov"],
            dn_exr_file="../renders/precomp/seq010_DN_v001/seq010_DN_v001_####.exr",
            comp_mov_file="../renders/comp/seq010_v001.mov",
            comp_exr_file="../renders/comp/seq010_v001/seq010_v001_####.exr",
            preview_mp4_file="../renders/precomp/previews/seq010_v001.mp4",
        )

        restored = module.GenerateNkRequest.from_dict(json.loads(json.dumps(request.to_dict())))

        self.assertEqual(restored.template_path, request.template_path)
        self.assertEqual(restored.project_name, "ProjectA")
        self.assertEqual(restored.artist_name, "Jack")
        self.assertEqual(restored.extra_plate_paths, ["/tmp/shot/plates/V2.mov"])
        self.assertEqual(restored.comp_mov_file, request.comp_mov_file)

    def test_generate_from_template_prefers_configured_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)

            with mock.patch.object(module, "find_nuke_executable", return_value="/custom/Nuke16.0") as mocked_find:
                with mock.patch.object(module.subprocess, "Popen", return_value=_FakeProcess(["done"], 0)):
                    result = module.generate_from_template(request, nuke_path="/custom/install")

            self.assertTrue(result.success)
            mocked_find.assert_called_once_with(["/custom/install"])

    def test_generate_from_template_surfaces_subprocess_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)

            with mock.patch.object(module, "find_nuke_executable", return_value="/opt/Nuke16.0"):
                with mock.patch.object(module.subprocess, "Popen", return_value=_FakeProcess(["template failed"], 3)):
                    result = module.generate_from_template(request)

            self.assertFalse(result.success)
            self.assertIn("Nuke exited with code 3: template failed", result.error)
            self.assertTrue(any(line.startswith("REQUEST_JSON: ") for line in result.stdout_lines))

    def test_generate_from_template_fails_when_runtime_template_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            request = self._request(root, root / "missing_template.nk")

            with mock.patch.object(module, "find_nuke_executable", return_value="/opt/Nuke16.0"):
                result = module.generate_from_template(request)

            self.assertFalse(result.success)
            self.assertIn("Nuke template not found", result.error)

    def test_edit_opens_template_and_saves_script(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)
            nuke = _FakeNuke()

            output_path = module._TemplateEditor(nuke).edit(request)

            self.assertEqual(output_path, request.script_path)
            self.assertEqual(nuke.opened, str(template))
            self.assertEqual(nuke.saved, (request.script_path, 1))
            self.assertEqual(
                nuke.root_node["project_directory"].script_value,
                "[python {nuke.script_directory()}]",
            )
            self.assertEqual(nuke.root_node["format"].value(), "seq010_1920x1080")

    def test_edit_updates_direct_template_nodes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)
            nuke = _FakeNuke(include_text10=True, include_text12=True)

            module._TemplateEditor(nuke).edit(request)

            self.assertEqual(nuke.toNode("Read1")["file"].value(), "../plates/V1.mov")
            self.assertEqual(nuke.toNode("Read1")["colorspace"].value(), request.colourspace)
            self.assertEqual(nuke.toNode("Retime1")["output.first"].value(), 1001)
            self.assertEqual(nuke.toNode("WriteDN1")["file"].value(), request.dn_exr_file)
            self.assertEqual(nuke.toNode("ReadDN1")["file"].value(), request.dn_exr_file)
            self.assertEqual(nuke.toNode("WriteCompMov")["file"].value(), COMP_MOV_EXPR)
            self.assertEqual(nuke.toNode("ReadCompMov")["file"].value(), COMP_MOV_EXPR)
            self.assertEqual(nuke.toNode("WriteCompEXR")["file"].value(), COMP_EXR_EXPR)
            self.assertEqual(nuke.toNode("ReadCompEXR")["file"].value(), COMP_EXR_EXPR)
            self.assertEqual(nuke.toNode("WriteCompMP4")["file"].value(), PREVIEW_MP4_EXPR)

            slate = nuke.toNode("MS_slate_overlay")
            self.assertEqual(slate["projecttext"].value(), "ProjectA")
            self.assertEqual(slate["artisttext"].value(), "Jack")
            self.assertEqual(slate["startfr"].value(), 1001)
            slate.begin()
            try:
                self.assertEqual(nuke.toNode("readtime")["tc1"].value(), "00:00:00:09")
            finally:
                slate.end()

    def test_edit_updates_static_output_paths_for_legacy_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)
            nuke = _FakeNuke(use_dynamic_output_paths=False)

            module._TemplateEditor(nuke).edit(request)

            self.assertEqual(nuke.toNode("WriteCompMov")["file"].value(), request.comp_mov_file)
            self.assertEqual(nuke.toNode("ReadCompMov")["file"].value(), request.comp_mov_file)
            self.assertEqual(nuke.toNode("WriteCompEXR")["file"].value(), request.comp_exr_file)
            self.assertEqual(nuke.toNode("ReadCompEXR")["file"].value(), request.comp_exr_file)
            self.assertEqual(nuke.toNode("WriteCompMP4")["file"].value(), request.preview_mp4_file)

    def test_extra_plates_create_read_and_retime_pairs_with_fixed_spacing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)
            extra_a = root / "shot" / "plates" / "V2.mov"
            extra_b = root / "shot" / "plates" / "V3.mov"
            extra_a.write_bytes(b"v2")
            extra_b.write_bytes(b"v3")
            request.extra_plate_paths = [str(extra_a), str(extra_b)]

            nuke = _FakeNuke()
            module._TemplateEditor(nuke).edit(request)

            read1 = nuke.toNode("Read1")
            retime1 = nuke.toNode("Retime1")
            read2 = nuke.toNode("Read2")
            read3 = nuke.toNode("Read3")
            retime2 = nuke.toNode("Retime2")
            retime3 = nuke.toNode("Retime3")

            self.assertIsNotNone(read2)
            self.assertIsNotNone(read3)
            self.assertIsNotNone(retime2)
            self.assertIsNotNone(retime3)
            self.assertEqual(read2["xpos"].value(), read1["xpos"].value() + 150)
            self.assertEqual(read3["xpos"].value(), read1["xpos"].value() + 300)
            self.assertEqual(read2["ypos"].value(), read1["ypos"].value())
            self.assertEqual(read3["ypos"].value(), read1["ypos"].value())
            self.assertEqual(retime2["xpos"].value(), read2["xpos"].value())
            self.assertEqual(retime3["xpos"].value(), read3["xpos"].value())
            self.assertEqual(retime2["ypos"].value(), retime1["ypos"].value())
            self.assertEqual(retime3["ypos"].value(), retime1["ypos"].value())
            self.assertIs(retime2.input(0), read2)
            self.assertIs(retime3.input(0), read3)

    def test_edit_fails_when_required_template_node_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template_current.nk"
            template.write_text("# template", encoding="utf-8")
            request = self._request(root, template)
            nuke = _FakeNuke()
            nuke.top_nodes = [node for node in nuke.top_nodes if node.name() != "WriteCompMP4"]

            with self.assertRaisesRegex(RuntimeError, "Template is missing required nodes: WriteCompMP4"):
                module._TemplateEditor(nuke).edit(request)


if __name__ == "__main__":
    unittest.main()
