"""Profile locally and submit once, with persistent receipts outside the cache."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sys

import app_cache
import deadline_client as client
from nuke_denoise import emit, run as profile
from nuke_denoise_display import DenoiseCancelled


def save_receipt(path, data):
    # A sibling replacement ensures a crash cannot leave a truncated receipt.
    temp = path.with_suffix(".pending")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


@app_cache.temporary_cache_task
def run(request_path, notify=emit):
    import os
    import psutil
    path = Path(request_path).resolve()
    directory = path.parent
    request = json.loads(path.read_text())
    options = request["deadline"]
    token = options["submission_id"]
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Invalid submission ID.")
    metadata = Path(options["shot_dir"]) / "shot_assets/deadline"
    receipt_path = metadata / (token + ".json")
    key = hashlib.sha256(str(Path(request["source"]).resolve()).encode()).hexdigest()
    lock = metadata / (key + ".lock")
    cancelled = lambda: (directory / "cancel").exists()
    acquired = attempted = False
    receipt = {"submission_id": token, "source": request["source"], "output_pattern": request["output_pattern"],
               "created": datetime.now(timezone.utc).isoformat(), "status": "preparing", "lock": str(lock),
               "supervisor_pid": os.getpid(), "supervisor_created": psutil.Process().create_time()}
    result = None
    try:
        if receipt_path.exists():
            old = json.loads(receipt_path.read_text())
            if old["status"] == "submitted":
                result = {"stage": "submitted", "job_id": old["job_id"], "receipt": str(receipt_path)}
                return result
            raise RuntimeError(f"This submission was already attempted. Review receipt before explicitly retrying: {receipt_path}")
        executable = client.discover_client(options.get("client", ""))
        notify({"stage": "deadline_preflight", "message": "Checking Deadline repository and shared paths…"})
        info = client.repository_info(executable, cancelled=cancelled, directory=directory)
        client.validate_options(options, info)
        client.validate_destination([request["source"], request["output_pattern"]], options, info,
                                    executable, cancelled=cancelled, directory=directory)
        metadata.mkdir(parents=True, exist_ok=True)
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(receipt_path))
        except FileExistsError:
            raise RuntimeError(f"An active or unresolved submission exists for this clip. Reconcile its receipt: {lock.read_text()}")
        acquired = True
        receipt["repository"] = info["repository"]
        receipt["machine_list"] = options.get("machine_list", "")
        receipt["chunked"] = options.get("chunked", True)
        receipt["chunk_size"] = options.get("chunk_size", 10)
        receipt["concurrent_tasks"] = options.get("concurrent_tasks", 1)
        save_receipt(receipt_path, receipt)
        request.update(profile_only=True, deadline_repository=info)
        path.write_text(json.dumps(request, indent=2))

        def relay(event):
            if event["stage"] not in {"prepared", "complete", "failed", "cancelled"}:
                notify(event)

        prepared = profile(path, notify=relay)
        if prepared["stage"] != "prepared":
            result = prepared
            receipt.update(status=prepared["stage"], error=prepared.get("error", ""))
            return result
        args = client.submission_files(directory, request, prepared, info)
        if cancelled():
            raise DenoiseCancelled("Cancelled before submission; no farm job was created.")
        receipt.update(status="submitting", first=prepared["first"], last=prepared["last"],
                       nuke_version=prepared["nuke_version"], profile_sha256=hashlib.sha256(Path(args[-1]).read_bytes()).hexdigest())
        save_receipt(receipt_path, receipt)
        notify({"stage": "submitting", "message": "Submitting prepared clip to Deadline…"})
        attempted = True
        output = client.command(executable, args, cancelled=cancelled, timeout=120, directory=directory)
        job_id = client.parse_job_id(output)
        receipt.update(status="submitted", job_id=job_id, response=output)
        result = {"stage": "submitted", "job_id": job_id, "receipt": str(receipt_path),
                  "message": "Queued in Deadline; monitor and cancel the render in Deadline Monitor."}
    except DenoiseCancelled as exc:
        receipt.update(status="cancelled", error=str(exc))
        result = {"stage": "cancelled", "error": str(exc)}
    except Exception as exc:
        status = "unknown" if attempted else "failed"
        receipt.update(status=status, error=str(exc))
        result = {"stage": "submission_unknown" if attempted else "failed", "error": str(exc),
                  "receipt": str(receipt_path)}
    finally:
        if acquired:
            save_receipt(receipt_path, receipt)
            if receipt["status"] not in {"submitting", "unknown"}:
                lock.unlink(missing_ok=True)
        if result is not None:
            result = {**result, "directory": str(directory)}
            (directory / "result.json").write_text(json.dumps(result, indent=2))
            notify(result)
    return result


def recover_result(request, fallback):
    """A persisted farm receipt takes precedence over a crashed local worker."""
    options = request["deadline"]
    path = Path(options["shot_dir"]) / "shot_assets/deadline" / (options["submission_id"] + ".json")
    if path.exists():
        receipt = json.loads(path.read_text())
        if receipt["status"] == "submitted":
            return {"stage": "submitted", "job_id": receipt["job_id"], "receipt": str(path)}
        if receipt["status"] in {"submitting", "unknown"}:
            return {"stage": "submission_unknown", "receipt": str(path),
                    "error": "Supervisor stopped during submission. Reconcile this receipt before retrying."}
    if fallback.get("stage") == "prepared":
        return {"stage": "failed", "error": "Profiling finished but submission did not start. Select this clip to retry."}
    return fallback


def reconcile(path, executable):
    path = Path(path).resolve()
    receipt = json.loads(path.read_text())
    if receipt["status"] == "preparing":
        import psutil
        try:
            process = psutil.Process(receipt["supervisor_pid"])
            alive = process.create_time() == receipt["supervisor_created"] and process.is_running()
        except (psutil.NoSuchProcess, KeyError):
            alive = False
        if alive:
            raise RuntimeError("This clip is still being prepared. Cancel it in Shotbox before reconciling.")
        receipt.update(status="failed", error="Preparation supervisor exited before farm submission.")
        save_receipt(path, receipt)
        lock = Path(receipt["lock"])
        if lock.exists() and lock.read_text() == str(path):
            lock.unlink()
        return receipt
    if receipt["status"] not in {"unknown", "submitting"}:
        return receipt
    response = client.command(client.discover_client(executable), ["-ExecuteScript",
                              Path(__file__).with_name("deadline_probe.py"), "reconcile", receipt["submission_id"]])
    jobs = json.loads(next(line.removeprefix("SHOTBOX_DEADLINE ") for line in response.splitlines()
                           if line.startswith("SHOTBOX_DEADLINE ")))["jobs"]
    if len(jobs) != 1:
        raise RuntimeError("Could not identify exactly one job. Leave the receipt locked and inspect Deadline Monitor; do not resubmit automatically.")
    receipt.update(status="submitted", job_id=jobs[0])
    save_receipt(path, receipt)
    lock = Path(receipt["lock"])
    if lock.exists() and lock.read_text() == str(path.resolve()):
        lock.unlink()
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--request")
    selection.add_argument("--reconcile", type=Path)
    parser.add_argument("--client", default="")
    args = parser.parse_args()
    if args.reconcile:
        print(json.dumps(reconcile(args.reconcile, args.client), indent=2))
    else:
        result = run(args.request)
        sys.exit(0 if result["stage"] == "submitted" else 1)
