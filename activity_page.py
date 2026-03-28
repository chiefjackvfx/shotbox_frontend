# activity_page.py
"""
ShotBox Recent Activity Page

Displays a live feed of recent activity from the team - status changes,
assignments, task creation, etc.
"""

import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QButtonGroup, QApplication
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSize, QThread, QObject
from PyQt6.QtGui import QPixmap

import http_help
from image_loader import ImageLoader


class ActivityFilterBar(QWidget):
    """Filter tabs for activity types."""
    
    filter_changed = pyqtSignal(str)  # Emits filter key
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        
        filters = [
            ("all", "All"),
            ("status", "Status"),
            ("assignments", "Assignments"),
            ("created", "Created"),
        ]
        
        for key, label in filters:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("filter_key", key)
            btn.setObjectName("activity_filter_tab")
            btn.clicked.connect(lambda checked, k=key: self.filter_changed.emit(k))
            self._button_group.addButton(btn)
            layout.addWidget(btn)
            
            if key == "all":
                btn.setChecked(True)
        
        layout.addStretch()


class ActivityEntry(QFrame):
    """Single activity entry widget."""
    
    clicked = pyqtSignal(dict)  # Emits activity data when clicked
    
    def __init__(self, activity_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._data = activity_data
        self.setObjectName("activity_entry")
        self._setup_ui()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def _setup_ui(self):
        self.setFrameShape(QFrame.Shape.NoFrame)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        
        # Thumbnail
        self._thumb = QLabel()
        self._thumb.setFixedSize(64, 36)
        self._thumb.setObjectName("activity_thumb")
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_thumbnail()
        layout.addWidget(self._thumb)
        
        # Content
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(2)
        
        # Main action line
        main_label = QLabel()
        main_label.setObjectName("activity_main")
        main_label.setWordWrap(True)
        
        username = self._data.get("username", "System")
        action_text = self._data.get("action_text", "made changes")
        main_label.setText(f'<span style="color: #6FA8DC; font-weight: 600;">{username}</span> '
                          f'<span style="color: #D4D4D4;">{action_text}</span>')
        content_layout.addWidget(main_label)
        
        # Location line (Shot · Timeline · Job)
        shot_code = self._data.get("shot_code", "")
        details = self._data.get("details", {})
        
        location_parts = []
        if shot_code:
            location_parts.append(f'<span style="color: #FF6B35;">{shot_code}</span>')
        
        if details:
            if details.get("timeline_title"):
                location_parts.append(f'<span style="color: #9CA3AF;">{details["timeline_title"]}</span>')
            if details.get("job_title"):
                location_parts.append(f'<span style="color: #F39C12;">{details["job_title"]}</span>')
        
        if location_parts:
            location_label = QLabel()
            location_label.setObjectName("activity_location")
            location_label.setText(' <span style="color: #404754;">·</span> '.join(location_parts))
            content_layout.addWidget(location_label)
        
        # Previous value (if applicable)
        old_value = self._data.get("old_value")
        if old_value and old_value != "None":
            prev_label = QLabel()
            prev_label.setObjectName("activity_previous")
            prev_label.setText(f'Previously: {old_value}')
            content_layout.addWidget(prev_label)
        
        layout.addLayout(content_layout, 1)
        
        # Time column
        time_layout = QVBoxLayout()
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(0)
        time_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        
        # Time ago
        time_ago = QLabel(self._data.get("timestamp_human", ""))
        time_ago.setObjectName("activity_time_ago")
        time_layout.addWidget(time_ago)
        
        # Actual time
        timestamp = self._data.get("timestamp", "")
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
            except:
                time_str = ""
        else:
            time_str = ""
        
        time_actual = QLabel(time_str)
        time_actual.setObjectName("activity_time_actual")
        time_layout.addWidget(time_actual)
        
        layout.addLayout(time_layout)
    
    def _set_thumbnail(self):
        """Set thumbnail placeholder icon based on action type."""
        action_type = self._data.get("action_type", "")
        
        icon_map = {
            "status_change": "✓",
            "artist_assigned": "👤",
            "task_created": "+",
            "shot_created": "+",
            "task_deleted": "✕",
            "priority_change": "!",
            "notes_updated": "✎",
            "duration_change": "◷",
        }
        
        icon = icon_map.get(action_type, "•")
        self._thumb.setText(icon)
    
    def set_thumbnail_from_url(self, url: str):
        """Load thumbnail from URL."""
        if not url:
            return

        def _on_loaded(pixmap: QPixmap | None) -> None:
            try:
                if pixmap is None:
                    return
                scaled = pixmap.scaled(
                    64, 36,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._thumb.setPixmap(scaled)
            except RuntimeError:
                return

        ImageLoader.instance().load(url, _on_loaded)
    
    def mousePressEvent(self, event):
        """Emit clicked signal with activity data."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._data)
        super().mousePressEvent(event)
    
    def set_latest(self, is_latest: bool):
        """Mark this entry as the latest (most recent)."""
        self.setProperty("latest", is_latest)
        self.style().unpolish(self)
        self.style().polish(self)
    
    @property
    def data(self) -> Dict[str, Any]:
        return self._data


class DateHeader(QFrame):
    """Date separator header."""
    
    def __init__(self, date_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("activity_date_header")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        
        label = QLabel(date_text)
        label.setObjectName("activity_date_label")
        layout.addWidget(label)
        
        layout.addStretch()


class ActivityPage(QWidget):
    """
    Recent Activity page showing live feed of team updates.
    """

    request_fetch = pyqtSignal(int, dict)
    activity_clicked = pyqtSignal(int, int)  # shot_id, job_id
    activity_notification = pyqtSignal(dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._activities: List[Dict[str, Any]] = []
        self._current_filter = "all"
        self._auto_refresh = True
        self._refresh_interval = 30000  # 30 seconds
        self._debug_notifications = False
        self._seen_activity_ids = set()
        self._initial_load_complete = False
        self._activity_fetch_in_progress = False
        self._pending_activity_params: Optional[Dict[str, Any]] = None
        self._request_seq = 0
        self._latest_request_id = 0

        self._activity_worker = ActivityWorker()
        self._activity_thread = QThread(self)
        self._activity_worker.moveToThread(self._activity_thread)
        self.request_fetch.connect(self._activity_worker.fetch)
        self._activity_worker.data_ready.connect(self._on_activity_data)
        self._activity_worker.error.connect(self._on_activity_error)
        self._activity_thread.start()
        
        self._setup_ui()
        self._setup_refresh_timer()
        
        # Initial load
        QTimer.singleShot(100, self.refresh_activities)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)
        
        # Header
        header_layout = QHBoxLayout()
        
        title = QLabel("Recent Activity")
        title.setObjectName("activity_title")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        # Filter tabs
        self._filter_bar = ActivityFilterBar()
        self._filter_bar.filter_changed.connect(self._on_filter_changed)
        header_layout.addWidget(self._filter_bar)
        
        # Refresh button
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setObjectName("activity_refresh_btn")
        self._refresh_btn.setFixedSize(28, 28)
        self._refresh_btn.clicked.connect(self.refresh_activities)
        self._refresh_btn.setToolTip("Refresh activity feed")
        header_layout.addWidget(self._refresh_btn)
        
        layout.addLayout(header_layout)
        
        # Scroll area for activity feed
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("activity_scroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self._feed_container = QWidget()
        self._feed_container.setObjectName("activity_feed_container")
        self._feed_layout = QVBoxLayout(self._feed_container)
        self._feed_layout.setContentsMargins(0, 0, 0, 0)
        self._feed_layout.setSpacing(2)
        self._feed_layout.addStretch()
        
        scroll.setWidget(self._feed_container)
        layout.addWidget(scroll)
        
        # Status bar
        self._status_label = QLabel("")
        self._status_label.setObjectName("activity_status")
        layout.addWidget(self._status_label)
    
    def _setup_refresh_timer(self):
        """Set up auto-refresh timer."""
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_activities)
        if self._auto_refresh:
            self._refresh_timer.start(self._refresh_interval)
    
    def _on_filter_changed(self, filter_key: str):
        """Handle filter tab change."""
        self._current_filter = filter_key
        self.refresh_activities()
    
    def refresh_activities(self):
        """Fetch and display recent activities."""
        params = {"limit": 100}

        if self._current_filter == "status":
            params["action_type"] = "status_change"
        elif self._current_filter == "assignments":
            params["action_type"] = "artist_assigned"
        elif self._current_filter == "created":
            params["action_type"] = "shot_created,task_created"

        self._trigger_activity_fetch(params)

    def _trigger_activity_fetch(self, params: Dict[str, Any]) -> None:
        if self._activity_fetch_in_progress:
            self._pending_activity_params = params
            return
        self._activity_fetch_in_progress = True
        self._request_seq += 1
        request_id = self._request_seq
        self._latest_request_id = request_id
        if hasattr(self, "_status_label"):
            self._status_label.setText("Refreshing activity...")
        self.request_fetch.emit(request_id, params)

    @pyqtSlot(int, list)
    def _on_activity_data(self, request_id: int, activities: List[Dict[str, Any]]) -> None:
        if request_id != self._latest_request_id:
            return
        self._activity_fetch_in_progress = False
        self._activities = activities
        self._emit_new_activity_notifications(activities)
        self._log_notifications(f"[Notifications] Activity refresh: {len(activities)} items")
        self._populate_feed(activities)
        self._status_label.setText(f"Showing {len(activities)} activities")
        self._drain_pending_activity_fetch()

    @pyqtSlot(int, str)
    def _on_activity_error(self, request_id: int, message: str) -> None:
        if request_id != self._latest_request_id:
            return
        self._activity_fetch_in_progress = False
        self._status_label.setText(f"Error: {message}")
        print(f"[Activity] Error: {message}")
        self._drain_pending_activity_fetch()

    def _drain_pending_activity_fetch(self) -> None:
        if self._pending_activity_params is None:
            return
        params = self._pending_activity_params
        self._pending_activity_params = None
        self._trigger_activity_fetch(params)

    def _emit_new_activity_notifications(self, activities: List[Dict[str, Any]]):
        current_ids = set()
        new_items: List[Dict[str, Any]] = []

        for activity in activities:
            activity_id = self._activity_key(activity)
            if activity_id is None:
                continue
            current_ids.add(activity_id)
            if self._initial_load_complete and activity_id not in self._seen_activity_ids:
                new_items.append(activity)

        if len(current_ids) > 500:
            current_ids = set(list(current_ids)[:500])

        self._seen_activity_ids = current_ids

        if not self._initial_load_complete:
            self._initial_load_complete = True
            self._log_notifications("[Notifications] Initial load complete; skipping notifications")
            return

        for activity in reversed(new_items):
            self.activity_notification.emit(activity)
        if new_items:
            self._log_notifications(f"[Notifications] Emitted {len(new_items)} new notifications")
        else:
            self._log_notifications("[Notifications] No new activities to notify")

    def _activity_key(self, activity: Dict[str, Any]):
        activity_id = activity.get("id")
        if activity_id:
            return activity_id
        timestamp = activity.get("timestamp")
        action_type = activity.get("action_type") or ""
        shot_id = activity.get("shot_id") or ""
        return f"{timestamp}:{action_type}:{shot_id}"
    
    def _populate_feed(self, activities: List[Dict[str, Any]]):
        """Populate the feed with activity entries, grouped by date."""
        # Clear existing entries
        while self._feed_layout.count() > 1:
            item = self._feed_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not activities:
            empty_label = QLabel("No recent activity")
            empty_label.setObjectName("activity_empty")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._feed_layout.insertWidget(0, empty_label)
            return
        
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        current_date = None
        insert_index = 0
        
        for i, activity in enumerate(activities):
            timestamp = activity.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                activity_date = dt.date()
            except:
                activity_date = today
            
            # Add date header if new date
            if activity_date != current_date:
                current_date = activity_date
                
                if activity_date == today:
                    date_text = "Today"
                elif activity_date == yesterday:
                    date_text = "Yesterday"
                else:
                    date_text = activity_date.strftime("%A, %b %d")
                
                header = DateHeader(date_text)
                self._feed_layout.insertWidget(insert_index, header)
                insert_index += 1
            
            # Create activity entry
            entry = ActivityEntry(activity)
            entry.clicked.connect(self._on_activity_clicked)
            
            if i == 0:
                entry.set_latest(True)
            
            # Load thumbnail if available
            thumbnail_url = activity.get("thumbnail_url")
            if thumbnail_url:
                entry.set_thumbnail_from_url(thumbnail_url)
            
            self._feed_layout.insertWidget(insert_index, entry)
            insert_index += 1
    
    def _on_activity_clicked(self, activity_data: Dict[str, Any]):
        """Handle click on an activity entry."""
        shot_id = activity_data.get("shot_id")
        details = activity_data.get("details", {})
        job_id = details.get("job_id")
        
        if shot_id:
            self.activity_clicked.emit(shot_id, job_id or 0)
            print(f"[Activity] Clicked: shot_id={shot_id}, job_id={job_id}")
    
    def set_auto_refresh(self, enabled: bool):
        """Enable or disable auto-refresh."""
        self._auto_refresh = enabled
        if enabled:
            self._refresh_timer.start(self._refresh_interval)
        else:
            self._refresh_timer.stop()
    
    def set_refresh_interval(self, seconds: int):
        """Set auto-refresh interval in seconds."""
        self._refresh_interval = seconds * 1000
        if self._auto_refresh:
            self._refresh_timer.setInterval(self._refresh_interval)

    def set_notifications_debug(self, enabled: bool) -> None:
        """Enable or disable notification debug logging."""
        self._debug_notifications = bool(enabled)

    def _log_notifications(self, message: str) -> None:
        if self._debug_notifications:
            print(message)

    def closeEvent(self, event):
        if hasattr(self, "_activity_thread"):
            self._activity_thread.quit()
            self._activity_thread.wait(1000)
        super().closeEvent(event)


class ActivityWorker(QObject):
    data_ready = pyqtSignal(int, list)
    error = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._api = http_help.DjangoAPI()
        self._busy = False

    @pyqtSlot(int, dict)
    def fetch(self, request_id: int, params: Dict[str, Any]) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            activities = self._api.get_recent_activity(**params)
            if not isinstance(activities, list):
                raise ValueError("API did not return a list")
            self.data_ready.emit(request_id, activities)
        except Exception as e:
            self.error.emit(request_id, str(e))
        finally:
            self._busy = False
