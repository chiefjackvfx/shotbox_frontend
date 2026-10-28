"""Deadline 10 discovery, read-only preflight and submission file generation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

from nuke_denoise_display import DenoiseCancelled, child_command, child_environment, stop_process


class CommandInterrupted(RuntimeError):
    pass


def discover_client(configured=""):
    name = "deadlinecommand.exe" if sys.platform == "win32" else "deadlinecommand"
    candidates = [configured] if configured else [
        str(Path(os.environ.get("DEADLINE_PATH", "")) / name), shutil.which(name),
        str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Thinkbox/Deadline10/bin" / name)
        if sys.platform == "win32" else "/opt/Thinkbox/Deadline10/bin/deadlinecommand",
    ]
    for value in candidates:
        if value and Path(value).is_file():
            return str(Path(value).resolve())
    raise RuntimeError("Deadline client not found. Set its executable in Settings → Deadline Denoise.")


def command(client, arguments, *, cancelled=lambda: False, timeout=60, directory=None):
    if cancelled():
        raise DenoiseCancelled("Cancelled before contacting Deadline.")
    argv = [client, *map(str, arguments)]
    environment = None
    if directory is not None and sys.platform == "linux":
        argv = child_command(argv)
        environment = child_environment(directory)
    process = subprocess.Popen(argv, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                               start_new_session=sys.platform != "win32")
    started = time.monotonic()
    try:
        while True:
            try:
                output, _ = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                if cancelled() or time.monotonic() - started > timeout:
                    raise CommandInterrupted("Deadline command interrupted or timed out; its outcome may be unknown.")
        if process.returncode:
            raise RuntimeError(output.strip() or f"Deadline exited with code {process.returncode}.")
        return output.strip()
    finally:
        stop_process(process)


def repository_info(client, **kwargs):
    text = command(client, ["-ExecuteScript", Path(__file__).with_name("deadline_probe.py")], **kwargs)
    for line in text.splitlines():
        if line.startswith("SHOTBOX_DEADLINE "):
            info = json.loads(line.removeprefix("SHOTBOX_DEADLINE "))
            break
    else:
        raise RuntimeError("Deadline returned no repository information.")
    return info


def within(path, root, *, case_sensitive=True):
    path, root = str(path).replace("\\", "/"), str(root).replace("\\", "/").rstrip("/")
    if not root:
        return False
    if not case_sensitive:
        path, root = path.casefold(), root.casefold()
    return path == root or path.startswith(root + "/")


def validate_shared(paths, roots, info):
    if not roots:
        raise RuntimeError("Configure shared project roots in Settings → Deadline Denoise.")
    for path in paths:
        if not any(within(path, r, case_sensitive=sys.platform != "win32") for r in roots):
            raise RuntimeError(f"Local-only or unapproved shared path: {path}")


def validate_destination(paths, options, info, executable, **kwargs):
    """Local projects may render only on this computer's verified worker."""
    roots = options.get("shared_roots", [])
    if roots and all(any(within(path, root) for root in roots) for path in paths):
        select_workers(options, info)
        validate_mapping_coverage(paths, options["platforms"], info)
        return
    if sys.platform != "linux" or not options.get("allow_local", False):
        validate_shared(paths, roots, info)
        return
    hostname = socket.gethostname()
    output = command(executable, ["-GetSlave", hostname], **kwargs)
    worker = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if (worker.get("HostName", "").casefold() != hostname.casefold()
            or worker.get("MachineOperatingSystem") != "Linux"
            or worker.get("Enabled") != "True" or worker.get("ID") != hostname):
        raise RuntimeError("Local project submission needs an enabled Linux Deadline worker named after this computer: " + hostname)
    options.update(machine_list=hostname, pool="none", group="none", local_project=True, platforms=["linux"])


def eligible_workers(options, info):
    """Qualification is an explicit operator decision, never inferred from config."""
    qualified = options.get("qualified_workers", [])
    requested = options.get("machine_list", "").split(",") if options.get("machine_list") else None
    return [w for w in info.get("workers", [])
            if w["name"] in qualified and (requested is None or w["name"] in requested)
            and w.get("enabled") and w.get("platform") in {"linux", "win32"}
            and (options.get("pool", "none") == "none" or options["pool"] in w.get("pools", []))
            and (options.get("group", "none") == "none" or options["group"] in w.get("groups", []))]


def select_workers(options, info):
    if not options.get("qualified_workers"):
        raise RuntimeError("Configure qualified workers in Settings → Deadline Denoise after native render qualification.")
    workers = eligible_workers(options, info)
    if not workers:
        raise RuntimeError("No qualified Windows/Linux workers match the selected pool and group.")
    options.update(machine_list=",".join(sorted(w["name"] for w in workers)),
                   platforms=sorted({w["platform"] for w in workers}))


