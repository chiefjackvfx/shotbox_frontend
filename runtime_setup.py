"""Install Shotbox and Matte together in the frontend's managed Python environment.

Called by setup_runtime.sh/ps1 using a managed base Python, never the environment
being replaced. Model files and legacy environments outside this frontend stay
untouched. Migration backups must be restored to ``venv`` before being used.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from app_cache import cache_directory, temporary_cache_lease, temporary_cache_task

FRONTEND_ROOT = Path(__file__).resolve().parent
QUALIFICATION_PYTHON = (3, 13)


def python_version_string(version):
    if version not in ((3, 12), (3, 13)):
        raise ValueError("Only Python 3.12 and 3.13 runtimes are supported.")
    return ".".join(map(str, version))


def default_python(root=FRONTEND_ROOT):
    manifest = root / "runtime_python.txt"
    version = tuple(map(int, manifest.read_text().strip().split(".")))
    python_version_string(version)
    return version


DEFAULT_PYTHON = default_python()
FRONTEND_MODULES = (
    "PyQt6.QtWidgets", "yaml", "requests", "psutil", "av", "numpy", "PIL.Image",
    "OpenImageIO", "OpenEXR", "PyOpenColorIO", "cv2", "pyperclip", "openpyxl",
    "opentimelineio",
)
INFERENCE_MODULES = (
    "torch", "torchvision", "torchaudio", "transformers", "ltx_pipelines.ic_lora",
)
INFERENCE_VERSIONS = {
    "torch": "2.11.0", "torchvision": "0.26.0", "torchaudio": "2.11.0",
    "transformers": "5.14.1", "ltx-core": "1.5.0", "ltx-pipelines": "1.5.0",
}
INTERPRETER_CHECK = r'''
import sys
import sysconfig
required = tuple(map(int, sys.argv[1].split(".")))
errors = []
if (sys.version_info[:2] != required or sys.implementation.name != "cpython"
        or sysconfig.get_config_var("Py_GIL_DISABLED")):
    errors.append(f"Standard CPython {sys.argv[1]} is required (not a free-threaded build).")
'''
PROBE = INTERPRETER_CHECK + r'''
import importlib
from importlib.metadata import version
import json
modules, expected = json.loads(sys.argv[2])
for module in modules:
    try:
        importlib.import_module(module)
    except Exception as exc:
        errors.append(f"{module}: {exc}")
for package, wanted in expected.items():
    try:
        found = version(package)
        if found.split("+")[0] != wanted:
            errors.append(f"{package} requires {wanted}; found {found}.")
    except Exception as exc:
        errors.append(f"{package}: {exc}")
if expected:
    torch = sys.modules.get("torch")
    if torch is not None and getattr(torch.version, "cuda", None) != "13.0":
        errors.append("The CUDA 13.0 PyTorch build is required.")
print(json.dumps(errors))
'''


def cuda_platform(system=None, machine=None):
    """CUDA dependencies support native Windows/Linux x86_64 installations."""
    system = sys.platform if system is None else system
    machine = platform.machine() if machine is None else machine
    return system in ("win32", "linux") and machine.lower() in ("amd64", "x86_64")


def environment_python(root, *, windows=None):
    windows = os.name == "nt" if windows is None else windows
    return Path(root) / "venv" / ("Scripts/python.exe" if windows else "bin/python")


def setup_environment(root):
    environment = dict(os.environ)
    temporary = cache_directory("temp") / "runtime-downloads"
    temporary.mkdir(parents=True, exist_ok=True)
    environment.update(UV_NO_CONFIG="1", UV_CACHE_DIR=str(temporary / "uv"),
                       PIP_CACHE_DIR=str(temporary / "pip"), PYTHONUTF8="1",
                       PYTHON_JIT="0",
                       UV_PYTHON_INSTALL_DIR=str(Path(root) / ".runtime" / "tools" / "python"))
    for name in ("TMPDIR", "TEMP", "TMP"):
        environment[name] = str(temporary)
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "QT_PLUGIN_PATH",
                 "QT_QPA_PLATFORM_PLUGIN_PATH", "QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
        environment.pop(name, None)
    return environment


@contextmanager
def setup_lock(runtime):
    """OS-held lock is released even if setup crashes or the machine restarts."""
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "setup.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            def lock():
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock():
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def lock():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            lock()
        except OSError as exc:
            raise RuntimeError("Another Shotbox environment setup is running. Wait for it to finish.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            unlock()


def _write_journal(journal, state):
    # Exclusive creation prevents a planted symlink from redirecting writes.
    pending = journal.with_name("migration-" + uuid.uuid4().hex + ".pending")
    try:
        with pending.open("x", encoding="utf-8") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(journal)
    finally:
        pending.unlink(missing_ok=True)


def _linked_directory(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _check_runtime_paths(root):
    for path in (root / ".runtime", root / ".runtime/backups", root / ".runtime/setup.lock",
                 root / ".runtime/migration.json"):
        if _linked_directory(path):
            raise RuntimeError(f"Refusing redirected runtime path: {path}")


def _process_identity(pid):
    """Return a process creation identifier so journal PIDs cannot be reused."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                if ctypes.get_last_error() == 5:
                    raise RuntimeError(f"Cannot inspect previous installer PID {pid}; stop it before retrying.")
                return None
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                    raise RuntimeError(f"Cannot inspect previous installer PID {pid}; stop it before retrying.")
                return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            finally:
                kernel.CloseHandle(handle)
        if sys.platform == "linux":
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
            return None if fields[0] == "Z" else fields[19]
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                capture_output=True, text=True, timeout=10, check=False)
        return result.stdout.strip() or None
    except (ProcessLookupError, FileNotFoundError):
        return None


