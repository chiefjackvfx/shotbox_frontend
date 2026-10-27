"""Paint flexible text without changing the values used by action handlers."""

from math import ceil

from PyQt6.QtCore import QPointF, QSize, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QTextLayout, QTextOption
from PyQt6.QtWidgets import QLabel, QPushButton, QStyle, QStyleOptionButton, QStyleOptionTab, QStylePainter, QTabBar


class NewFeatureTabBar(QTabBar):
    """Keep native tabs, with an orange shine on one new feature label."""

    def __init__(self, featured_text, parent=None):
        super().__init__(parent)
        self.featured_text = featured_text

    def paintEvent(self, event):
        painter = QStylePainter(self)
        # Paint the selected tab last, as Qt does for overlapping tab borders.
        indices = [i for i in range(self.count()) if i != self.currentIndex()]
        if self.currentIndex() >= 0:
            indices.append(self.currentIndex())
        for index in indices:
            if not self.isTabVisible(index):
                continue
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            if option.text != self.featured_text or not self.isTabEnabled(index):
                painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)
                continue
            rect = self.style().subElementRect(QStyle.SubElement.SE_TabBarTabText, option, self)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            text = option.fontMetrics.elidedText(option.text, self.elideMode(), rect.width())
            option.text = ''
            painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)
            font = self.font()
            font.setBold(True)
            path = QPainterPath()
            path.addText(QPointF(0, 0), font, text)
            bounds = path.boundingRect()
            path.translate(rect.center().x() - bounds.center().x(), rect.center().y() - bounds.center().y())
            painter.save()
            painter.setClipRect(self.tabRect(index))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for width, alpha in ((8, 18), (5, 28), (3, 48)):
                painter.setPen(QPen(QColor(255, 107, 53, alpha), width,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPath(path)
            shine = QLinearGradient(path.boundingRect().topLeft(), path.boundingRect().bottomLeft())
            stops = (((0, '#ffffff'), (0.4, '#fff8f3'), (0.55, '#ffe2d5'), (1, '#ffffff'))
                     if selected else
                     ((0, '#ffb18e'), (0.4, '#fff0e9'), (0.55, '#ff6b35'), (1, '#ff9971')))
            for stop, colour in stops:
                shine.setColorAt(stop, QColor(colour))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shine)
            painter.drawPath(path)
            painter.restore()


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
