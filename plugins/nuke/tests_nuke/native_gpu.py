"""Run real native backend gates on a copied Nuke capture, outside Nuke.

Example (ShotBox interpreter, working CUDA access required):
    toolbox/shotbox_matte/.venv/bin/python plugins/nuke/tests_nuke/native_gpu.py \
        --capture /path/to/capture_v001 --case short
    # Then repeat with --case segmented and --case full-range.

The long capability fixtures repeat those captured frames. This verifies native
execution/counts/mapping, not a 250-frame shot's content or matte quality.
No inference adapter is replaced and no weights are downloaded.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3] / "toolbox" / "shotbox_matte"
sys.path.insert(0, str(ROOT))

def write_json(path, data):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    from shotbox_matte.build_info import build_metadata
    from shotbox_matte.cli import build_parser, load_models
    from shotbox_matte.core import JobOptions
    from shotbox_matte.nuke_bridge import preflight, validate

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--case", choices=("short", "segmented", "full-range"), required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/nuke-native-gpu.json")
    parser.add_argument("--artifacts", type=Path, default=ROOT / "test-runs/nuke-native")
    parser.add_argument("--model-root", type=Path)
    args = parser.parse_args(argv)
    source = args.capture.resolve()
    inputs = sorted(source.glob("capture.*.exr"))
    if not inputs:
        parser.error("Capture directory must contain Nuke-written capture.NNNN.exr files")
    report_path = args.report.resolve()
    record = json.loads(report_path.read_text()) if report_path.is_file() else {
        "description": "Real existing ShotBox native worker; no mocked inference; repeated Nuke captures for long requests",
        "cases": {}, "quality_review": "Not performed; tiny synthetic coordinate fixture is unsuitable for subject quality assessment"}
    if args.case != "short" and record["cases"].get("short", {}).get("status") != "complete":
        parser.error("A successful real short case must be recorded before long GPU requests")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = args.artifacts.resolve() / (stamp + "-" + args.case + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    capture_dir = directory / "capture_v001"
    capture_dir.mkdir()
    count = min(3, len(inputs)) if args.case == "short" else 250
    for index in range(count):
        shutil.copyfile(inputs[index % len(inputs)], capture_dir / f"capture.{index + 1:04d}.exr")
    model_args = build_parser().parse_args([])
    if args.model_root:
        model_args.model_root = args.model_root
    else:
        preferences_file = Path.home() / ".nuke" / "shotbox_preferences.json"
        configured = json.loads(preferences_file.read_text()) if preferences_file.exists() else {}
        if configured.get("model_root"):
            model_args.model_root = Path(configured["model_root"])
        if configured.get("model_config"):
            model_args.models_config = Path(configured["model_config"])
    options = JobOptions(input_path=str(capture_dir), source_color="linear-srgb", fps=24.0,
                         generation_width=64, generation_height=64, first_frame=1, source_start=1,
                         source_end=count, suffix="_matte", models=load_models(model_args),
                         offload="cpu", temporal_mode="full-range" if args.case == "full-range" else "segmented")
    worker = directory / "worker.json"
    write_json(worker, asdict(options))
    scan = preflight(worker)
    write_json(directory / "preflight.json", scan)
    command = [sys.executable, "-u", "-X", "utf8", "-m", "shotbox_matte", "--worker", str(worker)]
    entry = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
             "attempt": str(directory), "command": command, "source_capture": str(source),
             "source_fixture": "Nuke-written coordinate/Grade capture; frames repeat for long requests",
             "selected_count": count, "temporal_mode": options.temporal_mode,
             "padded_counts": [chunk["padded_count"] for chunk in scan["plan"]["temporal_chunks"]],
             "spatial": scan["plan"]["spatial"], "backend": build_metadata()}
    record["cases"][args.case] = entry
    write_json(report_path, record)
    environment = os.environ.copy()
    environment.update(SHOTBOX_JOB_DIR=str(directory / "backend_jobs"), PYTHONUTF8="1",
                       PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    start_time = time.monotonic()
    with (directory / "events.jsonl").open("wb") as events, (directory / "stderr.log").open("wb") as errors:
        process = subprocess.run(command, cwd=ROOT, env=environment, stdout=events, stderr=errors)
    entry.update(exit_code=process.returncode, wall_seconds=round(time.monotonic() - start_time, 3),
                 finished_at=datetime.now(timezone.utc).isoformat())
    terminal = []
    for line in (directory / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
            if event.get("type") in ("result", "error", "cancelled"):
                terminal.append(event)
        except (ValueError, AttributeError):
            pass
    entry["terminal"] = terminal
    entry["status"] = "failed"
    if process.returncode == 0 and len(terminal) == 1 and terminal[0].get("type") == "result":
        result = terminal[0]["result"]
        source_manifest = source.parent / "integration.json"
        original = json.loads(source_manifest.read_text()) if source_manifest.is_file() else {}
        capture = {**scan["capture"], "start": -2, "end": count - 3, "view": "main", "fps":24.0, "pixel_aspect":1.0}
        manifest = {"schema_version":1, "integration_version":"0.1.0", "node_id":str(uuid.uuid4()),
                    "attempt_id":directory.name, "state":"validating", "created_at":entry["started_at"],
                    "original_attempt_root":str(directory), "worker_path":"worker.json",
                    "worker_sha256":hashlib.sha256(worker.read_bytes()).hexdigest(), "capture":capture,
                    "settings":{"source_color":"linear-srgb"}, "fingerprint":original.get("fingerprint", {}),
                    "host":original.get("host", {}), "probe":{}, "result":result, "worker_exit":0,
                    "outputs":[], "validation":{}}
        integration = directory / "integration.json"
        write_json(integration, manifest)
        try:
            validation = validate(integration)
            manifest.update(state="complete", outputs=validation["outputs"], validation=validation,
                            backend_manifest=validation["backend_manifest"])
            write_json(integration, manifest)
            entry.update(status="complete", integration=str(integration),
                         validation={key:value for key,value in validation.items() if key != "outputs"})
        except Exception as exc:
            entry["validation_error"] = str(exc)
    if entry["status"] != "complete":
        entry["stderr_tail"] = (directory / "stderr.log").read_text(encoding="utf-8", errors="replace")[-16000:]
    write_json(report_path, record)
    print(json.dumps({"case":args.case, "status":entry["status"], "wall_seconds":entry["wall_seconds"],
                      "attempt":str(directory), "report":str(report_path)}), flush=True)
    return 0 if entry["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
