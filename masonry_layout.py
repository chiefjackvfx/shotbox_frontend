"""Full-width, ordered shot rows; legacy FlowLayout stays unchanged."""

from PyQt6.QtCore import QRect, QSize
from flow_layout import FlowLayout


class MasonryLayout(FlowLayout):
    # Keep the existing class name for saved v02 layout integrations.
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
        target_width = max(1, max(preferred_widths, default=self.target_width))
        columns = min(max(1, len(visible_items)), max(1, (width + gap) // (target_width + gap)))
        card_width, extra_pixels = divmod(max(1, width - gap * (columns - 1)), columns)
        y = top

        for start in range(0, len(visible_items), columns):
            x = left
            row_height = 0
            for column, item in enumerate(visible_items[start:start + columns]):
                widget = item.widget()
                assigned_width = card_width + (column < extra_pixels)
                prepare = getattr(widget, "prepare_layout_width", None)
                # Qt probes widths (including zero) while sizing scroll areas.
                # A measurement must never resize previews or rearrange controls.
                if not test_only and prepare is not None:
                    prepare(assigned_width)
                height = item.heightForWidth(assigned_width) if item.hasHeightForWidth() else item.sizeHint().height()
                height = max(height, item.minimumSize().height(), widget.minimumHeight())
                if not test_only:
                    item.setGeometry(QRect(rect.x() + x, rect.y() + y, assigned_width, height))
                row_height = max(row_height, height)
                x += assigned_width + gap
            y += row_height + vertical_gap

        return y - (vertical_gap if visible_items else 0) + bottom
