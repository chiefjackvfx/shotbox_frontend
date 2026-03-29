# duration_updater.py
"""
Duration Updater Tool for ShotBox.
Scans for txt files in the project structure and updates shot durations via the API.

Structure expected:
    projectname/Nuke/Timelinename/shotname/shotname.txt

Run standalone:
    python duration_updater.py
"""

import os
import re
import platform
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QLineEdit,
    QProgressBar, QFrame, QMessageBox, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor

import http_help


TIMELINE_SKIP_DIRS = {"assets", "job_assets"}
SHOT_SKIP_DIRS = {"assets", "timeline_assets"}


def find_project_work_root(job_dir: Path) -> Optional[Path]:
    for folder_name in ("VFX", "Nuke", "nuke"):
        candidate = job_dir / folder_name
        if candidate.is_dir():
            return candidate
    return None


@dataclass
class ShotDurationInfo:
    """Represents a shot with duration info from txt file."""
    name: str
    path: Path
    txt_path: Path
    shot_id: int
    current_duration: str
    new_duration: str
    notes: Optional[str] = None
    last_conform: Optional[str] = None
    needs_update: bool = False
    selected: bool = True


@dataclass
class TimelineDurationInfo:
    """Represents a timeline with shots that have duration info."""
    name: str
    timeline_id: int
    shots: list[ShotDurationInfo] = field(default_factory=list)
    selected: bool = True


@dataclass
class JobDurationInfo:
    """Represents a job with timelines."""
    name: str
    job_id: int
    path: Path
    timelines: list[TimelineDurationInfo] = field(default_factory=list)
    selected: bool = True


def parse_shot_txt(txt_path: Path) -> dict:
    """
    Parse a shot txt file and extract relevant fields.
    
    Expected format:
        tvc010 
        Latest conform: None
        Notes: type any notes or shot brief here
        OG clip names: V1-0001_A005C033_2510311C.mov
        duration: 103
        edit start frame: 1032
    """
    data = {}
    
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract duration
        duration_match = re.search(r'^duration:\s*(\d+)', content, re.MULTILINE | re.IGNORECASE)
        if duration_match:
            data['duration'] = duration_match.group(1)
        
        # Extract notes
        notes_match = re.search(r'^Notes:\s*(.+)$', content, re.MULTILINE | re.IGNORECASE)
        if notes_match:
            data['notes'] = notes_match.group(1).strip()
            
        # Extract last conform
        last_conform_match = re.search(r'^Latest conform:\s*(.+)$', content, re.MULTILINE | re.IGNORECASE)
        if last_conform_match:
            data['last_conform'] = last_conform_match.group(1).strip()
            
    except Exception as e:
        print(f"Error parsing {txt_path}: {e}")
    
    return data


class ScanWorker(QObject):
    """Background worker to scan filesystem and match against API."""
    
    finished = pyqtSignal(list)  # Emits list of JobDurationInfo
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
                                shots_dict = {}
                                for s in tl.get("shots", []):
                                    shot_title = s.get("title")
                                    if shot_title:
                                        shots_dict[shot_title] = {
                                            "id": s.get("id"),
                                            "duration": s.get("duration", "0")
                                        }
                                existing_jobs[job_title]["timelines"][tl_title] = {
                                    "id": tl.get("id"),
                                    "shots": shots_dict
                                }
            except Exception as e:
                self.progress.emit(f"Warning: Could not fetch existing data: {e}")
                existing_jobs = {}
            
            self.progress.emit("Scanning filesystem for txt files...")
            
            scanned_jobs = []
            
            # Iterate through job directories
            for job_dir in sorted(p for p in self.root_path.iterdir() if p.is_dir()):
                # Check if job exists in DB
                if job_dir.name not in existing_jobs:
                    continue  # Skip jobs not in database
                
                self.progress.emit(f"Scanning job: {job_dir.name}")
                
                job_db_data = existing_jobs[job_dir.name]
                
                project_root_dir = find_project_work_root(job_dir)
                if project_root_dir is None:
                    continue
                
                job = JobDurationInfo(
                    name=job_dir.name,
                    job_id=job_db_data["id"],
                    path=job_dir
                )
                
                # Scan timelines
                for timeline_dir in sorted(p for p in project_root_dir.iterdir() if p.is_dir()):
                    if timeline_dir.name.lower() in TIMELINE_SKIP_DIRS:
                        continue
                    
                    # Check if timeline exists in DB
                    if timeline_dir.name not in job_db_data["timelines"]:
                        continue
                    
                    tl_db_data = job_db_data["timelines"][timeline_dir.name]
                    
                    timeline = TimelineDurationInfo(
                        name=timeline_dir.name,
                        timeline_id=tl_db_data["id"]
                    )
                    
                    # Scan shots
                    for shot_dir in sorted(p for p in timeline_dir.iterdir() if p.is_dir()):
                        if shot_dir.name.lower() in SHOT_SKIP_DIRS:
                            continue
                        # Check if shot exists in DB
                        if shot_dir.name not in tl_db_data["shots"]:
                            continue
                        
                        shot_db_data = tl_db_data["shots"][shot_dir.name]
                        
                        # Look for txt file
                        txt_path = shot_dir / f"{shot_dir.name}.txt"
                        if not txt_path.exists():
                            continue
                        
                        # Parse txt file
                        txt_data = parse_shot_txt(txt_path)
                        
                        if "duration" not in txt_data:
                            continue
                        
                        current_duration = str(shot_db_data.get("duration", "0"))
                        new_duration = txt_data["duration"]
                        needs_update = current_duration != new_duration
                        
                        shot = ShotDurationInfo(
                            name=shot_dir.name,
                            path=shot_dir,
                            txt_path=txt_path,
                            shot_id=shot_db_data["id"],
                            current_duration=current_duration,
                            new_duration=new_duration,
                            notes=txt_data.get("notes"),
                            last_conform=txt_data.get("last_conform"),
                            needs_update=needs_update,
                            selected=needs_update  # Only select shots that need updating
                        )
                        timeline.shots.append(shot)
                    
                    if timeline.shots:
                        job.timelines.append(timeline)
                
                if job.timelines:
                    scanned_jobs.append(job)
            
            # Count totals
            total_shots = sum(len(tl.shots) for job in scanned_jobs for tl in job.timelines)
            needs_update = sum(1 for job in scanned_jobs for tl in job.timelines for s in tl.shots if s.needs_update)
            
            self.progress.emit(f"Scan complete. Found {total_shots} shots with txt files, {needs_update} need updating.")
            self.finished.emit(scanned_jobs)
            
        except Exception as e:
            self.error.emit(str(e))


