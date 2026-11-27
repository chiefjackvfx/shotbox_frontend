from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication

import settings


class InMemorySettingsManager:
    def __init__(self, initial: dict | None = None):
        self._settings = copy.deepcopy(settings.DEFAULT_SETTINGS)
        if initial:
            self._settings.update(initial)
        self.settings_path = "/tmp/test_settings.yaml"

    def get(self, key: str, default=None):
        keys = key.split(".")
        value = self._settings
        try:
            for part in keys:
                value = value[part]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value, save: bool = True):
        keys = key.split(".")
        target = self._settings
        for part in keys[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[keys[-1]] = value

    def save(self):
        return True

    def get_polling_interval_ms(self):
        return int(self.get("polling_interval", 5) * 1000)


class SettingsStartupOptionalPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_settings_page(self):
        with (
            mock.patch.object(
                settings.SettingsPage, "_load_django_users", lambda self: None
            ),
            mock.patch.object(
                settings.SettingsPage, "_refresh_update_panel", lambda self: None
            ),
        ):
            return settings.SettingsPage(
                settings_manager=InMemorySettingsManager()
            )

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("desktop-file-edit"),
                         "Requires Linux desktop-file-utils")
    def test_terminal_checkbox_updates_linux_menu_shortcut(self):
        from PyQt6.QtWidgets import QCheckBox
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": directory, "GIO_LAUNCHED_DESKTOP_FILE": ""}
        ):
            applications = Path(directory) / "applications"
            applications.mkdir()
            shortcut = applications / "shotbox.desktop"
            shortcut.write_text(
                "[Desktop Entry]\nType=Application\nName=Shotbox\n"
                f'Exec="{settings.desktop_launcher.RUN_SCRIPT}"\nTerminal=true\n'
            )
            for initial in (True, False):
                page = self._make_settings_page()
                self.addCleanup(page.close)
                checkbox = next(box for box in page.findChildren(QCheckBox)
                                if box.text() == "Show terminal window when Shotbox starts")
                self.assertEqual(checkbox.isChecked(), initial)
                checkbox.click()
                self.assertEqual(settings.desktop_launcher.terminal_setting(shortcut), not initial)

    def test_dedicated_terminal_detaches_only_when_hidden(self):
        launcher = settings.desktop_launcher
        for preference in (False, True, None):
            with (
                self.subTest(preference=preference),
                mock.patch.object(launcher, "find_launcher", return_value=Path("shotbox.desktop")),
                mock.patch.object(launcher, "terminal_setting", return_value=preference),
                mock.patch.object(launcher.subprocess, "Popen") as spawn,
                mock.patch.object(launcher.os, "execv") as foreground,
            ):
                launcher.launch_from_dedicated_terminal()
                if preference is False:
                    self.assertTrue(spawn.call_args.kwargs["start_new_session"])
                    foreground.assert_not_called()
                else:
                    foreground.assert_called_once()
                    spawn.assert_not_called()

    def test_settings_page_has_no_manual_nuke_time_panel_or_private_state_access(self):
        from plugins.nuke.nuke_time_calc import managed_setup
        from PyQt6.QtWidgets import QGroupBox
        manager = InMemorySettingsManager()
        manager.set('server_url', 'http://192.168.10.88:8000/api/')
        manager.set('django_username', 7)
        with (
            mock.patch.object(managed_setup, 'load_config') as load_config,
            mock.patch.object(managed_setup, 'save_config') as save_config,
            mock.patch.object(managed_setup, 'install') as install,
            mock.patch.object(settings.SettingsPage, '_load_django_users'),
            mock.patch.object(settings.SettingsPage, '_refresh_update_panel'),
        ):
            page = settings.SettingsPage(settings_manager=manager)
            self.addCleanup(page.close)
            self.assertFalse(hasattr(page, 'nuke_time_widget'))
            self.assertFalse(hasattr(settings, 'NukeTimeSettingsWidget'))
            self.assertFalse(hasattr(page, '_update_nuke_time_context'))
            self.assertFalse(any('nuke time' in group.title().lower()
                                 for group in page.findChildren(QGroupBox)))
            self.assertEqual(page.server_url_edit.text(), 'http://192.168.10.88:8000/api/')
            self.assertEqual(manager.get('django_username'), 7)
            load_config.assert_not_called()
            save_config.assert_not_called()
            install.assert_not_called()

    def test_saved_server_and_artist_signals_expose_complete_normal_settings_snapshot(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                page = self._make_settings_page()
                self.addCleanup(page.close)
                events = []
                page.server_url_changed.connect(lambda value, page=page: events.append(
                    ('server_url', value, page._settings.get('django_username'))))
                page.settings_changed.connect(lambda key, value, page=page: events.append(
                    (key, value, page._settings.get('server_url'))))
                page.server_url_edit.setText('  http://192.168.10.88:8000/api/  ')
                page.django_user_combo.addItem('Artist 8', 8)
                page.django_user_combo.setCurrentIndex(page.django_user_combo.findData(8))
                with mock.patch.object(settings.QMessageBox, 'information'):
                    page._save_all_settings(automatic=automatic)
                self.assertIn(('server_url', 'http://192.168.10.88:8000/api/', 8), events)
                self.assertIn(('django_username', 8, 'http://192.168.10.88:8000/api/'), events)
                self.assertFalse(hasattr(page, 'nuke_time_widget'))
                self.assertFalse(page._autosave_timer.isActive())

    def test_failed_save_does_not_emit_server_or_artist_signals(self):
        page = self._make_settings_page()
        self.addCleanup(page.close)
        events = []
        page.server_url_changed.connect(lambda value: events.append(('server_url', value)))
        page.settings_changed.connect(lambda key, value: events.append((key, value)))
        page.server_url_edit.setText('https://changed.example/api/')
        page.django_user_combo.addItem('Artist 8', 8)
        page.django_user_combo.setCurrentIndex(page.django_user_combo.findData(8))
        with mock.patch.object(page._settings, 'save', return_value=False):
            page._save_all_settings(automatic=True)
        self.assertEqual(events, [])
        self.assertIn('Save failed', page.save_status_label.text())

    def test_cg_executables_browse_and_settings_persistence(self):
        page = self._make_settings_page()
        self.addCleanup(page.close)
        self.assertEqual(page.houdini_exe_path_edit.text(), "")
        with mock.patch.object(settings.QFileDialog, "getOpenFileName", return_value=("/opt/blender", "")):
            page.blender_exe_browse_btn.click()
        self.assertEqual(page._settings.get("blender_exe_path"), "")
        with mock.patch.object(settings.QFileDialog, "getOpenFileName", return_value=("/Houdini/bin/houdini", "")):
            page.houdini_exe_browse_btn.click()
        self.assertEqual(page._settings.get("houdini_exe_path"), "")
        page._save_all_settings(automatic=True)
        self.assertEqual(page._settings.get("houdini_exe_path"), "/Houdini/bin/houdini")
        page._load_current_values()
        self.assertEqual(page.houdini_exe_path_edit.text(), "/Houdini/bin/houdini")
        self.assertEqual(page.blender_exe_path_edit.text(), "/opt/blender")

    def _update_status(self, *, can_update=True):
        return settings.app_update.UpdateStatus(
            supported=True,
            can_check=True,
            can_update=can_update,
            has_update=True,
            is_dirty=not can_update,
            branch="main",
            current_branch="main",
            current_version="1.0.0",
            current_commit="current",
            current_display="1.0.0 (current)",
            status_message="Update available.",
            remote_version="2.0.0",
            remote_commit="remote",
            remote_display="2.0.0 (remote)",
            changelog_preview="2.0.0\n- Automatic updates",
        )

    def test_plugin_panel_tracks_unsaved_executable_and_installs(self):
        page = self._make_settings_page()
        self.addCleanup(page.close)
        self.assertFalse(page.install_3de_plugins_btn.isEnabled())
        self.assertIn("3D_Cones.py", page.plugins_scripts_label.text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "bin" / "3DE4"
            executable.parent.mkdir()
            executable.touch()
            destination = root / "sys_data" / "py_scripts"
            destination.mkdir(parents=True)
            page.threede_exe_path_edit.setText(str(executable))
            self.assertTrue(page.install_3de_plugins_btn.isEnabled())
            self.assertEqual(page.plugins_destination_label.text(), str(destination))
            self.assertEqual(page._settings.get("threede_exe_path"), "")
            install = settings.plugins_install.install_3de_plugins
            def checked_install(value):
                self.assertFalse(page.install_3de_plugins_btn.isEnabled())
                return install(value)
            with mock.patch.object(settings.plugins_install, "install_3de_plugins", side_effect=checked_install), mock.patch.object(settings.QMessageBox, "information") as information:
                page.install_3de_plugins_btn.click()
                information.assert_called_once()
            self.assertTrue((destination / "shotbox_publish.py").is_file())
            self.assertTrue((destination / "3D_Cones.py").is_file())
            self.assertFalse((destination / "tests").exists())
            self.assertIn("Restart 3DE", page.plugins_status_label.text())
            self.assertTrue(page.install_3de_plugins_btn.isEnabled())
            with mock.patch.object(settings.QMessageBox, "information"):
                page.install_3de_plugins_btn.click()
            self.assertIn(f"unchanged: {len(settings.plugins_install.bundled_scripts())}", page.plugins_status_label.text())
            page._load_current_values()
            self.assertFalse(page.install_3de_plugins_btn.isEnabled())
            self.assertEqual(page.plugins_status_label.text(), "")

    def test_plugin_panel_reports_partial_success_and_errors(self):
        page = self._make_settings_page()
        self.addCleanup(page.close)
        with mock.patch.object(settings.plugins_install, "resolve_destination", return_value=Path("/fake/scripts")):
            page.threede_exe_path_edit.setText("/fake/3DE4")
            result = settings.plugins_install.InstallResult(
                Path("/fake/scripts"), installed=["one.py"], failures={"two.py": "access denied"}
            )
            with mock.patch.object(settings.plugins_install, "install_3de_plugins", return_value=result), mock.patch.object(settings.QMessageBox, "warning") as warning:
                page.install_3de_plugins_btn.click()
                warning.assert_called_once()
            self.assertIn("Installed: 1", page.plugins_status_label.text())
            self.assertIn("two.py: access denied", page.plugins_status_label.text())
            self.assertTrue(page.install_3de_plugins_btn.isEnabled())
            with mock.patch.object(settings.plugins_install, "install_3de_plugins", side_effect=PermissionError("read-only")), mock.patch.object(settings.QMessageBox, "warning"):
                page.install_3de_plugins_btn.click()
            self.assertIn("read-only", page.plugins_status_label.text())
            self.assertTrue(page.install_3de_plugins_btn.isEnabled())
        page.threede_exe_path_edit.setText("/missing/3DE4")
        self.assertFalse(page.install_3de_plugins_btn.isEnabled())

    def test_plugin_panel_disables_install_when_bundle_missing(self):
        page = self._make_settings_page()
        self.addCleanup(page.close)
        with mock.patch.object(settings.plugins_install, "bundled_scripts", side_effect=FileNotFoundError("bundle missing")):
            page._refresh_plugins_panel()
        self.assertFalse(page.install_3de_plugins_btn.isEnabled())
        self.assertIn("bundle missing", page.plugins_scripts_label.text())

    def test_houdini_plugin_install_and_settings_persistence(self):
        page = self._make_settings_page()
        self.addCleanup(page.close)
        self.assertFalse(page.install_houdini_plugin_btn.isEnabled())
        with tempfile.TemporaryDirectory() as tmp:
            page.houdini_pref_combo.setCurrentText(tmp)
            self.assertTrue(page.install_houdini_plugin_btn.isEnabled())
            self.assertIn(tmp, page.houdini_destination_label.text())
            with mock.patch.object(settings.QMessageBox, "information"):
                page.install_houdini_plugin_btn.click()
                page._save_all_settings()
            self.assertEqual(page._settings.get("houdini_user_pref_dir"), tmp)
            self.assertTrue((Path(tmp) / "packages/shotbox_3de_import.json").is_file())
            self.assertIn("Restart Houdini", page.houdini_status_label.text())
            page.houdini_pref_combo.setCurrentText("")
            page._load_current_values()
            self.assertEqual(page.houdini_pref_combo.currentText(), tmp)
            with mock.patch.object(settings.plugins_install, "install_houdini_plugin", side_effect=PermissionError("denied")), mock.patch.object(settings.QMessageBox, "warning") as warning:
                page.install_houdini_plugin_btn.click()
                warning.assert_called_once()
            self.assertIn("denied", page.houdini_status_label.text())
            self.assertTrue(page.install_houdini_plugin_btn.isEnabled())

    def test_confirmed_update_launches_without_second_question(self):
        page = self._make_settings_page()
        fake_app = mock.Mock()
        try:
            with (
                mock.patch.object(
                    settings.app_update,
                    "launch_update_script",
                    return_value=(True, ""),
                ) as launch,
                mock.patch.object(settings.os, "getpid", return_value=1234),
                mock.patch.object(settings.QMessageBox, "question") as question,
                mock.patch.object(
                    settings.QApplication, "instance", return_value=fake_app
                ),
            ):
                launched = page.launch_update_and_restart(
                    self._update_status(), parent=page, confirm=False
                )

            self.assertTrue(launched)
            launch.assert_called_once_with(1234)
            question.assert_not_called()
            fake_app.quit.assert_called_once_with()
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_update_launch_failure_keeps_app_open_and_reports_error(self):
        page = self._make_settings_page()
        fake_app = mock.Mock()
        try:
            with (
                mock.patch.object(
                    settings.app_update,
                    "launch_update_script",
                    return_value=(False, "updater missing"),
                ),
                mock.patch.object(settings.QMessageBox, "critical") as critical,
                mock.patch.object(
                    settings.QApplication, "instance", return_value=fake_app
                ),
            ):
                launched = page.launch_update_and_restart(
                    self._update_status(), parent=page, confirm=False
                )

            self.assertFalse(launched)
            critical.assert_called_once()
            fake_app.quit.assert_not_called()
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_manual_update_control_keeps_confirmation_step(self):
        page = self._make_settings_page()
        status = self._update_status()
        try:
            with (
                mock.patch.object(
                    settings.app_update, "check_for_updates", return_value=status
                ),
                mock.patch.object(
                    page, "launch_update_and_restart", return_value=False
                ) as launch,
                mock.patch.object(settings.QApplication, "setOverrideCursor"),
                mock.patch.object(settings.QApplication, "restoreOverrideCursor"),
            ):
                page._on_update_and_restart()

            launch.assert_called_once_with(status, confirm=True)
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_normalize_changelog_markdown_trims_trailing_blank_blocks(self):
        changelog = """# Changelog

## 1.0.0 - 2026-03-28

### Changed

- First change
- Second change
-



"""

        normalized = settings._normalize_changelog_markdown(changelog)

        self.assertEqual(
            normalized,
            "\n".join(
                [
                    "# Changelog",
                    "",
                    "## 1.0.0 - 2026-03-28",
                    "",
                    "### Changed",
                    "",
                    "- First change",
                    "- Second change",
                ]
            ),
        )

    def test_startup_optional_page_checkboxes_load_and_save(self):
        manager = InMemorySettingsManager(
            {
                "enable_assignment_board": True,
                "enable_review_page": False,
                "enable_activity_page": True,
                "enable_import_page": False,
                "enable_xml_import_page": True,
                "enable_timeline_import_page": True,
            }
        )

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            choices = page.startup_tab_combo
            self.assertEqual([choices.itemText(i) for i in range(choices.count())], [
                "Tasks / Nuke dash", "Assignment Board", "Review", "Timeline Review",
                "Import (Legacy)", "XML Import V2 (Legacy)", "Import Timeline",
                "Activity", "Toolbox", "Settings",
            ])
            choices.setCurrentIndex(choices.findText("Toolbox"))
            self.assertTrue(page.enable_assignment_board_check.isChecked())
            self.assertFalse(page.enable_review_page_check.isChecked())
            self.assertTrue(page.enable_activity_page_check.isChecked())
            self.assertFalse(page.enable_import_page_check.isChecked())
            self.assertTrue(page.enable_xml_import_page_check.isChecked())
            self.assertTrue(page.enable_timeline_import_page_check.isChecked())

            page.enable_assignment_board_check.setChecked(False)
            page.enable_review_page_check.setChecked(True)
            page.enable_activity_page_check.setChecked(False)
            page.enable_import_page_check.setChecked(True)
            page.enable_xml_import_page_check.setChecked(False)
            page.enable_timeline_import_page_check.setChecked(False)

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertEqual(manager.get("startup_tab"), 9)
            self.assertFalse(manager.get("enable_assignment_board"))
            self.assertTrue(manager.get("enable_review_page"))
            self.assertFalse(manager.get("enable_activity_page"))
            self.assertTrue(manager.get("enable_import_page"))
            self.assertFalse(manager.get("enable_xml_import_page"))
            self.assertFalse(manager.get("enable_timeline_import_page"))
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_project_load_profiler_debug_checkbox_loads_and_saves(self):
        manager = InMemorySettingsManager(
            {
                "debug_modes": {
                    "general": False,
                    "api_calls": False,
                    "ui_updates": False,
                    "notifications": False,
                    "project_load_profiler": True,
                    "suppress_qt_multimedia_warnings": False,
                }
            }
        )

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertTrue(page.debug_project_load_profiler_check.isChecked())

            page.debug_project_load_profiler_check.setChecked(False)

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertFalse(manager.get("debug_modes.project_load_profiler"))
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_preview_size_combo_supports_nothumb_and_saves(self):
        manager = InMemorySettingsManager({"preview_thumbnail_size": "NoThumb"})

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            combo_items = [
                page.preview_size_combo.itemText(i)
                for i in range(page.preview_size_combo.count())
            ]
            self.assertIn("NoThumb", combo_items)
            self.assertEqual(page.preview_size_combo.currentText(), "NoThumb")

            page.preview_size_combo.setCurrentText("Small")

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertEqual(manager.get("preview_thumbnail_size"), "Small")
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_quick_view_screen_percentage_loads_and_saves(self):
        manager = InMemorySettingsManager({"quick_view_screen_percentage": 55})

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertEqual(page.quick_view_size_spin.value(), 55)
            self.assertEqual(page.quick_view_size_spin.minimum(), 25)
            self.assertEqual(page.quick_view_size_spin.maximum(), 100)

            page.quick_view_size_spin.setValue(45)
            emitted = []
            page.settings_changed.connect(
                lambda key, value: emitted.append((key, value))
            )

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertEqual(manager.get("quick_view_screen_percentage"), 45)
            self.assertIn(("quick_view_screen_percentage", 45), emitted)
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_nukedash_task_style_combo_loads_and_saves(self):
        manager = InMemorySettingsManager({"nukedash_task_style": "checklist"})

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertEqual(page.nukedash_task_style_combo.currentData(), "checklist")

            page.nukedash_task_style_combo.setCurrentIndex(
                page.nukedash_task_style_combo.findData("card")
            )

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertEqual(manager.get("nukedash_task_style"), "card")
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_nukedash_task_style_defaults_to_checklist(self):
        manager = InMemorySettingsManager()

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertEqual(page.nukedash_task_style_combo.currentData(), "checklist")
            self.assertEqual(manager.get("nukedash_task_style"), "checklist")
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_threede_executable_field_loads_and_saves(self):
        manager = InMemorySettingsManager(
            {"threede_exe_path": r"C:\Users\Giger\Documents\3DE4_win64_r8.0v2\bin\3DE4.exe"}
        )

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertEqual(
                page.threede_exe_path_edit.text(),
                r"C:\Users\Giger\Documents\3DE4_win64_r8.0v2\bin\3DE4.exe",
            )

            page.threede_exe_path_edit.setText(r"D:\Apps\3DE4\bin\3DE4.exe")

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertEqual(manager.get("threede_exe_path"), r"D:\Apps\3DE4\bin\3DE4.exe")
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_change_log_dropdown_populates_and_updates_scroll_view(self):
        manager = InMemorySettingsManager()

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertGreaterEqual(page.change_log_combo.count(), 2)
            self.assertEqual(page.change_log_combo.itemText(0), "All Changes")
            self.assertIn("Changelog", page.change_log_view.toPlainText())
            self.assertIn("<h1", page.change_log_view.toHtml())

            page.change_log_combo.setCurrentIndex(1)
            entry_heading = page.change_log_combo.currentText()
            entry_text = page.change_log_view.toPlainText()

            self.assertIn(entry_heading, entry_text)
            self.assertIn("<h2", page.change_log_view.toHtml())
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
