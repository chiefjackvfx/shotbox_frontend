from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("args, expected", [([], None), (["C:/shots/plate v001"], "C:/shots/plate v001"),
                                             (["--gui", "C:/shots/片_v001"], "C:/shots/片_v001")])
def test_windows_launcher_dispatches_gui_with_source(monkeypatch, args, expected):
    bootstrap = load_script("portable_bootstrap", "windows_bootstrap.py")
    monkeypatch.setattr(bootstrap, "configure_runtime", lambda root: None)
    received = []
    monkeypatch.setitem(sys.modules, "shotbox_matte.gui", SimpleNamespace(main=lambda path: received.append(path)))
    assert bootstrap.main(args) == 0
    assert received == [expected]


def test_windows_launcher_passes_worker_and_cli_options_without_gui(monkeypatch):
    bootstrap = load_script("portable_bootstrap", "windows_bootstrap.py")
    monkeypatch.setattr(bootstrap, "configure_runtime", lambda root: None)
    received = []
    monkeypatch.setitem(sys.modules, "shotbox_matte.cli",
                        SimpleNamespace(main=lambda args: received.append(args) or 130))
    args = ["--worker", "C:/shots/job with spaces.json"]
    assert bootstrap.main(args) == 130
    assert received == [args]


def test_bundle_validator_rejects_non_windows_binary(tmp_path):
    validator = load_script("portable_validator", "packaging/windows/validate_bundle.py")
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"\x7fELF" + bytes(256))
    with pytest.raises(ValueError, match="Not a Windows executable"):
        validator.pe_info(executable)


def test_bundle_validator_reads_real_pe_fields(tmp_path):
    validator = load_script("portable_validator", "packaging/windows/validate_bundle.py")
    payload = bytearray(256)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    struct.pack_into("<H", payload, 0x98 + 68, 2)
    executable = tmp_path / "app.exe"
    executable.write_bytes(payload)
    assert validator.pe_info(executable) == {"machine": 0x8664, "optional_header": 0x20B, "subsystem": 2}


def test_python_runtime_rejects_modified_archive(tmp_path):
    assembler = load_script("portable_assembler", "packaging/windows/assemble_bundle.py")
    archive = tmp_path / "python.zip"
    archive.write_bytes(b"modified embedded Python archive")
    with pytest.raises(ValueError, match="checksum"):
        assembler.prepare_python(tmp_path / "runtime", archive)


