# importer_page.py
"""
Filesystem Importer Page for ShotBox.
Scans a directory structure and allows selective import of jobs/timelines/shots via the API.
"""

import os
import platform
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QLineEdit,
    QProgressBar, QFrame, QMessageBox, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor

import http_help


# Valid image extensions for thumbnail support
VALID_EXTS = {".png", ".jpg", ".jpeg"}
TIMELINE_SKIP_DIRS = {"assets", "job_assets"}
SHOT_SKIP_DIRS = {"assets", "timeline_assets"}


def newest_image_in_dir(dir_path: Path) -> Optional[Path]:
    """Return newest PNG/JPG file in the given folder, or None."""
    if not dir_path.is_dir():
        return None
    candidates = [p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() in VALID_EXTS]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_project_work_root(job_dir: Path) -> Optional[Path]:
    for folder_name in ("VFX", "Nuke", "nuke"):
        candidate = job_dir / folder_name
        if candidate.is_dir():
            return candidate
    return None


@dataclass
class ScannedShot:
    """Represents a scanned shot directory."""
    name: str
    path: Path
    exists_in_db: bool = False
    selected: bool = True
    thumbnail_path: Optional[Path] = None  # Path to newest image in shot dir
    current_thumbnail: Optional[str] = None  # Current thumbnail filename in DB


@dataclass
class ScannedTimeline:
    """Represents a scanned timeline directory."""
    name: str
    path: Path
    shots: list[ScannedShot] = field(default_factory=list)
    exists_in_db: bool = False
    selected: bool = True


@dataclass
class ScannedJob:
    """Represents a scanned job directory."""
    name: str
    path: Path
    timelines: list[ScannedTimeline] = field(default_factory=list)
    exists_in_db: bool = False
    selected: bool = True


class ScanWorker(QObject):
    """Background worker to scan filesystem and check against API."""
    
    finished = pyqtSignal(list)  # Emits list of ScannedJob
    progress = pyqtSignal(str)   # Status messages
    error = pyqtSignal(str)
    
    def __init__(self, root_path: str):
        super().__init__()
        self.root_path = Path(root_path)
        self.api = http_help.DjangoAPI()
    
    def run(self):
        try:
            self.progress.emit("Fetching existing data from server...")
            
            # Get existing jobs from API
            existing_jobs = {}
            try:
                jobs_data = self.api.get_jobs()
                for job in jobs_data:
                    job_title = job.get("title")
                    if job_title:
                        existing_jobs[job_title] = {
                            "id": job.get("id"),
                            "timelines": {}
                        }
                        for tl in job.get("timelines", []):
                            tl_title = tl.get("title")
                            if tl_title:
                                # Store shot info as dict with thumbnail info
                                shots_dict = {}
                                for s in tl.get("shots", []):
                                    shot_title = s.get("title")
                                    if shot_title:
                                        # Extract just the filename from thumbnail path
                                        thumb = s.get("thumbnail")
                                        thumb_name = Path(thumb).name if thumb else None
                                        shots_dict[shot_title] = {
                                            "id": s.get("id"),
                                            "thumbnail": thumb_name
                                        }
                                existing_jobs[job_title]["timelines"][tl_title] = {
                                    "id": tl.get("id"),
                                    "shots": shots_dict
                                }
            except Exception as e:
                self.progress.emit(f"Warning: Could not fetch existing data: {e}")
                existing_jobs = {}
            
            self.progress.emit("Scanning filesystem...")
            
            scanned_jobs = []
            
            # Iterate through job directories
            for job_dir in sorted(p for p in self.root_path.iterdir() if p.is_dir()):
                self.progress.emit(f"Scanning job: {job_dir.name}")
                
                project_root_dir = find_project_work_root(job_dir)
                if project_root_dir is None:
                    continue  # Skip jobs without a supported work root
                
                job = ScannedJob(
                    name=job_dir.name,
                    path=job_dir,
                    exists_in_db=job_dir.name in existing_jobs
                )
                
                existing_job_data = existing_jobs.get(job_dir.name, {"timelines": {}})
                
                # Scan timelines
                for timeline_dir in sorted(p for p in project_root_dir.iterdir() if p.is_dir()):
                    if timeline_dir.name.lower() in TIMELINE_SKIP_DIRS:
                        continue
                    
                    timeline = ScannedTimeline(
                        name=timeline_dir.name,
                        path=timeline_dir,
                        exists_in_db=timeline_dir.name in existing_job_data["timelines"]
                    )
                    
                    existing_timeline_data = existing_job_data["timelines"].get(timeline_dir.name, {"shots": {}})
                    
                    # Scan shots
                    for shot_dir in sorted(p for p in timeline_dir.iterdir() if p.is_dir()):
                        if shot_dir.name.lower() in SHOT_SKIP_DIRS:
                            continue
                        # Find the newest image for thumbnail
                        thumb_path = newest_image_in_dir(shot_dir)
                        
                        # Check if shot exists and get current thumbnail
                        shot_db_info = existing_timeline_data["shots"].get(shot_dir.name, {})
                        exists = bool(shot_db_info)
                        current_thumb = shot_db_info.get("thumbnail") if exists else None
                        
                        shot = ScannedShot(
                            name=shot_dir.name,
                            path=shot_dir,
                            exists_in_db=exists,
                            thumbnail_path=thumb_path,
                            current_thumbnail=current_thumb
                        )
                        timeline.shots.append(shot)
                    
                    if timeline.shots:  # Only add timelines with shots
                        job.timelines.append(timeline)
                
                if job.timelines:  # Only add jobs with timelines
                    scanned_jobs.append(job)
            
            self.progress.emit(f"Scan complete. Found {len(scanned_jobs)} jobs.")
            self.finished.emit(scanned_jobs)
            
        except Exception as e:
            self.error.emit(str(e))


