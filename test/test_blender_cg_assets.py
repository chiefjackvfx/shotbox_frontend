"""Blender dialog and optional real-worker regression coverage."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from cg_asset_dialog import CgAssetDialog
import cg_asset_helpers as helpers


def test_blender_discovery_and_open_latest(tmp_path):
    export = tmp_path / "matchmove" / "export"
    export.mkdir(parents=True)
    source = export / "cam_blender.py"
    source.touch()
    assert helpers.discover_camera_exports({}, tmp_path, job_matchmove=export.parent,
                                           include_blender=True) == [source]
    prepared = tmp_path / "prepared.blend"
    prepared.write_bytes(b"test")
    root = tmp_path / "cg"
    first = helpers.publish_scene(prepared, root, "Test")
    latest = first.with_name("Test_v010.blend")
    latest.touch()
    first.with_name("Test_v99.blend1").touch()
    assert helpers.list_cg_projects(root) == [latest]
    with mock.patch.object(helpers, "resolve_blender", return_value=Path("/blender")), \
            mock.patch.object(helpers.subprocess, "Popen") as opened:
        helpers.open_scene(latest)
    assert opened.call_args.args[0] == ["/blender", str(latest)]


@pytest.mark.parametrize("with_cameras", [False, True])
def test_blender_dialog_round_trip(tmp_path, monkeypatch, with_cameras):
    blender = os.environ.get("SHOTBOX_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("Set SHOTBOX_TEST_BLENDER to run the background Blender round trip")
    app = QApplication.instance() or QApplication([])
    work = tmp_path / "work"
    work.mkdir()
    sources = []
    for name in ("A", "B"):
        path = tmp_path / f"{name}_blender.py"
        path.write_text(f"""import bpy
scene = bpy.context.scene
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Lens"))
rig = bpy.data.objects.new("Rig", None)
scene.collection.objects.link(cam)
scene.collection.objects.link(rig)
cam.parent = rig
cam['source'] = '{name}'
bg = cam.data.background_images.new()
bg.image_user.frame_start = 21
for frame in (21, 31, 41):
    rig.location.x = frame
    rig.keyframe_insert(data_path='location', frame=frame)
    cam.data.lens = frame
    cam.data.keyframe_insert(data_path='lens', frame=frame)
""")
        sources.append(path)
    dialog = CgAssetDialog(tmp_path / "cg", sources, "", work)
    warnings = []
    monkeypatch.setattr("cg_asset_dialog.QMessageBox.warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr("cg_asset_dialog.get_settings_manager", lambda: {"blender_exe_path": blender})
    try:
        assert dialog.camera_list.count() == 0
        dialog.application.setCurrentText("Blender")
        assert dialog.camera_list.count() == 2
        dialog.name_edit.setText("Camera")
        dialog.fps_spin.setValue(23.976)
        if with_cameras:
            for index in (1, 0):
                dialog.camera_list.item(index).setCheckState(Qt.CheckState.Checked)
        else:
            dialog.duration_spin.setValue(21)
        dialog.create_button.click()
        deadline = time.monotonic() + 45
        while not dialog.project_path and not warnings and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not warnings
        assert dialog.project_path and dialog.project_path.name == "Camera_v001.blend"
        check = """import bpy
s = bpy.context.scene
assert (s.frame_start, s.frame_end, s.frame_current) == (1001, 1021, 1001)
assert abs(s.render.fps / s.render.fps_base - 23.976) < 0.0001
"""
        if with_cameras:
            check += """assert s.camera['source'] == 'B'
cams = [o for o in s.objects if o.type == 'CAMERA']
assert len(cams) == 2 and cams[0].users_collection != cams[1].users_collection
for t in (1001, 1011, 1021):
    s.frame_set(t)
    for cam in cams:
        assert abs(cam.matrix_world.translation.x - (t - 980)) < 0.0001
        assert abs(cam.data.lens - (t - 980)) < 0.0001
        assert cam.data.background_images[0].image_user.frame_start == 1001
"""
        else:
            check += "assert len(s.objects) == 0"
        subprocess.run([blender, "--background", "--factory-startup", str(dialog.project_path),
                        "--python-exit-code", "1", "--python-expr", check], check=True, timeout=45)
    finally:
        dialog.stop_worker()
        dialog.close()
