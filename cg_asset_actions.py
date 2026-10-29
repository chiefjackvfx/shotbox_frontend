"""Dashboard and shot-card integration for filesystem-backed CG assets."""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QMenu, QMessageBox, QSizePolicy, QToolButton

import cg_asset_helpers as helpers
from settings import get_settings_manager
from flow_layout import FlowLayout
from presentation_widgets import FlexibleButton


def configured_houdini():
    return str(get_settings_manager().get("houdini_exe_path", "") or "")


def job_cg_root(page):
    assets = page._resolve_job_assets_dir()
    return helpers.resolve_cg_root(assets.parent, "job_assets") if assets else None


def shot_cg_root(card):
    root = card._normalized_shot_dir()
    return helpers.resolve_cg_root(Path(root), "shot_assets") if root else None


def refresh_buttons(page, *, loading=False):
    buttons = getattr(page, "_job_cg_buttons", [])
    layout = getattr(page, "horizontalLayout_assets_actions", None)
    if layout is None:
        return
    cg_root = None if loading else job_cg_root(page)
    try:
        projects = helpers.list_cg_projects(cg_root) if cg_root else []
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
        cg_root = job_cg_root(page)
        projects = helpers.list_cg_projects(cg_root) if cg_root else []
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
        project = show_create_dialog(job_cg_root(page), sources, configured_houdini(), page)
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


class ShotCgAssetRow(QFrame):
    """Wrap asset buttons and constrain long filenames to the available width."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shot_cg_assets")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        FlowLayout(self, margin=0, h_spacing=6, v_spacing=4)
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_buttons()

    def fit_buttons(self):
        for button in self.findChildren(FlexibleButton):
            limit = max(1, min(240, self.contentsRect().width()))
            if button.preferred_width_limit != limit:
                button.preferred_width_limit = limit
                button.updateGeometry()
        self.layout().invalidate()


def update_shot_visibility(card):
    buttons = getattr(card, "_shot_cg_buttons", [])
    compact = getattr(card, "_compact_mode", False)
    grid = getattr(card, "_v02_grid", False)
    card.shot_cg_assets.setVisible(bool(buttons) and not compact)
    for button in buttons:
        # Keep filename elision in every presentation, while QSS follows the card.
        button.v02 = True
        button.setProperty("v02", "true" if grid else "false")
        button.setProperty("compact", "true" if compact else "false")
        button.style().unpolish(button)
        button.style().polish(button)
        button.updateGeometry()
    card.shot_btns.setMaximumHeight(16777215 if buttons or grid else 50)
    card.shot_cg_assets.fit_buttons()


def refresh_shot_buttons(card):
    cg_root = shot_cg_root(card)
    try:
        projects = helpers.list_cg_projects(cg_root) if cg_root else []
    except OSError:
        projects = []
    buttons = getattr(card, "_shot_cg_buttons", [])
    layout = card.shot_cg_assets.layout()
    while len(buttons) > len(projects):
        button = buttons.pop()
        button.hide()
        layout.removeWidget(button)
        button.deleteLater()
    while len(buttons) < len(projects):
        button = FlexibleButton(card.shot_cg_assets)
        button.setObjectName("btn_open_shot_cg")
        button.fit_text = True
        button.v02 = True
        button.elide_mode = Qt.TextElideMode.ElideMiddle
        button.clicked.connect(lambda checked=False, button=button: open_shot_project(card, button))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda pos, button=button: show_context_menu(card, button, pos))
        layout.addWidget(button)
        buttons.append(button)
    card._shot_cg_buttons = buttons
    for button, project in zip(buttons, projects):
        button.setProperty("project_path", str(project))
        button.setText(project.name)
        button.setToolTip(f"Open Houdini scene\n{project}\nRight-click to open in Explorer")
        button.show()
    update_shot_visibility(card)
    layout.invalidate()
    card.updateGeometry()


def open_shot_project(card, button):
    previous = Path(button.property("project_path"))
    try:
        cg_root = shot_cg_root(card)
        projects = helpers.list_cg_projects(cg_root) if cg_root else []
        latest = next((project for project in projects if project.parent == previous.parent), None)
        if latest is None:
            raise FileNotFoundError(f"This Houdini scene is unavailable:\n{previous}")
        helpers.open_scene(latest, configured_houdini())
    except Exception as exc:
        QMessageBox.warning(card, "Open CG Asset", str(exc))
    finally:
        refresh_shot_buttons(card)


def create_for_shot(card):
    from cg_asset_dialog import show_create_dialog
    cg_root = shot_cg_root(card)
    if cg_root is None:
        QMessageBox.warning(card, "Create CG Asset", "No shot assets folder could be resolved.")
        return
    try:
        sources = helpers.discover_exports_in_matchmove([Path(card._resolved_matchmove_dir())])
        title = str((card.data or {}).get("title") or Path(card._normalized_shot_dir()).name)
        project = show_create_dialog(cg_root, sources, configured_houdini(), card, context_title=f"Shot {title}")
        if project is not None:
            refresh_shot_buttons(card)
            try:
                helpers.open_scene(project, configured_houdini())
            except Exception as exc:
                QMessageBox.warning(card, "CG Asset Created",
                                    f"Saved {project}\n\nCould not open Houdini:\n{exc}\n\n"
                                    "Use the asset button to retry opening.")
    except Exception as exc:
        QMessageBox.warning(card, "Create CG Asset", str(exc))
