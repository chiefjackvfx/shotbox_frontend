from __future__ import annotations

# main.py
"""
ShotBox Main Application
"""
import os
import sys
import platform
import importlib
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QTimer, Qt, qInstallMessageHandler
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QSizePolicy
import http_help
import filesIO
from duration_updater import DurationUpdaterPage
from settings import SettingsPage, get_settings_manager
from page_nukedash import page_nukedash
from nuke_headless_tasks import PreviewConfig
import widgets as widgets_module  # For updating BASE_URL
from shotbox_notifications import NotificationSystem

ENABLE_ASSIGNMENT_BOARD = False  # Default for restoring the Assignment Board tab on next launch.
ENABLE_REVIEW_PAGE = False  # Default for restoring the Review tab on next launch.
ENABLE_ACTIVITY_PAGE = False  # Default for restoring the Activity tab on next launch.
ENABLE_IMPORT_PAGE = False  # Default for restoring the Import tab on next launch.
ENABLE_XML_IMPORT_PAGE = False  # Default for restoring the XML Import tab on next launch.


def _load_optional_class(enabled: bool, module_name: str, class_name: str):
    if not enabled:
        return None
    module = importlib.import_module(module_name)
    return getattr(module, class_name, None)




# Get the directory where this script is located (for cross-platform path handling)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(SCRIPT_DIR, "ui")
APP_ICON_PATH = os.path.join(UI_DIR, "shotbox_logo.png")

# Debug flags - can be toggled from settings
DEBUG_GENERAL = False
DEBUG_API = False
DEBUG_UI = False
DEBUG_NOTIFICATIONS = False
DEBUG_PROJECT_LOAD_PROFILER = False

_qt_message_handler_prev = None


def _qt_message_filter(msg_type, context, message):
    global _qt_message_handler_prev
    if "QObject::disconnect" in message and "QFFmpeg::" in message:
        return
    if _qt_message_handler_prev:
        _qt_message_handler_prev(msg_type, context, message)


def _set_qt_multimedia_warning_filter(enabled: bool) -> None:
    global _qt_message_handler_prev
    if enabled:
        if _qt_message_handler_prev is None:
            _qt_message_handler_prev = qInstallMessageHandler(_qt_message_filter)
    else:
        if _qt_message_handler_prev is not None:
            qInstallMessageHandler(_qt_message_handler_prev)
            _qt_message_handler_prev = None