def validate_mapping_coverage(paths, platforms, info):
    # Linux-only jobs retain native paths. Windows jobs require explicit coverage.
    # Actual replacement is performed by Deadline, never by Shotbox.
    if "win32" not in platforms:
        return
    if not info.get("path_mapping_enabled"):
        raise RuntimeError("Enable Path Mapping in Deadline's Nuke plugin for Windows workers.")
    mapping = info.get("path_mappings", {})
    rules = mapping.get("GlobalRules", []) + mapping.get("SubmissionTimeRules", [])
    for path in paths:
        covered = False
        for rule in rules:
            if rule.get("RegularExpression") or rule.get("Region", "All") != "All":
                raise RuntimeError("Mixed denoise preflight requires literal, All-region path mappings; complex mappings need separate qualification.")
            if within(path, rule.get("Path", ""), case_sensitive=rule.get("CaseSensitive", True)):
                target = rule.get("WindowsPath", "").replace("\\", "/")
                if not re.match(r"^(?:[A-Za-z]:(?:/|$)|//[^/]+/[^/]+)", target):
                    raise RuntimeError(f"Missing absolute Windows mapping for {path}")
                covered = True
                break
        if not covered:
            raise RuntimeError(f"No Windows Deadline path mapping covers: {path}")


def render_guard(version, platforms):
    return ("import nuke, sys\nif sys.platform not in " + repr(tuple(platforms))
            + ":\n    raise RuntimeError('Worker platform was not selected for this Shotbox job')\n"
            + "if nuke.NUKE_VERSION_STRING != " + repr(version)
            + ":\n    raise RuntimeError('Shotbox denoise requires the exact profiling Nuke version')\n")


def validate_options(options, info):
    for key, choices in (("pool", "pools"), ("group", "groups")):
        if options.get(key, "none") not in info[choices]:
            raise RuntimeError(f"Unknown Deadline {key}: {options.get(key)}")
    priority = options.get("priority", 50)
    if not isinstance(priority, int) or not 0 <= priority <= info["max_priority"]:
        raise RuntimeError(f"Deadline priority must be between 0 and {info['max_priority']}.")
    chunk_size = options.get("chunk_size", 10)
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or not 1 <= chunk_size <= 1000000:
        raise RuntimeError("Frames per Deadline task must be between 1 and 1000000.")
    concurrent = options.get("concurrent_tasks", 1)
    if not isinstance(concurrent, int) or isinstance(concurrent, bool) or not 1 <= concurrent <= 16:
        raise RuntimeError("Concurrent Deadline tasks per worker must be between 1 and 16.")


def validate_version(version, info, platforms=("linux",)):
    match = re.fullmatch(r"(17\.\d+)v\d+", version)
    if not match:
        raise RuntimeError(f"Unsupported Nuke version: {version}")
    executable = info["executables"].get("RenderExecutable" + match[1].replace(".", "_"), "")
    paths = executable.split(";")
    for platform in platforms:
        if platform not in {"linux", "win32"}:
            raise RuntimeError(f"Unsupported worker platform: {platform}")
        def matches(path):
            normalized = path.replace("\\", "/")
            native = bool(re.match(r"^(?:[A-Za-z]:/|//)", normalized)) if platform == "win32" else normalized.startswith("/") and not normalized.startswith("//")
            return native and bool(re.search(r"(?:^|/)Nuke" + re.escape(version) + r"(?:/|$)", normalized, re.I))
        if not any(matches(p) for p in paths):
            raise RuntimeError(f"Configure exact Nuke {version} executables for {platform} in Deadline; version fallback is not allowed.")
    return match[1]


def write_info(path, values):
    lines = []
    for key, value in values.items():
        value = str(value)
        if any(c in value for c in "\r\n\x00"):
            raise ValueError(f"Invalid line break in Deadline field {key}.")
        lines.append(f"{key}={value}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def submission_files(directory, request, prepared, info):
    from nuke_denoise import build_nuke_command
    options = request["deadline"]
    version = validate_version(prepared["nuke_version"], info, options.get("platforms", ["linux"]))
    first, last = prepared["first"], prepared["last"]
    if last < first:
        raise ValueError("Invalid frame range.")
    chunked = options.get("chunked", True)
    chunk_size = min(options.get("chunk_size", 10), last-first+1) if chunked else last-first+1
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1:
        raise ValueError("Invalid frames per Deadline task.")
    output = Path(request["output_pattern"])
    job = Path(directory) / "job-info.job"
    plugin = Path(directory) / "plugin-info.job"
    job_values = {"Plugin": "Nuke", "Name": output.parent.name,
                    "BatchName": options["batch_name"], "Frames": f"{first}-{last}",
                    "ChunkSize": chunk_size, "ConcurrentTasks": options.get("concurrent_tasks", 1),
                    "LimitConcurrentTasksToNumberOfCpus": True, "Priority": options.get("priority", 50),
                    "Pool": options.get("pool", "none"), "Group": options.get("group", "none"),
                    "OutputDirectory0": output.parent.as_posix(), "OutputFilename0": output.name,
                    "ExtraInfoKeyValue0": "ShotboxSubmission=" + options["submission_id"]}
    if options.get("machine_list"):
        job_values["Whitelist"] = options["machine_list"]
    write_info(job, job_values)
    # Omit SceneFile: Deadline copies the first auxiliary file into its job data.
    write_info(plugin, {"Version": version, "NukeX": True, "BatchMode": bool(chunked),
                       "WriteNode": "Write1", "Threads": build_nuke_command(request["nuke"])[-1],
                       "RenderMode": "Render Full Resolution", "ContinueOnError": False,
                       "UseGpu": False})
    return [str(job), str(plugin), str(Path(directory) / "profiled.nk")]


def parse_job_id(text):
    matches = re.findall(r"^JobID=([a-fA-F0-9]{24})\s*$", text, re.MULTILINE)
    if len(matches) != 1:
        raise CommandInterrupted("Deadline did not return one unambiguous JobID. Reconcile in Deadline Monitor before retrying.")
    return matches[0]
