from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cg_asset_helpers as helpers
import houdini_cg_worker as worker
from asset_paths import asset_path


@pytest.mark.parametrize("name", ["", "..", "../test", "a/b", "a\\b", "a:", "CON", "nul.hip", "a.", "bad\x00name"])
def test_invalid_asset_names(name):
    with pytest.raises(ValueError):
        helpers.validate_asset_name(name)


def test_publish_legacy_path_lazy_creation_and_exclusive_name(tmp_path):
    legacy = tmp_path / "Job_Assets"
    legacy.mkdir()
    assets = asset_path(tmp_path, "job_assets")
    prepared = tmp_path / "prepared.hip"
    prepared.write_bytes(b"saved Houdini scene")
    assert not (assets / "cg").exists()
    project = helpers.publish_scene(prepared, assets, "Oak")
    assert project == legacy / "cg" / "Oak" / "Oak_v001.hip"
    assert project.read_bytes() == prepared.read_bytes()
    assert (project.parent / "geo").is_dir()
    assert (project.parent / "assets").is_dir()
    with pytest.raises(FileExistsError):
        helpers.publish_scene(prepared, assets, "Oak")
    with pytest.raises(FileExistsError):
        helpers.publish_scene(prepared, assets, "oak")
    assert project.read_bytes() == b"saved Houdini scene"
    assert not (tmp_path / "job_assets").exists()


def test_publish_failure_only_removes_owned_content(tmp_path):
    assets = tmp_path / "job_assets"
    assets.mkdir()
    unrelated = assets / "keep.txt"
    unrelated.write_text("keep")
    prepared = tmp_path / "prepared.hip"
    prepared.write_bytes(b"hip")

    def fail_copy(source, output):
        output.write(b"partial")
        raise OSError("disk full")

    with mock.patch.object(helpers.shutil, "copyfileobj", side_effect=fail_copy):
        with pytest.raises(OSError, match="disk full"):
            helpers.publish_scene(prepared, assets, "Oak")
    assert unrelated.read_text() == "keep"
    assert not (assets / "cg").exists()
    with pytest.raises(RuntimeError):
        helpers.publish_scene(tmp_path / "missing.hip", assets, "Oak")
    assert not (assets / "cg").exists()


def test_publish_preserves_concurrent_unowned_files(tmp_path):
    prepared = tmp_path / "prepared.hip"
    prepared.write_bytes(b"hip")
    assets = tmp_path / "job_assets"
    unowned = assets / "cg" / "Oak" / "notes.txt"

    def fail_copy(source, output):
        unowned.write_text("someone else's work")
        raise OSError("disk full")

    with mock.patch.object(helpers.shutil, "copyfileobj", side_effect=fail_copy):
        with pytest.raises(OSError):
            helpers.publish_scene(prepared, assets, "Oak")
    assert unowned.read_text() == "someone else's work"
    assert not (unowned.parent / "Oak_v001.hip").exists()


def test_project_versions_use_numeric_order_and_one_button_per_asset(tmp_path):
    assets = tmp_path / "job_assets"
    folder = assets / "cg" / "Oak"
    folder.mkdir(parents=True)
    for name in ("Oak_v001.hip", "Oak_v010.hip", "Oak_v9.hip", "Oak_v099.hiplc", "other_v999.hip"):
        (folder / name).touch()
    assert helpers.list_cg_projects(assets) == [folder / "Oak_v010.hip"]


def test_existing_legacy_cg_is_reused_when_lowercase_root_also_exists(tmp_path):
    canonical = tmp_path / "job_assets"
    canonical.mkdir()
    legacy_cg = tmp_path / "Job_Assets" / "cg"
    project = legacy_cg / "Oak" / "Oak_v001.hip"
    project.parent.mkdir(parents=True)
    project.touch()
    assert helpers.asset_destination(canonical, "Birch") == legacy_cg / "Birch"
    assert helpers.list_cg_projects(canonical) == [project]