class UpdateWorker(QObject):
    """Background worker to update shot durations via API."""
    
    finished = pyqtSignal(dict)  # Emits summary stats
    progress = pyqtSignal(int, int, str)  # current, total, message
    error = pyqtSignal(str)
    
    def __init__(self, jobs_to_update: list[JobDurationInfo]):
        super().__init__()
        self.jobs = jobs_to_update
        self.api = http_help.DjangoAPI()
    
    def run(self):
        try:
            stats = {
                "shots_updated": 0,
                "shots_skipped": 0,
                "shots_failed": 0,
            }
            
            # Count total selected items
            total = sum(
                1 for job in self.jobs if job.selected
                for tl in job.timelines if tl.selected
                for shot in tl.shots if shot.selected
            )
            current = 0
            
            for job in self.jobs:
                if not job.selected:
                    continue
                
                for timeline in job.timelines:
                    if not timeline.selected:
                        continue
                    
                    for shot in timeline.shots:
                        if not shot.selected:
                            continue
                        
                        current += 1
                        self.progress.emit(
                            current, total,
                            f"Updating: {job.name}/{timeline.name}/{shot.name}"
                        )
                        
                        try:
                            # Update shot duration via API
                            self.api.update_shot(shot.shot_id, duration=shot.new_duration)
                            stats["shots_updated"] += 1
                        except Exception as e:
                            print(f"Failed to update {shot.name}: {e}")
                            stats["shots_failed"] += 1
            
            self.finished.emit(stats)
            
        except Exception as e:
            self.error.emit(str(e))


