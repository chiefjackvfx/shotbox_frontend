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
import yaml
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, 
    QPushButton, QScrollArea, QFrame, QSizePolicy, QMessageBox,
    QButtonGroup, QRadioButton, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QIcon, QWheelEvent

import app_update
from app_version import UPDATE_BRANCH


# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(SCRIPT_DIR, "ui")


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
    
    # UI density settings
    "shots_layout_mode": "list",  # list/grid
    "compact_view_enabled": False,
    "preview_thumbnail_size": "Medium",  # NoThumb/Tiny/Small/Medium/Large
    "card_spacing": 8,  # px between shot cards
    "row_height": 0,  # px, 0 = auto
    
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
        "enabled": False,
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
        
        self._setup_ui()
        self._load_django_users()
        self._load_current_values()
        self._connect_signals()
        self._refresh_update_panel()
    
    def _setup_ui(self):
        """Build the settings UI."""
        # Main layout with scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Scroll area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        # Container widget for scroll content
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(16)
        container_layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel("Settings")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
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
        self.refresh_users_btn.setFixedWidth(30)
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
        self.test_connection_btn.setFixedWidth(120)
        server_layout.addRow("", self.test_connection_btn)
        
        container_layout.addWidget(server_group)
        
        # === Polling Section ===
        polling_group = self._create_group_box("Polling & Refresh")
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
        nuke_path_layout.addStretch()
        preview_layout.addRow("Nuke Executable:", nuke_path_layout)

        container_layout.addWidget(preview_group)
        
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
        layout_group = self._create_group_box("Shots Layout")
        layout_layout = QFormLayout(layout_group)

        self.shots_layout_combo = NoScrollComboBox()
        self.shots_layout_combo.addItem("List", "list")
        self.shots_layout_combo.addItem("Grid", "grid")
        layout_layout.addRow("Layout Mode:", self.shots_layout_combo)

        container_layout.addWidget(layout_group)

        # === UI Density Section ===
        density_group = self._create_group_box("UI Density")
        density_layout = QFormLayout(density_group)

        self.preview_size_combo = NoScrollComboBox()
        self.preview_size_combo.addItems(["NoThumb", "Tiny", "Small", "Medium", "Large"])
        density_layout.addRow("Preview Size:", self.preview_size_combo)

        self.card_spacing_spin = NoScrollSpinBox()
        self.card_spacing_spin.setRange(0, 40)
        self.card_spacing_spin.setSuffix(" px")
        density_layout.addRow("Card Spacing:", self.card_spacing_spin)

        self.row_height_spin = NoScrollSpinBox()
        self.row_height_spin.setRange(0, 400)
        self.row_height_spin.setSuffix(" px")
        self.row_height_spin.setToolTip("0 = auto")
        density_layout.addRow("Row Height:", self.row_height_spin)

        container_layout.addWidget(density_group)
        
        # === Window Section ===
        window_group = self._create_group_box("Window")
        window_layout = QFormLayout(window_group)
        
        self.remember_size_check = QCheckBox("Remember Window Size & Position")
        window_layout.addRow("", self.remember_size_check)
        
        self.always_on_top_check = QCheckBox("Always on Top")
        window_layout.addRow("", self.always_on_top_check)
        
        container_layout.addWidget(window_group)
        
        # === Session Restore Section ===
        session_group = self._create_group_box("Session Restore")
        session_layout = QVBoxLayout(session_group)
        
        self.remember_session_check = QCheckBox("Auto-load Last Project, Timeline & Scroll Position")
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
        updates_group = self._create_group_box("App Updates")
        updates_layout = QFormLayout(updates_group)

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

        update_buttons_layout = QHBoxLayout()
        self.check_updates_btn = QPushButton("Check for Updates")
        update_buttons_layout.addWidget(self.check_updates_btn)
        self.apply_update_btn = QPushButton("Update && Restart")
        self.apply_update_btn.setEnabled(False)
        update_buttons_layout.addWidget(self.apply_update_btn)
        update_buttons_layout.addStretch()
        updates_layout.addRow("", update_buttons_layout)

        container_layout.addWidget(updates_group)
        
        # === Action Buttons ===
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        
        self.reset_button = QPushButton("Reset to Defaults")
        self.reset_button.setObjectName("reset_button")
        buttons_layout.addWidget(self.reset_button)
        
        self.save_button = QPushButton("Save Settings")
        self.save_button.setObjectName("save_button")
        buttons_layout.addWidget(self.save_button)
        
        container_layout.addLayout(buttons_layout)
        
        # Add stretch at the end
        container_layout.addStretch()
        
        # Set up scroll area
        scroll.setWidget(container)
        main_layout.addWidget(scroll)
    
    def _create_group_box(self, title: str) -> QGroupBox:
        """Create a group box that inherits styling from the active QSS theme."""
        group = QGroupBox(title)
        return group
    
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
    
    def _load_current_values(self):
        """Load current settings values into UI widgets."""
        # Django user
        django_user_id = self._settings.get("django_username")
        if django_user_id is not None:
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
        self.card_spacing_spin.setValue(self._settings.get("card_spacing", 8))
        self.row_height_spin.setValue(self._settings.get("row_height", 0))
        
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
    
    def _connect_signals(self):
        """Connect UI signals to handlers."""
        # Save button
        self.save_button.clicked.connect(self._save_all_settings)
        
        # Reset button
        self.reset_button.clicked.connect(self._reset_to_defaults)
        
        # Refresh users button
        self.refresh_users_btn.clicked.connect(self._on_refresh_users)
        
        # Test connection button
        self.test_connection_btn.clicked.connect(self._on_test_connection)

        # Browse Nuke executable
        self.nuke_exe_browse_btn.clicked.connect(self._on_browse_nuke_exe)

        # Update controls
        self.check_updates_btn.clicked.connect(self._on_check_for_updates)
        self.apply_update_btn.clicked.connect(self._on_update_and_restart)
    
    def _save_all_settings(self):
        """Save all settings from UI to file."""
        # Django username
        self._settings.set("django_username", self.django_user_combo.currentData(), save=False)
        
        # Server
        self._settings.set("server_url", self.server_url_edit.text().strip(), save=False)
        
        # Polling (stored in seconds)
        self._settings.set("polling_interval", self.polling_interval_spin.value(), save=False)
        self._settings.set("auto_refresh_enabled", self.auto_refresh_check.isChecked(), save=False)
        self._settings.set("auto_refresh_interval", self.auto_refresh_interval_spin.value(), save=False)
        self._settings.set(
            "activity_auto_refresh", self.activity_auto_refresh_check.isChecked(), save=False
        )
        self._settings.set(
            "activity_refresh_interval",
            self.activity_refresh_interval_spin.value(),
            save=False,
        )

        # Preview Generation
        self._settings.set("preview_quality", self.preview_quality_combo.currentText(), save=False)
        self._settings.set("preview_output_subdir", self.preview_output_subdir_edit.text().strip(), save=False)
        self._settings.set("preview_overwrite", self.preview_overwrite_check.isChecked(), save=False)
        self._settings.set("nuke_exe_path", self.nuke_exe_path_edit.text().strip(), save=False)
        
        # Debug modes
        self._settings.set("debug_modes.general", self.debug_general_check.isChecked(), save=False)
        self._settings.set("debug_modes.api_calls", self.debug_api_check.isChecked(), save=False)
        self._settings.set("debug_modes.ui_updates", self.debug_ui_check.isChecked(), save=False)
        self._settings.set(
            "debug_modes.notifications", self.debug_notifications_check.isChecked(), save=False
        )
        self._settings.set(
            "debug_modes.project_load_profiler",
            self.debug_project_load_profiler_check.isChecked(),
            save=False,
        )
        self._settings.set(
            "debug_modes.suppress_qt_multimedia_warnings",
            self.debug_qt_multimedia_check.isChecked(),
            save=False,
        )
        
        # Appearance
        self._settings.set("theme_file", "dark_v01.qss", save=False)

        # Shots layout
        self._settings.set(
            "shots_layout_mode",
            self.shots_layout_combo.currentData() or "list",
            save=False
        )

        # UI Density
        self._settings.set("preview_thumbnail_size", self.preview_size_combo.currentText(), save=False)
        self._settings.set("card_spacing", self.card_spacing_spin.value(), save=False)
        self._settings.set("row_height", self.row_height_spin.value(), save=False)
        
        # Window
        self._settings.set("remember_window_size", self.remember_size_check.isChecked(), save=False)
        self._settings.set("always_on_top", self.always_on_top_check.isChecked(), save=False)
        
        # Session Restore
        self._settings.set("remember_last_session", self.remember_session_check.isChecked(), save=False)

        # Startup
        self._settings.set("startup_tab", self.startup_tab_combo.currentData(), save=False)
        self._settings.set("show_startup_loading_dialog", self.show_startup_loading_check.isChecked(), save=False)
        self._settings.set("enable_assignment_board", self.enable_assignment_board_check.isChecked(), save=False)
        self._settings.set("enable_review_page", self.enable_review_page_check.isChecked(), save=False)
        self._settings.set("enable_activity_page", self.enable_activity_page_check.isChecked(), save=False)
        self._settings.set("enable_import_page", self.enable_import_page_check.isChecked(), save=False)
        self._settings.set("enable_xml_import_page", self.enable_xml_import_page_check.isChecked(), save=False)
        
        # Notifications
        if self.notif_off_radio.isChecked():
            self._settings.set("notifications", "off", save=False)
        elif self.notif_silent_radio.isChecked():
            self._settings.set("notifications", "silent", save=False)
        else:
            self._settings.set("notifications", "on", save=False)

        self._settings.set(
            "notifications_do_not_disturb",
            self.notif_dnd_check.isChecked(),
            save=False
        )
        self._settings.set(
            "notifications_animations",
            self.notif_animations_check.isChecked(),
            save=False
        )
        self._settings.set(
            "notifications_subtle",
            self.notif_subtle_check.isChecked(),
            save=False
        )
        self._settings.set(
            "notifications_lifetime",
            self.notif_lifetime_spin.value(),
            save=False
        )
        self._settings.set(
            "notifications_size",
            self.notif_size_combo.currentText().lower(),
            save=False
        )
        
        # Now save to file
        if self._settings.save():
            QMessageBox.information(
                self,
                "Settings Saved",
                "Your settings have been saved successfully.\n\n"
                "Startup page toggles apply on the next app launch."
            )
            # Emit signals for settings that can be applied immediately
            self.settings_changed.emit("auto_refresh_enabled", self.auto_refresh_check.isChecked())
            self.settings_changed.emit("polling_interval", self._settings.get_polling_interval_ms())
            self.settings_changed.emit("always_on_top", self.always_on_top_check.isChecked())
            self.settings_changed.emit(
                "activity_auto_refresh", self.activity_auto_refresh_check.isChecked()
            )
            self.settings_changed.emit(
                "activity_refresh_interval", self.activity_refresh_interval_spin.value()
            )

            # Preview generation settings
            self.settings_changed.emit("preview_quality", self.preview_quality_combo.currentText())
            self.settings_changed.emit("preview_output_subdir", self.preview_output_subdir_edit.text().strip())
            self.settings_changed.emit("preview_overwrite", self.preview_overwrite_check.isChecked())
            self.settings_changed.emit("nuke_exe_path", self.nuke_exe_path_edit.text().strip())

            # UI density settings
            self.settings_changed.emit(
                "shots_layout_mode",
                self.shots_layout_combo.currentData() or "list"
            )
            self.settings_changed.emit("preview_thumbnail_size", self.preview_size_combo.currentText())
            self.settings_changed.emit("card_spacing", self.card_spacing_spin.value())
            self.settings_changed.emit("row_height", self.row_height_spin.value())

            # Startup settings
            self.settings_changed.emit("startup_tab", self.startup_tab_combo.currentData())
            self.settings_changed.emit("show_startup_loading_dialog", self.show_startup_loading_check.isChecked())
            
            # All debug modes
            self.settings_changed.emit("debug_modes.general", self.debug_general_check.isChecked())
            self.settings_changed.emit("debug_modes.api_calls", self.debug_api_check.isChecked())
            self.settings_changed.emit("debug_modes.ui_updates", self.debug_ui_check.isChecked())
            self.settings_changed.emit(
                "debug_modes.notifications", self.debug_notifications_check.isChecked()
            )
            self.settings_changed.emit(
                "debug_modes.project_load_profiler",
                self.debug_project_load_profiler_check.isChecked(),
            )
            self.settings_changed.emit(
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

            self.settings_changed.emit("notifications", mode_value)
            self.settings_changed.emit(
                "notifications_do_not_disturb", self.notif_dnd_check.isChecked()
            )
            self.settings_changed.emit(
                "notifications_animations", self.notif_animations_check.isChecked()
            )
            self.settings_changed.emit(
                "notifications_subtle", self.notif_subtle_check.isChecked()
            )
            self.settings_changed.emit(
                "notifications_lifetime", self.notif_lifetime_spin.value()
            )
            self.settings_changed.emit(
                "notifications_size", self.notif_size_combo.currentText().lower()
            )
            
            # Emit server URL change signal
            self.server_url_changed.emit(self.server_url_edit.text().strip())
        else:
            QMessageBox.warning(
                self,
                "Save Failed",
                "Failed to save settings. Please check file permissions."
            )
    
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
            self._settings.reset_to_defaults()
            self._load_current_values()
            QMessageBox.information(
                self,
                "Settings Reset",
                "All settings have been reset to defaults."
            )
    
    def _on_refresh_users(self):
        """Refresh Django users list."""
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

    def _apply_update_status(self, status: app_update.UpdateStatus) -> None:
        self.current_version_label.setText(status.current_display)

        if status.current_branch:
            self.update_branch_label.setText(f"{status.branch} (current: {status.current_branch})")
        else:
            self.update_branch_label.setText(status.branch)

        self.latest_publish_label.setText(status.remote_display or "Not checked yet.")
        self.update_status_label.setText(status.status_message)
        self.update_changelog_label.setText(status.changelog_preview)
        self._set_update_buttons_enabled(status.can_check, status.can_update)

    def _refresh_update_panel(self) -> None:
        status = app_update.inspect_install()
        self._apply_update_status(status)

    def _on_check_for_updates(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            status = app_update.check_for_updates()
        finally:
            QApplication.restoreOverrideCursor()
        self._apply_update_status(status)

    def _on_update_and_restart(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            status = app_update.check_for_updates()
        finally:
            QApplication.restoreOverrideCursor()

        self._apply_update_status(status)

        if not status.can_update:
            QMessageBox.warning(self, "Update Blocked", status.status_message)
            return

        remote_display = status.remote_display or "the latest published version"
        reply = QMessageBox.question(
            self,
            "Update ShotBox",
            f"Update to {remote_display} and restart ShotBox now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, error_message = app_update.launch_update_script(os.getpid())
        if not ok:
            QMessageBox.critical(
                self,
                "Update Failed",
                f"Could not launch the updater script.\n\n{error_message}",
            )
            return

        self.update_status_label.setText("Updater launched. ShotBox will now close.")
        app = QApplication.instance()
        if app:
            app.quit()
    
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
