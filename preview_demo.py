"""
Preview Demo - Modern Shot and Task Cards
Shows how to use the new reusable widgets
Run this file to preview the design
"""

import sys
import os
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QScrollArea
from PyQt6.QtCore import Qt

# Import our new widgets
from shot_card import ShotCard
from task_card import TaskCard


class PreviewWindow(QMainWindow):
    """Preview window for look dev and testing"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShotBox - Modern Widget Preview")
        self.setMinimumSize(140, 90)
        
        # Load QSS stylesheet
        self.load_stylesheet()
        
        # Setup UI
        self.setup_ui()
    
    def load_stylesheet(self):
        """Load the modern QSS stylesheet"""
        qss_path = os.path.join(os.path.dirname(__file__), "modern_styles.qss")
        
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())
        else:
            print(f"Warning: Could not find {qss_path}")
    
    def setup_ui(self):
        """Setup the preview UI"""
        # Central scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Container
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        
        # Create sample shot cards with tasks
        shot_cards_data = [
            {
                "id": 660,
                "title": "lnr010",
                "notes": "Camera movement through forest with drone tracking shot - needs color correction",
                "colour_code": "amber",
                "tasks": [
                    {"title": "match move", "artist": 1, "status": "unassigned", "notes": ""},
                    {"title": "set scan", "artist": 1, "status": "done", "notes": "photo geo"},
                    {"title": "prep 3d", "artist": 1, "status": "done", "notes": ""},
                    {"title": "add better trees", "artist": 1, "status": "in_progress", "notes": "need more variety"},
                    {"title": "pilon and plane", "artist": None, "status": "unassigned", "notes": ""},
                ]
            },
            {
                "id": 662,
                "title": "lnr030",
                "notes": "Car interior shot with dashboard visible",
                "colour_code": "none",
                "tasks": [
                    {"title": "car camera paint", "artist": None, "status": "unassigned", "notes": ""},
                ]
            },
            {
                "id": 663,
                "title": "lnr040",
                "notes": "Close-up interior car shot with reflection work needed",
                "colour_code": "green",
                "tasks": [
                    {"title": "roto face", "artist": 1, "status": "in_progress", "notes": "complex shot"},
                    {"title": "beauty work", "artist": 2, "status": "done", "notes": "looks great"},
                ]
            },
            {
                "id": 664,
                "title": "lnr050",
                "notes": "Exterior establishing shot",
                "colour_code": "red",
                "tasks": [
                    {"title": "sky replacement", "artist": 1, "status": "assigned", "notes": "waiting on plates"},
                    {"title": "wire removal", "artist": None, "status": "unassigned", "notes": ""},
                    {"title": "color grade", "artist": 3, "status": "not_started", "notes": ""},
                ]
            },
        ]
        
        # Create shot cards
        for shot_data in shot_cards_data:
            shot_card = self.create_shot_card(shot_data)
            layout.addWidget(shot_card)
        
        layout.addStretch()
        
        scroll.setWidget(container)
        self.setCentralWidget(scroll)
    
    def create_shot_card(self, shot_data):
        """Create a shot card with tasks"""
        # Create shot card
        card = ShotCard(shot_data)
        
        # Connect signals for demo
        card.nuke_clicked.connect(lambda: print(f"Open Nuke: {shot_data['title']}"))
        card.assets_clicked.connect(lambda: print(f"Open Assets: {shot_data['title']}"))
        card.precomp_clicked.connect(lambda: print(f"Open Precomp: {shot_data['title']}"))
        card.render_clicked.connect(lambda: print(f"Push to DVR: {shot_data['title']}"))
        card.hide_clicked.connect(lambda: print(f"Hide shot: {shot_data['title']}"))
        card.notes_edited.connect(lambda: print(f"Edit notes: {shot_data['title']}"))
        card.task_added.connect(lambda: print(f"Add task to: {shot_data['title']}"))
        card.color_changed.connect(lambda c: print(f"Color changed to {c}: {shot_data['title']}"))
        
        # Add task cards
        for task_data in shot_data.get("tasks", []):
            task_card = TaskCard(task_data)
            
            # Connect task signals
            task_card.hide_clicked.connect(lambda t=task_data: print(f"Hide task: {t['title']}"))
            task_card.delete_clicked.connect(lambda t=task_data: print(f"Delete task: {t['title']}"))
            
            card.add_task_widget(task_card)
        
        return card


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    
    # Set application-wide font
    app.setFont(app.font())
    
    window = PreviewWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