class ImportWorker(QObject):
    """Background worker to import selected items via API."""
    
    finished = pyqtSignal(dict)  # Emits summary stats
    progress = pyqtSignal(int, int, str)  # current, total, message
    error = pyqtSignal(str)
    
    def __init__(self, jobs_to_import: list[ScannedJob]):
        super().__init__()
        self.jobs = jobs_to_import
        self.api = http_help.DjangoAPI()
    
    def run(self):
        try:
            stats = {
                "jobs_created": 0,
                "jobs_existing": 0,
                "timelines_created": 0,
                "timelines_existing": 0,
                "shots_created": 0,
                "shots_existing": 0,
                "thumbnails_updated": 0,
                "thumbnails_skipped": 0,
            }
            
            # Count total items for progress
            total = sum(
                1 + len(job.timelines) + sum(len(tl.shots) for tl in job.timelines)
                for job in self.jobs if job.selected
            )
            current = 0
            
            for job in self.jobs:
                if not job.selected:
                    continue
                
                current += 1
                self.progress.emit(current, total, f"Processing job: {job.name}")
                
                # Create or get job
                job_data, job_created = self.api.get_or_create_job(job.name)
                job_id = job_data["id"]
                
                if job_created:
                    stats["jobs_created"] += 1
                else:
                    stats["jobs_existing"] += 1
                
                for timeline in job.timelines:
                    if not timeline.selected:
                        continue
                    
                    current += 1
                    self.progress.emit(current, total, f"Processing timeline: {job.name}/{timeline.name}")
                    
                    # Create or get timeline
                    tl_data, tl_created = self.api.get_or_create_timeline(job_id, timeline.name)
                    tl_id = tl_data["id"]
                    
                    if tl_created:
                        stats["timelines_created"] += 1
                    else:
                        stats["timelines_existing"] += 1
                    
                    for shot in timeline.shots:
                        if not shot.selected:
                            continue
                        
                        current += 1
                        self.progress.emit(current, total, f"Processing shot: {shot.name}")
                        
                        # Create or get shot
                        shot_data, shot_created = self.api.get_or_create_shot(
                            tl_id, shot.name, str(shot.path)
                        )
                        shot_id = shot_data["id"]
                        
                        if shot_created:
                            stats["shots_created"] += 1
                        else:
                            stats["shots_existing"] += 1
                        
                        # Handle thumbnail upload
                        if shot.thumbnail_path and shot.thumbnail_path.exists():
                            # Check if thumbnail needs updating
                            new_thumb_name = shot.thumbnail_path.name
                            current_thumb_name = shot.current_thumbnail
                            
                            needs_update = (
                                not current_thumb_name or 
                                current_thumb_name != new_thumb_name
                            )
                            
                            if needs_update:
                                try:
                                    self.progress.emit(current, total, f"Uploading thumbnail: {shot.name}")
                                    self.api.upload_shot_thumbnail(shot_id, str(shot.thumbnail_path))
                                    stats["thumbnails_updated"] += 1
                                except Exception as e:
                                    # Log but don't fail the whole import
                                    stats["thumbnails_skipped"] += 1
                            else:
                                stats["thumbnails_skipped"] += 1
            
            self.finished.emit(stats)
            
        except Exception as e:
            self.error.emit(str(e))