def test_camera_discovery_uses_configured_and_legacy_paths_without_execution(tmp_path):
    assets = tmp_path / "Job_Assets"
    shot = tmp_path / "VFX" / "timeline" / "shot"
    roots = [tmp_path / "custom_job", tmp_path / "custom_timeline", tmp_path / "custom_shot",
             shot / "Shot_Assets" / "matchmove"]
    expected = []
    for index, root in enumerate(roots):
        export = root / "export"
        export.mkdir(parents=True)
        path = export / f"camera{index}_hou.py"
        path.write_text("raise AssertionError('discovery must not execute scripts')")
        expected.append(path)
        (export / "not_a_houdini_export.py").touch()
    payload = {"timelines": [{"matchmove_path": "mapped:timeline", "shots": [
        {"base_path": str(shot), "matchmove_path": str(roots[2])},
        {"base_path": str(shot)},
        {"base_path": str(shot), "matchmove_path": str(roots[2])},
    ]}]}
    found = helpers.discover_camera_exports(payload, assets,
        lambda path: str(roots[1]) if path == "mapped:timeline" else path, roots[0])
    assert found == expected


def test_resolve_same_installation_and_do_not_ignore_invalid_explicit_setting(tmp_path, monkeypatch):
    suffix = ".exe" if os.name == "nt" else ""
    bin_dir = tmp_path / "Houdini Install" / "bin"
    bin_dir.mkdir(parents=True)
    houdini, hython = (bin_dir / (name + suffix) for name in ("houdini", "hython"))
    for executable in (houdini, hython):
        executable.touch()
        executable.chmod(0o755)
    monkeypatch.setenv("HFS", str(bin_dir.parent))
    assert helpers.resolve_houdini() == (houdini, hython)
    with pytest.raises(FileNotFoundError):
        helpers.resolve_houdini(str(tmp_path / "missing"))
    hython.unlink()
    monkeypatch.setenv("PATH", "")
    with pytest.raises(FileNotFoundError):
        helpers.resolve_houdini(str(houdini))


def test_worker_environment_excludes_personal_startup_and_uses_versioned_prefs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUDINI_PATH", "/personal/tools;&")
    monkeypatch.setenv("HSITE", "/studio")
    monkeypatch.setenv("PYTHONPATH", "/personal/python")
    env = helpers.worker_environment(Path("/installation/bin/hython"), tmp_path)
    assert env["HOUDINI_PATH"] == "/installation/houdini"
    assert env["HOUDINI_SCRIPT_PATH"] == "/installation/houdini/scripts"
    assert "__HVER__" in env["HOUDINI_USER_PREF_DIR"]
    assert str(tmp_path) in env["HOUDINI_USER_PREF_DIR"]
    assert env["HOUDINI_NO_ENV_FILE"] == "1"
    assert "PYTHONPATH" not in env and "HSITE" not in env
    assert env["HOUDINI_TEMP_DIR"] == str(tmp_path)


def test_launch_passes_saved_scene_directly_bypassing_new_scene_picker(tmp_path):
    project = tmp_path / "Oak_v001.hip"
    project.touch()
    with mock.patch.object(helpers, "resolve_houdini", return_value=(Path("/houdini"), Path("/hython"))), \
            mock.patch.object(helpers.subprocess, "Popen") as launch:
        helpers.open_scene(project, "configured")
    assert launch.call_args.args[0] == ["/houdini", str(project)]
    assert launch.call_args.kwargs["env"]["JOB"] == str(tmp_path)
    project.unlink()
    with pytest.raises(FileNotFoundError):
        helpers.open_scene(project)


class Key:
    def __init__(self, hou, time=0, value=0):
        self.hou, self.time, self.value = hou, time, value

    def copy(self):
        return Key(self.hou, self.time, self.value)

    def frame(self):
        return self.time * self.hou.fps + 1

    def setFrame(self, frame):
        self.time = (frame - 1) / self.hou.fps

    def setTime(self, time):
        self.time = time

    def setValue(self, value):
        self.value = value


