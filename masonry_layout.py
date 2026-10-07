"""Equal-width, height-aware shot columns; legacy FlowLayout stays unchanged."""

from PyQt6.QtCore import QRect, QSize
from flow_layout import FlowLayout


class MasonryLayout(FlowLayout):
    target_width = 720

    def expandingDirections(self):
        from PyQt6.QtCore import Qt
        return Qt.Orientation.Horizontal

    def minimumSize(self):
        left, top, right, bottom = self.getContentsMargins()
        return QSize(left + right, top + bottom)

    def _do_layout(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        width = max(1, rect.width() - left - right)
        gap = max(0, self.horizontalSpacing())
        vertical_gap = max(0, self.verticalSpacing())
        visible_items = [
            item for item in self._item_list
            if item.widget() is not None and not item.widget().isHidden()
        ]
        preferred_widths = []
        for item in visible_items:
            preferred = getattr(item.widget(), "preferred_layout_width", None)
            preferred_widths.append(preferred() if preferred else self.target_width)
        card_width = min(width, max(1, max(preferred_widths, default=self.target_width)))
        columns = min(max(1, len(visible_items)), max(1, (width + gap) // (card_width + gap)))
        heights = [top] * columns
        used = [False] * columns

        for item in visible_items:
            widget = item.widget()
            column = min(range(columns), key=lambda index: (heights[index], index))
            assigned_width = card_width
            prepare = getattr(widget, "prepare_layout_width", None)
            # Qt probes widths (including zero) while sizing scroll areas.
            # A measurement must never resize previews or rearrange controls.
            if not test_only and prepare is not None:
                prepare(assigned_width)
            height = item.heightForWidth(assigned_width) if item.hasHeightForWidth() else item.sizeHint().height()
            height = max(height, item.minimumSize().height(), widget.minimumHeight())
            if not test_only:
                item.setGeometry(QRect(
                    rect.x() + left + column * (card_width + gap), rect.y() + heights[column],
                    assigned_width, height,
                ))
            heights[column] += height + vertical_gap
            used[column] = True

        return max(
            (height - vertical_gap if occupied else top)
            for height, occupied in zip(heights, used)
        ) + bottom
