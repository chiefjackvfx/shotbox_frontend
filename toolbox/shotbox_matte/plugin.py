"""Create Matte as an owned window in Shotbox's existing Qt application."""
from pathlib import Path

from .configuration import APPLICATION_ROOT, load_configuration


def open_tool(parent):
    from PyQt6.QtCore import QSettings, QStandardPaths, Qt
    from app_cache import cache_directory, temporary_cache_lease, temporary_cache_task
    from .shotbox_matte.gui import create_window
    from .shotbox_matte.runtime import RuntimeConfig

    values = load_configuration()
    state = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / "ShotBox" / "Matte"
    state.mkdir(parents=True, exist_ok=True)
    temporary = cache_directory("temp") / "shotbox-matte"
    runtime = RuntimeConfig(APPLICATION_ROOT, Path(values["interpreter"]), values["model_root"],
                            state, temporary, values["model_config"])
    lease = temporary_cache_lease()
    lease.__enter__()
    try:
        temporary.mkdir(parents=True, exist_ok=True)
        settings = QSettings(str(state / "settings.ini"), QSettings.Format.IniFormat)
        window = create_window(settings=settings, runtime=runtime)
        window.setParent(parent, Qt.WindowType.Window)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window._start_job = temporary_cache_task(window._start_job)
        window.destroyed.connect(lambda: lease.__exit__(None, None, None))
        return window
    except BaseException:
        lease.__exit__(None, None, None)
        raise