# Only set XCB platform on Linux (Windows auto-detects the correct plugin)
if platform.system() == "Linux":
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Initialize settings manager first
        self._settings_manager = get_settings_manager()
        user_id = self._settings_manager.get("django_username")
        if user_id:
            http_help.DjangoAPI.set_current_user_by_id(user_id)

        self._enable_assignment_board = bool(
            self._settings_manager.get("enable_assignment_board", ENABLE_ASSIGNMENT_BOARD)
        )
        self._enable_review_page = bool(
            self._settings_manager.get("enable_review_page", ENABLE_REVIEW_PAGE)
        )
        self._enable_activity_page = bool(
            self._settings_manager.get("enable_activity_page", ENABLE_ACTIVITY_PAGE)
        )
        self._enable_import_page = bool(
            self._settings_manager.get("enable_import_page", ENABLE_IMPORT_PAGE)
        )
        self._enable_xml_import_page = bool(
            self._settings_manager.get("enable_xml_import_page", ENABLE_XML_IMPORT_PAGE)
        )
        
        # Apply settings to modules before creating pages
        self._apply_initial_settings()

        # Notifications
        self._notification_system = NotificationSystem()
        self._apply_notification_settings()

        self.setWindowTitle('ShotBox')
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))
        self.page_nukedash = page_nukedash()
        
        # Create the importer page
        importer_page_class = _load_optional_class(
            self._enable_import_page, "importer_page", "ImporterPage"
        )
        if importer_page_class is not None:
            self.page_importer = importer_page_class()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.page_nukedash, 'Tasks / Nuke dash')
        assignment_board_class = _load_optional_class(
            self._enable_assignment_board, "page_assignment_board", "AssignmentBoardPage"
        )
        if assignment_board_class is not None:
            self.page_assignment_board = assignment_board_class()
            self.tabs.addTab(self.page_assignment_board, 'Assignment Board')
        review_page_class = _load_optional_class(
            self._enable_review_page, "review_page", "ReviewPage"
        )
        if review_page_class is not None:
            self.page_review = review_page_class()
            self.tabs.addTab(self.page_review, 'Review')
        if hasattr(self, "page_importer"):
            self.tabs.addTab(self.page_importer, 'Import')

        # XML Import V2 (combined XML parsing + Nuke setup + Django import)
        xml_import_page_class = _load_optional_class(
            self._enable_xml_import_page, "import_xml_v2", "XMLImportPage"
        )
        if xml_import_page_class is not None:
            self.page_xmlImport = xml_import_page_class()
            self.tabs.addTab(self.page_xmlImport, 'XML Import')

        self.page_DurationUpdaterPage = DurationUpdaterPage()
        #self.tabs.addTab(self.page_DurationUpdaterPage, "Duration Updater")
        
        # Activity Page
        activity_page_class = _load_optional_class(
            self._enable_activity_page, "activity_page", "ActivityPage"
        )
        if activity_page_class is not None:
            self.page_activity = activity_page_class()
            self.tabs.addTab(self.page_activity, '📋 Activity')
        
        # Settings page (keep at the end)
        self.page_settings = SettingsPage(self._settings_manager)
        self.tabs.addTab(self.page_settings, '⚙ Settings')
        
        # Connect settings signals
        self.page_settings.settings_changed.connect(self._on_settings_changed)
        self.page_settings.server_url_changed.connect(self._on_server_url_changed)
        
        self.setCentralWidget(self.tabs)
        self._relax_minimum_sizes()
        
        self._shown_once = False
        self._review_sync_in_progress = False
        self._review_files_io = filesIO.Folders()
        self._review_refresh_requested = False
        
        # Apply saved window settings
        self._apply_window_settings()
        
        # Apply runtime settings into pages
        self._apply_runtime_settings_to_pages()
        if hasattr(self, "page_review"):
            self._wire_review_page()
        if hasattr(self, "page_assignment_board"):
            self._wire_assignment_board()
        if hasattr(self, "page_activity"):
            self._wire_activity_page()
        self._apply_startup_tab()

    def _relax_minimum_sizes(self):
        """Allow the main window to shrink below child size hints."""
        self.setMinimumSize(0, 0)
        self.tabs.setMinimumSize(0, 0)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        pages = [
            self.page_nukedash,
            self.page_settings,
        ]
        if hasattr(self, "page_assignment_board"):
            pages.insert(1, self.page_assignment_board)
        if hasattr(self, "page_review"):
            pages.insert(1, self.page_review)
        if hasattr(self, "page_importer"):
            pages.insert(-1, self.page_importer)
        if hasattr(self, "page_xmlImport"):
            pages.insert(-1, self.page_xmlImport)
        if hasattr(self, "page_activity"):
            pages.insert(-1, self.page_activity)
        for page in pages:
            page.setMinimumSize(0, 0)
            page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            if isinstance(page, QMainWindow):
                central = page.centralWidget()
                if central:
                    central.setMinimumSize(0, 0)
                    central.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        
    def _on_activity_clicked(self, shot_id: int, job_id: int):
        """Handle click on activity entry - navigate to the shot."""
        # Switch to Tasks tab
        self.tabs.setCurrentWidget(self.page_nukedash)
        
        # You could expand the relevant job/timeline here if you have that functionality
        print(f"[Activity] Navigate to shot {shot_id} in job {job_id}")

    def _apply_initial_settings(self):
        """Apply settings to modules before they are instantiated."""
        # Apply server URL to http_help and widgets (must happen before any API objects are created)
        server_url = self._settings_manager.get("server_url", "http://192.168.10.207:8000")
        self._update_server_url(server_url)

        # Apply theme early (so UI and dialogs pick it up on first paint)
        theme_file = self._settings_manager.get("theme_file", "dark_v01.qss")
        self._apply_theme_file(theme_file)

        # Apply debug flags early (modules/pages can read these globals)
        global DEBUG_GENERAL, DEBUG_API, DEBUG_UI, DEBUG_NOTIFICATIONS, DEBUG_PROJECT_LOAD_PROFILER
        DEBUG_GENERAL = bool(self._settings_manager.get("debug_modes.general", False))
        DEBUG_API = bool(self._settings_manager.get("debug_modes.api_calls", False))
        DEBUG_UI = bool(self._settings_manager.get("debug_modes.ui_updates", False))
        DEBUG_NOTIFICATIONS = bool(
            self._settings_manager.get("debug_modes.notifications", False)
        )
        DEBUG_PROJECT_LOAD_PROFILER = bool(
            self._settings_manager.get("debug_modes.project_load_profiler", False)
        )
        self._debug_notifications = DEBUG_NOTIFICATIONS

        suppress_qt_warnings = bool(
            self._settings_manager.get("debug_modes.suppress_qt_multimedia_warnings", False)
        )
        _set_qt_multimedia_warning_filter(suppress_qt_warnings)

        # Apply preview generation defaults
        preview_quality = self._settings_manager.get("preview_quality", "medium")
        PreviewConfig.DEFAULT_QUALITY = preview_quality or PreviewConfig.DEFAULT_QUALITY
        preview_subdir = self._settings_manager.get(
            "preview_output_subdir", PreviewConfig.PREVIEW_SUBDIR
        )
        PreviewConfig.PREVIEW_SUBDIR = preview_subdir or PreviewConfig.PREVIEW_SUBDIR
    
    
    def _apply_theme_file(self, theme_file: str) -> None:
        """Apply a QSS theme file to the whole app."""
        _ = theme_file
        locked_theme_file = "dark_v01.qss"

        qss_paths = [
            os.path.join(SCRIPT_DIR, "ui", locked_theme_file),  # ./ui/theme.qss
            os.path.join(SCRIPT_DIR, locked_theme_file),        # ./theme.qss (legacy)
        ]

        for qss_path in qss_paths:
            if os.path.exists(qss_path):
                try:
                    with open(qss_path, "r", encoding="utf-8") as f:
                        qss = f.read()
                    app = QtWidgets.QApplication.instance()
                    if app:
                        app.setStyleSheet(qss)
                    return
                except Exception as e:
                    print(f"[Theme] Error loading theme {locked_theme_file}: {e}")
                    return

        print(f"[Theme] Theme file not found: {locked_theme_file}")

    def _update_server_url(self, url: str):
        """Update server URL in all relevant modules."""
        if not url:
            return
        
        base_url = url.rstrip('/')
        api_url = base_url + '/api/'
        
        # Update http_help.DjangoAPI default base_url
        http_help.DjangoAPI.set_default_base_url(api_url)
        
        # Update widgets.BASE_URL
        widgets_module.BASE_URL = base_url
    
    def _apply_window_settings(self):
        """Apply saved window size/position and always-on-top settings."""
        if self._settings_manager.get("remember_window_size", True):
            width = self._settings_manager.get("window_width", 1200)
            height = self._settings_manager.get("window_height", 800)
            x = self._settings_manager.get("window_x", 100)
            y = self._settings_manager.get("window_y", 100)
            self.resize(width, height)
            self.move(x, y)
        
        if self._settings_manager.get("always_on_top", False):
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

    def _startup_tab_widget(self, startup_tab: int):
        logical_tabs = {
            0: getattr(self, "page_nukedash", None),
            1: getattr(self, "page_review", None),
            2: getattr(self, "page_xmlImport", None),
            3: getattr(self, "page_activity", None),
            4: getattr(self, "page_settings", None),
        }
        return logical_tabs.get(startup_tab)

    def _apply_startup_tab(self) -> None:
        """Select the configured startup tab using stable logical tab ids."""
        startup_tab = self._settings_manager.get("startup_tab", 0)
        if not isinstance(startup_tab, int):
            return
        widget = self._startup_tab_widget(startup_tab)
        if widget is not None:
            self.tabs.setCurrentWidget(widget)
    
    def _apply_runtime_settings_to_pages(self) -> None:
        """Push saved settings into already-created page widgets/timers."""
        # Polling interval for the API worker (page_nukedash._timer)
        polling_ms = self._settings_manager.get_polling_interval_ms()
        if hasattr(self, "page_nukedash") and hasattr(self.page_nukedash, "_timer"):
            self.page_nukedash._timer.setInterval(polling_ms)

        # Auto refresh toggle (reuses existing checkbox logic)
        auto_refresh_enabled = bool(self._settings_manager.get("auto_refresh_enabled", True))
        if hasattr(self, "page_nukedash") and hasattr(self.page_nukedash, "checkBox_refresh"):
            self.page_nukedash.checkBox_refresh.setChecked(auto_refresh_enabled)

        # Activity feed refresh settings
        activity_auto_refresh = bool(self._settings_manager.get("activity_auto_refresh", True))
        activity_interval = int(self._settings_manager.get("activity_refresh_interval", 30))
        if hasattr(self, "page_activity"):
            self.page_activity.set_auto_refresh(activity_auto_refresh)
            self.page_activity.set_refresh_interval(activity_interval)
            self.page_activity.set_notifications_debug(
                bool(getattr(self, "_debug_notifications", False))
            )

        # UI density defaults for nukedash
        shots_layout_mode = self._settings_manager.get("shots_layout_mode", "list")
        compact_view_enabled = bool(self._settings_manager.get("compact_view_enabled", False))
        preview_size = self._settings_manager.get("preview_thumbnail_size", "Medium")
        card_spacing = self._settings_manager.get("card_spacing", 8)
        row_height = self._settings_manager.get("row_height", 0)
        if hasattr(self, "page_nukedash") and hasattr(self.page_nukedash, "apply_ui_density_settings"):
            if hasattr(self.page_nukedash, "checkBox_compact_view"):
                self.page_nukedash.checkBox_compact_view.setChecked(compact_view_enabled)
            if hasattr(self.page_nukedash, "apply_shots_layout_mode"):
                self.page_nukedash.apply_shots_layout_mode(shots_layout_mode)
            self.page_nukedash.apply_ui_density_settings(
                preview_size=preview_size,
                card_spacing=card_spacing,
                row_height=row_height
            )


    def _apply_notification_settings(self) -> None:
        if not getattr(self, "_notification_system", None):
            return
        mode = self._settings_manager.get("notifications", "off")
        self._notification_system.set_mode(mode)

        lifetime_seconds = self._settings_manager.get("notifications_lifetime", 5)
        self._notification_system.set_lifetime_ms(int(lifetime_seconds) * 1000)

        size = self._settings_manager.get("notifications_size", "normal")
        self._notification_system.set_size(str(size).lower())

        animations = self._settings_manager.get("notifications_animations", True)
        self._notification_system.set_animations(bool(animations))

        dnd = self._settings_manager.get("notifications_do_not_disturb", False)
        self._notification_system.set_do_not_disturb(bool(dnd))

        subtle = self._settings_manager.get("notifications_subtle", False)
        self._notification_system.set_subtle_mode(bool(subtle))

    def _wire_review_page(self) -> None:
        if not hasattr(self, "page_review") or not hasattr(self, "page_nukedash"):
            return

        if hasattr(self.page_review, "review_selection_bar"):
            bar = self.page_review.review_selection_bar
            bar.job_changed.connect(self._on_review_job_selected)
            bar.timeline_changed.connect(self._on_review_timeline_selected)
            bar.refresh_clicked.connect(self._on_review_refresh_clicked)

        self.page_nukedash.jobs_data_updated.connect(self._on_jobs_data_updated_for_review)
        self.page_nukedash.active_job_changed.connect(self._on_active_job_changed_for_review)
        self.page_nukedash.active_timeline_changed.connect(self._on_active_timeline_changed_for_review)

    def _wire_activity_page(self) -> None:
        if not hasattr(self, "page_activity"):
            return
        if hasattr(self.page_activity, "activity_notification"):
            self.page_activity.activity_notification.connect(self._on_activity_notification)

    def _log_notifications(self, message: str) -> None:
        if getattr(self, "_debug_notifications", False):
            print(message)

    def _on_activity_notification(self, activity: dict) -> None:
        self._log_notifications(f"[Notifications] Received activity: {activity.get('id')}")
        if not getattr(self, "_notification_system", None):
            self._log_notifications("[Notifications] Notification system not initialized")
            return
        if not self._notification_system.is_enabled():
            self._log_notifications("[Notifications] Notification system disabled by settings")
            return

        username = activity.get("username") or "System"
        action = activity.get("action_text") or activity.get("action_type") or "Updated"
        shot_code = activity.get("shot_code") or ""
        detail = self._format_activity_detail(activity)
        time_ago = activity.get("timestamp_human")

        self._notification_system.show_notification(
            username=username,
            action=action,
            shot_code=shot_code,
            detail=detail,
            time_ago=time_ago,
        )
        self._log_notifications("[Notifications] Notification shown")

    def _format_activity_detail(self, activity: dict) -> str:
        old_value = activity.get("old_value")
        new_value = activity.get("new_value")
        if old_value and new_value and old_value != "None":
            return f"{old_value} → {new_value}"
        if new_value and new_value != "None":
            return str(new_value)

        details = activity.get("details", {}) or {}
        task_title = details.get("task_title")
        if task_title:
            return f"Task: {task_title}"
        field_name = details.get("field")
        if field_name:
            return f"Field: {field_name}"
        return ""

    def _wire_assignment_board(self) -> None:
        if not hasattr(self, "page_assignment_board") or not hasattr(self, "page_nukedash"):
            return
        self.page_nukedash.jobs_data_updated.connect(self._on_jobs_data_updated_for_assignment)
        self.page_nukedash.active_job_changed.connect(self._on_active_job_changed_for_assignment)
        self.page_nukedash.active_timeline_changed.connect(self._on_active_timeline_changed_for_assignment)
        self.page_assignment_board.drag_state_changed.connect(self._on_assignment_drag_state_changed)
        if hasattr(self, "tabs"):
            self.tabs.currentChanged.connect(self._on_tab_changed)
        QTimer.singleShot(0, self._sync_assignment_board_from_nukedash)

    def _on_tab_changed(self, index: int) -> None:
        if not hasattr(self, "tabs") or not hasattr(self, "page_assignment_board"):
            return
        if self.tabs.widget(index) is self.page_assignment_board:
            self._sync_assignment_board_from_nukedash()

    def _sync_assignment_board_from_nukedash(self) -> None:
        if not hasattr(self, "page_assignment_board") or not hasattr(self, "page_nukedash"):
            return
        jobs_by_id = getattr(self.page_nukedash, "_jobs_by_id", None)
        if not jobs_by_id:
            return
        jobs = list(jobs_by_id.values())
        if jobs:
            self._on_jobs_data_updated_for_assignment(jobs)

    def _on_assignment_drag_state_changed(self, active: bool) -> None:
        if hasattr(self, "page_nukedash") and hasattr(self.page_nukedash, "set_auto_refresh_paused"):
            self.page_nukedash.set_auto_refresh_paused(active)

    def _on_jobs_data_updated_for_assignment(self, jobs: list):
        if not hasattr(self, "page_assignment_board"):
            return
        self.page_assignment_board.set_jobs_data(jobs)

    def _on_active_job_changed_for_assignment(self, job_data: dict):
        if not hasattr(self, "page_assignment_board"):
            return
        job_id = job_data.get("id")
        if job_id is not None:
            self.page_assignment_board.set_active_job_id(job_id)

    def _on_active_timeline_changed_for_assignment(self, index: int):
        if not hasattr(self, "page_assignment_board"):
            return
        job_data = self._get_active_job_data()
        if not job_data:
            return
        timelines = job_data.get("timelines", [])
        if not (0 <= index < len(timelines)):
            return
        timeline_id = timelines[index].get("id")
        if hasattr(self.page_assignment_board, "set_active_timeline_id"):
            self.page_assignment_board.set_active_timeline_id(timeline_id)

    def _on_jobs_data_updated_for_review(self, jobs: list):
        if not hasattr(self, "page_review"):
            return
        bar = getattr(self.page_review, "review_selection_bar", None)
        if not bar:
            return
        should_update = self._review_refresh_requested or bar.combo_job.count() == 0
        if not should_update:
            return

        self.page_review.set_job_options(jobs)

        active_job_id = getattr(self.page_nukedash, "_active_job_id", None)
        if active_job_id is not None:
            self._sync_review_selection(job_id=active_job_id)

            job_data = self.page_nukedash._jobs_by_id.get(active_job_id)
            if job_data:
                timelines = job_data.get("timelines", [])
                if hasattr(self.page_nukedash, "timelines_tabs"):
                    timeline_index = self.page_nukedash.timelines_tabs.currentIndex()
                else:
                    timeline_index = 0
                if 0 <= timeline_index < len(timelines):
                    timeline_id = timelines[timeline_index].get("id")
                    self.page_review.set_timeline_options(timelines)
                    self._sync_review_selection(job_id=active_job_id, timeline_id=timeline_id)
                    if timeline_id is not None:
                        self._update_review_shots(job_data, timeline_id)

        self._review_refresh_requested = False

    def _on_review_refresh_clicked(self):
        self._review_refresh_requested = True
        self.page_nukedash.refresh_clicked()

    def _on_active_job_changed_for_review(self, job_data: dict):
        if not hasattr(self, "page_review"):
            return
        timelines = job_data.get("timelines", [])
        self.page_review.set_timeline_options(timelines)

        active_job_id = job_data.get("id")
        timeline_index = 0
        if hasattr(self.page_nukedash, "timelines_tabs"):
            timeline_index = self.page_nukedash.timelines_tabs.currentIndex()

        timeline_id = None
        if 0 <= timeline_index < len(timelines):
            timeline_id = timelines[timeline_index].get("id")

        self._sync_review_selection(job_id=active_job_id, timeline_id=timeline_id)
        if timeline_id is not None:
            self._update_review_shots(job_data, timeline_id)
        else:
            self.page_review.set_shots([])

    def _on_active_timeline_changed_for_review(self, index: int):
        job_data = self._get_active_job_data()
        if not job_data:
            return
        timelines = job_data.get("timelines", [])
        if not (0 <= index < len(timelines)):
            return
        timeline = timelines[index]
        timeline_id = timeline.get("id")
        self._sync_review_selection(job_id=job_data.get("id"), timeline_id=timeline_id)
        if timeline_id is not None:
            self._update_review_shots(job_data, timeline_id)

    def _on_review_job_selected(self, job_id: int):
        if self._review_sync_in_progress:
            return
        self._select_job_in_tasks(job_id)

    def _on_review_timeline_selected(self, timeline_id: int):
        if self._review_sync_in_progress:
            return
        job_data = self._get_active_job_data()
        if not job_data:
            return
        timelines = job_data.get("timelines", [])
        for idx, timeline in enumerate(timelines):
            if timeline.get("id") == timeline_id:
                if hasattr(self.page_nukedash, "timelines_tabs"):
                    self.page_nukedash.timelines_tabs.setCurrentIndex(idx)
                return

    def _sync_review_selection(self, job_id: int | None = None, timeline_id: int | None = None):
        if not hasattr(self, "page_review"):
            return
        self._review_sync_in_progress = True
        try:
            if job_id is not None:
                self.page_review.set_selected_job_id(job_id)
            if timeline_id is not None:
                self.page_review.set_selected_timeline_id(timeline_id)
        finally:
            self._review_sync_in_progress = False

    def _get_active_job_data(self) -> dict | None:
        if not hasattr(self.page_nukedash, "_jobs_by_id"):
            return None
        active_job_id = getattr(self.page_nukedash, "_active_job_id", None)
        if active_job_id is None:
            return None
        return self.page_nukedash._jobs_by_id.get(active_job_id)

    def _select_job_in_tasks(self, job_id: int):
        if not hasattr(self.page_nukedash, "comboBox_jobs"):
            return
        combo = self.page_nukedash.comboBox_jobs
        for i in range(combo.count()):
            if combo.itemData(i) == job_id:
                combo.setCurrentIndex(i)
                return

    def _update_review_shots(self, job_data: dict, timeline_id: int):
        if not hasattr(self, "page_review"):
            return
        timeline = None
        for tl in job_data.get("timelines", []):
            if tl.get("id") == timeline_id:
                timeline = tl
                break
        if not timeline:
            self.page_review.set_shots([])
            return
        shots = self._build_review_shots_from_timeline(timeline)
        self.page_review.set_shots(shots)

    def _build_review_shots_from_timeline(self, timeline: dict) -> list:
        shots = []
        for shot in timeline.get("shots", []):
            preview_video = shot.get("preview_video")
            base_path = shot.get("base_path")
            if not preview_video:
                continue

            preview_path = Path(preview_video)
            if preview_path.is_absolute():
                full_path = Path(self._review_files_io.convert_path(preview_video))
            elif base_path:
                full_path = Path(self._review_files_io.convert_path(base_path)) / preview_video
            else:
                full_path = None

            if not full_path or not full_path.exists():
                continue

            shots.append({
                "id": shot.get("id"),
                "title": shot.get("title", f"Shot {shot.get('id', '')}"),
                "video_path": str(full_path),
                "tasks": shot.get("tasks", []),
                "base_path": base_path,
                "preview_video": preview_video,
                "thumbnail": f"{widgets_module.BASE_URL}{shot.get('thumbnail')}" if shot.get("thumbnail") else None,
                "duration": shot.get("duration"),
                "handles": shot.get("handles"),
                "colourspace": shot.get("colourspace") or shot.get("colorspace"),
                "edit_inpoint": shot.get("edit_inpoint"),
                "edit_outpoint": shot.get("edit_outpoint"),
            })

        return shots


    def _on_settings_changed(self, key: str, value):
        """Handle settings changes that need immediate action."""
        if key == "always_on_top":
            # Toggle always on top - works immediately without restart
            was_visible = self.isVisible()
            if value:
                self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            if was_visible:
                self.show()  # Re-show to apply the flag change
        
        elif key == "auto_refresh_enabled":
            # Toggle auto-refresh in nukedash page
            if hasattr(self, 'page_nukedash') and hasattr(self.page_nukedash, 'checkBox_refresh'):
                self.page_nukedash.checkBox_refresh.setChecked(value)
        
        elif key == "polling_interval":
            # Update polling timer interval (value is already in ms)
            if hasattr(self, 'page_nukedash') and hasattr(self.page_nukedash, '_timer'):
                self.page_nukedash._timer.setInterval(value)

        elif key == "activity_auto_refresh":
            if hasattr(self, "page_activity"):
                self.page_activity.set_auto_refresh(bool(value))

        elif key == "activity_refresh_interval":
            if hasattr(self, "page_activity"):
                self.page_activity.set_refresh_interval(int(value))

        elif key == "preview_quality":
            PreviewConfig.DEFAULT_QUALITY = value or PreviewConfig.DEFAULT_QUALITY

        elif key == "preview_output_subdir":
            PreviewConfig.PREVIEW_SUBDIR = value or PreviewConfig.PREVIEW_SUBDIR

        elif key == "preview_thumbnail_size":
            if hasattr(self, 'page_nukedash'):
                self.page_nukedash.apply_ui_density_settings(preview_size=value)

        elif key.startswith("notifications"):
            self._apply_notification_settings()

        elif key == "debug_modes.suppress_qt_multimedia_warnings":
            _set_qt_multimedia_warning_filter(bool(value))

        elif key == "card_spacing":
            if hasattr(self, 'page_nukedash'):
                self.page_nukedash.apply_ui_density_settings(card_spacing=value)

        elif key == "row_height":
            if hasattr(self, 'page_nukedash'):
                self.page_nukedash.apply_ui_density_settings(row_height=value)

        elif key == "shots_layout_mode":
            if hasattr(self, "page_nukedash") and hasattr(self.page_nukedash, "apply_shots_layout_mode"):
                self.page_nukedash.apply_shots_layout_mode(value)

        elif key == "compact_view_enabled":
            if hasattr(self, "page_nukedash") and hasattr(self.page_nukedash, "checkBox_compact_view"):
                self.page_nukedash.checkBox_compact_view.setChecked(bool(value))

        elif key == "startup_tab":
            if isinstance(value, int):
                widget = self._startup_tab_widget(value)
                if widget is not None:
                    self.tabs.setCurrentWidget(widget)

        elif key == "remember_last_session":
            if hasattr(self, "page_nukedash") and hasattr(
                self.page_nukedash, "_on_remember_last_session_changed"
            ):
                self.page_nukedash._on_remember_last_session_changed(bool(value))
        
        elif key == "debug_modes.general":
            # Store in a global or module-level variable for general debugging
            global DEBUG_GENERAL
            DEBUG_GENERAL = value
        
        elif key == "debug_modes.api_calls":
            # Could hook into http_help if we add debug logging there
            global DEBUG_API
            DEBUG_API = value
        
        elif key == "debug_modes.ui_updates":
            # For UI update debugging
            global DEBUG_UI
            DEBUG_UI = value

        elif key == "debug_modes.notifications":
            global DEBUG_NOTIFICATIONS
            DEBUG_NOTIFICATIONS = bool(value)
            self._debug_notifications = DEBUG_NOTIFICATIONS
            if hasattr(self, "page_activity"):
                self.page_activity.set_notifications_debug(DEBUG_NOTIFICATIONS)

        elif key == "debug_modes.project_load_profiler":
            global DEBUG_PROJECT_LOAD_PROFILER
            DEBUG_PROJECT_LOAD_PROFILER = bool(value)
            if hasattr(self, "page_nukedash") and hasattr(
                self.page_nukedash, "_set_project_load_profiler_enabled"
            ):
                self.page_nukedash._set_project_load_profiler_enabled(bool(value))
    
    def _on_server_url_changed(self, url: str):
        """Handle server URL change."""
        self._update_server_url(url)
    
    def showEvent(self, event):
        """Show loading dialog when main window appears."""
        super().showEvent(event)
        # Only show on first appearance
        if not self._shown_once:
            self._shown_once = True
            # Show the loading dialog centered on the main window after it's visible
            show_loading = self._settings_manager.get("show_startup_loading_dialog", True)
            if show_loading and hasattr(self.page_nukedash, '_initial_load') and self.page_nukedash._initial_load:
                # Use immediate timer for first show
                QTimer.singleShot(10, self._show_initial_loading)
    
    def _show_initial_loading(self):
        """Show the initial loading dialog."""
        if hasattr(self.page_nukedash, '_loading_dialog') and self.page_nukedash._initial_load:
            self.page_nukedash._loading_dialog.show_loading(self, "Connecting to server...")
    
    def moveEvent(self, event):
        """Re-center loading dialog when window moves."""
        super().moveEvent(event)
        if hasattr(self.page_nukedash, '_loading_dialog'):
            dialog = self.page_nukedash._loading_dialog
            if dialog.isVisible():
                dialog.center_on_parent()
        
        # Save window position if setting enabled
        if self._settings_manager.get("remember_window_size", True):
            pos = self.pos()
            self._settings_manager.set("window_x", pos.x(), save=False)
            self._settings_manager.set("window_y", pos.y(), save=False)
    
    def resizeEvent(self, event):
        """Re-center loading dialog when window resizes."""
        super().resizeEvent(event)
        if hasattr(self.page_nukedash, '_loading_dialog'):
            dialog = self.page_nukedash._loading_dialog
            if dialog.isVisible():
                dialog.center_on_parent()
        
        # Save window size if setting enabled
        if self._settings_manager.get("remember_window_size", True):
            size = self.size()
            self._settings_manager.set("window_width", size.width(), save=False)
            self._settings_manager.set("window_height", size.height(), save=False)
    
    def closeEvent(self, event):
        """Persist UI state and stop worker threads when closing the application."""
        # Save session state (job, timeline, scroll) before closing
        if hasattr(self, 'page_nukedash') and hasattr(self.page_nukedash, '_save_session_state'):
            try:
                self.page_nukedash._save_session_state()
            except Exception:
                pass  # Silent fail on shutdown
        
        # Save window geometry before closing
        if self._settings_manager.get("remember_window_size", True):
            self._settings_manager.save()
        
        # Stop the API worker thread
        if hasattr(self, 'page_nukedash') and hasattr(self.page_nukedash, '_thread'):
            try:
                self.page_nukedash._thread.quit()
                self.page_nukedash._thread.wait(1000)
            except Exception:
                pass
        
        super().closeEvent(event)

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))
    dark_theme_path = os.path.join(SCRIPT_DIR, "ui", "dark_v01.qss")
    with open(dark_theme_path, "r", encoding="utf-8") as f:
        qss = f.read()
    app.setStyleSheet(qss)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec())
