"""Reversible arrangement of existing shot and task controls."""

from PyQt6.QtWidgets import QBoxLayout, QHBoxLayout, QLayout, QPushButton, QSizePolicy

from presentation_widgets import FlexibleButton, FlexibleLabel


class BoxSnapshot:
    def __init__(self, layout):
        self.layout = layout
        self.direction = layout.direction()
        self.spacing = layout.spacing()
        self.margins = layout.contentsMargins()
        self.items = []
        while layout.count():
            stretch = layout.stretch(0)
            item = layout.takeAt(0)
            widget = item.widget()
            # Keep widget references, not obsolete QWidgetItem size caches.
            self.items.append((widget, item if widget is None else None, stretch, item.alignment()))

    def restore(self):
        self.layout.setDirection(self.direction)
        self.layout.setSpacing(self.spacing)
        self.layout.setContentsMargins(self.margins)
        for widget, item, stretch, alignment in self.items:
            if widget is not None:
                self.layout.addWidget(widget, stretch, alignment)
            else:
                self.layout.addItem(item)
            self.layout.setStretch(self.layout.count() - 1, stretch)


def clear_box(layout):
    while layout.count():
        item = layout.takeAt(0)
        child = item.layout()
        if child is not None:
            clear_box(child)


def row(*widgets, stretch_first=False):
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if stretch_first and index == 0 else 0)
    return layout


def mark_v02(widget, enabled):
    for child in [widget, *widget.findChildren(FlexibleLabel), *widget.findChildren(FlexibleButton)]:
        if isinstance(child, (FlexibleLabel, FlexibleButton)):
            child.v02 = enabled
            child.updateGeometry()
            child.update()
        child.setProperty("v02", "true" if enabled else "false")
        child.style().unpolish(child)
        child.style().polish(child)


def arrange_shot_header(card, width):
    narrow = width < 520
    values = (card.label_frame_range, card.label_edit_inpoint, card.label_edit_outpoint)
    controls = (card.btn_hide_shot, card.btn_colour_none, card.btn_green, card.btn_amber, card.btn_red)
    visible_values = [label for label in values if not label.isHidden()]
    visible_controls = [control for control in controls if not control.isHidden()]
    required = card.label_shot.fontMetrics().horizontalAdvance(card.label_shot.text())
    required += sum(label.sizeHint().width() for label in visible_values)
    required += sum(min(control.maximumWidth(), max(control.minimumWidth(), control.sizeHint().width())) for control in visible_controls)
    required += 6 * (len(visible_values) + len(visible_controls))
    inline = not narrow and required <= width - 36
    signature = (narrow, inline)
    if signature == getattr(card, "_v02_header_signature", None):
        return
    card._v02_header_signature = signature
    header = card.horizontalLayout_6
    clear_box(header)
    header.setDirection(QBoxLayout.Direction.LeftToRight if inline else QBoxLayout.Direction.TopToBottom)
    header.setSpacing(6 if inline else 4)
    # Match the approved compact header, falling back before a title is cut off.
    header.addWidget(card.label_shot, 1 if inline else 0)
    if inline:
        for widget in (*values, *controls):
            header.addWidget(widget)
        return
    metadata = row(*values)
    metadata.addStretch()
    if narrow:
        header.addLayout(metadata)
        buttons = row(*controls)
        buttons.addStretch()
        header.addLayout(buttons)
    else:
        for control in controls:
            metadata.addWidget(control)
        header.addLayout(metadata)


def arrange_shot_actions(card):
    # Fit Nuke to its filename and give Render the remaining row width.
    if getattr(card, "_v02_actions_split", False):
        return
    card._v02_actions_split = True
    actions = card.horizontalLayout_3
    clear_box(actions)
    actions.setSpacing(4)
    actions.setDirection(QBoxLayout.Direction.TopToBottom)
    files = row(card.btn_open_nuke, card.btn_latest_render)
    files.setStretch(1, 1)
    actions.addLayout(files)
    folders = row(card.btn_open_assets, card.btn_open_precomp)
    folders.addStretch()
    actions.addLayout(folders)


