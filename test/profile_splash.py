"""Standalone Shotbox artist picker design preview."""
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from artist_picker import ProfileSplash
from input_behaviour import install_input_wheel_guard
from settings import SettingsManager, get_settings_file_path
from themes import DEFAULT_THEME, apply_theme

# Editable sample roster: username, display name, Shotbox groups.
SAMPLE_PROFILES = [
    ("avery", "Avery Morgan", ["VFX"]),
    ("frankie", "Frankie Chen", ["VFX", "Production"]),
    ("jules", "Jules Patel", ["VFX"]),
    ("sam", "Sam Rivers", ["Production"]),
]

def main():
    app = QApplication(sys.argv)
    settings_path = get_settings_file_path()
    theme = (SettingsManager(settings_path).get("theme_file", DEFAULT_THEME)
             if Path(settings_path).is_file() else DEFAULT_THEME)
    apply_theme(theme)
    install_input_wheel_guard(app)
    memory = QSettings("Shotbox", "ProfileSplashPrototype")
    roster = [(user, user, name) for user, name, groups in SAMPLE_PROFILES if "VFX" in groups]
    dialog = ProfileSplash(roster, memory)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        memory.setValue("last_artist", dialog.artist_id)
        print(dialog.artist_id)


if __name__ == "__main__":
    main()