class InstallerStillRunning(RuntimeError):
    """Keep migration files intact until an installer process has stopped."""


def run_setup_command(command, *, root, environment, journal=None, state=None):
    options = {"cwd": root, "env": environment}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    child = subprocess.Popen(command, **options)
    try:
        if journal is not None:
            state["child"] = {"pid": child.pid, "identity": _process_identity(child.pid)}
            _write_journal(journal, state)
        returncode = child.wait()
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
    except BaseException:
        # A cancelled setup must stop the installer and its descendants before
        # restoring/removing the environment they may still be writing into.
        if child.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                   capture_output=True, timeout=20, check=False)
                else:
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        child.kill()
                    else:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
                if os.name != "nt":
                    # The group leader can exit before one of its build helpers.
                    # Stop remaining descendants before rollback touches files.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            except ProcessLookupError:
                child.wait(timeout=10)
            except (OSError, subprocess.SubprocessError) as exc:
                raise InstallerStillRunning(
                    f"Installer PID {child.pid} could not be stopped. Wait for it to exit, then retry setup."
                ) from exc
        raise
    finally:
        if journal is not None and child.poll() is not None:
            state.pop("child", None)
            _write_journal(journal, state)


def _remove_environment(environment):
    if environment.is_symlink():
        environment.unlink()
    elif environment.exists():
        shutil.rmtree(environment)


def recover_migration(root):
    """Restore the original canonical path after any interrupted migration."""
    _check_runtime_paths(root)
    runtime = root / ".runtime"
    journal = runtime / "migration.json"
    if not journal.exists():
        return
    state = json.loads(journal.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("schema") != 1 or
            not isinstance(state.get("complete"), bool)):
        raise RuntimeError(f"Cannot recover unknown migration journal: {journal}")
    backup_name = state.get("backup")
    if backup_name is not None and (not isinstance(backup_name, str) or
                                    re.fullmatch(r"venv-[0-9a-f]{32}", backup_name) is None):
        raise RuntimeError(f"Unsafe backup path in migration journal: {journal}")
    child = state.get("child")
    if child is not None:
        if (not isinstance(child, dict) or type(child.get("pid")) is not int or child["pid"] <= 0 or
                not isinstance(child.get("identity"), (str, type(None)))):
            raise RuntimeError(f"Invalid installer process in migration journal: {journal}")
        identity = _process_identity(child["pid"])
        if identity is not None and (child["identity"] is None or identity == child["identity"]):
            raise InstallerStillRunning(f"Previous installer PID {child['pid']} is still running. Wait, then retry.")
    environment = root / "venv"
    backup = runtime / "backups" / backup_name if backup_name else None
    if not state.get("complete"):
        if backup is not None and (backup.exists() or _linked_directory(backup)):
            _remove_environment(environment)
            backup.rename(environment)
            print("[Shotbox] Restored the previous environment after interrupted setup.", flush=True)
        elif backup is None:
            _remove_environment(environment)
        # A named but absent backup means interruption preceded the rename;
        # the original environment is still in place and must not be deleted.
    journal.unlink()


