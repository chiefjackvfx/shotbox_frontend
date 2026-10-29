from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMenu

import cg_asset_actions as actions
import cg_asset_helpers as helpers
import widgets
from test_shotcard_matchmove import FakeFolders


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_card(app, tmp_path, monkeypatch):
    monkeypatch.setattr(widgets, "HAS_MULTIMEDIA", False)
    for method in ("_setup_nk_polling", "_setup_render_polling", "_setup_preview_polling"):
        monkeypatch.setattr(widgets.ShotCard, method, lambda self: None)
    monkeypatch.setattr(widgets.ImageLoader, "instance", lambda: mock.Mock())
    monkeypatch.setattr(actions, "configured_houdini", lambda: "/configured/houdini")
    cards = []

    def build(root=None, **overrides):
        data = dict(id=7, title="sho010", notes="", base_path=str(root or tmp_path / "shot010"),
                    tasks=[], colourspace="", matchmove_path=None)
        data.update(overrides)
        card = widgets.ShotCard(data, api=mock.Mock(), folders=FakeFolders())
        cards.append(card)
        return card

    yield build
    for card in cards:
        card.close()
        card.deleteLater()
    app.processEvents()


def add_project(root, name="Oak", version="001", spelling="shot_assets"):
    project = root / spelling / "cg" / name / f"{name}_v{version}.hip"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_bytes(b"hip")
    return project


def test_shot_menu_creates_cg_without_precomp_or_matchmove(make_card, monkeypatch):
    card = make_card()
    with mock.patch.object(actions, "create_for_shot") as create:
        menu = QMenu(card)
        assert card._populate_assets_menu(menu)
        action = next(action for action in menu.actions() if action.text() == "Create CG Asset")
        assert action.isEnabled()
        action.trigger()
        create.assert_called_once_with(card)
    assert not Path(card.shot_dir).exists()


def test_shot_creation_discovers_only_own_exports_and_publishes_in_shot(make_card, tmp_path, monkeypatch):
    root = tmp_path / "Project Space" / "VFX" / "timeline" / "shot010"
    own_export = root / "Shot_Assets" / "matchmove" / "export" / "shot_camera_hou.py"
    own_export.parent.mkdir(parents=True)
    own_export.write_text("raise AssertionError('discovery must not run this')")
    other = tmp_path / "job_assets" / "matchmove" / "export" / "other_hou.py"
    other.parent.mkdir(parents=True)
    other.touch()
    prepared = tmp_path / "prepared.hip"
    prepared.write_bytes(b"prepared scene")
    card = make_card(root)
    calls = []

    def create(cg_root, sources, configured, parent, **kwargs):
        calls.append((cg_root, sources, configured, parent, kwargs))
        return helpers.publish_scene(prepared, cg_root, "Oak")

    monkeypatch.setattr("cg_asset_dialog.show_create_dialog", create)
    with mock.patch.object(helpers, "open_scene") as launch:
        actions.create_for_shot(card)
    project = root / "Shot_Assets" / "cg" / "Oak" / "Oak_v001.hip"
    assert project.read_bytes() == b"prepared scene"
    assert (project.parent / "geo").is_dir() and (project.parent / "assets").is_dir()
    assert calls == [(project.parent.parent, [own_export], "/configured/houdini", card,
                      {"context_title": "Shot sho010"})]
    launch.assert_called_once_with(project, "/configured/houdini")
    assert card._shot_cg_buttons[0].text() == project.name
    assert not (root / "job_assets").exists()
    assert not (root / "shot_assets").exists()


def test_camera_discovery_honours_mapped_configured_matchmove(make_card, tmp_path, monkeypatch):
    configured = tmp_path / "Custom Matchmove"
    export = configured / "export" / "custom_hou.py"
    export.parent.mkdir(parents=True)
    export.touch()
    card = make_card(matchmove_path="windows:matchmove")
    card.filesIO.convert_path = lambda path: str(configured) if path == "windows:matchmove" else path
    dialog = mock.Mock(return_value=None)
    monkeypatch.setattr("cg_asset_dialog.show_create_dialog", dialog)
    actions.create_for_shot(card)
    assert dialog.call_args.args[1] == [export]
    assert dialog.call_args.args[0] == Path(card.shot_dir) / "shot_assets" / "cg"
    assert not (Path(card.shot_dir) / "shot_assets").exists()


