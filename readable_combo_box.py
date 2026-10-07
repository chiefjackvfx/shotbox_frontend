"""Content-aware combo boxes with readable, screen-bounded popup menus."""

import re

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import (
    QComboBox, QSizePolicy, QStyle, QStyleOptionComboBox, QStylePainter, QToolTip,
)


class ReadableComboBox(QComboBox):
    def __init__(self, parent=None, *, compact=False, elide_mode=Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self.compact = compact
        self.elide_mode = elide_mode
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.view().setTextElideMode(Qt.TextElideMode.ElideRight)

    def _content_size(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        width = max((option.fontMetrics.horizontalAdvance(self.itemText(index))
                     for index in range(self.count())), default=0)
        if not option.currentIcon.isNull():
            width += option.iconSize.width() + 4
        size = self.style().sizeFromContents(
            QStyle.ContentsType.CT_ComboBox, option,
            QSize(width, option.fontMetrics.height()), self)
        # Stylesheet sizeFromContents can omit space occupied by the dropdown.
        # Use its actual text rectangle so short values also fit beside it.
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self)
        chrome = max(0, self.width() - field.width())
        # Leave slack for fractional glyph widths rounded by horizontalAdvance.
        size.setWidth(max(size.width(), width + chrome + 8))
        return size

    def sizeHint(self):
        size = self._content_size()
        return QSize(min(size.width(), 640), max(size.height(), super().sizeHint().height()))

    def minimumSizeHint(self):
        size = self.sizeHint()
        return QSize(size.width() if self.compact else min(160, size.width()), size.height())

    def _label_option(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        content = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self)
        available = max(0, content.width() - 4)
        if not option.currentIcon.isNull():
            available = max(0, available - option.iconSize.width() - 4)
        suffix = (re.search(r"(_v\d+[^/\\]*)$", option.currentText, re.IGNORECASE)
                  if self.elide_mode == Qt.TextElideMode.ElideMiddle else None)
        if suffix and option.fontMetrics.horizontalAdvance(suffix[0] + "…") < available:
            prefix = option.currentText[:suffix.start()]
            remaining = available - option.fontMetrics.horizontalAdvance(suffix[0])
            option.currentText = option.fontMetrics.elidedText(prefix, Qt.TextElideMode.ElideRight, remaining) + suffix[0]
        else:
            option.currentText = option.fontMetrics.elidedText(option.currentText, self.elide_mode, available)
        return option

    def paintEvent(self, event):
        if self.isEditable():
            return super().paintEvent(event)
        painter = QStylePainter(self)
        option = self._label_option()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.currentText():
            tooltip = self.toolTip()
            text = self.currentText()
            if tooltip and tooltip != text:
                text += "\n" + tooltip
            QToolTip.showText(event.globalPos(), text, self)
            return True
        return super().event(event)

    def showPopup(self):
        # Selected labels may shrink with the layout. Menus get the space
        # needed to compare complete names, independently of the closed control.
        bounds = self.screen().availableGeometry()
        width = min(max(self.width(), self._content_size().width() + 24), bounds.width() - 16)
        # Leave room for the popup's frame and scroll bar, rather than making
        # the view alone as wide as the screen-bounded outer window.
        self.view().setMinimumWidth(max(1, width - 32))
        for index in range(self.count()):
            if not self.itemData(index, Qt.ItemDataRole.ToolTipRole):
                self.setItemData(index, self.itemText(index), Qt.ItemDataRole.ToolTipRole)
        super().showPopup()
        popup = self.view().window()
        popup.setMaximumWidth(bounds.width() - 16)
        geometry = popup.geometry()
        geometry.setWidth(min(max(width, geometry.width()), bounds.width() - 16))
        geometry.moveLeft(max(bounds.left(), min(geometry.left(), bounds.right() - geometry.width() + 1)))
        popup.setGeometry(geometry)
