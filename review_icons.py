"""Small geometric Review icons, independent of platform emoji fonts."""

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


@lru_cache(maxsize=32)
def review_icon(name: str) -> QIcon:
    """Render monochrome icons for normal, selected, and disabled controls."""
    icon = QIcon()
    for mode, state, colour in (
        (QIcon.Mode.Normal, QIcon.State.Off, "#D4D4D4"),
        (QIcon.Mode.Normal, QIcon.State.On, "#FFFFFF"),
        (QIcon.Mode.Active, QIcon.State.Off, "#FFFFFF"),
        (QIcon.Mode.Active, QIcon.State.On, "#FFFFFF"),
        (QIcon.Mode.Disabled, QIcon.State.Off, "#6B7280"),
        (QIcon.Mode.Disabled, QIcon.State.On, "#6B7280"),
    ):
        for scale in (1, 2):
            pixmap = QPixmap(20 * scale, 20 * scale)
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(colour), 1.6, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

            def line(x1, y1, x2, y2):
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            def polygon(points):
                painter.setBrush(QColor(colour))
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))

            if name == "select":
                polygon([(4, 2), (4, 16), (8, 12), (11, 18),
                         (14, 16), (11, 11), (17, 11)])
            elif name == "circle":
                painter.drawEllipse(QRectF(3, 3, 14, 14))
            elif name == "rectangle":
                painter.drawRect(QRectF(3, 4, 14, 12))
            elif name in ("arrow", "next", "previous"):
                if name == "previous":
                    painter.translate(20, 0)
                    painter.scale(-1, 1)
                line(3, 10, 17, 10)
                line(12, 5, 17, 10)
                line(12, 15, 17, 10)
            elif name == "freehand":
                path = QPainterPath(QPointF(3, 15))
                path.cubicTo(2, 2, 10, 2, 8, 11)
                path.cubicTo(6, 19, 14, 5, 17, 8)
                painter.drawPath(path)
            elif name == "text":
                line(4, 4, 16, 4)
                line(10, 4, 10, 16)
                line(7, 16, 13, 16)
            elif name == "range":
                for x, tip in ((3, 6), (17, 14)):
                    line(x, 4, x, 16)
                    line(x, 4, tip, 4)
                    line(x, 16, tip, 16)
                line(7, 10, 13, 10)
            elif name == "pause":
                painter.setBrush(QColor(colour))
                painter.drawRect(QRectF(5, 4, 3, 12))
                painter.drawRect(QRectF(12, 4, 3, 12))
            elif name in ("play", "step_forward", "step_back", "start", "end"):
                if name in ("step_back", "start"):
                    painter.translate(20, 0)
                    painter.scale(-1, 1)
                polygon([(7, 4), (15, 10), (7, 16)])
                if name in ("step_forward", "step_back"):
                    line(3, 4, 3, 16)
                elif name in ("start", "end"):
                    line(18, 4, 18, 16)
            elif name in ("forward_10", "back_10"):
                if name == "back_10":
                    painter.translate(20, 0)
                    painter.scale(-1, 1)
                polygon([(3, 4), (10, 10), (3, 16)])
                polygon([(11, 4), (18, 10), (11, 16)])
            else:
                painter.end()
                raise ValueError(f"Unknown Review icon: {name}")
            painter.end()
            icon.addPixmap(pixmap, mode, state)
    return icon


def set_review_button_icon(button, name: str):
    """Keep icon controls compact, with an accessible name from their tooltip."""
    button.setText("")
    button.setIcon(review_icon(name))
    button.setIconSize(QSize(20, 20))
    button.setProperty("review_icon", "true")
    button.setAccessibleName(button.toolTip())
