# settings.py
"""
ShotBox Settings Module

Handles application settings with:
- YAML configuration file management (username_settings.yaml)
- Settings UI page for the main window
- Default values and reset functionality
- Django user integration for activity tracking
"""
import os
import sys
import getpass
import re
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, 
    QPushButton, QScrollArea, QFrame, QSizePolicy, QMessageBox, QFileDialog,
    QButtonGroup, QRadioButton, QApplication, QTextBrowser
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer, QSignalBlocker
from PyQt6.QtGui import QFont, QIcon, QWheelEvent

import app_update
import plugins_install
from app_version import UPDATE_BRANCH


# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(SCRIPT_DIR, "ui")
CHANGELOG_PATH = Path(SCRIPT_DIR) / "CHANGELOG.md"
CHANGELOG_ENTRY_RE = re.compile(
    r"^##\s+(.+?)(?:\r?\n)(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL
)


def _normalize_changelog_markdown(markdown_text: str) -> str:
    """Clean changelog markdown so the viewer does not render blank trailing blocks."""
    text = (markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines = []
    blank_run = 0

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if re.fullmatch(r"\s*[-*+]\s*", line):
            continue
        if not line.strip():
            blank_run += 1
            if blank_run > 1:
                continue
            cleaned_lines.append("")
            continue
        blank_run = 0
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def get_settings_file_path() -> str:
    """Get the path to the settings YAML file (username_settings.yaml)."""
    try:
        username = getpass.getuser()
    except Exception:
        username = "default"
    return os.path.join(SCRIPT_DIR, f"{username}_settings.yaml")


# Default settings values
DEFAULT_SETTINGS = {
    # User identification
    "django_username": None,  # Linked Django user ID
    
    # Server/API settings
    "server_url": "http://192.168.10.207:8000",
    
    # Polling settings (stored in seconds for user convenience)
    "polling_interval": 5,  # seconds

    # Preview generation settings
    "preview_quality": "medium",  # low/medium/high
    "preview_output_subdir": "renders/precomp/previews",
    "preview_overwrite": False,
    "nuke_exe_path": "",
    "threede_exe_path": "",
    "houdini_user_pref_dir": "",
    
    # UI density settings
    "shots_layout_mode": "list",  # list/grid
    "compact_view_enabled": False,
    "nukedash_task_style": "checklist",  # card/checklist
    "preview_thumbnail_size": "Medium",  # NoThumb/Tiny/Small/Medium/Large
    "card_spacing": 8,  # px between shot cards
    "row_height": 0,  # px, 0 = auto
    "quick_view_screen_percentage": 70,
    
    # Auto-refresh settings
    "auto_refresh_enabled": True,
    "auto_refresh_interval": 5,  # seconds
    "activity_auto_refresh": True,
    "activity_refresh_interval": 30,  # seconds
    
    # Debug settings
    "debug_modes": {
        "general": False,
        "api_calls": False,
        "ui_updates": False,
        "notifications": False,
        "project_load_profiler": False,
        "suppress_qt_multimedia_warnings": False,
    },
    
    # Appearance settings
    "theme_file": "dark_v01.qss",  # QSS filename (locked)
    
    # Window settings
    "remember_window_size": True,
    "window_width": 1200,
    "window_height": 800,
    "window_x": 100,
    "window_y": 100,
    "always_on_top": False,
    
    # Session restore settings
    "remember_last_session": True,  # Enable/disable session restore
    "last_job_id": None,  # Last selected job ID
    "last_timeline_index": 0,  # Last selected timeline tab index
    "last_scroll_position": 0,  # Last scroll position in timeline
    "nukedash_filter_state": {
        "enabled": True,
        "sort_mode": "title_asc",
        "artist_id": None,
        "status_values": [],
        "show_hidden_shots": False,
        "show_hidden_tasks": False,
        "show_to_conform": False,
    },

    # Startup behavior
    "startup_tab": 0,  # 0=Tasks, 1=Review, 2=XML Import, 3=Activity, 4=Settings
    "show_startup_loading_dialog": True,
    "enable_assignment_board": False,
    "enable_review_page": False,
    "enable_activity_page": False,
    "enable_import_page": False,
    "enable_xml_import_page": False,
    
    # Notifications (future feature)
    "notifications": "off",  # "off", "silent", "on"
    "notifications_lifetime": 5,  # seconds
    "notifications_size": "normal",  # compact/normal/large
    "notifications_animations": True,
    "notifications_do_not_disturb": False,
    "notifications_subtle": False,
}


class NoScrollSpinBox(QSpinBox):
    """QSpinBox that ignores scroll wheel events to prevent accidental changes."""
    
    def wheelEvent(self, event: QWheelEvent):
        # Ignore scroll wheel - don't change value
        event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that ignores scroll wheel events."""
    
    def wheelEvent(self, event: QWheelEvent):
        event.ignore()


class NoScrollComboBox(QComboBox):
    """QComboBox that ignores scroll wheel events to prevent accidental changes."""
    
    def wheelEvent(self, event: QWheelEvent):
        event.ignore()


class SettingsManager:
    """Manages loading, saving, and accessing settings."""
    
    def __init__(self, settings_path: Optional[str] = None):
        """
        Initialize the settings manager.
        
        Args:
            settings_path: Optional custom path to settings file.
                          If None, uses username_settings.yaml
        """
        self._settings_path = settings_path or get_settings_file_path()
        self._settings: Dict[str, Any] = {}
        self._load_settings()
    
    @property
    def settings_path(self) -> str:
        """Get the current settings file path."""
        return self._settings_path
    
    def _load_settings(self) -> None:
        """Load settings from YAML file, creating with defaults if not exists."""
        if os.path.exists(self._settings_path):
            try:
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    loaded = yaml.safe_load(f)
                    if loaded is None:
                        loaded = {}
                    # Merge with defaults (loaded values override defaults)
                    self._settings = self._deep_merge(DEFAULT_SETTINGS.copy(), loaded)
            except Exception as e:
                print(f"[Settings] Error loading settings: {e}")
                self._settings = DEFAULT_SETTINGS.copy()
        else:
            # Create new settings file with defaults
            self._settings = DEFAULT_SETTINGS.copy()
            self._save_settings()
    
    def _deep_merge(self, base: dict, override: dict) -> dict:
        """Deep merge only known keys from override into base defaults."""
        result = base.copy()
        for key, value in override.items():
            if key not in result:
                continue
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def _save_settings(self) -> bool:
        """Save current settings to YAML file."""
        try:
            with open(self._settings_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._settings, f, default_flow_style=False, allow_unicode=True)
            return True
        except Exception as e:
            print(f"[Settings] Error saving settings: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value by key. Supports nested keys with dot notation.
        
        Example: get("debug_modes.general")
        """
        keys = key.split('.')
        value = self._settings
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any, save: bool = True) -> None:
        """
        Set a setting value by key. Supports nested keys with dot notation.
        
        Args:
            key: Setting key (e.g., "debug_modes.general")
            value: Value to set
            save: Whether to immediately save to file
        """
        keys = key.split('.')
        target = self._settings
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        
        if save:
            self._save_settings()
    
    def get_all(self) -> Dict[str, Any]:
        """Get all settings as a dictionary."""
        return self._settings.copy()
    
    def reset_to_defaults(self) -> None:
        """Reset all settings to default values."""
        self._settings = DEFAULT_SETTINGS.copy()
        self._save_settings()
    
    def save(self) -> bool:
        """Explicitly save settings to file."""
        return self._save_settings()
    
    def get_polling_interval_ms(self) -> int:
        """Get polling interval in milliseconds (for internal use)."""
        return int(self.get("polling_interval", 5) * 1000)
    
    def get_auto_refresh_interval_ms(self) -> int:
        """Get auto refresh interval in milliseconds (for internal use)."""
        return int(self.get("auto_refresh_interval", 5) * 1000)