def set_shot_presentation(card, enabled):
    if enabled == getattr(card, "_v02_grid", False):
        return
    card._v02_grid = enabled
    boxes = (
        card.verticalLayout_5, card.verticalLayout_3, card.horizontalLayout_6,
        card.horizontalLayout_metadata, card.horizontalLayout_3,
        card.horizontalLayout_last_conform,
    )
    if enabled:
        card._v02_shot_boxes = [BoxSnapshot(box) for box in boxes]
        card._legacy_shot_policy = card.sizePolicy()
        card._legacy_minimum_width = card.minimumWidth()
        card._legacy_shot_constraint = card.layout().sizeConstraint()
        card._legacy_preview_stretches = [card.horizontalLayout_2.stretch(i) for i in range(card.horizontalLayout_2.count())]
        card._legacy_title_wrap = card.label_shot.wordWrap()
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        card.setMinimumWidth(0)
        card.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        card.label_notes.setWordWrap(True)
        card.label_shot.setWordWrap(True)
        card.label_shot.wrap_anywhere = True
        card._v02_header_signature = None
        card._v02_actions_split = None
        card.label_shot.setToolTip(card.label_shot.toolTip() or card.label_shot.text())

        # Keep the actions beside the preview, like the original compact card.
        card.verticalLayout_5.addWidget(card.frame_6)
        card.verticalLayout_5.addWidget(card.frame_5)
        card.verticalLayout_5.addWidget(card.frame)
        card.verticalLayout_5.addWidget(card.frame_tasks)
        card.verticalLayout_5.setSpacing(6)
        arrange_shot_header(card, getattr(card, "_v02_layout_width", 600))
        for frame in (card.frame_metadata, card.frame_2, card.frame_last_conform, card.shot_btns):
            card.verticalLayout_3.addWidget(frame)
        card.verticalLayout_3.setSpacing(4)
        card.verticalLayout_3.addStretch()
        card.horizontalLayout_metadata.setDirection(QBoxLayout.Direction.LeftToRight)
        card.horizontalLayout_metadata.setSpacing(6)
        card.horizontalLayout_metadata.addWidget(card.label_colourspace, 1)
        card.horizontalLayout_metadata.addWidget(card.label_original_clip, 2)
        card.horizontalLayout_last_conform.addWidget(card.label_last_conform, 1)
        card.frame_metadata.setMaximumHeight(16777215)
        card.frame_6.setMaximumHeight(16777215)
        card.shot_btns.setMaximumHeight(16777215)

    else:
        for box in boxes:
            clear_box(box)
        for snapshot in card._v02_shot_boxes:
            snapshot.restore()
        del card._v02_shot_boxes
        card.setSizePolicy(card._legacy_shot_policy)
        card.setMinimumWidth(card._legacy_minimum_width)
        card.layout().setSizeConstraint(card._legacy_shot_constraint)
        card.label_notes.setWordWrap(False)
        card.label_shot.setWordWrap(card._legacy_title_wrap)
        card.label_shot.wrap_anywhere = False
        card.frame_metadata.setMaximumHeight(30)
        card.frame_6.setMaximumHeight(50)
        card.shot_btns.setMaximumHeight(50)
        for index, stretch in enumerate(card._legacy_preview_stretches):
            card.horizontalLayout_2.setStretch(index, stretch)
    mark_v02(card, enabled)
    for frame in (card.frame_7, card.frame_tasks, card.frame_metadata, card.btn_open_assets, card.btn_open_precomp):
        frame.setProperty("v02", "true" if enabled else "false")
        frame.style().unpolish(frame)
        frame.style().polish(frame)
    card.set_compact_mode(card._compact_mode)
    card.refresh_file_state_tooltips()
    card.updateGeometry()


def preferred_shot_width(card):
    """Measure a readable width independently of the current card geometry."""
    # The file row reserves half its space for each filename.
    files_width = 2 * max(
        QPushButton.sizeHint(button).width()
        for button in (card.btn_open_nuke, card.btn_latest_render)
    ) + 6
    if not card._compact_mode and card._thumbnails_enabled:
        files_width += card._base_thumb_target_width + 10
    header_widgets = (
        card.label_shot, card.label_frame_range, card.label_edit_inpoint,
        card.label_edit_outpoint, card.btn_hide_shot, card.btn_colour_none,
        card.btn_green, card.btn_amber, card.btn_red,
    )
    header_widths = []
    for widget in header_widgets:
        if widget.isHidden():
            continue
        if isinstance(widget, FlexibleLabel):
            header_widths.append(widget.fontMetrics().horizontalAdvance(widget.text()))
        else:
            header_widths.append(min(
                widget.maximumWidth(), max(widget.minimumWidth(), widget.sizeHint().width())
            ))
    header_width = sum(header_widths) + 6 * max(0, len(header_widths) - 1)
    return max(720, files_width + 36, header_width + 36)


