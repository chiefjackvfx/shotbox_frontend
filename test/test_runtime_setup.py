"""Exercise shared-environment migration without changing installed runtimes."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app_cache
import runtime_setup as setup


@pytest.fixture
def root(tmp_path, monkeypatch):
    directory = tmp_path / "Shotbox with spaces" / "frontend"
    directory.mkdir(parents=True)
    (directory / "requirements.txt").write_text("example-package\n")
    monkeypatch.setattr(app_cache, "CACHE_ROOT", directory / ".cache")
    return directory


def old_environment(root):
    environment = root / "venv"
    environment.mkdir()
    (environment / "original").write_text("keep me")
    python = setup.environment_python(root)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("old interpreter")
    return environment


@pytest.fixture
def commands(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "venv" in command:
            directory = Path(command[-1])
            python = setup.environment_python(directory.parent)
            python.parent.mkdir(parents=True)
            python.write_text("new interpreter")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup, "run_setup_command", run)
    monkeypatch.setattr(setup, "compatible_environment", lambda *_, **__: False)
    monkeypatch.setattr(setup, "verify_environment", lambda *_, **__: None)
    return calls


def test_fresh_setup_creates_at_final_path_and_leaves_parent_environment(root, commands):
    parent = root.parent / ".venv"
    parent.mkdir()
    (parent / "important").write_text("backend runtime")
    python = setup.prepare_runtime(root, uv=root / ".runtime/tools/bin/uv", base_python="managed312", inference=True)
    assert python == setup.environment_python(root)
    create, install = [item[0] for item in commands]
    assert create[-1] == str(root / "venv")
    assert create[create.index("--python") + 1] == "managed312"
    assert install[-2:] == ["--torch-backend", "cu130"]
    assert install[install.index("--requirements") + 1] == str(root / "requirements.txt")
    assert install[install.index("--python") + 1] == str(python)
    assert (parent / "important").read_text() == "backend runtime"
    assert not (root / ".runtime/migration.json").exists()


def test_incompatible_environment_backup_is_retained_at_success(root, commands):
    old_environment(root)
    setup.prepare_runtime(root, uv="uv", inference=True)
    backups = list((root / ".runtime/backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "original").read_text() == "keep me"
    assert not (root / "venv/original").exists()
    assert setup.environment_python(root).read_text() == "new interpreter"


@pytest.mark.parametrize("stage", ["create", "install", "verify", "interrupt"])
@pytest.mark.parametrize("required", [(3, 12), (3, 13)])
def test_failed_migration_restores_old_environment_at_original_path(root, commands, monkeypatch, stage, required):
    old_environment(root)
    original_run = setup.run_setup_command

    def run(command, **kwargs):
        if stage == "create" and "venv" in command:
            raise subprocess.CalledProcessError(1, command)
        if stage in ("install", "interrupt") and "install" in command:
            if stage == "interrupt":
                raise KeyboardInterrupt()
            raise subprocess.CalledProcessError(1, command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(setup, "run_setup_command", run)
    if stage == "verify":
        def fail(*_, **__):
            raise RuntimeError("OpenImageIO DLL failed")
        monkeypatch.setattr(setup, "verify_environment", fail)
    with pytest.raises((subprocess.CalledProcessError, RuntimeError, KeyboardInterrupt)):
        setup.prepare_runtime(root, uv="uv", inference=True, required_python=required)
    assert (root / "venv/original").read_text() == "keep me"
    assert setup.environment_python(root).read_text() == "old interpreter"
    assert not (root / ".runtime/migration.json").exists()
    assert not list((root / ".runtime/backups").iterdir())


def test_failed_fresh_setup_removes_partial_environment(root, commands, monkeypatch):
    monkeypatch.setattr(setup, "verify_environment", lambda *_, **__: (_ for _ in ()).throw(RuntimeError("bad wheel")))
    with pytest.raises(RuntimeError, match="bad wheel"):
        setup.prepare_runtime(root, uv="uv")
    assert not (root / "venv").exists()


def test_compatible312_is_reused_and_has_no_migration_backup(root, commands, monkeypatch):
    old_environment(root)
    monkeypatch.setattr(setup, "compatible_environment", lambda *_, **__: True)
    setup.prepare_runtime(root, uv="uv", inference=False)
    assert len(commands) == 1
    assert "--torch-backend" not in commands[0][0]
    assert (root / "venv/original").is_file()
    assert not (root / ".runtime/backups").exists()


def test_symlink_to_shared_environment_is_not_updated(root, commands):
    shared = root.parent / ".venv"
    shared.mkdir()
    (shared / "backend").write_text("preserved")
    try:
        (root / "venv").symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    setup.prepare_runtime(root, uv="uv", inference=True)
    assert (shared / "backend").read_text() == "preserved"
    assert not (root / "venv").is_symlink()


@pytest.mark.parametrize("point", ["before_rename", "building", "complete", "fresh"])
def test_interrupted_journal_recovery(root, point):
    runtime = root / ".runtime"
    (runtime / "backups").mkdir(parents=True)
    if point != "fresh":
        old_environment(root)
    state = {"schema": 1, "backup": "venv-" + "a" * 32 if point != "fresh" else None,
             "complete": point == "complete"}
    if point in ("building", "complete"):
        (root / "venv").rename(runtime / "backups" / ("venv-" + "a" * 32))
    if point != "before_rename":
        (root / "venv").mkdir()
        (root / "venv/partial").write_text("replacement")
    setup._write_journal(runtime / "migration.json", state)
    setup.recover_migration(root)
    if point == "complete":
        assert (root / "venv/partial").exists()
        assert (runtime / "backups" / ("venv-" + "a" * 32) / "original").exists()
    elif point == "fresh":
        assert not (root / "venv").exists()
    else:
        assert (root / "venv/original").read_text() == "keep me"
    assert not (runtime / "migration.json").exists()


def test_journal_rejects_unsafe_paths_without_touching_environment(root):
    old_environment(root)
    runtime = root / ".runtime"
    runtime.mkdir()
    setup._write_journal(runtime / "migration.json", {"schema": 1, "backup": "../venv-external", "complete": False})
    with pytest.raises(RuntimeError, match="Unsafe backup path"):
        setup.recover_migration(root)
    assert (root / "venv/original").is_file()


def test_setup_lock_rejects_another_owner_then_releases(root):
    runtime = root / ".runtime"
    with (setup.setup_lock(runtime), pytest.raises(RuntimeError, match="Another Shotbox"),
          setup.setup_lock(runtime)):
        pytest.fail("Second owner was allowed")
    with setup.setup_lock(runtime):
        pass


@pytest.mark.parametrize("system,machine,expected", [
    ("win32", "AMD64", True), ("linux", "x86_64", True),
    ("darwin", "arm64", False), ("darwin", "x86_64", False), ("linux", "aarch64", False),
])
def test_platforms(system, machine, expected):
    assert setup.cuda_platform(system, machine) is expected


def test_setup_environment_removes_foreign_python_and_qt_paths(root, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/foreign/python")
    monkeypatch.setenv("PYTHONPATH", "/foreign/modules")
    monkeypatch.setenv("VIRTUAL_ENV", "/shared/env")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/foreign/qt")
    environment = setup.setup_environment(root)
    assert all(name not in environment for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "QT_PLUGIN_PATH"))
    for name in ("TMPDIR", "TEMP", "TMP", "UV_CACHE_DIR", "PIP_CACHE_DIR"):
        assert Path(environment[name]).is_relative_to(root / ".cache/temp")
    assert environment["UV_PYTHON_INSTALL_DIR"] == str(root / ".runtime/tools/python")


def test_native_import_failure_retains_original_diagnostic_and_executable(root, monkeypatch):
    monkeypatch.setattr(setup.subprocess, "run", lambda *_, **__: subprocess.CompletedProcess(
        "probe", 0, json.dumps(["OpenImageIO: DLL load failed (missing library)"]), ""))
    with pytest.raises(RuntimeError, match="OpenImageIO: DLL load failed") as error:
        setup.verify_environment(setup.environment_python(root), root, {}, inference=True)
    assert str(setup.environment_python(root)) in str(error.value)


def test_probe_uses_frontend_imports_and_pinned_cuda_stack(root, monkeypatch):
    recorded = []
    monkeypatch.setattr(setup.subprocess, "run", lambda command, **__: (
        recorded.append(command) or subprocess.CompletedProcess(command, 0, "optional notice\n[]\n", "")))
    setup.verify_environment(setup.environment_python(root), root, {}, inference=True)
    modules, versions = json.loads(recorded[0][-1])
    assert {"OpenImageIO", "OpenEXR", "PyOpenColorIO", "opentimelineio", "torch", "ltx_pipelines.ic_lora"}.issubset(modules)
    assert versions["torch"] == "2.11.0"
    setup.verify_environment(setup.environment_python(root), root, {}, inference=False)
    modules, versions = json.loads(recorded[1][-1])
    assert "torch" not in modules and versions == {}
    assert 'opentimelineio' in modules


@pytest.mark.parametrize("qualification", [False, True])
@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_shell_bootstrap_uses_local_managed_python_without_system_changes(root, qualification, system):
    if os.name == "nt":
        pytest.skip("POSIX bootstrap")
    source = Path(setup.__file__).with_name("setup_runtime.sh")
    shutil.copy2(source, root / source.name)
    shutil.copy2(source.with_name("runtime_python.txt"), root / "runtime_python.txt")
    tools = root / "test-bin"
    tools.mkdir()
    uname = tools / "uname"
    uname.write_text(f"#!/bin/sh\nprintf '%s\\n' '{system}'\n")
    uname.chmod(0o755)
    uv = root / ".runtime/tools/bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = python ] && [ "$2" = find ]; then\n'
        '  printf "%s\\n" "$SHOTBOX_TEST_PYTHON"\n'
        'else printf "%s\\n" "$*" >> "$SHOTBOX_TEST_COMMANDS"; fi\n')
    uv.chmod(0o755)
    (root / "runtime_setup.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['SHOTBOX_TEST_OUTPUT']).write_text(json.dumps({'args':sys.argv[1:], 'env':dict(os.environ), 'leases': list(str(p) for p in (Path(__file__).parent/'.cache/temp/.active-tasks').glob('*.json'))}))\n")
    environment = dict(os.environ, SHOTBOX_TEST_PYTHON=sys.executable,
                       PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""),
                       SHOTBOX_TEST_OUTPUT=str(root / "result.json"),
                       SHOTBOX_TEST_COMMANDS=str(root / "commands.txt"))
    args = ["--qualify-python313"] if qualification else []
    result = subprocess.run(["sh", str(root / source.name), *args], env=environment,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    version = "3.13"
    assert (root / "commands.txt").read_text().strip() == f"python install {version} --no-bin --no-registry"
    recorded = json.loads((root / "result.json").read_text())
    assert recorded["args"] == ["--uv", str(uv), *args]
    assert recorded["env"]["UV_PYTHON_INSTALL_DIR"] == str(root / ".runtime/tools/python")
    assert recorded["env"]["UV_NO_MODIFY_PATH"] == "1"
    assert recorded["env"]["PYTHON_JIT"] == "0"
    assert len(recorded["leases"]) == 1
    assert not list((root / ".cache/temp/.active-tasks").glob("*.json"))


def test_installers_updaters_and_runs_use_shared_frontend_environment():
    frontend = Path(setup.__file__).parent
    for filename in ("windows_install_shotbox_frontend.bat", "windows_update_shotbox.bat"):
        text = (frontend / filename).read_text()
        assert "setup_runtime.ps1" in text
        assert "activate.bat" not in text and "-3.11" not in text
    for filename in ("mac_install_shotbox_frontend.command", "update_shotbox_frontend.sh"):
        text = (frontend / filename).read_text()
        assert "setup_runtime.sh" in text
        assert "python3.11" not in text
    text = (frontend / "run_shotbox_frontend.sh").read_text()
    assert "../.venv" not in text
    assert 'PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"' in text


@pytest.mark.parametrize("redirect", ["runtime", "backups", "journal", "lock"])
def test_runtime_redirects_are_rejected(root, commands, redirect):
    outside = root.parent / "outside"
    outside.mkdir()
    runtime = root / ".runtime"
    if redirect != "runtime":
        runtime.mkdir()
    source = {"runtime": runtime, "backups": runtime / "backups",
              "journal": runtime / "migration.json", "lock": runtime / "setup.lock"}[redirect]
    target = outside
    if redirect in ("journal", "lock"):
        target = outside / "important"
        target.write_text("preserve")
    try:
        source.symlink_to(target, target_is_directory=redirect in ("runtime", "backups"))
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(RuntimeError, match="Refusing redirected"):
        setup.prepare_runtime(root, uv="uv")
    assert not commands
    if target.is_file():
        assert target.read_text() == "preserve"


def test_recovery_waits_for_previous_installer_then_restores(root):
    old_environment(root)
    runtime = root / ".runtime"
    backup_name = "venv-" + "f" * 32
    (runtime / "backups").mkdir(parents=True)
    (root / "venv").rename(runtime / "backups" / backup_name)
    (root / "venv").mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        setup._write_journal(runtime / "migration.json", {
            "schema": 1, "backup": backup_name, "complete": False,
            "child": {"pid": child.pid, "identity": setup._process_identity(child.pid)},
        })
        with pytest.raises(setup.InstallerStillRunning, match="still running"):
            setup.recover_migration(root)
        assert (runtime / "backups" / backup_name / "original").exists()
        child.terminate()
        child.wait(timeout=10)
        setup.recover_migration(root)
        assert (root / "venv/original").exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_cancel_stops_real_owned_child_before_returning(root, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX process-group termination")
    real_popen = subprocess.Popen
    children = []

    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        real_wait = child.wait
        interrupted = False

        def wait(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return real_wait(*args, **kwargs)

        child.wait = wait
        return child

    monkeypatch.setattr(setup.subprocess, "Popen", popen)
    runtime = root / ".runtime"
    runtime.mkdir()
    state = {"schema": 1, "backup": None, "complete": False}
    try:
        with pytest.raises(KeyboardInterrupt):
            setup.run_setup_command(
                [sys.executable, "-c", "import time; time.sleep(60)"], root=root,
                environment=os.environ.copy(), journal=runtime / "migration.json", state=state,
            )
        assert children[0].poll() is not None
        assert "child" not in json.loads((runtime / "migration.json").read_text())
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)


def test_process_id_reuse_does_not_block_recovery(root, monkeypatch):
    runtime = root / ".runtime"
    runtime.mkdir()
    (root / "venv").mkdir()
    setup._write_journal(runtime / "migration.json", {
        "schema": 1, "backup": None, "complete": False,
        "child": {"pid": 12345, "identity": "old creation time"},
    })
    monkeypatch.setattr(setup, "_process_identity", lambda *_: "new creation time")
    setup.recover_migration(root)
    assert not (root / "venv").exists()


def test_313_qualification_migrates_with_requested_probe_and_frontend_requirements(root, commands, monkeypatch):
    old_environment(root)
    checked = []
    monkeypatch.setattr(setup, "compatible_environment", lambda *_, **kw: checked.append(kw) or False)
    monkeypatch.setattr(setup, "verify_environment", lambda *_, **kw: checked.append(kw))
    requirements = root.parent / "requirements.txt"
    setup.prepare_runtime(root, uv="uv", base_python="managed313", inference=True,
                          required_python=(3, 13), requirements=requirements)
    assert all(item["required_python"] == (3, 13) for item in checked)
    create, install = [item[0] for item in commands]
    assert create[create.index("--python") + 1] == "managed313"
    assert install[install.index("--requirements") + 1] == str(requirements)
    assert len(list((root / ".runtime/backups").iterdir())) == 1
    assert setup.DEFAULT_PYTHON == (3, 13)


def test_reuses_313_environment_without_backup(root, commands, monkeypatch):
    old_environment(root)
    checked = []
    monkeypatch.setattr(setup, "compatible_environment", lambda *_, **kw: checked.append(kw) or True)
    setup.prepare_runtime(root, uv="uv", required_python=(3, 13), inference=False)
    assert checked == [{"required_python": (3, 13)}]
    assert len(commands) == 1
    assert not (root / ".runtime/backups").exists()


@pytest.mark.parametrize("required", [(3, 12), (3, 13)])
def test_interpreter_check_uses_real_python_and_rejects_wrong_minor(root, required):
    assert setup.compatible_environment(Path(sys.executable), root, os.environ.copy(), required_python=required) == (
        sys.version_info[:2] == required)


def test_interpreter_check_rejects_free_threaded_build(root):
    code = "import sysconfig; sysconfig.get_config_var = lambda name: 1\n" + setup.INTERPRETER_CHECK
    result = subprocess.run([sys.executable, "-c", code + "\nprint(errors)",
                             f"{sys.version_info.major}.{sys.version_info.minor}"],
                            cwd=root, capture_output=True, text=True, check=True)
    assert "not a free-threaded build" in result.stdout


def test_313_dependency_probe_requests_correct_interpreter(root, monkeypatch):
    recorded = []
    monkeypatch.setattr(setup.subprocess, "run", lambda command, **__: (
        recorded.append(command) or subprocess.CompletedProcess(command, 0, "[]\n", "")))
    setup.verify_environment(Path(sys.executable), root, {}, inference=True, required_python=(3, 13))
    assert recorded[0][-2] == "3.13"
    assert json.loads(recorded[0][-1])[1] == setup.INFERENCE_VERSIONS


@pytest.mark.parametrize("system", ["linux", "win32", "darwin"])
def test_production_default_is_313_on_every_platform(root, monkeypatch, system):
    (root / "runtime_python.txt").write_text("3.13\n")
    monkeypatch.setattr(setup.sys, "platform", system)
    assert setup.default_python(root) == (3, 13)


def test_shared_manifest_can_restore_312_for_rollback(root):
    (root / "runtime_python.txt").write_text("3.12\n")
    assert setup.default_python(root) == (3, 12)