def test_buttons_open_latest_numeric_version_and_explorer(make_card, tmp_path):
    first = add_project(tmp_path)
    card = make_card(tmp_path)
    button = card._shot_cg_buttons[0]
    assert button.text() == first.name
    assert button.objectName() == "btn_open_shot_cg"
    latest = add_project(tmp_path, version="010")
    add_project(tmp_path, version="9")
    with mock.patch.object(helpers, "open_scene") as launch:
        button.click()
        launch.assert_called_once_with(latest, "/configured/houdini")
    assert button.text() == latest.name
    with mock.patch.object(actions.QMenu, "exec", new=lambda menu, *args: menu.actions()[0].trigger()):
        actions.show_context_menu(card, button, button.rect().center())
    assert card.filesIO.opened_locations == [latest.parent]


def test_refresh_clears_removed_projects_and_changed_shot_paths(make_card, tmp_path):
    project = add_project(tmp_path / "one")
    card = make_card(tmp_path / "one")
    assert len(card._shot_cg_buttons) == 1
    # Data refresh must discard the previous shot's assets.
    card.update_from_data(dict(card.data, base_path=str(tmp_path / "two")))
    assert card._shot_cg_buttons == []
    assert card.shot_cg_assets.isHidden()
    second = add_project(tmp_path / "two", name="Birch")
    card.apply_file_state_snapshot(None)
    assert [button.text() for button in card._shot_cg_buttons] == [second.name]
    second.unlink()
    card.apply_file_state_snapshot(None)
    assert card._shot_cg_buttons == []
    assert project.is_file()


def test_cancel_and_launch_failure_keep_asset_publication_safe(make_card, tmp_path, monkeypatch):
    card = make_card(tmp_path)
    monkeypatch.setattr("cg_asset_dialog.show_create_dialog", lambda *args, **kwargs: None)
    actions.create_for_shot(card)
    assert not (tmp_path / "shot_assets").exists()
    project = add_project(tmp_path)
    monkeypatch.setattr("cg_asset_dialog.show_create_dialog", lambda *args, **kwargs: project)
    with mock.patch.object(helpers, "open_scene", side_effect=OSError("launch failed")), \
            mock.patch.object(actions.QMessageBox, "warning") as warning:
        actions.create_for_shot(card)
    assert "retry" in warning.call_args.args[-1]
    assert project.is_file() and card._shot_cg_buttons[0].text() == project.name


def test_multiple_assets_wrap_elide_and_survive_layout_switches(app, make_card, tmp_path):
    for name in ("Very_Long_Asset_Name_" * 3, "Birch", "Oak"):
        add_project(tmp_path, name=name)
    card = make_card(tmp_path)
    assert len(card._shot_cg_buttons) == 3
    card.show()
    for _ in range(2):
        card.set_v02_grid(True)
        card.prepare_layout_width(420)
        card.resize(420, card.sizeHint().height())
        app.processEvents()
        row = card.shot_cg_assets
        row.fit_buttons()
        row.layout().setGeometry(row.contentsRect())
        app.processEvents()
        for button in card._shot_cg_buttons:
            assert button.v02 and button.elide_mode == Qt.TextElideMode.ElideMiddle
            assert button.property("v02") == "true"
            assert str(tmp_path) in button.toolTip()
            assert button.width() <= max(1, row.contentsRect().width())
        assert row.layout().heightForWidth(150) > row.layout().heightForWidth(700)
        card.set_compact_mode(True)
        assert row.isHidden() and card.btn_open_matchmove.isHidden()
        card.set_compact_mode(False)
        assert not row.isHidden()
        card.set_v02_grid(False)
        assert card.horizontalLayout_3.indexOf(row) == 3
        assert all(button.property("v02") == "false" for button in card._shot_cg_buttons)
    card.close()


def test_assets_can_be_added_and_removed_while_in_grid_mode(make_card, tmp_path):
    card = make_card(tmp_path)
    card.set_v02_grid(True)
    project = add_project(tmp_path)
    card.apply_file_state_snapshot(None)
    assert len(card._shot_cg_buttons) == 1
    project.unlink()
    card.apply_file_state_snapshot(None)
    card.set_v02_grid(False)
    assert card._shot_cg_buttons == [] and card.shot_cg_assets.isHidden()
