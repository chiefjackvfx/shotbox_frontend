"""Dashboard integration for filesystem-backed job CG assets."""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMenu, QMessageBox, QToolButton

import cg_asset_helpers as helpers
from settings import get_settings_manager


def configured_houdini():
    return str(get_settings_manager().get("houdini_exe_path", "") or "")


def refresh_buttons(page, *, loading=False):
    buttons = getattr(page, "_job_cg_buttons", [])
    layout = getattr(page, "horizontalLayout_assets_actions", None)
    if layout is None:
        return
    assets = None if loading else page._resolve_job_assets_dir()
    try:
        projects = helpers.list_cg_projects(assets) if assets else []
    except OSError:
        projects = []
    while len(buttons) > len(projects):
        button = buttons.pop()
        button.hide()
        layout.removeWidget(button)
        button.deleteLater()
    while len(buttons) < len(projects):
        button = QToolButton(page.btn_open_job_assets.parentWidget())
        button.setObjectName("btn_open_job_cg")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.clicked.connect(lambda checked=False, button=button: open_project(page, button))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, button=button: show_context_menu(page, button, pos))
        layout.insertWidget(layout.indexOf(page.btn_open_job_assets) + 1 + len(buttons), button)
        buttons.append(button)
    page._job_cg_buttons = buttons
    for button, project in zip(buttons, projects):
        button.setProperty("project_path", str(project))
        button.setText(project.name)
        button.setToolTip(f"Open Houdini scene\n{project}\nRight-click to open in Explorer")
        button.show()
    layout.invalidate()


def open_project(page, button):
    previous = Path(button.property("project_path"))
    try:
        assets = page._resolve_job_assets_dir()
        projects = helpers.list_cg_projects(assets) if assets else []
        latest = next((project for project in projects if project.parent == previous.parent), None)
        if latest is None:
            raise FileNotFoundError(f"This Houdini scene is unavailable:\n{previous}")
        helpers.open_scene(latest, configured_houdini())
    except Exception as exc:
        QMessageBox.warning(page, "Open CG Asset", str(exc))
    finally:
        refresh_buttons(page)


def show_context_menu(page, button, pos):
    menu = QMenu(page)
    action = menu.addAction("Open in Explorer")
    action.triggered.connect(lambda: open_folder(page, Path(button.property("project_path")).parent))
    try:
        menu.exec(button.mapToGlobal(pos))
    finally:
        menu.deleteLater()


def open_folder(page, folder):
    try:
        if not folder.is_dir():
            raise FileNotFoundError(f"This asset folder is unavailable:\n{folder}")
        page.filesIO.openFileLocation(str(folder))
    except Exception as exc:
        QMessageBox.warning(page, "Open CG Asset Folder", str(exc))


def create_for_job(page):
    from cg_asset_dialog import show_create_dialog
    assets = page._resolve_job_assets_dir()
    if assets is None:
        QMessageBox.warning(page, "Create CG Asset", "No job assets folder could be resolved.")
        return
    try:
        sources = helpers.discover_camera_exports(
            page._active_job_data() or {}, assets, page.filesIO.convert_path,
            page._resolved_job_matchmove_dir())
        project = show_create_dialog(assets, sources, configured_houdini(), page)
        if project is not None:
            refresh_buttons(page)
            try:
                helpers.open_scene(project, configured_houdini())
            except Exception as exc:
                QMessageBox.warning(page, "CG Asset Created",
                                    f"Saved {project}\n\nCould not open Houdini:\n{exc}\n\n"
                                    "Use the asset button to retry opening.")
    except Exception as exc:
        QMessageBox.warning(page, "Create CG Asset", str(exc))
