# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'shot_card.ui'
##
## Converted to PyQt6
################################################################################

from PyQt6.QtCore import (QCoreApplication, QMetaObject, QRect, QSize, Qt)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QSpacerItem,
    QVBoxLayout, QWidget)

class Ui_ShotCard(object):
    def setupUi(self, ShotCard):
        if not ShotCard.objectName():
            ShotCard.setObjectName(u"ShotCard")
        ShotCard.resize(1011, 776)
        self.verticalLayout_4 = QVBoxLayout(ShotCard)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.verticalLayout_4.setContentsMargins(1, 1, 1, 1)
        self.frame_7 = QFrame(ShotCard)
        self.frame_7.setObjectName(u"frame_7")
        self.frame_7.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_7.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout_5 = QVBoxLayout(self.frame_7)
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.verticalLayout_5.setContentsMargins(2, 2, 2, 2)
        self.frame_5 = QFrame(self.frame_7)
        self.frame_5.setObjectName(u"frame_5")
        self.frame_5.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_5.setFrameShadow(QFrame.Shadow.Raised)
        self.horizontalLayout_2 = QHBoxLayout(self.frame_5)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.label_thumbnail = QLabel(self.frame_5)
        self.label_thumbnail.setObjectName(u"label_thumbnail")
        self.label_thumbnail.setMinimumSize(QSize(80, 45))
        self.label_thumbnail.setMaximumSize(QSize(320, 180))
        self.label_thumbnail.setFrameShape(QFrame.Shape.Box)
        self.label_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_2.addWidget(self.label_thumbnail)

        self.frame_4 = QFrame(self.frame_5)
        self.frame_4.setObjectName(u"frame_4")
        self.frame_4.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_4.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout_3 = QVBoxLayout(self.frame_4)
        self.verticalLayout_3.setSpacing(0)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.verticalLayout_3.setContentsMargins(1, 1, 1, 1)
        self.frame_6 = QFrame(self.frame_4)
        self.frame_6.setObjectName(u"frame_6")
        self.frame_6.setMaximumSize(QSize(16777215, 50))
        self.frame_6.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_6.setFrameShadow(QFrame.Shadow.Raised)
        self.horizontalLayout_6 = QHBoxLayout(self.frame_6)
        self.horizontalLayout_6.setSpacing(0)
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.horizontalLayout_6.setContentsMargins(0, 0, 0, 0)
        self.label_shot = QLabel(self.frame_6)
        self.label_shot.setObjectName(u"label_shot")

        self.horizontalLayout_6.addWidget(self.label_shot)

        self.horizontalSpacer_4 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_6.addItem(self.horizontalSpacer_4)

        self.btn_hide_shot = QPushButton(self.frame_6)
        self.btn_hide_shot.setObjectName(u"btn_hide_shot")

        self.horizontalLayout_6.addWidget(self.btn_hide_shot)

        self.btn_colour_none = QPushButton(self.frame_6)
        self.btn_colour_none.setObjectName(u"btn_colour_none")
        self.btn_colour_none.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_6.addWidget(self.btn_colour_none)

        self.btn_green = QPushButton(self.frame_6)
        self.btn_green.setObjectName(u"btn_green")
        self.btn_green.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_6.addWidget(self.btn_green)

        self.btn_amber = QPushButton(self.frame_6)
        self.btn_amber.setObjectName(u"btn_amber")
        self.btn_amber.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_6.addWidget(self.btn_amber)

        self.btn_red = QPushButton(self.frame_6)
        self.btn_red.setObjectName(u"btn_red")
        self.btn_red.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_6.addWidget(self.btn_red)


        self.verticalLayout_3.addWidget(self.frame_6)

        self.frame_2 = QFrame(self.frame_4)
        self.frame_2.setObjectName(u"frame_2")
        self.frame_2.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_2.setFrameShadow(QFrame.Shadow.Raised)
        self.horizontalLayout_4 = QHBoxLayout(self.frame_2)
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.horizontalLayout_4.setContentsMargins(0, 0, 2, 0)
        self.label_notes = QLabel(self.frame_2)
        self.label_notes.setObjectName(u"label_notes")

        self.horizontalLayout_4.addWidget(self.label_notes)

        self.btn_edit_shot_notes = QPushButton(self.frame_2)
        self.btn_edit_shot_notes.setObjectName(u"btn_edit_shot_notes")
        self.btn_edit_shot_notes.setMaximumSize(QSize(20, 16777215))
        self.btn_edit_shot_notes.setFlat(True)

        self.horizontalLayout_4.addWidget(self.btn_edit_shot_notes)


        self.verticalLayout_3.addWidget(self.frame_2)

        self.shot_btns = QFrame(self.frame_4)
        self.shot_btns.setObjectName(u"shot_btns")
        self.shot_btns.setMaximumSize(QSize(16777215, 50))
        self.shot_btns.setFrameShape(QFrame.Shape.StyledPanel)
        self.shot_btns.setFrameShadow(QFrame.Shadow.Raised)
        self.horizontalLayout_3 = QHBoxLayout(self.shot_btns)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.horizontalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.btn_open_nuke = QPushButton(self.shot_btns)
        self.btn_open_nuke.setObjectName(u"btn_open_nuke")

        self.horizontalLayout_3.addWidget(self.btn_open_nuke)

        self.btn_open_assets = QPushButton(self.shot_btns)
        self.btn_open_assets.setObjectName(u"btn_open_assets")

        self.horizontalLayout_3.addWidget(self.btn_open_assets)

        self.btn_open_precomp = QPushButton(self.shot_btns)
        self.btn_open_precomp.setObjectName(u"btn_open_precomp")

        self.horizontalLayout_3.addWidget(self.btn_open_precomp)

        self.btn_latest_render = QPushButton(self.shot_btns)
        self.btn_latest_render.setObjectName(u"btn_latest_render")

        self.horizontalLayout_3.addWidget(self.btn_latest_render)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_3.addItem(self.horizontalSpacer)


        self.verticalLayout_3.addWidget(self.shot_btns)


        self.horizontalLayout_2.addWidget(self.frame_4)


        self.verticalLayout_5.addWidget(self.frame_5)

        self.frame = QFrame(self.frame_7)
        self.frame.setObjectName(u"frame")
        self.frame.setMaximumSize(QSize(16777215, 30))
        self.frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame.setFrameShadow(QFrame.Shadow.Raised)
        self.horizontalLayout = QHBoxLayout(self.frame)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.label_4 = QLabel(self.frame)
        self.label_4.setObjectName(u"label_4")

        self.horizontalLayout.addWidget(self.label_4)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer_2)

        self.bnt_addTask = QPushButton(self.frame)
        self.bnt_addTask.setObjectName(u"bnt_addTask")
        self.bnt_addTask.setMaximumSize(QSize(16777215, 30))

        self.horizontalLayout.addWidget(self.bnt_addTask)


        self.verticalLayout_5.addWidget(self.frame)

        self.frame_tasks = QFrame(self.frame_7)
        self.frame_tasks.setObjectName(u"frame_tasks")
        self.frame_tasks.setFrameShape(QFrame.Shape.Box)
        self.frame_tasks.setFrameShadow(QFrame.Shadow.Raised)
        self.gridLayout = QGridLayout(self.frame_tasks)
        self.gridLayout.setObjectName(u"gridLayout")

        self.verticalLayout_5.addWidget(self.frame_tasks)


        self.verticalLayout_4.addWidget(self.frame_7)


        self.retranslateUi(ShotCard)

        QMetaObject.connectSlotsByName(ShotCard)
    # setupUi

    def retranslateUi(self, ShotCard):
        ShotCard.setWindowTitle("")
        self.label_thumbnail.setText(QCoreApplication.translate("ShotCard", u"image", None))
        self.label_shot.setText(QCoreApplication.translate("ShotCard", u"Shot name", None))
        self.btn_hide_shot.setText(QCoreApplication.translate("ShotCard", u"Hide", None))
        self.btn_colour_none.setText("")
        self.btn_green.setText("")
        self.btn_amber.setText("")
        self.btn_red.setText("")
        self.label_notes.setText(QCoreApplication.translate("ShotCard", u"Shot Notes.....", None))
        self.btn_edit_shot_notes.setText("")
        self.btn_open_nuke.setText(QCoreApplication.translate("ShotCard", u"Open Nuke", None))
        self.btn_open_assets.setText(QCoreApplication.translate("ShotCard", u"assets folder", None))
        self.btn_open_precomp.setText(QCoreApplication.translate("ShotCard", u"Precomp folder", None))
        self.btn_latest_render.setText(QCoreApplication.translate("ShotCard", u"latest", None))
        self.label_4.setText(QCoreApplication.translate("ShotCard", u"Tasks:", None))
        self.bnt_addTask.setText(QCoreApplication.translate("ShotCard", u"Add task", None))
    # retranslateUi