class ImporterPage(QWidget):
    """Main importer page widget."""
    
    # Default paths for different platforms
    DEFAULT_PATHS = {
        "Darwin": "/Volumes/projects/PROJECTS",  # macOS
        "Linux": "/mnt/projects/PROJECTS",        # Linux (adjust as needed)
        "Windows": "P:/PROJECTS",                  # Windows (adjust as needed)
    }
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanned_jobs: list[ScannedJob] = []
        self._scan_thread: Optional[QThread] = None
        self._import_thread: Optional[QThread] = None
        
        self._setup_ui()
        
        # Set default path based on platform
        default_path = self.DEFAULT_PATHS.get(platform.system(), "")
        if default_path and Path(default_path).exists():
            self.path_input.setText(default_path)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Title
        title = QLabel("Filesystem Importer")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Description
        desc = QLabel(
            "Scan a project root directory to find jobs, timelines, and shots. "
            "Select which items to import into the database."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888;")
        layout.addWidget(desc)
        
        # Path selection row
        path_frame = QFrame()
        path_layout = QHBoxLayout(path_frame)
        path_layout.setContentsMargins(0, 0, 0, 0)
        
        path_label = QLabel("Root Path:")
        path_layout.addWidget(path_label)
        
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select project root directory...")
        path_layout.addWidget(self.path_input, 1)
        
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._on_browse)
        path_layout.addWidget(self.browse_btn)
        
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self._on_scan)
        path_layout.addWidget(self.scan_btn)
        
        layout.addWidget(path_frame)
        
        # Tree view for scanned items
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Status", "Path"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        # Set column widths
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        
        # Connect item changed for checkbox handling
        self.tree.itemChanged.connect(self._on_item_changed)
        
        layout.addWidget(self.tree, 1)
        
        # Status and progress
        self.status_label = QLabel("Ready. Select a directory and click Scan.")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Bottom buttons
        btn_frame = QFrame()
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self._select_all)
        btn_layout.addWidget(self.select_all_btn)
        
        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.clicked.connect(self._select_none)
        btn_layout.addWidget(self.select_none_btn)
        
        self.select_new_btn = QPushButton("Select New Only")
        self.select_new_btn.clicked.connect(self._select_new_only)
        btn_layout.addWidget(self.select_new_btn)
        
        btn_layout.addStretch()
        
        self.import_btn = QPushButton("Import Selected")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._on_import)
        self.import_btn.setStyleSheet("font-weight: bold;")
        btn_layout.addWidget(self.import_btn)
        
        layout.addWidget(btn_frame)
    
    def _on_browse(self):
        """Open directory picker dialog."""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Project Root Directory",
            self.path_input.text() or str(Path.home())
        )
        if path:
            self.path_input.setText(path)
    
    def _on_scan(self):
        """Start scanning the selected directory."""
        root_path = self.path_input.text().strip()
        if not root_path:
            QMessageBox.warning(self, "No Path", "Please select a root directory first.")
            return
        
        if not Path(root_path).is_dir():
            QMessageBox.warning(self, "Invalid Path", "The selected path is not a valid directory.")
            return
        
        # Disable UI during scan
        self.scan_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.tree.clear()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.setVisible(True)
        
        # Start scan in background thread
        self._scan_thread = QThread()
        self._scan_worker = ScanWorker(root_path)
        self._scan_worker.moveToThread(self._scan_thread)
        
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.error.connect(self._scan_thread.quit)
        
        self._scan_thread.start()
    
    def _on_scan_progress(self, message: str):
        """Update status during scan."""
        self.status_label.setText(message)
    
    def _on_scan_finished(self, jobs: list[ScannedJob]):
        """Handle scan completion."""
        self._scanned_jobs = jobs
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        
        # Populate tree
        self._populate_tree()
        
        # Update status
        total_jobs = len(jobs)
        total_timelines = sum(len(j.timelines) for j in jobs)
        total_shots = sum(len(tl.shots) for j in jobs for tl in j.timelines)
        
        new_jobs = sum(1 for j in jobs if not j.exists_in_db)
        new_timelines = sum(1 for j in jobs for tl in j.timelines if not tl.exists_in_db)
        new_shots = sum(1 for j in jobs for tl in j.timelines for s in tl.shots if not s.exists_in_db)
        
        # Count thumbnails needing update
        thumbs_to_update = sum(
            1 for j in jobs for tl in j.timelines for s in tl.shots 
            if s.thumbnail_path and s.current_thumbnail != s.thumbnail_path.name
        )
        
        self.status_label.setText(
            f"Found {total_jobs} jobs, {total_timelines} timelines, {total_shots} shots. "
            f"New: {new_jobs} jobs, {new_timelines} timelines, {new_shots} shots. "
            f"Thumbnails to update: {thumbs_to_update}"
        )
        
        self.import_btn.setEnabled(total_jobs > 0)
    
    def _on_scan_error(self, error: str):
        """Handle scan error."""
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.status_label.setText(f"Scan failed: {error}")
        QMessageBox.critical(self, "Scan Error", f"Failed to scan directory:\n{error}")
    
    def _populate_tree(self):
        """Populate the tree widget with scanned data."""
        self.tree.blockSignals(True)
        self.tree.clear()
        
        for job in self._scanned_jobs:
            job_item = QTreeWidgetItem([
                job.name,
                "Exists" if job.exists_in_db else "New",
                str(job.path)
            ])
            job_item.setFlags(job_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            job_item.setCheckState(0, Qt.CheckState.Checked if job.selected else Qt.CheckState.Unchecked)
            job_item.setData(0, Qt.ItemDataRole.UserRole, ("job", job))
            
            # Style based on status
            if job.exists_in_db:
                job_item.setForeground(1, QColor("#888"))
            else:
                job_item.setForeground(1, QColor("#4a9"))
            
            for timeline in job.timelines:
                tl_item = QTreeWidgetItem([
                    timeline.name,
                    "Exists" if timeline.exists_in_db else "New",
                    str(timeline.path)
                ])
                tl_item.setFlags(tl_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                tl_item.setCheckState(0, Qt.CheckState.Checked if timeline.selected else Qt.CheckState.Unchecked)
                tl_item.setData(0, Qt.ItemDataRole.UserRole, ("timeline", timeline))
                
                if timeline.exists_in_db:
                    tl_item.setForeground(1, QColor("#888"))
                else:
                    tl_item.setForeground(1, QColor("#4a9"))
                
                for shot in timeline.shots:
                    # Build status text
                    status_parts = []
                    if shot.exists_in_db:
                        status_parts.append("Exists")
                    else:
                        status_parts.append("New")
                    
                    # Thumbnail status
                    if shot.thumbnail_path:
                        if shot.current_thumbnail == shot.thumbnail_path.name:
                            status_parts.append("Thumb: ✓")
                        else:
                            status_parts.append("Thumb: Update")
                    else:
                        status_parts.append("Thumb: None")
                    
                    status_text = " | ".join(status_parts)
                    
                    shot_item = QTreeWidgetItem([
                        shot.name,
                        status_text,
                        str(shot.path)
                    ])
                    shot_item.setFlags(shot_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    shot_item.setCheckState(0, Qt.CheckState.Checked if shot.selected else Qt.CheckState.Unchecked)
                    shot_item.setData(0, Qt.ItemDataRole.UserRole, ("shot", shot))
                    
                    if shot.exists_in_db:
                        shot_item.setForeground(1, QColor("#888"))
                    else:
                        shot_item.setForeground(1, QColor("#4a9"))
                    
                    # Highlight if thumbnail needs update
                    if shot.thumbnail_path and shot.current_thumbnail != shot.thumbnail_path.name:
                        shot_item.setForeground(1, QColor("#f90"))  # Orange for thumb update
                    
                    tl_item.addChild(shot_item)
                
                job_item.addChild(tl_item)
            
            self.tree.addTopLevelItem(job_item)
        
        # Expand all by default
        self.tree.expandAll()
        self.tree.blockSignals(False)
    
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle checkbox state changes with cascading."""
        if column != 0:
            return
        
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        
        item_type, obj = data
        is_checked = item.checkState(0) == Qt.CheckState.Checked
        obj.selected = is_checked
        
        # Block signals to prevent recursion
        self.tree.blockSignals(True)
        
        # Cascade down to children
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            child_data = child.data(0, Qt.ItemDataRole.UserRole)
            if child_data:
                child_data[1].selected = is_checked
            
            # Cascade to grandchildren (shots)
            for j in range(child.childCount()):
                grandchild = child.child(j)
                grandchild.setCheckState(0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
                grandchild_data = grandchild.data(0, Qt.ItemDataRole.UserRole)
                if grandchild_data:
                    grandchild_data[1].selected = is_checked
        
        self.tree.blockSignals(False)
    
    def _select_all(self):
        """Select all items."""
        self.tree.blockSignals(True)
        for job in self._scanned_jobs:
            job.selected = True
            for tl in job.timelines:
                tl.selected = True
                for shot in tl.shots:
                    shot.selected = True
        self._update_tree_checkboxes()
        self.tree.blockSignals(False)
    
    def _select_none(self):
        """Deselect all items."""
        self.tree.blockSignals(True)
        for job in self._scanned_jobs:
            job.selected = False
            for tl in job.timelines:
                tl.selected = False
                for shot in tl.shots:
                    shot.selected = False
        self._update_tree_checkboxes()
        self.tree.blockSignals(False)
    
    def _select_new_only(self):
        """Select only items that don't exist in the database."""
        self.tree.blockSignals(True)
        for job in self._scanned_jobs:
            job.selected = not job.exists_in_db
            for tl in job.timelines:
                tl.selected = not tl.exists_in_db
                for shot in tl.shots:
                    shot.selected = not shot.exists_in_db
        self._update_tree_checkboxes()
        self.tree.blockSignals(False)
    
    def _update_tree_checkboxes(self):
        """Update tree checkboxes from data model."""
        for i in range(self.tree.topLevelItemCount()):
            job_item = self.tree.topLevelItem(i)
            job_data = job_item.data(0, Qt.ItemDataRole.UserRole)
            if job_data:
                job_item.setCheckState(0, Qt.CheckState.Checked if job_data[1].selected else Qt.CheckState.Unchecked)
            
            for j in range(job_item.childCount()):
                tl_item = job_item.child(j)
                tl_data = tl_item.data(0, Qt.ItemDataRole.UserRole)
                if tl_data:
                    tl_item.setCheckState(0, Qt.CheckState.Checked if tl_data[1].selected else Qt.CheckState.Unchecked)
                
                for k in range(tl_item.childCount()):
                    shot_item = tl_item.child(k)
                    shot_data = shot_item.data(0, Qt.ItemDataRole.UserRole)
                    if shot_data:
                        shot_item.setCheckState(0, Qt.CheckState.Checked if shot_data[1].selected else Qt.CheckState.Unchecked)
    
    def _on_import(self):
        """Start importing selected items."""
        # Check if anything is selected
        selected_count = sum(
            1 for job in self._scanned_jobs if job.selected
            for tl in job.timelines if tl.selected
            for shot in tl.shots if shot.selected
        )
        
        if selected_count == 0:
            QMessageBox.warning(self, "Nothing Selected", "Please select at least one item to import.")
            return
        
        # Confirm import
        reply = QMessageBox.question(
            self,
            "Confirm Import",
            f"Import {selected_count} selected shots?\n\n"
            "This will create any missing jobs, timelines, and shots in the database.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Disable UI during import
        self.scan_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        # Filter to selected jobs only
        jobs_to_import = [j for j in self._scanned_jobs if j.selected]
        
        # Start import in background thread
        self._import_thread = QThread()
        self._import_worker = ImportWorker(jobs_to_import)
        self._import_worker.moveToThread(self._import_thread)
        
        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.error.connect(self._import_thread.quit)
        
        self._import_thread.start()
    
    def _on_import_progress(self, current: int, total: int, message: str):
        """Update progress during import."""
        if total > 0:
            self.progress_bar.setValue(int(100 * current / total))
        self.status_label.setText(message)
    
    def _on_import_finished(self, stats: dict):
        """Handle import completion."""
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.import_btn.setEnabled(True)
        
        self.status_label.setText(
            f"Import complete! "
            f"Created: {stats['jobs_created']} jobs, {stats['timelines_created']} timelines, {stats['shots_created']} shots. "
            f"Thumbnails: {stats['thumbnails_updated']} updated, {stats['thumbnails_skipped']} skipped."
        )
        
        QMessageBox.information(
            self,
            "Import Complete",
            f"Successfully imported:\n\n"
            f"Jobs: {stats['jobs_created']} created, {stats['jobs_existing']} existing\n"
            f"Timelines: {stats['timelines_created']} created, {stats['timelines_existing']} existing\n"
            f"Shots: {stats['shots_created']} created, {stats['shots_existing']} existing\n"
            f"Thumbnails: {stats['thumbnails_updated']} updated, {stats['thumbnails_skipped']} skipped"
        )
        
        # Re-scan to update the tree with new statuses
        self._on_scan()
    
    def _on_import_error(self, error: str):
        """Handle import error."""
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.import_btn.setEnabled(True)
        self.status_label.setText(f"Import failed: {error}")
        QMessageBox.critical(self, "Import Error", f"Failed to import:\n{error}")
