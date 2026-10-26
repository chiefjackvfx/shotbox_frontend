"""Create Matte as an owned window in Shotbox's existing Qt application."""
from pathlib import Path
import sys

from .configuration import APPLICATION_ROOT, load_configuration


def _host_context():
    from PyQt6.QtCore import QSettings, QStandardPaths
    from app_cache import cache_directory
    from .shotbox_matte.runtime import RuntimeConfig

    values = load_configuration()
    state = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "ShotBox" / "Matte"
    state.mkdir(parents=True, exist_ok=True)
    temporary = cache_directory("temp") / "shotbox-matte"
    runtime = RuntimeConfig(APPLICATION_ROOT, Path(sys.executable), values["model_root"],
                            state, temporary, values["model_config"])
    settings = QSettings(str(state / "settings.ini"), QSettings.Format.IniFormat)
    return settings, runtime


def _owned_window(parent, factory):
    from PyQt6.QtCore import Qt
    from app_cache import temporary_cache_lease

    settings, runtime = _host_context()
    lease = temporary_cache_lease()
    lease.__enter__()
    try:
        runtime.temporary_directory.mkdir(parents=True, exist_ok=True)
        window = factory(settings, runtime)
        window.setParent(parent, Qt.WindowType.Window)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(lambda: lease.__exit__(None, None, None))
        return window
    except BaseException:
        lease.__exit__(None, None, None)
        raise


def open_tool(parent):
    from app_cache import temporary_cache_task
    from .shotbox_matte.gui import create_window

    window = _owned_window(parent, lambda settings, runtime: create_window(settings=settings, runtime=runtime))
    window._start_job = temporary_cache_task(window._start_job)
    return window


def open_roto(parent, source_frame):
    from .roto_dialog import RotoDialog

    return _owned_window(parent, lambda settings, runtime: RotoDialog(
        parent, source_frame, settings=settings, runtime=runtime))