def apply_shot_width(card, width):
    if not getattr(card, "_v02_grid", False):
        return
    narrow = width < 520 or card._compact_mode
    card._v02_layout_width = width
    arrange_shot_header(card, width)
    card.horizontalLayout_2.setDirection(
        QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
    )
    card.horizontalLayout_2.setSpacing(10)
    card.horizontalLayout_2.setStretch(0, 0)
    card.horizontalLayout_2.setStretch(1, 1)
    details_width = width - 36
    if not narrow and card._thumbnails_enabled:
        details_width -= card._current_thumb_target_width() + 10
    # Long script names must still leave room for the render label.
    nuke_width_limit = max(1, (details_width - 6) // 2)
    if card.btn_open_nuke.preferred_width_limit != nuke_width_limit:
        card.btn_open_nuke.preferred_width_limit = nuke_width_limit
        card.btn_open_nuke.updateGeometry()
    arrange_shot_actions(card)
    card.label_last_conform.setToolTip(card.label_last_conform.text())
    for task in getattr(card, "_task_widgets_by_id", {}).values():
        set_task_presentation(task, True, max(1, width - 36))
    card._apply_thumb_scale()


def set_task_presentation(task, enabled, width=600):
    task._v02_layout_width = width
    changed = enabled != getattr(task, "_v02_grid", False)
    if changed:
        task._v02_grid = enabled
        if task._is_checklist_presentation():
            if enabled:
                task._v02_task_box = BoxSnapshot(task.task_layout)
                task._legacy_title_policy = task.title_stack.sizePolicy()
                task._legacy_progress_size = task.check_done_task.size()
            else:
                clear_box(task.task_layout)
                task._v02_task_box.restore()
                del task._v02_task_box
                task._v02_task_arrangement = None
                task.title_stack.setSizePolicy(task._legacy_title_policy)
                task.check_done_task.setFixedSize(task._legacy_progress_size)
        mark_v02(task, enabled)
        task.task_frame.setProperty("v02", "true" if enabled else "false")
        task.task_frame.style().unpolish(task.task_frame)
        task.task_frame.style().polish(task.task_frame)
        for control in (
            getattr(task, "edit_title_inline", None), getattr(task, "edit_notes_inline", None),
            task.btn_priority, task.btn_budget_hours,
        ):
            if control is not None:
                control.setProperty("v02", "true" if enabled else "false")
                control.style().unpolish(control)
                control.style().polish(control)

    if not enabled or not task._is_checklist_presentation():
        return
    narrow = width < 420
    single_line = width >= 520
    arrangement = (narrow, single_line)
    if changed or arrangement != getattr(task, "_v02_task_arrangement", None):
        task._v02_task_arrangement = arrangement
        clear_box(task.task_layout)
        task.task_layout.setDirection(QBoxLayout.Direction.LeftToRight if single_line else QBoxLayout.Direction.TopToBottom)
        task.task_layout.setSpacing(4 if single_line else 2)
        if single_line:
            for control in (task.check_done_task, task.title_stack, task.edit_notes_inline, task.btn_status,
                            task.btn_assigned, task.btn_priority, task.btn_budget_hours, task.btn_hide_task, task.btn_delete_task):
                task.task_layout.addWidget(control)
            task.task_layout.setStretch(1, 1)
            task.task_layout.setStretch(2, 1)
        else:
            arrange_task_lines(task, narrow)
    task.task_layout.setContentsMargins(6, 3, 6, 3)
    task.check_done_task.setFixedSize(20, 20)
    task.edit_title_inline.setMinimumWidth(0)
    task.edit_notes_inline.setMinimumWidth(0)
    available = max(50, (width - 32) // 2)
    if single_line:
        # Keep status/artist columns aligned across mixed task statuses.
        column_width = max(80, min(100, width // 7))
        task.btn_status.setMinimumWidth(column_width)
        task.btn_assigned.setMinimumWidth(column_width)
    else:
        task.btn_status.setMinimumWidth(min(available, max(64, min(116, task.btn_status.fontMetrics().horizontalAdvance(task.btn_status.text().upper()) + 12))))
        task.btn_assigned.setMinimumWidth(min(available, max(70, min(110, task.btn_assigned.fontMetrics().horizontalAdvance(task.btn_assigned.text().upper()) + 12))))
    title = str(task._data.get("title") or "Task")
    if task.btn_task_title.text() != title:
        task.btn_task_title.setText(title)
    task.title_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


def arrange_task_lines(task, narrow):
    first = row(task.check_done_task, task.title_stack)
    first.setStretch(1, 1)
    if not narrow:
        first.addWidget(task.btn_status)
        first.addWidget(task.btn_assigned)
    task.task_layout.addLayout(first)
    if narrow:
        task.task_layout.addLayout(row(task.btn_status, task.btn_assigned))
        task.task_layout.addWidget(task.edit_notes_inline)
        second = row(task.btn_priority, task.btn_budget_hours, task.btn_hide_task, task.btn_delete_task)
        second.addStretch()
    else:
        second = row(task.edit_notes_inline, task.btn_priority, task.btn_budget_hours, task.btn_hide_task, task.btn_delete_task, stretch_first=True)
    task.task_layout.addLayout(second)