def compatible_environment(python, root, environment, *, required_python=DEFAULT_PYTHON):
    if not python.is_file():
        return False
    try:
        result = subprocess.run(
            [str(python), "-c", INTERPRETER_CHECK + "\nsys.exit(bool(errors))",
             python_version_string(required_python)],
            cwd=root, env=environment, timeout=30, capture_output=True, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def verify_environment(python, root, environment, *, inference, required_python=DEFAULT_PYTHON):
    modules = list(FRONTEND_MODULES) + (list(INFERENCE_MODULES) if inference else [])
    versions = INFERENCE_VERSIONS if inference else {}
    try:
        result = subprocess.run(
            [str(python), "-c", PROBE, python_version_string(required_python), json.dumps([modules, versions])], cwd=root,
            env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"Import check exited with code {result.returncode}.")
        errors = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(errors, list):
            raise TypeError(f"Invalid dependency check result from {python}")
        if errors:
            raise RuntimeError(f"Dependency verification failed in {python}:\n" + "\n".join(errors))
    except (OSError, ValueError, TypeError, IndexError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not verify dependencies in {python}: {exc}") from exc


@temporary_cache_task
def prepare_runtime(root=FRONTEND_ROOT, *, uv, base_python=None, inference=None,
                    required_python=DEFAULT_PYTHON, requirements=None):
    version = python_version_string(required_python)
    root = Path(root).resolve()
    _check_runtime_paths(root)
    runtime = root / ".runtime"
    python = environment_python(root)
    environment = setup_environment(root)
    base_python = str(base_python or sys.executable)
    inference = cuda_platform() if inference is None else inference
    with setup_lock(runtime), temporary_cache_lease():
        recover_migration(root)
        canonical = root / "venv"
        migrate = _linked_directory(canonical) or not compatible_environment(
            python, root, environment, required_python=required_python)
        journal = runtime / "migration.json"
        backup = None
        state = None
        if migrate:
            if canonical.exists() or _linked_directory(canonical):
                backups = runtime / "backups"
                backups.mkdir(parents=True, exist_ok=True)
                backup = backups / ("venv-" + uuid.uuid4().hex)
            state = {"schema": 1, "backup": backup.name if backup else None, "complete": False}
            _write_journal(journal, state)
        try:
            if migrate:
                if backup is not None:
                    print(f"[Shotbox] Preserving the old environment at {backup}", flush=True)
                    canonical.rename(backup)
                # Create at its final path: venv entry points are not relocatable.
                run_setup_command([str(uv), "--no-config", "venv", "--python", base_python, str(canonical)],
                                  root=root, environment=environment, journal=journal, state=state)
            print(f"[Shotbox] Installing Shotbox and Matte dependencies in {python}", flush=True)
            command = [str(uv), "--no-config", "pip", "install", "--python", str(python),
                       "--requirements", str(requirements or root / "requirements.txt")]
            if inference:
                command.extend(["--torch-backend", "cu130"])
            run_setup_command(command, root=root, environment=environment,
                              journal=journal if migrate else None, state=state)
            verify_environment(python, root, environment, inference=inference, required_python=required_python)
            if migrate:
                _write_journal(journal, {"schema": 1, "backup": backup.name if backup else None, "complete": True})
                journal.unlink()
        except BaseException as exc:
            if migrate and not isinstance(exc, InstallerStillRunning):
                recover_migration(root)
            raise
    print(f"[Shotbox] Python {version} environment ready: {python}", flush=True)
    if backup is not None:
        print(f"[Shotbox] Rollback backup retained at {backup}. Restore it to {canonical} before use.", flush=True)
    if not inference:
        print("[Shotbox] Matte generation requires Windows/Linux x86_64 and a compatible NVIDIA GPU.", flush=True)
    return python


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--qualify-python313", action="store_true",
                        help="Prepare isolated .runtime/qualification/python313/venv; keep production unchanged.")
    args = parser.parse_args(argv)
    required = QUALIFICATION_PYTHON if args.qualify_python313 else DEFAULT_PYTHON
    version = python_version_string(required)
    import sysconfig
    if (sys.version_info[:2] != required or sys.prefix != sys.base_prefix
            or sys.implementation.name != "cpython" or sysconfig.get_config_var("Py_GIL_DISABLED")):
        print(f"Run setup_runtime.sh or setup_runtime.ps1 to use Shotbox's managed standard CPython {version}.",
              file=sys.stderr)
        return 1
    try:
        if args.qualify_python313:
            from runtime_qualification import qualify_runtime
            qualify_runtime(uv=args.uv)
        else:
            prepare_runtime(uv=args.uv)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[Shotbox] Setup failed: {exc}\nCheck the connection and free disk space, then retry the installer.",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
