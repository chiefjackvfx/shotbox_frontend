"""Live commercial-Houdini acceptance check; all output stays in protected cache.

Run with ShotBox's Python, not hython. Supply real 3DE exports using --export.
Without exports this creates representative sparse and time-sampled cameras.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_cache
import cg_asset_helpers as helpers


@app_cache.temporary_cache_task
def validate(configured, exports):
    _, hython = helpers.resolve_houdini(configured)
    with app_cache.temporary_cache_lease(), tempfile.TemporaryDirectory(
        prefix="houdini-cg-acceptance-", dir=app_cache.cache_directory("temp")
    ) as directory:
        work = Path(directory)
        env = helpers.worker_environment(hython, work)
        sources = [Path(path).resolve() for path in exports]
        generated = not sources
        if generated:
            for number, (first, duration, fps) in enumerate([(1, 80, 24), (2001, 120, 30)]):
                source = work / f"camera{number}_hou.py"
                source.write_text(f'''import hou
hou.setFps({fps})
scene = hou.node('/obj').createNode('null', '3DEScene')
cam = hou.node('/obj').createNode('cam', 'camera')
cam.setFirstInput(scene)
for node in [scene, cam]:
    for frame in [{first}, {first + duration - 1}]:
        key = hou.Keyframe()
        key.setTime((frame - 1) / {fps})
        key.setValue(frame)
        node.parm('tx').setKeyframe(key)
''', encoding="utf-8")
                sources.append(source)

        def run(mode, **kwargs):
            output = work / f"{mode}.hip"
            request_path, result_path = work / f"{mode}.json", work / f"{mode}.result.json"
            request_path.write_text(json.dumps(dict(mode=mode, fps=25, output=str(output), **kwargs)), encoding="utf-8")
            result = subprocess.run(
                [str(hython), str(Path(__file__).parents[1] / "houdini_cg_worker.py"), str(request_path), str(result_path)],
                cwd=work, env=env, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(result.stdout[-6000:] + result.stderr[-6000:])
            print(result.stdout.strip())
            return json.loads(result_path.read_text(encoding="utf-8"))

        prepared = run("prepare", sources=[str(path) for path in sources])
        summaries = prepared["cameras"]
        if generated:
            assert [summary["duration"] for summary in summaries] == [80, 120], summaries
        duration = helpers.longest_duration(summaries)
        if duration is None:
            raise RuntimeError("The selected exports have no detectable animation duration.")
        assets = work / "job_assets"
        destination = helpers.asset_destination(assets, "Acceptance")
        saved = run("save", prepared=prepared["output"], duration=duration, destination=destination.as_posix())
        project = helpers.publish_scene(Path(saved["output"]), assets, "Acceptance")
        probe = work / "probe.py"
        report = work / "probe.json"
        probe.write_text('''import hou, json, sys
hou.hipFile.load(sys.argv[1], suppress_save_prompt=True)
obj = hou.node('/obj')
cameras = [node for node in obj.allSubChildren() if node.type().name() == 'cam']
ranges = []
for root in obj.children():
    frames = [key.frame() for node in [root, *root.allSubChildren()]
              for parm in node.parms() for keys in [parm.keyframes()]
              if len(keys) > 1 for key in keys]
    if frames:
        ranges.append([min(frames), max(frames)])
report = dict(fps=hou.fps(), frame_range=hou.playbar.frameRange(),
              playback_range=hou.playbar.playbackRange(), job=hou.getenv('JOB'),
              cameras=len(cameras), animation_ranges=ranges, ui=hou.isUIAvailable(),
              houdini_path=hou.houdiniPath())
with open(sys.argv[2], 'w') as stream:
    json.dump(report, stream)
''', encoding="utf-8")
        result = subprocess.run([str(hython), str(probe), str(project), str(report)],
                                cwd=work, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:] + result.stderr[-6000:])
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["fps"] == 25, data
        assert data["frame_range"] == [1001, 1001 + duration - 1], data
        assert data["playback_range"] == data["frame_range"], data
        assert Path(data["job"]) == project.parent, data
        assert data["cameras"] >= len(sources), data
        assert not data["ui"], data
        assert all(abs(first - 1001) < 1e-5 for first, _ in data["animation_ranges"]), data
        if generated:
            assert sorted(round(last - first) + 1 for first, last in data["animation_ranges"]) == [80, 120], data
        print(json.dumps(data, indent=2))
        print("PASS: imported cameras, FPS, duration, saved scene and reopening")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--houdini", default="", help="Houdini executable (matching hython required)")
    parser.add_argument("--export", action="append", default=[], help="Real 3DE .py export; repeat for multiple cameras")
    args = parser.parse_args()
    try:
        validate(args.houdini, args.export)
    except Exception as exc:
        print(f"Houdini acceptance failed: {exc}", file=sys.stderr)
        sys.exit(1)
