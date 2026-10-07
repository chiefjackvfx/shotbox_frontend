"""Paint flexible text without changing the values used by action handlers."""

from math import ceil

from PyQt6.QtCore import QPointF, QSize, Qt
from PyQt6.QtGui import QTextLayout, QTextOption
from PyQt6.QtWidgets import QLabel, QPushButton, QStyle, QStyleOptionButton, QStylePainter


class FlexibleLabel(QLabel):
    v02 = False
    wrap_anywhere = False
    elide_text = False
    elide_mode = Qt.TextElideMode.ElideRight

    def setText(self, text):
        changed = text != self.text()
        super().setText(text)
        if changed and self.v02 and self.wrap_anywhere:
            self.updateGeometry()

    def _wrapped_text(self, width):
        layout = QTextLayout(self.text(), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        margins = self.contentsMargins()
        available = max(1, width - margins.left() - margins.right() - 2 * self.margin())
        height = 0.0
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(available)
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, ceil(height)

    def heightForWidth(self, width):
        if self.v02 and self.wrap_anywhere:
            _, height = self._wrapped_text(width)
            margins = self.contentsMargins()
            return height + margins.top() + margins.bottom() + 2 * self.margin()
        return super().heightForWidth(width)

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(0, size.height()) if self.v02 or self.elide_text else size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(0, size.height()) if self.v02 or self.elide_text else size

    def paintEvent(self, event):
        if self.v02 and self.wrap_anywhere:
            painter = QStylePainter(self)
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.setClipRect(self.contentsRect())
            layout, _ = self._wrapped_text(self.width())
            layout.draw(painter, QPointF(self.contentsRect().left() + self.margin(), self.contentsRect().top() + self.margin()))
            return
        if not (self.v02 or self.elide_text) or self.wordWrap():
            return super().paintEvent(event)
        painter = QStylePainter(self)
        text = self.fontMetrics().elidedText(self.text(), self.elide_mode, self.contentsRect().width())
        painter.drawItemText(self.contentsRect(), self.alignment(), self.palette(), self.isEnabled(), text, self.foregroundRole())


class FlexibleButton(QPushButton):
    v02 = False
    elide_mode = Qt.TextElideMode.ElideRight
    elide_suffix = ""
    fit_text = False
    preferred_width_limit = None

    def sizeHint(self):
        size = super().sizeHint()
        if self.v02 and self.fit_text:
            return QSize(min(size.width(), self.preferred_width_limit or size.width()), size.height())
        return QSize(0, size.height()) if self.v02 else size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(0, size.height()) if self.v02 else size

    def paintEvent(self, event):
        if not self.v02:
            return super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        content = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        available = max(0, content.width())
        suffix = self.elide_suffix
        suffix_width = option.fontMetrics.horizontalAdvance(suffix)
        # Keep the version and lock owner intact when shortening a filename.
        if suffix and option.text.endswith(suffix) and suffix_width + option.fontMetrics.horizontalAdvance("…") < available:
            filename = option.text[:-len(suffix)]
            option.text = option.fontMetrics.elidedText(filename, self.elide_mode, available - suffix_width) + suffix
        else:
            option.text = option.fontMetrics.elidedText(option.text, self.elide_mode, available)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