class Parm:
    def __init__(self, node, name):
        self.node, self.name, self.keys = node, name, []

    def keyframes(self):
        return tuple(key.copy() for key in self.keys)

    def deleteAllKeyframes(self):
        self.keys = []

    def setKeyframes(self, keys):
        self.keys = [key.copy() for key in keys]

    def setKeyframe(self, key):
        self.keys.append(key.copy())

    def path(self):
        return self.node.path() + "/" + self.name


class Node:
    def __init__(self, hou, parent, kind, name):
        self.hou, self.parent, self.kind, self.name = hou, parent, kind, name
        self.child_nodes, self.parameters, self.metadata = [], {}, {}

    def createNode(self, kind, name):
        node = Node(self.hou, self, kind, name)
        self.child_nodes.append(node)
        return node

    def path(self):
        return (self.parent.path() if self.parent else "") + "/" + self.name

    def type(self):
        return SimpleNamespace(name=lambda: self.kind)

    def parms(self):
        return list(self.parameters.values())

    def parm(self, name):
        return self.parameters.setdefault(name, Parm(self, name))

    def children(self):
        return tuple(self.child_nodes)

    def allSubChildren(self):
        return tuple(node for child in self.child_nodes for node in [child, *child.allSubChildren()])

    def setName(self, name, unique_name=False):
        candidate, index = name, 2
        while any(node != self and node.name == candidate for node in self.parent.children()):
            candidate, index = f"{name}{index}", index + 1
        self.name = candidate

    def node(self, name):
        return next((node for node in self.children() if node.name == name), None)

    def collapseIntoSubnet(self, roots, subnet_name):
        subnet = self.createNode("subnet", subnet_name)
        for node in roots:
            self.child_nodes.remove(node)
            subnet.child_nodes.append(node)
            node.parent = subnet
        return subnet

    def setUserData(self, key, value):
        self.metadata[key] = value

    def setComment(self, text):
        self.comment = text

    def layoutChildren(self):
        pass


class Hou:
    def __init__(self):
        self.fps = 25
        self.obj = Node(self, None, "obj", "obj")

    def node(self, path):
        assert path == "/obj"
        return self.obj

    def setFps(self, fps):
        self.fps = fps

    def Keyframe(self):
        return Key(self)


def write_export(path, first, last, fps, *, multiple_roots=False):
    path.write_text(f'''import hou
hou.setFps({fps})
root = hou.node('/obj').createNode('subnet', 'camera_root')
cam = root.createNode('cam', 'camera')
for node in [root, cam]:
    for frame in [{first}, {last}]:
        key = hou.Keyframe()
        key.setFrame(frame)
        key.setValue(frame)
        node.parm('tx').setKeyframe(key)
''' + ("hou.node('/obj').createNode('null', '3DEScene')\n" if multiple_roots else ""))


def test_import_groups_roots_shifts_whole_hierarchy_and_preserves_prior_cameras(tmp_path, monkeypatch):
    hou = Hou()
    monkeypatch.setitem(sys.modules, "hou", hou)
    source = tmp_path / "camera_hou.py"
    write_export(source, 1, 80, 24, multiple_roots=True)
    summary = worker.import_camera(hou, source, 25)
    assert summary["duration"] == 80  # two sparse keys still span 80 frames
    assert summary["first_frame"] == 1
    assert len(hou.obj.children()) == 1
    container = hou.obj.children()[0]
    assert container.kind == "subnet"
    assert len(container.children()) == 2
    before = worker.snapshot_keys([container])
    assert [round(key.frame()) for _, keys in before for key, _ in keys] == [1001, 1080, 1001, 1080]
    assert container.metadata["3de_camera_source"] == str(source)
    # Repeated filenames and a different export FPS must not overwrite/rescale the first import.
    write_export(source, 2001, 2100, 30)
    second = worker.import_camera(hou, source, 50)
    assert second["duration"] == 100
    assert len(hou.obj.children()) == 2
    assert hou.obj.children()[0].name != hou.obj.children()[1].name
    for root, end in zip(hou.obj.children(), [1080, 1100]):
        frames = [round(frame) for _, keys in worker.snapshot_keys([root]) for _, frame in keys]
        assert min(frames) == 1001 and max(frames) == end
    assert hou.fps == 50


