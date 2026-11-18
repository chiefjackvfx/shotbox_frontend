import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from datetime import datetime, timezone
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QFrame, QPushButton, QLabel, QVBoxLayout, QWidget

from app_cache import cache_directory
import themes


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_style = self.app.styleSheet()
        self.original_theme = getattr(self.app, "_shotbox_theme", themes.DEFAULT_THEME)
        self.addCleanup(self.restore_theme)
        themes.apply_theme(themes.DEFAULT_THEME)

    def test_window_logo_follows_theme(self):
        from PyQt6.QtGui import QIcon
        window = QWidget()
        for theme, asset in (("gta6_vice_sunset.qss", "shotbox_vice_logo.png"),
                             (themes.CHRISTMAS_THEME, "shotbox_christmas_logo.png"),
                             (themes.DEFAULT_THEME, "shotbox_logo.png")):
            themes.apply_theme(theme)
            expected = QIcon(str(themes.UI_DIR / asset)).pixmap(64, 64)
            self.assertFalse(expected.isNull())
            self.assertEqual(window.windowIcon().pixmap(64, 64).toImage(), expected.toImage())

    def restore_theme(self):
        themes.apply_theme(self.original_theme)
        self.app.setStyleSheet(self.original_style)

    def test_existing_and_new_local_styles_roundtrip_without_colour_drift(self):
        frame = QFrame()
        self.addCleanup(frame.close)
        source = "background-color: #1A1D23; color: #D4D4D4; border: 1px solid #FF6B35;"
        themes.set_widget_style(frame, source)
        themes.apply_theme("gta6_vice_sunset.qss")
        frame.ensurePolished()
        self.assertEqual(frame.palette().color(QPalette.ColorRole.Window).name(), "#171126")
        self.assertEqual(frame.palette().color(QPalette.ColorRole.WindowText).name(), "#fff5ea")
        self.assertIn("#F48BB5", frame.styleSheet())
        late_frame = QFrame()
        self.addCleanup(late_frame.close)
        themes.set_widget_style(late_frame, source)
        self.assertEqual(late_frame.styleSheet(), frame.styleSheet())
        themes.apply_theme(themes.DEFAULT_THEME)
        self.assertEqual(frame.styleSheet(), source)
        self.assertEqual(late_frame.styleSheet(), source)
        themes.apply_theme("gta6_vice_sunset.qss")
        self.assertIn("#F48BB5", frame.styleSheet())

    def test_console_chrome_and_typography_switch_off_with_original_theme(self):
        window = QWidget()
        self.addCleanup(window.close)
        layout = QVBoxLayout(window)
        header = themes.create_theme_header(window)
        layout.addWidget(header)
        window.resize(900, 140)
        window.show()
        self.app.processEvents()
        self.assertTrue(header.isHidden())
        themes.apply_theme("gta6_vice_sunset.qss")
        self.app.processEvents()
        self.assertTrue(header.isVisible())
        title = header.findChild(QLabel, "vice_console_title")
        self.assertTrue(title.font().italic())
        self.assertFalse(header.property("compact"))
        window.resize(360, 140)
        self.app.processEvents()
        self.assertTrue(header.property("compact"))
        themes.apply_theme(themes.DEFAULT_THEME)
        self.app.processEvents()
        self.assertTrue(header.isHidden())
        self.assertEqual(self.app.styleSheet(),
                         (themes.UI_DIR / themes.DEFAULT_THEME).read_text(encoding="utf-8"))

    def test_private_toolbox_style_restores_after_console_theme_is_disabled(self):
        from toolbox_page import ToolboxPage
        page = ToolboxPage()
        self.addCleanup(page.close)
        original = page.styleSheet()
        themes.apply_theme("gta6_vice_sunset.qss")
        page.ensurePolished()
        title = page.findChild(QLabel, "toolbox_title")
        self.assertTrue(title.font().italic())
        themes.apply_theme(themes.DEFAULT_THEME)
        self.assertEqual(page.styleSheet(), original)
        self.assertFalse(title.font().italic())

    def test_christmas_switches_back_to_gta_and_original_without_style_drift(self):
        header = themes.create_theme_header()
        self.addCleanup(header.deleteLater)
        frame = QFrame()
        self.addCleanup(frame.close)
        source = 'background: #1A1D23; color: #D4D4D4;'
        themes.set_widget_style(frame, source)
        themes.apply_theme(themes.CHRISTMAS_THEME)
        themes.refresh_theme_headers(datetime(2026, 12, 1, tzinfo=timezone.utc))
        frame.ensurePolished()
        self.assertEqual(frame.palette().color(QPalette.ColorRole.Window).name(), '#0c2019')
        self.assertFalse(header.isHidden())
        self.assertEqual(header.findChild(QLabel, 'vice_console_title').text(), 'SHOTBOX')
        self.assertIn('TO THE PARTY', header.findChild(QLabel, 'christmas_party_countdown').text())
        button = QPushButton()
        self.addCleanup(button.close)
        button.setObjectName('btn_status')
        button.setProperty('status', 'approved')
        button.ensurePolished()
        self.assertEqual(button.palette().color(QPalette.ColorRole.Button).name(), '#3a6a4a')
        action = QPushButton()
        self.addCleanup(action.close)
        themes.set_widget_style(action, 'background: #FF6B35; color: #fff;')
        action.ensurePolished()
        self.assertEqual(action.palette().color(QPalette.ColorRole.Button).name(), '#d6b77a')
        self.assertEqual(action.palette().color(QPalette.ColorRole.ButtonText).name(), '#0c2019')
        themes.apply_theme('gta6_vice_sunset.qss')
        self.assertEqual(header.findChild(QLabel, 'vice_console_title').text(), 'SHOTBOX')
        themes.apply_theme(themes.DEFAULT_THEME)
        self.assertTrue(header.isHidden())
        self.assertEqual(frame.styleSheet(), source)

    def test_status_and_shot_colours_and_media_canvas_survive(self):
        themes.apply_theme("gta6_vice_sunset.qss")
        button = QPushButton()
        self.addCleanup(button.close)
        button.setObjectName("btn_status")
        button.setProperty("status", "approved")
        button.ensurePolished()
        self.assertEqual(button.palette().color(QPalette.ColorRole.Button).name(), "#3a6a4a")
        frame = QFrame()
        self.addCleanup(frame.close)
        source = ('QFrame[color="custom"] { background: #FF6B35; color: #FFFFFF; }'
                  'QFrame#media { background: #08090a; }')
        themes.set_widget_style(frame, source)
        self.assertEqual(frame.styleSheet(), source)

    def test_pastel_action_fill_has_dark_text(self):
        themes.apply_theme("gta6_vice_sunset.qss")
        button = QPushButton()
        self.addCleanup(button.close)
        themes.set_widget_style(button, "QPushButton { background: #FF6B35; color: #FFFFFF; }")
        button.ensurePolished()
        self.assertEqual(button.palette().color(QPalette.ColorRole.Button).name(), "#f48bb5")
        self.assertEqual(button.palette().color(QPalette.ColorRole.ButtonText).name(), "#171126")
        themes.set_widget_style(button, "background: #FF6B35; color: #fff;")
        self.assertEqual(button.palette().color(QPalette.ColorRole.ButtonText).name(), "#171126")

    def test_missing_or_unreadable_pack_falls_back(self):
        with tempfile.TemporaryDirectory(dir=cache_directory("temp")) as directory:
            root = Path(directory)
            (root / themes.DEFAULT_THEME).write_text(
                (themes.UI_DIR / themes.DEFAULT_THEME).read_text(encoding="utf-8"), encoding="utf-8")
            with patch.object(themes, "UI_DIR", root):
                self.assertEqual(themes.apply_theme("gta6_vice_sunset.qss"), themes.DEFAULT_THEME)
                (root / "gta6_vice_sunset.qss").write_bytes(b"\xff\xfe")
                self.assertEqual(themes.apply_theme("gta6_vice_sunset.qss"), themes.DEFAULT_THEME)
        self.assertEqual(self.app.styleSheet(),
                         (themes.UI_DIR / themes.DEFAULT_THEME).read_text(encoding="utf-8"))

    def test_christmas_checkbox_has_visible_check_mark(self):
        from PyQt6.QtWidgets import QCheckBox
        themes.apply_theme(themes.CHRISTMAS_THEME)
        box = QCheckBox()
        box.setChecked(True)
        box.resize(box.sizeHint())
        image = box.grab().toImage()
        # The ivory tick is inside the indicator, not its gold outline.
        self.assertTrue(any(image.pixelColor(x, y).name() == '#fff4de'
                            for x in range(3, 14) for y in range(3, 14)))

    def test_unknown_theme_uses_original(self):
        for value in ("../../custom.qss", "missing.qss", None, []):
            self.assertEqual(themes.apply_theme(value), themes.DEFAULT_THEME)

    def test_main_window_dispatches_theme_changes(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from main import MainWindow
        handler = Mock()
        MainWindow._on_settings_changed(SimpleNamespace(_apply_theme_file=handler),
                                        "theme_file", "gta6_vice_sunset.qss")
        handler.assert_called_once_with("gta6_vice_sunset.qss")

    def test_styles_changed_by_another_feature_are_not_overwritten(self):
        frame = QFrame()
        self.addCleanup(frame.close)
        themes.set_widget_style(frame, "color: #FF6B35;")
        frame.setStyleSheet("color: #4CAF50;")
        themes.apply_theme("gta6_vice_sunset.qss")
        self.assertEqual(frame.styleSheet(), "color: #4CAF50;")

    def test_deleted_local_widget_does_not_break_switch(self):
        from PyQt6 import sip
        frame = QFrame()
        themes.set_widget_style(frame, "color: #FF6B35;")
        sip.delete(frame)
        self.assertEqual(themes.apply_theme("gta6_vice_sunset.qss"), "gta6_vice_sunset.qss")


if __name__ == "__main__":
    unittest.main()