class DurationUpdaterPage(QWidget):
    """Main duration updater page widget."""
    
    # Default paths for different platforms
    DEFAULT_PATHS = {
        "Darwin": "/Volumes/projects/PROJECTS",
        "Linux": "/mnt/projects/PROJECTS",
        "Windows": "P:/PROJECTS",
    }
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanned_jobs: list[JobDurationInfo] = []
        self._scan_thread: Optional[QThread] = None
        self._update_thread: Optional[QThread] = None
        
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
        title = QLabel("Duration Updater")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Description
        desc = QLabel(
            "Scan for shot txt files and update durations in the database. "
            "Looks for txt files in: Project/Nuke/Timeline/Shot/Shot.txt"
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
        self.tree.setHeaderLabels(["Name", "Current", "New", "Status"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        # Set column widths
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        
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
        
        self.select_changed_btn = QPushButton("Select Changed Only")
        self.select_changed_btn.clicked.connect(self._select_changed_only)
        btn_layout.addWidget(self.select_changed_btn)
        
        btn_layout.addStretch()
        
        self.update_btn = QPushButton("Update Selected")
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self._on_update)
        self.update_btn.setStyleSheet("font-weight: bold;")
        btn_layout.addWidget(self.update_btn)
        
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
        self.update_btn.setEnabled(False)
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
    
    def _on_scan_finished(self, jobs: list[JobDurationInfo]):
        """Handle scan completion."""
        self._scanned_jobs = jobs
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.update_btn.setEnabled(bool(jobs))
        
        self._populate_tree()
    
    def _on_scan_error(self, error: str):
        """Handle scan error."""
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.status_label.setText(f"Scan failed: {error}")
        QMessageBox.critical(self, "Scan Error", f"Failed to scan:\n{error}")
    
    def _populate_tree(self):
        """Fill the tree widget with scanned data."""
        self.tree.clear()
        self.tree.blockSignals(True)
        
        for job in self._scanned_jobs:
            job_item = QTreeWidgetItem([job.name, "", "", ""])
            job_item.setFlags(job_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            job_item.setCheckState(0, Qt.CheckState.Checked if job.selected else Qt.CheckState.Unchecked)
            job_item.setData(0, Qt.ItemDataRole.UserRole, ("job", job))
            
            for timeline in job.timelines:
                tl_item = QTreeWidgetItem([timeline.name, "", "", ""])
                tl_item.setFlags(tl_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                tl_item.setCheckState(0, Qt.CheckState.Checked if timeline.selected else Qt.CheckState.Unchecked)
                tl_item.setData(0, Qt.ItemDataRole.UserRole, ("timeline", timeline))
                
                for shot in timeline.shots:
                    status = "Needs update" if shot.needs_update else "Up to date"
                    shot_item = QTreeWidgetItem([
                        shot.name,
                        shot.current_duration,
                        shot.new_duration,
                        status
                    ])
                    shot_item.setFlags(shot_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    shot_item.setCheckState(0, Qt.CheckState.Checked if shot.selected else Qt.CheckState.Unchecked)
                    shot_item.setData(0, Qt.ItemDataRole.UserRole, ("shot", shot))
                    
                    # Color coding
                    if shot.needs_update:
                        shot_item.setForeground(3, QColor("#f90"))  # Orange for needs update
                    else:
                        shot_item.setForeground(3, QColor("#4a9"))  # Green for up to date
                    
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
    
    def _select_changed_only(self):
        """Select only shots that need updating."""
        self.tree.blockSignals(True)
        for job in self._scanned_jobs:
            job.selected = any(
                shot.needs_update
                for tl in job.timelines
                for shot in tl.shots
            )
            for tl in job.timelines:
                tl.selected = any(shot.needs_update for shot in tl.shots)
                for shot in tl.shots:
                    shot.selected = shot.needs_update
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
    
    def _on_update(self):
        """Start updating selected items."""
        # Count selected items
        selected_count = sum(
            1 for job in self._scanned_jobs if job.selected
            for tl in job.timelines if tl.selected
            for shot in tl.shots if shot.selected
        )
        
        if selected_count == 0:
            QMessageBox.warning(self, "Nothing Selected", "Please select at least one shot to update.")
            return
        
        # Confirm update
        reply = QMessageBox.question(
            self,
            "Confirm Update",
            f"Update duration for {selected_count} selected shots?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Disable UI during update
        self.scan_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        # Filter to selected jobs only
        jobs_to_update = [j for j in self._scanned_jobs if j.selected]
        
        # Start update in background thread
        self._update_thread = QThread()
        self._update_worker = UpdateWorker(jobs_to_update)
        self._update_worker.moveToThread(self._update_thread)
        
        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.finished.connect(self._on_update_finished)
        self._update_worker.progress.connect(self._on_update_progress)
        self._update_worker.error.connect(self._on_update_error)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_worker.error.connect(self._update_thread.quit)
        
        self._update_thread.start()
    
    def _on_update_progress(self, current: int, total: int, message: str):
        """Update progress during update."""
        if total > 0:
            self.progress_bar.setValue(int(100 * current / total))
        self.status_label.setText(message)
    
    def _on_update_finished(self, stats: dict):
        """Handle update completion."""
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.update_btn.setEnabled(True)
        
        self.status_label.setText(
            f"Update complete! "
            f"Updated: {stats['shots_updated']}, Failed: {stats['shots_failed']}"
        )
        
        QMessageBox.information(
            self,
            "Update Complete",
            f"Successfully updated:\n\n"
            f"Shots updated: {stats['shots_updated']}\n"
            f"Shots failed: {stats['shots_failed']}"
        )
        
        # Re-scan to update the tree with new values
        self._on_scan()
    
    def _on_update_error(self, error: str):
        """Handle update error."""
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.update_btn.setEnabled(True)
        self.status_label.setText(f"Update failed: {error}")
        QMessageBox.critical(self, "Update Error", f"Failed to update:\n{error}")


# Run as standalone application
if __name__ == "__main__":
    import sys
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Optional: Set dark theme
    # from PyQt6.QtGui import QPalette, QColor
    # palette = QPalette()
    # palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    # palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    # app.setPalette(palette)
    
    window = DurationUpdaterPage()
    window.setWindowTitle("ShotBox - Duration Updater")
    window.resize(900, 600)
    window.show()
    
    sys.exit(app.exec())
