"""Stage exactly four 000test clips and submit them to a specified Linux worker."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_cache
from denoise_template import create_template_request
import deadline_denoise


@app_cache.temporary_cache_task
def acceptance(source_root, staging_root, executable, worker):
    sources = sorted(source_root.glob("VFX/*/*/plates/*.mxf"))[:4]
    if len(sources) != 4:
        raise RuntimeError("Exactly four source clips are required.")
    token = uuid.uuid4().hex
    staging = staging_root / ("000test_ShotboxDeadline_" + token[:8])
    staging.mkdir(exist_ok=False)
    directory = Path(tempfile.mkdtemp(prefix="deadline-four-", dir=app_cache.cache_directory("temp")))
    print(directory, flush=True)
    report = {"staging": str(staging), "clips": []}
    for index, source in enumerate(sources, 1):
        shot = staging / source.parent.parent.name
        (shot / "plates").mkdir(parents=True)
        target = shot / "plates" / source.name
        shutil.copy2(source, target)
        metadata = json.loads((source.parent.parent / "shot_assets/timeline_import.json").read_text())
        plate = next(p for p in metadata["plates"] if Path(p.get("copied_path", "")).name == source.name)
        job = directory / str(index)
        job.mkdir()
        path = create_template_request(job, {"source": str(target), "shot_dir": str(shot), "shot_name": shot.name,
                                            "colourspace": plate["input_colourspace"], "fps": metadata["fps"], "mode": "new"}, executable)
        request = json.loads(path.read_text())
        request["deadline"] = {"submission_id": uuid.uuid4().hex, "shot_dir": str(shot),
                               "batch_name": "Shotbox four-clip acceptance " + token[:8], "pool": "linux",
                               "group": "none", "priority": 50, "shared_roots": [str(staging_root)],
                               "qualified_workers": [worker], "machine_list": worker}
        path.write_text(json.dumps(request, indent=2))
        events = []

        def notify(event):
            events.append(event)
            if event["stage"] != "debug":
                print(index, event, flush=True)

        result = deadline_denoise.run(path, notify=notify)
        report["clips"].append({"request": str(path), "source": str(source), "result": result, "events": events})
        (directory / "acceptance.json").write_text(json.dumps(report, indent=2))
        if result["stage"] != "submitted":
            raise RuntimeError(result)
    print("Submitted exactly four jobs. Report:", directory / "acceptance.json", flush=True)


@app_cache.temporary_cache_task
def reuse_staging(report_path, executable, worker, concurrent):
    """Re-test four previously staged clips without copying their footage again."""
    previous = json.loads(report_path.read_text())
    assert len(previous["clips"]) == 4
    directory = Path(tempfile.mkdtemp(prefix="deadline-four-chunked-", dir=app_cache.cache_directory("temp")))
    print(directory, flush=True)
    batch = "Shotbox four-clip chunks " + uuid.uuid4().hex[:8]
    report = {"staging": previous["staging"], "clips": []}
    for index, clip in enumerate(previous["clips"], 1):
        old = json.loads(Path(clip["request"]).read_text())
        job = directory / str(index)
        job.mkdir()
        shot = Path(old["deadline"]["shot_dir"])
        path = create_template_request(job, {"source": old["source"], "shot_dir": str(shot),
            "shot_name": shot.name, "colourspace": old["colourspace"], "fps": old["fps"], "mode": "new"}, executable)
        request = json.loads(path.read_text())
        request["deadline"] = {**old["deadline"], "submission_id": uuid.uuid4().hex,
                               "batch_name": batch, "qualified_workers": [worker], "machine_list": worker,
                               "chunked": True, "chunk_size": 10, "concurrent_tasks": concurrent}
        path.write_text(json.dumps(request, indent=2))
        events = []

        def notify(event):
            events.append(event)
            if event["stage"] != "debug":
                print(index, event, flush=True)

        result = deadline_denoise.run(path, notify=notify)
        report["clips"].append({"request": str(path), "source": clip["source"], "result": result, "events": events})
        (directory / "acceptance.json").write_text(json.dumps(report, indent=2))
        assert result["stage"] == "submitted", result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, nargs="?")
    parser.add_argument("staging_root", type=Path, nargs="?")
    parser.add_argument("--reuse-report", type=Path)
    parser.add_argument("--concurrent-tasks", type=int, default=1)
    parser.add_argument("--nuke", required=True)
    parser.add_argument("--worker", required=True)
    args = parser.parse_args()
    if args.reuse_report:
        reuse_staging(args.reuse_report, args.nuke, args.worker, args.concurrent_tasks)
    elif args.source_root and args.staging_root:
        acceptance(args.source_root, args.staging_root, args.nuke, args.worker)
    else:
        parser.error("Provide source_root and staging_root, or --reuse-report.")