def test_static_camera_requires_manual_duration_and_missing_camera_fails(tmp_path):
    hou = Hou()
    source = tmp_path / "static.py"
    source.write_text("hou.node('/obj').createNode('cam', 'camera')")
    assert worker.import_camera(hou, source, 25)["duration"] is None
    source.write_text("hou.node('/obj').createNode('null', 'not_camera')")
    with pytest.raises(ValueError, match="did not create a camera"):
        worker.import_camera(hou, source, 25)
    assert helpers.longest_duration([{"duration": 80}, {"duration": 120}]) == 120
    assert helpers.longest_duration([{"duration": 80}, {"duration": None}]) is None


def test_export_executes_once_and_does_not_retry_partial_failure(tmp_path):
    source = tmp_path / "broken.py"
    source.write_text("hou.node('/obj').createNode('cam', 'camera')\nraise ValueError('broken')")
    hou = Hou()
    with pytest.raises(ValueError, match="broken"):
        worker.import_camera(hou, source, 25)
    assert len(hou.obj.children()) == 1


def test_floating_point_frame_noise_does_not_add_an_extra_frame():
    summary = worker.animation_summary([(None, [(None, 1001.00000000001), (None, 1100.0000000001)])])
    assert summary["duration"] == 100


def test_single_expression_keys_do_not_extend_camera_animation_bounds():
    channels = [(None, [(None, 1)]), (None, [(None, 1001), (None, 1120)])]
    assert worker.animation_summary(channels) == {
        "first_frame": 1001, "last_frame": 1120, "duration": 120}
    assert worker.animation_summary([(None, [(None, 1)])])["duration"] is None


def test_final_save_preserves_key_frames_sets_job_and_both_ranges(tmp_path):
    hou = Hou()
    root = hou.obj.createNode("cam", "camera")
    for frame in (1001, 1120):
        key = hou.Keyframe()
        key.setFrame(frame)
        root.parm("tx").setKeyframe(key)
    hou.licenseCategoryType = SimpleNamespace(Commercial="commercial")
    hou.licenseCategory = lambda: "commercial"
    hou.playbar = mock.Mock()
    hou.putenv = mock.Mock()
    hou.setFrame = mock.Mock()
    hou.hipFile = mock.Mock()
    output = tmp_path / "saved.hip"
    hou.hipFile.save.side_effect = lambda path, **kwargs: Path(path).write_bytes(b"hip")
    result = worker.run_request(hou, dict(mode="save", fps=50, duration=80,
        destination="/job_assets/cg/Oak", output=str(output)))
    hou.playbar.setFrameRange.assert_called_once_with(1001, 1080)
    hou.playbar.setPlaybackRange.assert_called_once_with(1001, 1080)
    hou.setFrame.assert_called_once_with(1001)
    hou.putenv.assert_called_once_with("JOB", "/job_assets/cg/Oak")
    assert [round(key.frame()) for key in root.parm("tx").keyframes()] == [1001, 1120]
    assert result["output"] == str(output) and output.read_bytes() == b"hip"


def test_worker_refuses_noncommercial_or_missing_scene_output(tmp_path):
    hou = Hou()
    hou.licenseCategoryType = SimpleNamespace(Commercial="commercial")
    hou.licenseCategory = lambda: "indie"
    request = dict(mode="prepare", fps=25, sources=[], output=str(tmp_path / "scene.hip"))
    with pytest.raises(RuntimeError, match="commercial"):
        worker.run_request(hou, request)
    hou.licenseCategory = lambda: "commercial"
    hou.hipFile = mock.Mock()  # A successful API return without a file is not success.
    with pytest.raises(RuntimeError, match="expected .hip"):
        worker.run_request(hou, request)