class SettingsPage(QWidget):
    """Settings page widget for the main window tabs."""
    
    # Signal emitted when settings change that require immediate action
    settings_changed = pyqtSignal(str, object)  # key, new_value
    server_url_changed = pyqtSignal(str)  # new server URL
    
    def __init__(self, settings_manager: Optional[SettingsManager] = None, parent=None):
        super().__init__(parent)
        
        # Use provided settings manager or create new one
        self._settings = settings_manager or SettingsManager()
        
        # Cache for Django users
        self._django_users = []
        self._autosave_pending = False
        self._unsaved_setting_keys = set()
        self._automatic_save = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(500)
        self._autosave_timer.timeout.connect(self._flush_autosave)

        self._setup_ui()
        self._load_django_users()
        self._load_current_values()
        self._connect_signals()
        self._refresh_update_panel()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._flush_autosave)

    def _setup_ui(self):
        """Build the settings UI."""
        # Main layout with scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Scroll area for settings
        self.settings_scroll_area = QScrollArea()
        self.settings_scroll_area.setWidgetResizable(True)
        self.settings_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.settings_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        
        # Container widget for scroll content
        container = QWidget()
        container.setMaximumWidth(1600)
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(16)
        container_layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel("Settings")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setObjectName("settings_title")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        container_layout.addWidget(title)
        
        # Settings file info
        file_info = QLabel(f"Settings file: {self._settings.settings_path}")
        file_info.setObjectName("settings_file_info")
        file_info.setWordWrap(True)
        container_layout.addWidget(file_info)
        
        # === System Section ===
        system_group = self._create_group_box("System")
        system_layout = QFormLayout(system_group)

        # Django username dropdown
        self.django_user_combo = NoScrollComboBox()
        self.django_user_combo.setMinimumWidth(200)
        django_user_layout = QHBoxLayout()
        django_user_layout.addWidget(self.django_user_combo)
        
        self.refresh_users_btn = QPushButton("↻")
        self.refresh_users_btn.setFixedWidth(40)
        self.refresh_users_btn.setToolTip("Refresh user list from server")
        django_user_layout.addWidget(self.refresh_users_btn)
        django_user_layout.addStretch()
        
        system_layout.addRow("Django Username:", django_user_layout)
        
        container_layout.addWidget(system_group)
        
        # === Server/API Section ===
        server_group = self._create_group_box("Server / API")
        server_layout = QFormLayout(server_group)
        
        self.server_url_edit = QLineEdit()
        self.server_url_edit.setPlaceholderText("http://192.168.10.207:8000")
        server_layout.addRow("Server URL:", self.server_url_edit)
        
        # Connection test button
        self.test_connection_btn = QPushButton("Test Connection")
        self.test_connection_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        server_layout.addRow("", self.test_connection_btn)
        
        container_layout.addWidget(server_group)
        
        # === Polling Section ===
        polling_group = self._create_group_box("Polling && Refresh")
        polling_layout = QFormLayout(polling_group)
        
        # Polling interval (in seconds)
        self.polling_interval_spin = NoScrollSpinBox()
        self.polling_interval_spin.setRange(1, 60)
        self.polling_interval_spin.setSingleStep(1)
        self.polling_interval_spin.setSuffix(" sec")
        self.polling_interval_spin.setToolTip("How often to poll the API for updates")
        polling_layout.addRow("API Polling Interval:", self.polling_interval_spin)
        
        # Auto-refresh toggle
        self.auto_refresh_check = QCheckBox("Enable Auto-Refresh")
        polling_layout.addRow("", self.auto_refresh_check)
        
        # Auto-refresh interval (in seconds)
        self.auto_refresh_interval_spin = NoScrollSpinBox()
        self.auto_refresh_interval_spin.setRange(1, 60)
        self.auto_refresh_interval_spin.setSingleStep(1)
        self.auto_refresh_interval_spin.setSuffix(" sec")
        polling_layout.addRow("Auto-Refresh Interval:", self.auto_refresh_interval_spin)

        # Activity auto-refresh toggle
        self.activity_auto_refresh_check = QCheckBox("Enable Activity Auto-Refresh")
        polling_layout.addRow("", self.activity_auto_refresh_check)

        # Activity refresh interval (in seconds)
        self.activity_refresh_interval_spin = NoScrollSpinBox()
        self.activity_refresh_interval_spin.setRange(5, 300)
        self.activity_refresh_interval_spin.setSingleStep(5)
        self.activity_refresh_interval_spin.setSuffix(" sec")
        self.activity_refresh_interval_spin.setToolTip("How often to refresh the Activity feed")
        polling_layout.addRow("Activity Refresh Interval:", self.activity_refresh_interval_spin)
        
        container_layout.addWidget(polling_group)

        # === Preview Generation Section ===
        preview_group = self._create_group_box("Preview Generation")
        preview_layout = QFormLayout(preview_group)

        self.preview_quality_combo = NoScrollComboBox()
        self.preview_quality_combo.addItems(["low", "medium", "high"])
        preview_layout.addRow("Quality:", self.preview_quality_combo)

        self.preview_output_subdir_edit = QLineEdit()
        self.preview_output_subdir_edit.setPlaceholderText("renders/precomp/previews")
        preview_layout.addRow("Output Subdir:", self.preview_output_subdir_edit)

        self.preview_overwrite_check = QCheckBox("Overwrite Existing Previews")
        preview_layout.addRow("", self.preview_overwrite_check)

        self.nuke_exe_path_edit = QLineEdit()
        self.nuke_exe_path_edit.setPlaceholderText("C:/Program Files/Nuke15.2v2/Nuke15.2.exe")
        nuke_path_layout = QHBoxLayout()
        nuke_path_layout.addWidget(self.nuke_exe_path_edit)
        self.nuke_exe_browse_btn = QPushButton("Browse")
        self.nuke_exe_browse_btn.setFixedWidth(80)
        nuke_path_layout.addWidget(self.nuke_exe_browse_btn)
        preview_layout.addRow("Nuke Executable:", nuke_path_layout)

        self.threede_exe_path_edit = QLineEdit()
        self.threede_exe_path_edit.setPlaceholderText(
            "C:/Users/Giger/Documents/3DE4_win64_r8.0v2/bin/3DE4.exe"
        )
        three_de_path_layout = QHBoxLayout()
        three_de_path_layout.addWidget(self.threede_exe_path_edit)
        self.threede_exe_browse_btn = QPushButton("Browse")
        self.threede_exe_browse_btn.setFixedWidth(80)
        three_de_path_layout.addWidget(self.threede_exe_browse_btn)
        preview_layout.addRow("3DE Executable:", three_de_path_layout)

        container_layout.addWidget(preview_group)

        plugins_group = self._create_group_box("Plugins")
        plugins_layout = QFormLayout(plugins_group)
        self.plugins_destination_label = QLabel()
        self.plugins_destination_label.setWordWrap(True)
        self.plugins_scripts_label = QLabel()
        self.plugins_scripts_label.setWordWrap(True)
        self.plugins_status_label = QLabel()
        self.plugins_status_label.setWordWrap(True)
        self.install_3de_plugins_btn = QPushButton("Install / Update 3DE Plugins")
        plugins_layout.addRow("3DE destination:", self.plugins_destination_label)
        plugins_layout.addRow("Bundled scripts:", self.plugins_scripts_label)
        plugins_layout.addRow("", self.install_3de_plugins_btn)
        plugins_layout.addRow("", self.plugins_status_label)
        self.houdini_pref_combo = NoScrollComboBox()
        self.houdini_pref_combo.setEditable(True)
        self.houdini_pref_combo.addItem("")
        self.houdini_pref_combo.addItems(plugins_install.houdini_preferences_candidates())
        self.houdini_pref_combo.lineEdit().setPlaceholderText("Select Houdini user preferences folder")
        houdini_path_row = QHBoxLayout()
        houdini_path_row.setSpacing(8)
        houdini_path_row.addWidget(self.houdini_pref_combo, 1)
        self.houdini_pref_browse_btn = QPushButton("Browse")
        houdini_path_row.addWidget(self.houdini_pref_browse_btn)
        self.houdini_destination_label = QLabel()
        self.houdini_destination_label.setWordWrap(True)
        self.houdini_status_label = QLabel()
        self.houdini_status_label.setWordWrap(True)
        self.install_houdini_plugin_btn = QPushButton("Install / Update Houdini Plugin")
        self.install_houdini_plugin_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        plugins_layout.addRow("Houdini preferences:", houdini_path_row)
        plugins_layout.addRow("Destination:", self.houdini_destination_label)
        plugins_layout.addRow("", self.install_houdini_plugin_btn)
        plugins_layout.addRow("", self.houdini_status_label)
        container_layout.addWidget(plugins_group)
        
        # === Debug Modes Section ===
        debug_group = self._create_group_box("Debug Modes")
        debug_layout = QVBoxLayout(debug_group)
        
        self.debug_general_check = QCheckBox("General Debug")
        self.debug_general_check.setToolTip("Enable general debug logging")
        debug_layout.addWidget(self.debug_general_check)
        
        self.debug_api_check = QCheckBox("API Calls Debug")
        self.debug_api_check.setToolTip("Log all API requests and responses")
        debug_layout.addWidget(self.debug_api_check)
        
        self.debug_ui_check = QCheckBox("UI Updates Debug")
        self.debug_ui_check.setToolTip("Log UI refresh and update events")
        debug_layout.addWidget(self.debug_ui_check)

        self.debug_notifications_check = QCheckBox("Notifications Debug")
        self.debug_notifications_check.setToolTip("Log activity notification diagnostics")
        debug_layout.addWidget(self.debug_notifications_check)

        self.debug_project_load_profiler_check = QCheckBox("Project Load Profiler")
        self.debug_project_load_profiler_check.setToolTip(
            "Print a per-project switch timing breakdown to the console"
        )
        debug_layout.addWidget(self.debug_project_load_profiler_check)

        self.debug_qt_multimedia_check = QCheckBox("Suppress Qt Multimedia Warnings")
        self.debug_qt_multimedia_check.setToolTip(
            "Hide noisy QFFmpeg disconnect warnings in the console"
        )
        debug_layout.addWidget(self.debug_qt_multimedia_check)
        
        container_layout.addWidget(debug_group)
        
        # === Appearance Section ===
        appearance_group = self._create_group_box("Appearance")
        appearance_layout = QFormLayout(appearance_group)
        
        # Theme is locked to dark_v01 for now.
        self.theme_label = QLabel("dark_v01")
        appearance_layout.addRow("Theme (QSS):", self.theme_label)
        
        container_layout.addWidget(appearance_group)

        # === Shots Layout Section ===
        layout_layout = appearance_layout

        self.shots_layout_combo = NoScrollComboBox()
        self.shots_layout_combo.addItem("List", "list")
        self.shots_layout_combo.addItem("Grid", "grid")
        layout_layout.addRow("Shots Layout:", self.shots_layout_combo)

        # === UI Density Section ===
        density_layout = appearance_layout

        self.preview_size_combo = NoScrollComboBox()
        self.preview_size_combo.addItems(["NoThumb", "Tiny", "Small", "Medium", "Large"])
        density_layout.addRow("Preview Size:", self.preview_size_combo)

        self.nukedash_task_style_combo = NoScrollComboBox()
        self.nukedash_task_style_combo.addItem("Classic Cards", "card")
        self.nukedash_task_style_combo.addItem("Checklist Rows", "checklist")
        density_layout.addRow("Task Style:", self.nukedash_task_style_combo)

        self.card_spacing_spin = NoScrollSpinBox()
        self.card_spacing_spin.setRange(0, 40)
        self.card_spacing_spin.setSuffix(" px")
        density_layout.addRow("Card Spacing:", self.card_spacing_spin)

        self.row_height_spin = NoScrollSpinBox()
        self.row_height_spin.setRange(0, 400)
        self.row_height_spin.setSuffix(" px")
        self.row_height_spin.setToolTip("0 = auto")
        density_layout.addRow("Row Height:", self.row_height_spin)

        self.quick_view_size_spin = NoScrollSpinBox()
        self.quick_view_size_spin.setRange(25, 100)
        self.quick_view_size_spin.setSingleStep(5)
        self.quick_view_size_spin.setSuffix(" %")
        self.quick_view_size_spin.setToolTip(
            "Percentage of the available screen used when Quick View opens"
        )
        density_layout.addRow("Quick View Size:", self.quick_view_size_spin)

        
        # === Window Section ===
        window_layout = appearance_layout
        
        self.remember_size_check = QCheckBox("Remember Window Size && Position")
        window_layout.addRow("", self.remember_size_check)
        
        self.always_on_top_check = QCheckBox("Always on Top")
        window_layout.addRow("", self.always_on_top_check)
        
        
        # === Session Restore Section ===
        session_group = self._create_group_box("Session Restore")
        session_layout = QVBoxLayout(session_group)
        
        self.remember_session_check = QCheckBox("Auto-load Last Project, Timeline && Scroll Position")
        self.remember_session_check.setToolTip(
            "When enabled, ShotBox will restore your last selected project, "
            "timeline tab, and scroll position when you restart the application."
        )
        session_layout.addWidget(self.remember_session_check)
        
        # Info label
        session_info = QLabel(
            "This will remember which project/job you had open, which timeline tab "
            "was selected, and where you were scrolled to."
        )
        session_info.setWordWrap(True)
        session_info.setObjectName("session_info_label")
        session_layout.addWidget(session_info)
        
        container_layout.addWidget(session_group)

        # === Startup Section ===
        startup_group = self._create_group_box("Startup")
        startup_layout = QFormLayout(startup_group)

        self.startup_tab_combo = NoScrollComboBox()
        self.startup_tab_combo.addItem("Tasks", 0)
        self.startup_tab_combo.addItem("Review", 1)
        self.startup_tab_combo.addItem("XML Import", 2)
        self.startup_tab_combo.addItem("Activity", 3)
        self.startup_tab_combo.addItem("Settings", 4)
        startup_layout.addRow("Default Tab:", self.startup_tab_combo)

        self.show_startup_loading_check = QCheckBox("Show Loading Dialog on Startup")
        startup_layout.addRow("", self.show_startup_loading_check)

        self.enable_assignment_board_check = QCheckBox("Enable Assignment Board Tab")
        startup_layout.addRow("", self.enable_assignment_board_check)

        self.enable_review_page_check = QCheckBox("Enable Review Tab")
        startup_layout.addRow("", self.enable_review_page_check)

        self.enable_activity_page_check = QCheckBox("Enable Activity Tab")
        startup_layout.addRow("", self.enable_activity_page_check)

        self.enable_import_page_check = QCheckBox("Enable Import Tab")
        startup_layout.addRow("", self.enable_import_page_check)

        self.enable_xml_import_page_check = QCheckBox("Enable XML Import Tab")
        startup_layout.addRow("", self.enable_xml_import_page_check)

        startup_pages_note = QLabel(
            "Optional page toggles apply on the next app launch."
        )
        startup_pages_note.setWordWrap(True)
        startup_pages_note.setObjectName("startup_pages_note")
        startup_layout.addRow("", startup_pages_note)

        container_layout.addWidget(startup_group)
        
        # === Notifications Section ===
        notif_group = self._create_group_box("Notifications")
        notif_layout = QVBoxLayout(notif_group)

        mode_row = QHBoxLayout()
        mode_label = QLabel("Mode:")
        mode_row.addWidget(mode_label)

        self.notif_button_group = QButtonGroup(self)

        self.notif_off_radio = QRadioButton("Off")
        self.notif_button_group.addButton(self.notif_off_radio)
        mode_row.addWidget(self.notif_off_radio)

        self.notif_silent_radio = QRadioButton("Silent")
        self.notif_button_group.addButton(self.notif_silent_radio)
        mode_row.addWidget(self.notif_silent_radio)

        self.notif_on_radio = QRadioButton("On")
        self.notif_button_group.addButton(self.notif_on_radio)
        mode_row.addWidget(self.notif_on_radio)

        mode_row.addStretch()
        notif_layout.addLayout(mode_row)

        self.notif_dnd_check = QCheckBox("Do Not Disturb")
        notif_layout.addWidget(self.notif_dnd_check)

        self.notif_animations_check = QCheckBox("Enable Animations")
        notif_layout.addWidget(self.notif_animations_check)

        self.notif_subtle_check = QCheckBox("Subtle Mode")
        notif_layout.addWidget(self.notif_subtle_check)

        notif_form = QFormLayout()

        self.notif_lifetime_spin = NoScrollSpinBox()
        self.notif_lifetime_spin.setRange(2, 30)
        self.notif_lifetime_spin.setSuffix("s")
        notif_form.addRow("Lifetime:", self.notif_lifetime_spin)

        self.notif_size_combo = NoScrollComboBox()
        self.notif_size_combo.addItems(["Compact", "Normal", "Large"])
        notif_form.addRow("Size:", self.notif_size_combo)

        notif_layout.addLayout(notif_form)

        container_layout.addWidget(notif_group)

        # === App Updates Section ===
        self.updates_group = self._create_group_box("App Updates")
        updates_layout = QFormLayout(self.updates_group)

        self.current_version_label = QLabel("Unknown")
        self.current_version_label.setWordWrap(True)
        updates_layout.addRow("Current Version:", self.current_version_label)

        self.update_branch_label = QLabel(UPDATE_BRANCH)
        updates_layout.addRow("Tracked Branch:", self.update_branch_label)

        self.latest_publish_label = QLabel("Not checked yet.")
        self.latest_publish_label.setWordWrap(True)
        updates_layout.addRow("Latest Publish:", self.latest_publish_label)

        self.update_status_label = QLabel("Checking local install status...")
        self.update_status_label.setWordWrap(True)
        updates_layout.addRow("Update Status:", self.update_status_label)

        self.update_changelog_label = QLabel("No changelog preview loaded yet.")
        self.update_changelog_label.setWordWrap(True)
        self.update_changelog_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        updates_layout.addRow("Latest Changelog:", self.update_changelog_label)

        self.change_log_combo = NoScrollComboBox()
        updates_layout.addRow("Change Log:", self.change_log_combo)

        self.change_log_view = QTextBrowser()
        self.change_log_view.setReadOnly(True)
        self.change_log_view.setMinimumHeight(240)
        self.change_log_view.setPlaceholderText("No changelog loaded.")
        self.change_log_view.setOpenExternalLinks(False)
        updates_layout.addRow("", self.change_log_view)

        self._changelog_entries = []
        self._load_change_log_entries()

        update_buttons_layout = QHBoxLayout()
        self.check_updates_btn = QPushButton("Check for Updates")
        update_buttons_layout.addWidget(self.check_updates_btn)
        self.apply_update_btn = QPushButton("Update && Restart")
        self.apply_update_btn.setEnabled(False)
        update_buttons_layout.addWidget(self.apply_update_btn)
        update_buttons_layout.addStretch()
        updates_layout.addRow("", update_buttons_layout)

        container_layout.addWidget(self.updates_group)
        
        # === Action Buttons ===
        buttons_layout = QHBoxLayout()
        self.save_status_label = QLabel("Changes save automatically. Startup options apply next launch.")
        self.save_status_label.setWordWrap(True)
        buttons_layout.addWidget(self.save_status_label)
        buttons_layout.addStretch()
        
        self.reset_button = QPushButton("Reset to Defaults")
        self.reset_button.setObjectName("reset_button")
        buttons_layout.addWidget(self.reset_button)
        
        self.save_button = QPushButton("Save Settings")
        self.save_button.setObjectName("save_button")
        buttons_layout.addWidget(self.save_button)
        
        # Group related settings into independent columns, avoiding stretched
        # controls and blank grid rows when neighbouring sections differ in size.
        self._settings_columns_layout = QGridLayout()
        self._settings_columns_layout.setContentsMargins(0, 0, 0, 0)
        self._settings_columns_layout.setSpacing(24)
        self._settings_columns = []
        for groups in (
            (system_group, server_group, polling_group, preview_group, plugins_group, self.updates_group),
            (appearance_group, startup_group, session_group, notif_group, debug_group),
        ):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(16)
            for group in groups:
                container_layout.removeWidget(group)
                column_layout.addWidget(group)
                group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            column_layout.addStretch()
            self._settings_columns.append(column)
        container_layout.addLayout(self._settings_columns_layout)
        container_layout.addStretch()
        self._settings_wide = None
        self._update_settings_columns()

        # Use the same label alignment and spacing throughout the page.
        for form in container.findChildren(QFormLayout):
            form.setContentsMargins(16, 18, 16, 16)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(10)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item and isinstance(item.widget(), QLabel):
                    item.widget().setFixedWidth(172)
                    item.widget().setWordWrap(True)
        for spin in container.findChildren(QSpinBox):
            spin.setFixedWidth(120)
            spin.setMinimumHeight(28)
        for combo in container.findChildren(QComboBox):
            if combo not in (self.change_log_combo, self.houdini_pref_combo):
                combo.setFixedWidth(240)
        for row_layout in (django_user_layout, nuke_path_layout, three_de_path_layout, update_buttons_layout):
            row_layout.setSpacing(8)
        for group in (session_group, notif_group, debug_group):
            group.layout().setContentsMargins(16, 18, 16, 16)
            group.layout().setSpacing(10)
        notif_form.setContentsMargins(0, 8, 0, 0)
        self.install_3de_plugins_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        # Set up scroll area
        self.settings_scroll_area.setWidget(container)
        self.settings_scroll_area.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        main_layout.addWidget(self.settings_scroll_area)

        # Keep save/reset accessible regardless of scroll position.
        footer = QWidget()
        footer.setMaximumWidth(1600)
        footer.setLayout(buttons_layout)
        buttons_layout.setContentsMargins(20, 12, 20, 12)
        footer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.addWidget(footer)
        main_layout.addLayout(footer_row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_settings_columns()

    def _update_settings_columns(self):
        if not hasattr(self, "_settings_columns"):
            return
        wide = self.width() >= 1250
        if wide == self._settings_wide:
            return
        self._settings_wide = wide
        for column in self._settings_columns:
            self._settings_columns_layout.removeWidget(column)
        for index, column in enumerate(self._settings_columns):
            self._settings_columns_layout.addWidget(column, 0 if wide else index, index if wide else 0)
        self._settings_columns_layout.setColumnStretch(0, 1)
        self._settings_columns_layout.setColumnStretch(1, 1 if wide else 0)
    
    def _create_group_box(self, title: str) -> QGroupBox:
        """Create a group box that inherits styling from the active QSS theme."""
        group = QGroupBox(title)
        return group

    def _read_change_log_text(self) -> str:
        try:
            return _normalize_changelog_markdown(
                CHANGELOG_PATH.read_text(encoding="utf-8")
            )
        except OSError:
            return "Could not load changelog."

    def _load_change_log_entries(self) -> None:
        changelog_text = self._read_change_log_text()
        entries = [("All Changes", changelog_text)]

        for match in CHANGELOG_ENTRY_RE.finditer(changelog_text or ""):
            heading = match.group(1).strip()
            body = match.group(2).strip()
            if not heading:
                continue
            entry_text = f"## {heading}"
            if body:
                entry_text = f"{entry_text}\n\n{body}"
            entries.append((heading, entry_text))

        self._changelog_entries = entries
        self.change_log_combo.blockSignals(True)
        self.change_log_combo.clear()
        for heading, entry_text in self._changelog_entries:
            self.change_log_combo.addItem(heading, entry_text)
        self.change_log_combo.blockSignals(False)
        self.change_log_combo.setCurrentIndex(0)
        self._update_change_log_view()

    def _update_change_log_view(self) -> None:
        if not self._changelog_entries:
            self.change_log_view.setMarkdown("Could not load changelog.")
            return
        entry_text = self.change_log_combo.currentData()
        if entry_text is None:
            entry_text = self._changelog_entries[0][1]
        self.change_log_view.setMarkdown(str(entry_text))
        self.change_log_view.verticalScrollBar().setValue(0)
    
    def _load_django_users(self):
        """Load Django users from API for the dropdown."""
        try:
            import http_help
            api = http_help.DjangoAPI()
            # Temporarily use saved server URL if different
            saved_url = self._settings.get("server_url", "")
            if saved_url:
                api.base_url = saved_url.rstrip('/') + '/api/'
            
            self._django_users = api.get_users()
        except Exception as e:
            print(f"[Settings] Could not load Django users: {e}")
            self._django_users = []
        
        self._populate_user_combo()
    
    def _populate_user_combo(self):
        """Populate Django user dropdown."""
        blocker = QSignalBlocker(self.django_user_combo)
        selected_user = self._settings.get("django_username")
        self.django_user_combo.clear()
        self.django_user_combo.addItem("(Not linked)", None)
        
        for user in sorted(self._django_users, key=lambda u: (u.get("username") or "").lower()):
            user_id = user.get("id")
            username = user.get("username", f"User {user_id}")
            first_name = user.get("first_name", "")
            
            display = username
            if first_name:
                display = f"{username} ({first_name})"
            
            self.django_user_combo.addItem(display, user_id)

        index = self.django_user_combo.findData(selected_user)
        if index < 0 and selected_user is not None:
            self.django_user_combo.addItem(f"User {selected_user} (unavailable)", selected_user)
            index = self.django_user_combo.count() - 1
        self.django_user_combo.setCurrentIndex(max(0, index))
    
    def _load_current_values(self):
        """Load current settings values into UI widgets."""
        blockers = [QSignalBlocker(widget) for widget in self._settings_controls()]
        # Django user
        django_user_id = self._settings.get("django_username")
        index = self.django_user_combo.findData(django_user_id)
        if index >= 0:
            self.django_user_combo.setCurrentIndex(index)
        
        # Server
        self.server_url_edit.setText(self._settings.get("server_url", ""))
        
        # Polling (convert from seconds)
        self.polling_interval_spin.setValue(self._settings.get("polling_interval", 5))
        self.auto_refresh_check.setChecked(self._settings.get("auto_refresh_enabled", True))
        self.auto_refresh_interval_spin.setValue(self._settings.get("auto_refresh_interval", 5))
        self.activity_auto_refresh_check.setChecked(
            self._settings.get("activity_auto_refresh", True)
        )
        self.activity_refresh_interval_spin.setValue(
            self._settings.get("activity_refresh_interval", 30)
        )

        # Preview Generation
        preview_quality = self._settings.get("preview_quality", "medium")
        if preview_quality:
            idx = self.preview_quality_combo.findText(preview_quality)
            if idx >= 0:
                self.preview_quality_combo.setCurrentIndex(idx)
        self.preview_output_subdir_edit.setText(
            self._settings.get("preview_output_subdir", "renders/precomp/previews")
        )
        self.preview_overwrite_check.setChecked(self._settings.get("preview_overwrite", False))
        self.nuke_exe_path_edit.setText(self._settings.get("nuke_exe_path", ""))
        self.threede_exe_path_edit.setText(self._settings.get("threede_exe_path", ""))
        self._refresh_plugins_panel()
        self.houdini_pref_combo.setCurrentText(self._settings.get("houdini_user_pref_dir", ""))
        self._refresh_houdini_plugin_panel()
        
        # Debug modes
        self.debug_general_check.setChecked(self._settings.get("debug_modes.general", False))
        self.debug_api_check.setChecked(self._settings.get("debug_modes.api_calls", False))
        self.debug_ui_check.setChecked(self._settings.get("debug_modes.ui_updates", False))
        self.debug_notifications_check.setChecked(
            self._settings.get("debug_modes.notifications", False)
        )
        self.debug_project_load_profiler_check.setChecked(
            self._settings.get("debug_modes.project_load_profiler", False)
        )
        self.debug_qt_multimedia_check.setChecked(
            self._settings.get("debug_modes.suppress_qt_multimedia_warnings", False)
        )
        
        # Appearance
        self.theme_label.setText("dark_v01")

        # Shots layout
        layout_mode = self._settings.get("shots_layout_mode", "list")
        idx = self.shots_layout_combo.findData(layout_mode)
        if idx >= 0:
            self.shots_layout_combo.setCurrentIndex(idx)

        # UI Density
        preview_size = self._settings.get("preview_thumbnail_size", "Medium")
        idx = self.preview_size_combo.findText(preview_size)
        if idx >= 0:
            self.preview_size_combo.setCurrentIndex(idx)
        task_style = self._settings.get("nukedash_task_style", "checklist")
        idx = self.nukedash_task_style_combo.findData(task_style)
        if idx >= 0:
            self.nukedash_task_style_combo.setCurrentIndex(idx)
        self.card_spacing_spin.setValue(self._settings.get("card_spacing", 8))
        self.row_height_spin.setValue(self._settings.get("row_height", 0))
        self.quick_view_size_spin.setValue(
            self._settings.get("quick_view_screen_percentage", 70)
        )
        
        # Window
        self.remember_size_check.setChecked(self._settings.get("remember_window_size", True))
        self.always_on_top_check.setChecked(self._settings.get("always_on_top", False))
        
        # Session Restore
        self.remember_session_check.setChecked(self._settings.get("remember_last_session", True))

        # Startup
        startup_tab = self._settings.get("startup_tab", 0)
        idx = self.startup_tab_combo.findData(startup_tab)
        if idx >= 0:
            self.startup_tab_combo.setCurrentIndex(idx)
        self.show_startup_loading_check.setChecked(
            self._settings.get("show_startup_loading_dialog", True)
        )
        self.enable_assignment_board_check.setChecked(
            self._settings.get("enable_assignment_board", False)
        )
        self.enable_review_page_check.setChecked(
            self._settings.get("enable_review_page", False)
        )
        self.enable_activity_page_check.setChecked(
            self._settings.get("enable_activity_page", False)
        )
        self.enable_import_page_check.setChecked(
            self._settings.get("enable_import_page", False)
        )
        self.enable_xml_import_page_check.setChecked(
            self._settings.get("enable_xml_import_page", False)
        )
        
        # Notifications
        notif_value = self._settings.get("notifications", "off")
        if notif_value == "off":
            self.notif_off_radio.setChecked(True)
        elif notif_value == "silent":
            self.notif_silent_radio.setChecked(True)
        else:
            self.notif_on_radio.setChecked(True)

        self.notif_dnd_check.setChecked(
            self._settings.get("notifications_do_not_disturb", False)
        )
        self.notif_animations_check.setChecked(
            self._settings.get("notifications_animations", True)
        )
        self.notif_subtle_check.setChecked(
            self._settings.get("notifications_subtle", False)
        )

        self.notif_lifetime_spin.setValue(
            self._settings.get("notifications_lifetime", 5)
        )

        size_value = self._settings.get("notifications_size", "normal")
        size_text = str(size_value).capitalize()
        idx = self.notif_size_combo.findText(size_text)
        if idx >= 0:
            self.notif_size_combo.setCurrentIndex(idx)
    
    def _settings_controls(self):
        # Named form controls only: exclude the read-only changelog selector and
        # child editors inside spin boxes/combos, whose parent already signals.
        return [widget for widget in vars(self).values()
                if isinstance(widget, (QLineEdit, QSpinBox, QDoubleSpinBox,
                                       QCheckBox, QRadioButton, QComboBox))
                and widget is not self.change_log_combo]

    def _schedule_autosave(self, *_):
        self._autosave_pending = True
        self.save_status_label.setText("Unsaved changes…")
        self._autosave_timer.start()

    def _flush_autosave(self):
        if self._autosave_pending:
            self._save_all_settings(automatic=True)

    def hideEvent(self, event):
        self._flush_autosave()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._flush_autosave()
        super().closeEvent(event)

    def _set_pending_setting(self, key, value, save=False):
        if self._settings.get(key) != value:
            self._unsaved_setting_keys.add(key)
        self._settings.set(key, value, save=save)

    def _emit_saved_setting(self, key, value):
        if not self._automatic_save or key in self._unsaved_setting_keys:
            self.settings_changed.emit(key, value)

    def _connect_signals(self):
        """Connect UI signals to handlers."""
        # Save button
        self.save_button.clicked.connect(self._save_all_settings)
        for widget in self._settings_controls():
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._schedule_autosave)
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self._schedule_autosave)
            elif isinstance(widget, QComboBox):
                signal = widget.currentTextChanged if widget.isEditable() else widget.currentIndexChanged
                signal.connect(self._schedule_autosave)
            else:
                widget.toggled.connect(self._schedule_autosave)
        
        # Reset button
        self.reset_button.clicked.connect(self._reset_to_defaults)
        
        # Refresh users button
        self.refresh_users_btn.clicked.connect(self._on_refresh_users)
        
        # Test connection button
        self.test_connection_btn.clicked.connect(self._on_test_connection)

        # Browse Nuke executable
        self.nuke_exe_browse_btn.clicked.connect(self._on_browse_nuke_exe)
        self.threede_exe_browse_btn.clicked.connect(self._on_browse_threede_exe)
        self.threede_exe_path_edit.textChanged.connect(self._refresh_plugins_panel)
        self.install_3de_plugins_btn.clicked.connect(self._on_install_3de_plugins)
        self.houdini_pref_combo.currentTextChanged.connect(self._refresh_houdini_plugin_panel)
        self.houdini_pref_browse_btn.clicked.connect(self._on_browse_houdini_preferences)
        self.install_houdini_plugin_btn.clicked.connect(self._on_install_houdini_plugin)

        # Update controls
        self.check_updates_btn.clicked.connect(self._on_check_for_updates)
        self.apply_update_btn.clicked.connect(self._on_update_and_restart)
        self.change_log_combo.currentIndexChanged.connect(self._update_change_log_view)
    
    def _save_all_settings(self, checked=False, *, automatic=False):
        """Save the form; automatic saves report status without modal dialogs."""
        self._autosave_timer.stop()
        self._automatic_save = automatic
        # Django username
        self._set_pending_setting("django_username", self.django_user_combo.currentData(), save=False)
        
        # Server
        self._set_pending_setting("server_url", self.server_url_edit.text().strip(), save=False)
        
        # Polling (stored in seconds)
        self._set_pending_setting("polling_interval", self.polling_interval_spin.value(), save=False)
        self._set_pending_setting("auto_refresh_enabled", self.auto_refresh_check.isChecked(), save=False)
        self._set_pending_setting("auto_refresh_interval", self.auto_refresh_interval_spin.value(), save=False)
        self._set_pending_setting(
            "activity_auto_refresh", self.activity_auto_refresh_check.isChecked(), save=False
        )
        self._set_pending_setting(
            "activity_refresh_interval",
            self.activity_refresh_interval_spin.value(),
            save=False,
        )

        # Preview Generation
        self._set_pending_setting("preview_quality", self.preview_quality_combo.currentText(), save=False)
        self._set_pending_setting("preview_output_subdir", self.preview_output_subdir_edit.text().strip(), save=False)
        self._set_pending_setting("preview_overwrite", self.preview_overwrite_check.isChecked(), save=False)
        self._set_pending_setting("nuke_exe_path", self.nuke_exe_path_edit.text().strip(), save=False)
        self._set_pending_setting("threede_exe_path", self.threede_exe_path_edit.text().strip(), save=False)
        self._set_pending_setting("houdini_user_pref_dir", self.houdini_pref_combo.currentText().strip(), save=False)
        
        # Debug modes
        self._set_pending_setting("debug_modes.general", self.debug_general_check.isChecked(), save=False)
        self._set_pending_setting("debug_modes.api_calls", self.debug_api_check.isChecked(), save=False)
        self._set_pending_setting("debug_modes.ui_updates", self.debug_ui_check.isChecked(), save=False)
        self._set_pending_setting(
            "debug_modes.notifications", self.debug_notifications_check.isChecked(), save=False
        )
        self._set_pending_setting(
            "debug_modes.project_load_profiler",
            self.debug_project_load_profiler_check.isChecked(),
            save=False,
        )
        self._set_pending_setting(
            "debug_modes.suppress_qt_multimedia_warnings",
            self.debug_qt_multimedia_check.isChecked(),
            save=False,
        )
        
        # Appearance
        self._set_pending_setting("theme_file", "dark_v01.qss", save=False)

        # Shots layout
        self._set_pending_setting(
            "shots_layout_mode",
            self.shots_layout_combo.currentData() or "list",
            save=False
        )

        # UI Density
        self._set_pending_setting("preview_thumbnail_size", self.preview_size_combo.currentText(), save=False)
        self._set_pending_setting(
            "nukedash_task_style",
            self.nukedash_task_style_combo.currentData() or "checklist",
            save=False,
        )
        self._set_pending_setting("card_spacing", self.card_spacing_spin.value(), save=False)
        self._set_pending_setting("row_height", self.row_height_spin.value(), save=False)
        self._set_pending_setting(
            "quick_view_screen_percentage",
            self.quick_view_size_spin.value(),
            save=False,
        )
        
        # Window
        self._set_pending_setting("remember_window_size", self.remember_size_check.isChecked(), save=False)
        self._set_pending_setting("always_on_top", self.always_on_top_check.isChecked(), save=False)
        
        # Session Restore
        self._set_pending_setting("remember_last_session", self.remember_session_check.isChecked(), save=False)

        # Startup
        self._set_pending_setting("startup_tab", self.startup_tab_combo.currentData(), save=False)
        self._set_pending_setting("show_startup_loading_dialog", self.show_startup_loading_check.isChecked(), save=False)
        self._set_pending_setting("enable_assignment_board", self.enable_assignment_board_check.isChecked(), save=False)
        self._set_pending_setting("enable_review_page", self.enable_review_page_check.isChecked(), save=False)
        self._set_pending_setting("enable_activity_page", self.enable_activity_page_check.isChecked(), save=False)
        self._set_pending_setting("enable_import_page", self.enable_import_page_check.isChecked(), save=False)
        self._set_pending_setting("enable_xml_import_page", self.enable_xml_import_page_check.isChecked(), save=False)
        
        # Notifications
        if self.notif_off_radio.isChecked():
            self._set_pending_setting("notifications", "off", save=False)
        elif self.notif_silent_radio.isChecked():
            self._set_pending_setting("notifications", "silent", save=False)
        else:
            self._set_pending_setting("notifications", "on", save=False)

        self._set_pending_setting(
            "notifications_do_not_disturb",
            self.notif_dnd_check.isChecked(),
            save=False
        )
        self._set_pending_setting(
            "notifications_animations",
            self.notif_animations_check.isChecked(),
            save=False
        )
        self._set_pending_setting(
            "notifications_subtle",
            self.notif_subtle_check.isChecked(),
            save=False
        )
        self._set_pending_setting(
            "notifications_lifetime",
            self.notif_lifetime_spin.value(),
            save=False
        )
        self._set_pending_setting(
            "notifications_size",
            self.notif_size_combo.currentText().lower(),
            save=False
        )
        
        # Now save to file
        if self._settings.save():
            self._autosave_pending = False
            self.save_status_label.setText("Settings saved. Startup options apply next launch.")
            if not automatic:
                QMessageBox.information(
                    self, "Settings Saved",
                    "Your settings have been saved successfully.\n\n"
                    "Startup page toggles apply on the next app launch."
                )
            # Emit signals for settings that can be applied immediately
            self._emit_saved_setting("auto_refresh_enabled", self.auto_refresh_check.isChecked())
            self._emit_saved_setting("polling_interval", self._settings.get_polling_interval_ms())
            self._emit_saved_setting("always_on_top", self.always_on_top_check.isChecked())
            self._emit_saved_setting(
                "activity_auto_refresh", self.activity_auto_refresh_check.isChecked()
            )
            self._emit_saved_setting(
                "activity_refresh_interval", self.activity_refresh_interval_spin.value()
            )

            # Preview generation settings
            self._emit_saved_setting("preview_quality", self.preview_quality_combo.currentText())
            self._emit_saved_setting("preview_output_subdir", self.preview_output_subdir_edit.text().strip())
            self._emit_saved_setting("preview_overwrite", self.preview_overwrite_check.isChecked())
            self._emit_saved_setting("nuke_exe_path", self.nuke_exe_path_edit.text().strip())
            self._emit_saved_setting("threede_exe_path", self.threede_exe_path_edit.text().strip())

            # UI density settings
            self._emit_saved_setting(
                "shots_layout_mode",
                self.shots_layout_combo.currentData() or "list"
            )
            self._emit_saved_setting("preview_thumbnail_size", self.preview_size_combo.currentText())
            self._emit_saved_setting(
                "nukedash_task_style",
                self.nukedash_task_style_combo.currentData() or "checklist",
            )
            self._emit_saved_setting("card_spacing", self.card_spacing_spin.value())
            self._emit_saved_setting("row_height", self.row_height_spin.value())
            self._emit_saved_setting(
                "quick_view_screen_percentage",
                self.quick_view_size_spin.value(),
            )

            # Startup settings
            self._emit_saved_setting("startup_tab", self.startup_tab_combo.currentData())
            self._emit_saved_setting("show_startup_loading_dialog", self.show_startup_loading_check.isChecked())
            
            # All debug modes
            self._emit_saved_setting("debug_modes.general", self.debug_general_check.isChecked())
            self._emit_saved_setting("debug_modes.api_calls", self.debug_api_check.isChecked())
            self._emit_saved_setting("debug_modes.ui_updates", self.debug_ui_check.isChecked())
            self._emit_saved_setting(
                "debug_modes.notifications", self.debug_notifications_check.isChecked()
            )
            self._emit_saved_setting(
                "debug_modes.project_load_profiler",
                self.debug_project_load_profiler_check.isChecked(),
            )
            self._emit_saved_setting(
                "debug_modes.suppress_qt_multimedia_warnings",
                self.debug_qt_multimedia_check.isChecked(),
            )

            # Notification settings
            if self.notif_off_radio.isChecked():
                mode_value = "off"
            elif self.notif_silent_radio.isChecked():
                mode_value = "silent"
            else:
                mode_value = "on"

            self._emit_saved_setting("notifications", mode_value)
            self._emit_saved_setting(
                "notifications_do_not_disturb", self.notif_dnd_check.isChecked()
            )
            self._emit_saved_setting(
                "notifications_animations", self.notif_animations_check.isChecked()
            )
            self._emit_saved_setting(
                "notifications_subtle", self.notif_subtle_check.isChecked()
            )
            self._emit_saved_setting(
                "notifications_lifetime", self.notif_lifetime_spin.value()
            )
            self._emit_saved_setting(
                "notifications_size", self.notif_size_combo.currentText().lower()
            )
            
            # Emit server URL change signal
            if not automatic or "server_url" in self._unsaved_setting_keys:
                self.server_url_changed.emit(self.server_url_edit.text().strip())
            self._unsaved_setting_keys.clear()
        else:
            self._autosave_pending = True
            self.save_status_label.setText("Save failed. Check file permissions, then click Save Settings to retry.")
            if not automatic:
                QMessageBox.warning(
                    self, "Save Failed",
                    "Failed to save settings. Please check file permissions."
                )
        self._automatic_save = False
    
    def _reset_to_defaults(self):
        """Reset all settings to defaults after confirmation."""
        reply = QMessageBox.question(
            self,
            "Reset Settings",
            "Are you sure you want to reset all settings to their default values?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._autosave_timer.stop()
            self._autosave_pending = False
            self._unsaved_setting_keys.clear()
            self._settings.reset_to_defaults()
            self._load_current_values()
            self.save_status_label.setText("Defaults restored. Startup options apply next launch.")
            QMessageBox.information(
                self,
                "Settings Reset",
                "All settings have been reset to defaults."
            )
    
    def _on_refresh_users(self):
        """Refresh Django users list."""
        self._flush_autosave()
        self._load_django_users()
        QMessageBox.information(self, "Users Refreshed", f"Loaded {len(self._django_users)} users from server.")

    def _on_browse_nuke_exe(self):
        """Browse for Nuke executable path."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Nuke Executable",
            "",
            "All Files (*)"
        )
        if path:
            self.nuke_exe_path_edit.setText(path)

    def _refresh_plugins_panel(self):
        self.plugins_status_label.clear()
        self.install_3de_plugins_btn.setEnabled(False)
        try:
            scripts = plugins_install.bundled_scripts()
            self.plugins_scripts_label.setText(", ".join(path.name for path in scripts))
        except (OSError, ValueError) as exc:
            self.plugins_scripts_label.setText(str(exc))
            self.plugins_destination_label.setText("Unavailable")
            return
        try:
            destination = plugins_install.resolve_destination(self.threede_exe_path_edit.text())
        except (OSError, ValueError) as exc:
            self.plugins_destination_label.setText(str(exc))
            return
        self.plugins_destination_label.setText(str(destination))
        self.install_3de_plugins_btn.setEnabled(True)

    def _on_install_3de_plugins(self):
        self.install_3de_plugins_btn.setEnabled(False)
        try:
            result = plugins_install.install_3de_plugins(self.threede_exe_path_edit.text())
        except (OSError, ValueError) as exc:
            message = f"Could not install 3DE plugins: {exc}"
            failed = True
        else:
            message = (
                f"Installed: {len(result.installed)}; updated: {len(result.updated)}; "
                f"unchanged: {len(result.unchanged)}.\nDestination: {result.destination}"
            )
            failed = bool(result.failures)
            if failed:
                message += "\nFailed:\n" + "\n".join(
                    f"{name}: {reason}" for name, reason in result.failures.items()
                )
            if not failed or result.installed or result.updated:
                message += "\nRestart 3DE to load the updated plugins."
        finally:
            self._refresh_plugins_panel()
        self.plugins_status_label.setText(message)
        if failed:
            QMessageBox.warning(self, "3DE Plugin Installation", message)
        else:
            QMessageBox.information(self, "3DE Plugin Installation", message)

    def _refresh_houdini_plugin_panel(self):
        self.houdini_status_label.clear()
        self.install_houdini_plugin_btn.setEnabled(False)
        try:
            destination = plugins_install.resolve_houdini_preferences(self.houdini_pref_combo.currentText())
        except (OSError, ValueError) as exc:
            self.houdini_destination_label.setText(str(exc))
            return
        self.houdini_destination_label.setText(str(destination / "shotbox_3de_import"))
        self.install_houdini_plugin_btn.setEnabled(True)

    def _on_browse_houdini_preferences(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Houdini User Preferences Folder", self.houdini_pref_combo.currentText())
        if folder:
            self.houdini_pref_combo.setCurrentText(folder)

    def _on_install_houdini_plugin(self):
        self.install_houdini_plugin_btn.setEnabled(False)
        try:
            result = plugins_install.install_houdini_plugin(self.houdini_pref_combo.currentText())
            failed = bool(result.failures)
            message = f"Installed: {len(result.installed)}; updated: {len(result.updated)}; unchanged: {len(result.unchanged)}."
            if failed:
                message += "\nFailed:\n" + "\n".join(f"{name}: {error}" for name, error in result.failures.items())
            else:
                message += "\nRestart Houdini and enable the ShotBox shelf if it is not visible."
        except (OSError, ValueError) as exc:
            failed = True
            message = f"Could not install Houdini plugin: {exc}"
        finally:
            self._refresh_houdini_plugin_panel()
        self.houdini_status_label.setText(message)
        (QMessageBox.warning if failed else QMessageBox.information)(self, "Houdini Plugin Installation", message)

    def _on_browse_threede_exe(self):
        """Browse for 3DE executable path."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select 3DE Executable",
            "",
            "All Files (*)"
        )
        if path:
            self.threede_exe_path_edit.setText(path)
    
    def _on_test_connection(self):
        """Test connection to server."""
        url = self.server_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Test Failed", "Please enter a server URL.")
            return
        
        try:
            import requests
            test_url = url.rstrip('/') + '/api/'
            response = requests.get(test_url, timeout=5)
            if response.ok:
                QMessageBox.information(
                    self,
                    "Connection Successful",
                    f"Successfully connected to:\n{test_url}\n\nStatus: {response.status_code}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Connection Issue",
                    f"Server responded with status {response.status_code}"
                )
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(
                self,
                "Connection Failed",
                f"Could not connect to server at:\n{url}\n\nPlease check the URL and ensure the server is running."
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Connection Error",
                f"Error testing connection:\n{str(e)}"
            )

    def _set_update_buttons_enabled(self, can_check: bool, can_update: bool) -> None:
        self.check_updates_btn.setEnabled(can_check)
        self.apply_update_btn.setEnabled(can_update)

    def apply_update_status(self, status: app_update.UpdateStatus) -> None:
        """Display an update status produced by a manual or automatic check."""
        self.current_version_label.setText(status.current_display)

        if status.current_branch:
            self.update_branch_label.setText(f"{status.branch} (current: {status.current_branch})")
        else:
            self.update_branch_label.setText(status.branch)

        self.latest_publish_label.setText(status.remote_display or "Not checked yet.")
        self.update_status_label.setText(status.status_message)
        self.update_changelog_label.setText(status.changelog_preview)
        self._set_update_buttons_enabled(status.can_check, status.can_update)

    def _apply_update_status(self, status: app_update.UpdateStatus) -> None:
        """Backward-compatible internal alias for update-status display."""
        self.apply_update_status(status)

    def set_update_check_in_progress(self) -> None:
        """Show the automatic checker state and prevent overlapping checks."""
        self.update_status_label.setText("Checking for updates...")
        self._set_update_buttons_enabled(False, False)

    def focus_update_section(self) -> None:
        """Scroll the Settings page to the app-update controls."""
        self.settings_scroll_area.ensureWidgetVisible(self.updates_group)

    def _refresh_update_panel(self) -> None:
        status = app_update.inspect_install()
        self.apply_update_status(status)

    def _on_check_for_updates(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            status = app_update.check_for_updates()
        finally:
            QApplication.restoreOverrideCursor()
        self.apply_update_status(status)

    def _on_update_and_restart(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            status = app_update.check_for_updates()
        finally:
            QApplication.restoreOverrideCursor()

        self.launch_update_and_restart(status, confirm=True)

    def launch_update_and_restart(
        self,
        status: app_update.UpdateStatus,
        *,
        parent=None,
        confirm: bool = True,
    ) -> bool:
        """Launch an available update, optionally asking for confirmation first."""
        self.apply_update_status(status)
        dialog_parent = parent or self

        if not status.can_update:
            QMessageBox.warning(dialog_parent, "Update Blocked", status.status_message)
            return False

        remote_display = status.remote_display or "the latest published version"
        if confirm:
            reply = QMessageBox.question(
                dialog_parent,
                "Update ShotBox",
                f"Update to {remote_display} and restart ShotBox now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        ok, error_message = app_update.launch_update_script(os.getpid())
        if not ok:
            QMessageBox.critical(
                dialog_parent,
                "Update Failed",
                f"Could not launch the updater script.\n\n{error_message}",
            )
            return False

        self.update_status_label.setText("Updater launched. ShotBox will now close.")
        app = QApplication.instance()
        if app:
            app.quit()
        return True
    
    def get_settings_manager(self) -> SettingsManager:
        """Get the settings manager instance."""
        return self._settings


# Singleton instance for global access
_settings_manager: Optional[SettingsManager] = None


def get_settings_manager() -> SettingsManager:
    """Get or create the global settings manager instance."""
    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    return _settings_manager


def get_setting(key: str, default: Any = None) -> Any:
    """Convenience function to get a setting value."""
    return get_settings_manager().get(key, default)


def set_setting(key: str, value: Any, save: bool = True) -> None:
    """Convenience function to set a setting value."""
    get_settings_manager().set(key, value, save)
