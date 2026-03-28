# flow_layout.py
from PyQt6.QtWidgets import QLayout, QLayoutItem, QSizePolicy
from PyQt6.QtCore import Qt, QRect, QSize, QPoint


class FlowLayout(QLayout):
    """
    A layout that arranges widgets in a flowing grid that wraps based on available width.
    Similar to CSS flexbox with wrapping.
    """
    
    def __init__(self, parent=None, margin=0, h_spacing=-1, v_spacing=-1):
        super().__init__(parent)
        
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._item_list = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item: QLayoutItem):
        """Add an item to the layout."""
        self._item_list.append(item)

    def horizontalSpacing(self):
        """Get horizontal spacing between items."""
        if self._h_space >= 0:
            return self._h_space
        else:
            return self._smart_spacing(QSizePolicy.ControlType.PushButton)

    def verticalSpacing(self):
        """Get vertical spacing between items."""
        if self._v_space >= 0:
            return self._v_space
        else:
            return self._smart_spacing(QSizePolicy.ControlType.PushButton)

    def setSpacing(self, spacing: int):
        """Set both horizontal and vertical spacing."""
        self._h_space = spacing
        self._v_space = spacing
        super().setSpacing(spacing)
        self.invalidate()

    def count(self):
        """Return the number of items in the layout."""
        return len(self._item_list)

    def itemAt(self, index: int):
        """Get item at specific index."""
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int):
        """Remove and return item at specific index."""
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        """Layout expands horizontally."""
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        """Layout height depends on width."""
        return True

    def heightForWidth(self, width: int):
        """Calculate height needed for given width."""
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect: QRect):
        """Position all items in the layout."""
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        """Suggested size for the layout."""
        return self.minimumSize()

    def minimumSize(self):
        """Minimum size needed for the layout."""
        size = QSize()
        
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        
        left, top, right, bottom = self.getContentsMargins()
        size += QSize(left + right, top + bottom)
        return size

    def _do_layout(self, rect: QRect, test_only: bool):
        """
        Arrange items in a flowing grid.
        
        Args:
            rect: Rectangle to lay out items in
            test_only: If True, only calculate height without actually positioning items
            
        Returns:
            Total height needed
        """
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0
        
        h_space = self.horizontalSpacing()
        v_space = self.verticalSpacing()

        for item in self._item_list:
            widget = item.widget()
            
            space_x = h_space
            space_y = v_space
            
            next_x = x + item.sizeHint().width() + space_x
            
            # Check if we need to wrap to next line
            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + bottom

    def _smart_spacing(self, control_type):
        """Get smart spacing from parent widget or style."""
        parent = self.parent()
        if not parent:
            return -1
        
        if parent.isWidgetType():
            return parent.style().layoutSpacing(
                control_type,
                control_type,
                Qt.Orientation.Horizontal
            )
        else:
            return parent.spacing()