def tiny_bundle(root: Path) -> Path:
    """Complete structurally valid portable folder with tiny stand-in model bytes."""
    validator = load_script("portable_validator", "packaging/windows/validate_bundle.py")
    payload = bytearray(256)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    struct.pack_into("<H", payload, 0x98 + 68, 2)
    for name in ("ShotBoxMatte.exe", "windows_runtime/python.exe", "windows_runtime/python312.dll"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    for name in ("windows_bootstrap.py", "shotbox_matte/__main__.py", "shotbox_matte/core.py",
                 "windows_runtime/python312._pth", "windows_runtime/Lib/site-packages/torch/__init__.py",
                 "windows_runtime/Lib/site-packages/PyQt6/Qt6/plugins/platforms/qwindows.dll",
                 "windows_runtime/Lib/site-packages/ltx_core/__init__.py",
                 "windows_runtime/Lib/site-packages/ltx_pipelines/ic_lora.py",
                 "windows_runtime/Lib/site-packages/numpy/__init__.py",
                 "windows_runtime/Lib/site-packages/av/__init__.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# bundled source\n")
    (root / "models").mkdir()
    models = {}
    for index in range(6):
        name = f"model{index}.safetensors"
        (root / "models" / name).write_bytes(b"x")
        models[str(index)] = {"file": name, "bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
    (root / "models/manifest.json").write_text(json.dumps({"models": models}))
    (root / "portable-package.json").write_text(json.dumps(validator.source_metadata(root)))
    return root


def test_build_identity_survives_relocation_and_ignores_user_data(tmp_path):
    build = load_script("build_info", "shotbox_matte/build_info.py")
    first = tiny_bundle(tmp_path / "first")
    second = tiny_bundle(tmp_path / "moved")
    (second / "settings.ini").write_text("prompt=changed\n")
    (second / "models/model0.safetensors").write_bytes(b"different model")
    assert build.build_metadata(first) == build.build_metadata(second)
    assert build.build_label() == f"{build.APP_VERSION} ({build.BUILD_ID})"


@pytest.mark.parametrize("relative", ["windows_bootstrap.py", "shotbox_matte/core.py", "shotbox_matte/extra.py"])
def test_bundle_validator_detects_modified_and_stale_app_sources(tmp_path, relative):
    validator = load_script("portable_validator", "packaging/windows/validate_bundle.py")
    root = tiny_bundle(tmp_path)
    assert validator.validate(root)["ok"]
    (root / relative).write_text("# unexpected source\n")
    report = validator.validate(root)
    assert not report["ok"]
    assert any("App source differs" in error for error in report["errors"])


def test_code_update_contains_app_and_launcher_without_weights_runtime_or_settings(tmp_path):
    exporter = load_script("portable_export", "packaging/windows/export_update.py")
    root = tiny_bundle(tmp_path / "bundle")
    (root / "settings.ini").write_text("personal settings")
    output = tmp_path / "update.zip"
    report = exporter.export_update(root, output)
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert {"ShotBoxMatte.exe", "windows_bootstrap.py", "shotbox_matte/core.py", "portable-package.json",
                "UPDATE_README.txt", "update-package.json"} <= names
        assert not any(name.startswith(("models/", "windows_runtime/")) for name in names)
        assert "settings.ini" not in names
        assert json.loads(archive.read("update-package.json"))["build_id"] == report["build_id"]
    assert not report["includes_models"] and not report["includes_runtime"]


def test_code_update_rejects_unassembled_source_changes(tmp_path):
    exporter = load_script("portable_export", "packaging/windows/export_update.py")
    root = tiny_bundle(tmp_path / "bundle")
    (root / "windows_bootstrap.py").write_text("# edited after assembly\n")
    with pytest.raises(ValueError, match="assemble it again"):
        exporter.export_update(root, tmp_path / "update.zip")


def test_code_only_rebuild_reuses_runtime_without_dependency_install(monkeypatch, tmp_path):
    builder = load_script("windows_builder", "build_windows.py")
    root = tiny_bundle(tmp_path / "bundle")
    calls = []
    monkeypatch.setattr(sys, "argv", ["build_windows.py", "--code-only", "--output", str(root)])
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kwargs: calls.append(command))
    assert builder.main() == 0
    scripts = [Path(command[1]).name for command in calls]
    assert scripts == ["build_launcher.py", "assemble_bundle.py", "validate_bundle.py", "export_update.py"]


def test_code_only_rebuild_requires_existing_runtime_before_compiling(monkeypatch, tmp_path):
    builder = load_script("windows_builder", "build_windows.py")
    monkeypatch.setattr(sys, "argv", ["build_windows.py", "--code-only", "--output", str(tmp_path)])
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: pytest.fail("must fail before compiling"))
    with pytest.raises(SystemExit) as error:
        builder.main()
    assert error.value.code == 2


def test_assembly_refreshes_sources_offline_and_preserves_cached_models_and_settings(monkeypatch, tmp_path):
    assembler = load_script("portable_assembler", "packaging/windows/assemble_bundle.py")
    project = tiny_bundle(tmp_path / "project")
    bundle = tiny_bundle(tmp_path / "portable")
    (project / "shotbox_matte/build_info.py").write_bytes((ROOT / "shotbox_matte/build_info.py").read_bytes())
    (project / "models.local.json").write_text("{}")
    (project / "third_party_licenses").mkdir()
    (bundle / "shotbox_matte/stale.py").write_text("# removed in new build")
    (bundle / "settings.ini").write_text("prompt=keep my prompt\n")
    model = bundle / "models/model0.safetensors"
    previous = model.stat()
    monkeypatch.setattr(assembler, "PROJECT", project)
    monkeypatch.setattr(assembler.urllib.request, "urlretrieve", lambda *args: pytest.fail("must reuse runtime"))
    assembler.assemble(bundle)
    assert not (bundle / "shotbox_matte/stale.py").exists()
    assert (bundle / "settings.ini").read_text() == "prompt=keep my prompt\n"
    assert model.stat().st_ino == previous.st_ino and model.stat().st_mtime_ns == previous.st_mtime_ns
    assert json.loads((bundle / "portable-package.json").read_text())["source_sha256"] == assembler.app_metadata(project)["source_sha256"]
