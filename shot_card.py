"""
ShotCard UI Setup - With Color Indicator Strip
Replaces shot_card.ui with a function that sets up UI directly on the widget
Now includes a 12px color indicator strip on the left side of the card
"""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSpacerItem, QVBoxLayout
)


def setup_shot_card_ui(widget):
    """
    Set up the ShotCard UI directly on the given widget.
    This exactly mirrors the structure from shot_card.ui / Ui_ShotCard.
    
    Args:
        widget: The QWidget instance to set up (typically 'self' in ShotCard.__init__)
    """
    if not widget.objectName():
        widget.setObjectName("ShotCard")
    
    # Main layout - verticalLayout_4
    widget.verticalLayout_4 = QVBoxLayout(widget)
    widget.verticalLayout_4.setObjectName("verticalLayout_4")
    widget.verticalLayout_4.setContentsMargins(1, 1, 1, 1)
    
    # frame_7 - the main outer frame that contains everything
    widget.frame_7 = QFrame(widget)
    widget.frame_7.setObjectName("frame_7")
    widget.frame_7.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame_7.setFrameShadow(QFrame.Shadow.Raised)
    
    # NEW: Horizontal layout for color indicator + content
    widget.main_horizontal_layout = QHBoxLayout(widget.frame_7)
    widget.main_horizontal_layout.setObjectName("main_horizontal_layout")
    widget.main_horizontal_layout.setContentsMargins(0, 0, 0, 0)
    widget.main_horizontal_layout.setSpacing(0)
    
    # NEW: Color indicator strip (12px wide, full height)
    widget.color_indicator = QFrame(widget.frame_7)
    widget.color_indicator.setObjectName("color_indicator")
    widget.color_indicator.setFixedWidth(12)
    widget.color_indicator.setFrameShape(QFrame.Shape.NoFrame)
    # Default to "none" color - will be styled via QSS
    widget.color_indicator.setProperty("shot_color", "none")
    
    widget.main_horizontal_layout.addWidget(widget.color_indicator)
    
    # Content container (holds all the existing content)
    widget.content_container = QFrame(widget.frame_7)
    widget.content_container.setObjectName("content_container")
    widget.content_container.setFrameShape(QFrame.Shape.NoFrame)
    
    widget.verticalLayout_5 = QVBoxLayout(widget.content_container)
    widget.verticalLayout_5.setObjectName("verticalLayout_5")
    widget.verticalLayout_5.setContentsMargins(8, 8, 8, 8)
    
    widget.main_horizontal_layout.addWidget(widget.content_container)
    
    # frame_5 - contains thumbnail and info section side by side
    widget.frame_5 = QFrame(widget.content_container)
    widget.frame_5.setObjectName("frame_5")
    widget.frame_5.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame_5.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.horizontalLayout_2 = QHBoxLayout(widget.frame_5)
    widget.horizontalLayout_2.setObjectName("horizontalLayout_2")
    widget.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
    
    # label_thumbnail
    widget.label_thumbnail = QLabel(widget.frame_5)
    widget.label_thumbnail.setObjectName("label_thumbnail")
    widget.label_thumbnail.setMinimumSize(QSize(80, 45))
    widget.label_thumbnail.setMaximumSize(QSize(320, 180))
    widget.label_thumbnail.setFrameShape(QFrame.Shape.Box)
    widget.label_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.label_thumbnail.setText("image")
    
    widget.horizontalLayout_2.addWidget(widget.label_thumbnail)
    
    # frame_4 - right side info section
    widget.frame_4 = QFrame(widget.frame_5)
    widget.frame_4.setObjectName("frame_4")
    widget.frame_4.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame_4.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.verticalLayout_3 = QVBoxLayout(widget.frame_4)
    widget.verticalLayout_3.setSpacing(0)
    widget.verticalLayout_3.setObjectName("verticalLayout_3")
    widget.verticalLayout_3.setContentsMargins(1, 1, 1, 1)
    
    # frame_6 - shot name row with hide and color buttons
    widget.frame_6 = QFrame(widget.frame_4)
    widget.frame_6.setObjectName("frame_6")
    widget.frame_6.setMaximumSize(QSize(16777215, 50))
    widget.frame_6.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame_6.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.horizontalLayout_6 = QHBoxLayout(widget.frame_6)
    widget.horizontalLayout_6.setSpacing(0)
    widget.horizontalLayout_6.setObjectName("horizontalLayout_6")
    widget.horizontalLayout_6.setContentsMargins(0, 0, 0, 0)
    
    widget.label_shot = QLabel(widget.frame_6)
    widget.label_shot.setObjectName("label_shot")
    widget.label_shot.setText("Shot name")
    
    widget.horizontalLayout_6.addWidget(widget.label_shot)
    
    # Small spacer between title and frame range
    widget.horizontalSpacer_title = QSpacerItem(20, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_6.addItem(widget.horizontalSpacer_title)
    
    # Frame range label
    widget.label_frame_range = QLabel(widget.frame_6)
    widget.label_frame_range.setObjectName("label_frame_range")
    widget.label_frame_range.setText("1001-1140")
    
    widget.horizontalLayout_6.addWidget(widget.label_frame_range)
    
    # Separator spacer
    widget.horizontalSpacer_meta1 = QSpacerItem(10, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_6.addItem(widget.horizontalSpacer_meta1)
    
    # Edit inpoint label
    widget.label_edit_inpoint = QLabel(widget.frame_6)
    widget.label_edit_inpoint.setObjectName("label_edit_inpoint")
    widget.label_edit_inpoint.setText("In: —")
    
    widget.horizontalLayout_6.addWidget(widget.label_edit_inpoint)
    
    # Separator spacer
    widget.horizontalSpacer_meta2 = QSpacerItem(10, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_6.addItem(widget.horizontalSpacer_meta2)
    
    # Edit outpoint label
    widget.label_edit_outpoint = QLabel(widget.frame_6)
    widget.label_edit_outpoint.setObjectName("label_edit_outpoint")
    widget.label_edit_outpoint.setText("Out: —")
    
    widget.horizontalLayout_6.addWidget(widget.label_edit_outpoint)
    
    widget.horizontalSpacer_4 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_6.addItem(widget.horizontalSpacer_4)
    
    widget.btn_hide_shot = QPushButton(widget.frame_6)
    widget.btn_hide_shot.setObjectName("btn_hide_shot")
    widget.btn_hide_shot.setText("Hide")
    
    widget.horizontalLayout_6.addWidget(widget.btn_hide_shot)
    
    widget.btn_colour_none = QPushButton(widget.frame_6)
    widget.btn_colour_none.setObjectName("btn_colour_none")
    widget.btn_colour_none.setMaximumSize(QSize(20, 20))
    widget.btn_colour_none.setText("")
    
    widget.horizontalLayout_6.addWidget(widget.btn_colour_none)
    
    widget.btn_green = QPushButton(widget.frame_6)
    widget.btn_green.setObjectName("btn_green")
    widget.btn_green.setMaximumSize(QSize(20, 20))
    widget.btn_green.setText("")
    
    widget.horizontalLayout_6.addWidget(widget.btn_green)
    
    widget.btn_amber = QPushButton(widget.frame_6)
    widget.btn_amber.setObjectName("btn_amber")
    widget.btn_amber.setMaximumSize(QSize(20, 20))
    widget.btn_amber.setText("")
    
    widget.horizontalLayout_6.addWidget(widget.btn_amber)
    
    widget.btn_red = QPushButton(widget.frame_6)
    widget.btn_red.setObjectName("btn_red")
    widget.btn_red.setMaximumSize(QSize(20, 20))
    widget.btn_red.setText("")
    
    widget.horizontalLayout_6.addWidget(widget.btn_red)
    
    widget.verticalLayout_3.addWidget(widget.frame_6)
    
    # frame_metadata - second row for colourspace and original clip
    widget.frame_metadata = QFrame(widget.frame_4)
    widget.frame_metadata.setObjectName("frame_metadata")
    widget.frame_metadata.setMaximumSize(QSize(16777215, 30))
    widget.frame_metadata.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame_metadata.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.horizontalLayout_metadata = QHBoxLayout(widget.frame_metadata)
    widget.horizontalLayout_metadata.setSpacing(0)
    widget.horizontalLayout_metadata.setObjectName("horizontalLayout_metadata")
    widget.horizontalLayout_metadata.setContentsMargins(0, 0, 0, 0)
    
    # Colourspace label
    widget.label_colourspace = QLabel(widget.frame_metadata)
    widget.label_colourspace.setObjectName("label_colourspace")
    widget.label_colourspace.setText("Colourspace: —")
    
    widget.horizontalLayout_metadata.addWidget(widget.label_colourspace)
    
    # Separator spacer
    widget.horizontalSpacer_meta3 = QSpacerItem(15, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_metadata.addItem(widget.horizontalSpacer_meta3)
    
    # Original clip label (right-click for menu)
    widget.label_original_clip = QLabel(widget.frame_metadata)
    widget.label_original_clip.setObjectName("label_original_clip")
    widget.label_original_clip.setText("Clip: —")
    
    widget.horizontalLayout_metadata.addWidget(widget.label_original_clip)
    
    # Expanding spacer to push everything left
    widget.horizontalSpacer_meta_end = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_metadata.addItem(widget.horizontalSpacer_meta_end)
    
    widget.verticalLayout_3.addWidget(widget.frame_metadata)
    
    # frame_2 - notes row
    widget.frame_2 = QFrame(widget.frame_4)
    widget.frame_2.setObjectName("frame_2")
    widget.frame_2.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame_2.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.horizontalLayout_4 = QHBoxLayout(widget.frame_2)
    widget.horizontalLayout_4.setObjectName("horizontalLayout_4")
    widget.horizontalLayout_4.setContentsMargins(0, 0, 2, 0)
    
    widget.label_notes = QLabel(widget.frame_2)
    widget.label_notes.setObjectName("label_notes")
    widget.label_notes.setText("Shot Notes.....")
    
    widget.horizontalLayout_4.addWidget(widget.label_notes)
    
    widget.btn_edit_shot_notes = QPushButton(widget.frame_2)
    widget.btn_edit_shot_notes.setObjectName("btn_edit_shot_notes")
    widget.btn_edit_shot_notes.setMaximumSize(QSize(20, 16777215))
    widget.btn_edit_shot_notes.setFlat(True)
    widget.btn_edit_shot_notes.setText("✎")
    widget.btn_edit_shot_notes.setToolTip("Edit notes")
    
    widget.horizontalLayout_4.addWidget(widget.btn_edit_shot_notes)
    
    widget.verticalLayout_3.addWidget(widget.frame_2)
    
    # shot_btns - action buttons row
    widget.shot_btns = QFrame(widget.frame_4)
    widget.shot_btns.setObjectName("shot_btns")
    widget.shot_btns.setMaximumSize(QSize(16777215, 50))
    widget.shot_btns.setFrameShape(QFrame.Shape.StyledPanel)
    widget.shot_btns.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.horizontalLayout_3 = QHBoxLayout(widget.shot_btns)
    widget.horizontalLayout_3.setObjectName("horizontalLayout_3")
    widget.horizontalLayout_3.setContentsMargins(0, 0, 0, 0)
    
    widget.btn_open_nuke = QPushButton(widget.shot_btns)
    widget.btn_open_nuke.setObjectName("btn_open_nuke")
    widget.btn_open_nuke.setText("Open Nuke")
    
    widget.horizontalLayout_3.addWidget(widget.btn_open_nuke)
    
    widget.btn_open_assets = QPushButton(widget.shot_btns)
    widget.btn_open_assets.setObjectName("btn_open_assets")
    widget.btn_open_assets.setText("assets folder")
    
    widget.horizontalLayout_3.addWidget(widget.btn_open_assets)
    
    widget.btn_open_precomp = QPushButton(widget.shot_btns)
    widget.btn_open_precomp.setObjectName("btn_open_precomp")
    widget.btn_open_precomp.setText("Precomp folder")
    
    widget.horizontalLayout_3.addWidget(widget.btn_open_precomp)
    
    widget.btn_latest_render = QPushButton(widget.shot_btns)
    widget.btn_latest_render.setObjectName("btn_latest_render")
    widget.btn_latest_render.setText("latest")

    
    widget.horizontalLayout_3.addWidget(widget.btn_latest_render)

    widget.label_last_conform = QLabel(widget.shot_btns)
    widget.label_last_conform.setObjectName("label_last_conform")
    widget.label_last_conform.setText("None")

    widget.horizontalLayout_3.addWidget(widget.label_last_conform)

    
    widget.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout_3.addItem(widget.horizontalSpacer)
    
    widget.verticalLayout_3.addWidget(widget.shot_btns)
    
    # Add frame_4 to frame_5
    widget.horizontalLayout_2.addWidget(widget.frame_4)
    
    # Add frame_5 to content_container (was frame_7)
    widget.verticalLayout_5.addWidget(widget.frame_5)
    
    # frame - tasks header row
    widget.frame = QFrame(widget.content_container)
    widget.frame.setObjectName("frame")
    widget.frame.setMaximumSize(QSize(16777215, 30))
    widget.frame.setFrameShape(QFrame.Shape.StyledPanel)
    widget.frame.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.horizontalLayout = QHBoxLayout(widget.frame)
    widget.horizontalLayout.setObjectName("horizontalLayout")
    widget.horizontalLayout.setContentsMargins(0, 0, 0, 0)
    
    widget.label_4 = QLabel(widget.frame)
    widget.label_4.setObjectName("label_4")
    widget.label_4.setText("Tasks:")
    
    widget.horizontalLayout.addWidget(widget.label_4)
    
    widget.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    widget.horizontalLayout.addItem(widget.horizontalSpacer_2)
    
    widget.bnt_addTask = QPushButton(widget.frame)
    widget.bnt_addTask.setObjectName("bnt_addTask")
    widget.bnt_addTask.setMaximumSize(QSize(16777215, 30))
    widget.bnt_addTask.setText("Add task")
    
    widget.horizontalLayout.addWidget(widget.bnt_addTask)
    
    widget.verticalLayout_5.addWidget(widget.frame)
    
    # frame_tasks - container for task widgets
    widget.frame_tasks = QFrame(widget.content_container)
    widget.frame_tasks.setObjectName("frame_tasks")
    widget.frame_tasks.setFrameShape(QFrame.Shape.Box)
    widget.frame_tasks.setFrameShadow(QFrame.Shadow.Raised)
    
    widget.gridLayout = QGridLayout(widget.frame_tasks)
    widget.gridLayout.setObjectName("gridLayout")
    
    widget.verticalLayout_5.addWidget(widget.frame_tasks)
    
    # Add frame_7 to main layout
    widget.verticalLayout_4.addWidget(widget.frame_7)