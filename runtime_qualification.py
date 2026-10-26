"""Isolated Python 3.13 installation evidence, never a release acceptance claim."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime

import runtime_setup as setup
from app_cache import temporary_cache_lease, temporary_cache_task

METADATA_PROBE = r'''
from importlib.metadata import distributions
import json, platform, sys, sysconfig
report = {
    "python": sys.version, "executable": sys.executable,
    "implementation": sys.implementation.name,
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "platform": platform.platform(), "machine": platform.machine(),
    "packages": sorted(
        [{"name": d.metadata["Name"], "version": d.version} for d in distributions()],
        key=lambda d: d["name"].lower()),
}
if sys.argv[1] == "inference":
    import torch
    report["cuda"] = {"runtime": torch.version.cuda, "available": torch.cuda.is_available()}
    if report["cuda"]["available"]:
        report["cuda"]["devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
print(json.dumps(report))
'''


def qualification_root():
    root = setup.FRONTEND_ROOT
    for part in (".runtime", "qualification", "python313"):
        root = root / part
        if setup._linked_directory(root):
            raise RuntimeError(f"Refusing redirected qualification path: {root}")
    return root


@temporary_cache_task
def qualify_runtime(*, uv):
    """Install the current requirements and persist honest, platform-local evidence."""
    root = qualification_root()
    root.mkdir(parents=True, exist_ok=True)
    if setup._linked_directory(root / "setup.lock"):
        raise RuntimeError(f"Refusing redirected qualification lock: {root / 'setup.lock'}")
    requirements = setup.FRONTEND_ROOT / "requirements.txt"
    inference = setup.cuda_platform()
    environment = setup.setup_environment(root)
    report = {
        "schema": 1, "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "requested_python": "3.13", "system": platform.system(), "machine": platform.machine(),
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "inference_dependencies": inference, "status": "failed", "promotion_ready": False,
        "acceptance": "Installation evidence only; see PYTHON313_QUALIFICATION.md for separate acceptance results.",
        "bootstrap_python": sys.version,
    }
    # Serialize installation and evidence capture, in addition to the migration lock.
    with setup.setup_lock(root), temporary_cache_lease():
        try:
            report["phase"] = "installation_and_imports"
            python = setup.prepare_runtime(root, uv=uv, required_python=(3, 13), requirements=requirements)
            report["phase"] = "dependency_consistency"
            setup.run_setup_command([str(uv), "--no-config", "pip", "check", "--python", str(python)],
                                    root=root, environment=environment)
            report["phase"] = "environment_metadata"
            metadata = subprocess.run(
                [str(python), "-c", METADATA_PROBE, "inference" if inference else "desktop"],
                cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=True, timeout=180,
            )
            report["environment"] = json.loads(metadata.stdout.strip().splitlines()[-1])
            if inference and shutil.which("nvidia-smi"):
                driver = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                    cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=30,
                )
                report["nvidia_smi"] = {"returncode": driver.returncode, "stdout": driver.stdout.strip(),
                                        "stderr": driver.stderr.strip()}
            report.update(status="installed_and_import_verified", phase="complete")
        except BaseException as exc:
            report["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            report["finished_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
            setup._write_journal(root / "report.json", report)
            print(f"[Shotbox] Qualification evidence: {root / 'report.json'}", flush=True)
    return root / "report.json"
