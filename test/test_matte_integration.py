"""Real owned-window and worker boundaries, plus managed Nuke installation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QSettings, QStandardPaths
from PyQt6.QtWidgets import QApplication

import app_cache
import plugins_install
from toolbox import registry
from toolbox.shotbox_matte import configuration
from toolbox.shotbox_matte.shotbox_matte.gui import create_window, MODEL_FIELDS
from toolbox.shotbox_matte.shotbox_matte.runtime import RuntimeConfig


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(app_cache, "CACHE_ROOT", tmp_path / ".cache")
    monkeypatch.setattr(configuration, "preferences_path", lambda: tmp_path / ".nuke" / "shotbox_preferences.json")
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda *_: str(tmp_path / "state"))
    return tmp_path


def wait(app, condition):
    deadline = time.monotonic() + 10
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Qt work did not finish")
        time.sleep(.005)
    app.processEvents()


def test_registry_does_not_import_ui_or_inference():
    root = Path(__file__).resolve().parents[1]
    code = "import sys; import toolbox.registry; assert not any(x in sys.modules for x in ('torch','PyQt6','ltx_pipelines','toolbox.shotbox_matte.plugin'))"
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_owned_window_reuse_and_cache_protection(app, isolated, monkeypatch):
    from toolbox_page import ToolboxPage
    name, organization = app.applicationName(), app.organizationName()
    cwd, environment = Path.cwd(), dict(os.environ)
    page = ToolboxPage()
    try:
        page._open_tool(registry.TOOLS[0])
        window = page._tool_windows["shotbox_matte"]
        assert window.parent() is page and window.isWindow()
        page._open_tool(registry.TOOLS[0])
        assert page._tool_windows["shotbox_matte"] is window
        assert QApplication.instance() is app
        assert (app.applicationName(), app.organizationName()) == (name, organization)
        assert Path.cwd() == cwd and dict(os.environ) == environment
        sentinel = app_cache.cache_directory("temp") / "keep.json"
        sentinel.touch()
        assert app_cache.clear_cache_files()["active_temp"] and sentinel.exists()
        wait(app, lambda: not window.is_busy())
        window.close()
        wait(app, lambda: not window.isVisible())
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not page._tool_windows
        assert not app_cache.clear_cache_files()["active_temp"]
        assert not sentinel.exists()
        page._open_tool(registry.TOOLS[0])
        assert page._tool_windows["shotbox_matte"] is not window
    finally:
        page.close_tools()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        page.close()


def test_worker_uses_host_interpreter_directory_and_environment(app, isolated):
    application = isolated / "application with spaces"
    package = application / "shotbox_matte"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    record = application / "record.json"
    (package / "__main__.py").write_text(
        "import json,sys,os,pathlib,time\n"
        "time.sleep(.2)\n"
        "pathlib.Path('record.json').write_text(json.dumps({'python':sys.executable,'cwd':os.getcwd(),'path':os.environ.get('PYTHONPATH'),'job':sys.argv[-1],"
        "'temporary':os.environ.get('SHOTBOX_TEMP_DIR'),'jobs':os.environ.get('SHOTBOX_JOB_DIR')}))\n"
        "print(json.dumps({'type':'error','message':'controlled worker failure'}),flush=True)\n"
        "sys.exit(7)\n")
    temporary = app_cache.cache_directory("temp")
    state = isolated / "state"
    state.mkdir()
    runtime = RuntimeConfig(application, Path(sys.executable), "", state, temporary)
    window = create_window(QSettings(str(isolated / "settings.ini"), QSettings.Format.IniFormat), runtime=runtime)
    weights = isolated / "weight.bin"
    weights.touch()
    options = window.options()
    options["models"] = {key: str(weights) for key, _ in MODEL_FIELDS}
    before = Path.cwd()
    try:
        assert window._start_job(options)
        assert window._worker.process.program() == sys.executable
        wait(app, lambda: window._process is None)
        received = json.loads(record.read_text())
        assert received["python"] == os.path.abspath(sys.executable)
        assert received["cwd"] == str(application)
        assert received["path"] is None
        assert Path(received["job"]).is_relative_to(temporary)
        assert received["temporary"] == str(temporary)
        assert received["jobs"] == str(temporary / "jobs")
        assert "controlled worker failure" in window.log.toPlainText()
        assert Path.cwd() == before
    finally:
        window.close()
        app.processEvents()


def test_external_model_paths_survive_reopening(app, isolated):
    models = isolated / "external models"
    models.mkdir()
    weight = models / "ltx-2.5-22b-distilled-transformer-bf16.safetensors"
    weight.touch()
    settings = QSettings(str(isolated / "settings.ini"), QSettings.Format.IniFormat)
    # Older saved preferences must retain their explicit external model paths.
    settings.setValue("preferences", json.dumps({"model_root": str(models), "models": {"transformer": str(weight)}}))
    runtime = RuntimeConfig(isolated, Path(sys.executable), str(models), isolated, isolated)
    for _ in range(2):
        window = create_window(settings=settings, runtime=runtime)
        assert window.model_root.text() == str(models)
        assert window.model_paths["transformer"].text() == str(weight)
        wait(app, lambda: not window.is_busy())
        window.close()
        app.processEvents()


def test_nuke_install_is_repeatable_and_preserves_unrelated_startup(isolated):
    home = isolated / ".nuke"
    home.mkdir()
    original = "import nuke\nnuke.pluginAddPath('/studio/other')\nSTUDIO_SETTING = 42\n"
    (home / "init.py").write_text(original)
    values = {"interpreter": "/runtime/python", "model_root": "/models", "model_config": "", "cache_root": ""}
    first = plugins_install.install_nuke_plugin(home, values)
    assert not first.failures
    installed = (home / "init.py").read_text()
    assert original.strip() in installed
    assert installed.count(plugins_install.NUKE_BLOCK_START) == 1
    assert (home / "init.py.shotbox-backup").read_text() == original
    second = plugins_install.install_nuke_plugin(home, values)
    assert not second.failures
    assert not second.installed and not second.updated
    assert (home / "init.py").read_text() == installed
    assert json.loads((home / "shotbox_preferences.json").read_text())["model_root"] == "/models"
    assert "registered" in plugins_install.nuke_plugin_status(home)


def test_nuke_legacy_registration_migrates_and_custom_registration_is_rejected():
    old = "import nuke\nnuke.pluginAddPath('/tools/Shotbox_alpha/integrations/nuke')\nCUSTOM=1\n"
    updated = plugins_install.nuke_startup_text(old, Path('/new/shotbox_frontend/plugins/nuke'))
    assert 'Shotbox_alpha' not in updated and 'CUSTOM=1' in updated
    nested = "if True:\n    nuke.pluginAddPath('/tools/Shotbox_alpha/integrations/nuke')\n"
    with pytest.raises(ValueError, match="manually"):
        plugins_install.nuke_startup_text(nested, Path('/new/plugins/nuke'))


def test_cross_process_cache_marker_protects_temp_files(isolated):
    directory = app_cache.cache_directory("temp")
    markers = directory / ".active-tasks"
    markers.mkdir()
    marker = markers / "external.json"
    marker.write_text(json.dumps({"pid": os.getpid()}))
    sentinel = directory / "active-external.json"
    sentinel.touch()
    assert app_cache.clear_cache_files()["active_temp"]
    assert sentinel.exists()
    marker.write_text(json.dumps({"pid": 999999999}))
    assert not app_cache.clear_cache_files()["active_temp"]
    assert not sentinel.exists()


def test_shared_configuration_saves_external_paths_and_preserves_other_values(isolated):
    path = configuration.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "studio_note": "preserve", "model_root": "/old/models"}))
    configuration.save_configuration({"interpreter": "/external/runtime/python", "model_root": "/external/models",
                                      "model_config": "", "cache_root": ""})
    saved = json.loads(path.read_text())
    assert saved["studio_note"] == "preserve"
    assert saved["model_root"] == "/external/models"
    assert saved["application_root"] == str(configuration.APPLICATION_ROOT)
    assert configuration.load_configuration()["interpreter"] == "/external/runtime/python"


@pytest.mark.parametrize("saved_python", ["", "/external/custom/python", str(configuration.APPLICATION_ROOT / ".venv/bin/python")])
def test_host_uses_current_python_regardless_of_saved_nuke_override(app, isolated, saved_python):
    from toolbox.shotbox_matte import plugin

    path = configuration.preferences_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "interpreter": saved_python}))
    _settings, runtime = plugin._host_context()
    assert runtime.worker_python == Path(sys.executable)


@pytest.mark.parametrize("application, interpreter", [
    ("/old/shotbox/toolbox/shotbox_matte", "/old/shotbox/toolbox/shotbox_matte/.venv/bin/python"),
    (r"C:\old Shotbox\toolbox\shotbox_matte", r"C:\old Shotbox\toolbox\shotbox_matte\.venv-windows\Scripts\python.exe"),
    (str(configuration.APPLICATION_ROOT), str(configuration.APPLICATION_ROOT / ".venv-linux/bin/python")),
])
def test_legacy_private_runtime_migrates_without_touching_files(isolated, application, interpreter):
    path = configuration.preferences_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "application_root": application, "interpreter": interpreter,
                                "model_root": "/existing/models", "studio_note": "keep"}))
    assert configuration.load_configuration()["interpreter"] == ""
    configuration.save_configuration({"cache_root": "/cache"})
    saved = json.loads(path.read_text())
    assert saved["interpreter"] == ""
    assert saved["model_root"] == "/existing/models"
    assert saved["studio_note"] == "keep"


def test_settings_only_exposes_models_and_cache_and_preserves_nuke_override(app, isolated):
    from toolbox.shotbox_matte.settings_widget import MatteSettingsWidget
    from PyQt6.QtWidgets import QPushButton

    configuration.save_configuration({"interpreter": "/custom/nuke/python", "model_root": "/models"})
    widget = MatteSettingsWidget()
    try:
        assert set(widget.fields) == {"model_root", "model_config", "cache_root"}
        assert not any("Runtime" in button.text() for button in widget.findChildren(QPushButton))
        widget.fields["model_root"].setText("/new/models")
        assert widget.save()
        values = configuration.load_configuration()
        assert values["interpreter"] == "/custom/nuke/python"
        assert values["model_root"] == "/new/models"
    finally:
        widget.close()


@pytest.mark.parametrize("override", ["/custom/nuke/python", "legacy"])
def test_nuke_registration_preserves_custom_override_and_migrates_managed_runtime(isolated, override):
    home = isolated / ".nuke"
    home.mkdir(parents=True)
    old_application = "/old/shotbox/toolbox/shotbox_matte"
    saved_python = old_application + "/.venv/bin/python" if override == "legacy" else override
    (home / "shotbox_preferences.json").write_text(json.dumps({
        "schema_version": 1, "application_root": old_application, "interpreter": saved_python, "studio_note": "keep"}))
    result = plugins_install.install_nuke_plugin(home, {"model_root": "/models"})
    assert not result.failures
    saved = json.loads((home / "shotbox_preferences.json").read_text())
    assert saved["interpreter"] == ("" if override == "legacy" else override)
    assert saved["studio_note"] == "keep"
