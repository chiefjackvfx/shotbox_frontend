"""Web-style comment cards and a compact composer for native Timeline Review."""
from datetime import datetime
from functools import partial

from PyQt6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen
from PyQt6.QtGui import QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from task_create_dialog import TITLE_PRESETS, NOTES_PRESETS, BUDGET_PRESETS, PRIORITY_PRESETS
from themes import set_widget_style


SIDEBAR_STYLE = """
/* Palette and metrics follow tasks/static/tasks/review_polish.css. */
QWidget#timeline_review_sidebar, QWidget#timeline_task_details {
    background: #191c22; color: #e7eaf0;
}
QWidget#timeline_review_sidebar *, QWidget#timeline_task_details * {
    font-family: "Segoe UI", "Arial", sans-serif; font-size: 12px;
    text-transform: none; letter-spacing: 0px;
}
QLabel { background: transparent; border: none; padding: 0; }
QScrollArea { border: none; background: transparent; }
QWidget#review_comment_content { background: #191c22; }
QFrame#review_sidebar_header { border: none; border-bottom: 1px solid #303641; background: #191c22; }
QLabel#review_sidebar_title { font-size: 13px; font-weight: 600; }
QLabel#review_task_count { background: #2a303b; border-radius: 5px; padding: 2px 6px; color: #b7c1d0; font: 10px monospace; }
QFrame#review_comment_card { background: #20252e; border: 1px solid #303743; border-radius: 9px; }
QFrame#review_comment_card[selected="true"] { background: #2b2726; border-color: #b76e4d; }
QLabel#review_avatar { background: #344354; border-radius: 15px; color: #d4e7fa; font-size: 10px; font-weight: 700; }
QLabel#review_comment_author { color: #d1d8e3; font-size: 11px; }
QLabel#review_comment_date { color: #96a0b0; font-size: 10px; }
QLabel#review_field_label { color: #9eaabb; font-size: 10px; }
QLabel#review_comment_title { color: #eef0f4; font-size: 13px; font-weight: 600; }
QLabel#review_comment_notes { color: #c8cfda; font-size: 12px; }
QLabel#review_version_note { color: #ffb886; font-size: 10px; }
QFrame#review_card_fields { background: transparent; border: none; border-top: 1px solid #2a303b; border-radius: 0; }
QFrame#review_card_fields QComboBox { background: #252c36; border-color: #3a4352; font-size: 11px; padding: 5px 7px; padding-right: 16px; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #161b22; color: #e0e6ef; border: 1px solid #394250;
    border-radius: 5px; padding: 6px 9px; min-height: 16px;
    selection-background-color: #506986;
}
QComboBox { padding-right: 22px; }
QComboBox#review_comment_filter { background: #20252e; border-color: #343b48; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #79879c; }
QPushButton:focus { border-color: #ff8758; }
QComboBox::drop-down { border: none; width: 20px; background: transparent; }
QComboBox QAbstractItemView { background: #20252e; color: #e7eaf0; }
QPlainTextEdit { padding: 8px 10px; }
QPushButton {
    background: #252a33; color: #e7eaf0; border: 1px solid #303641;
    border-radius: 5px; padding: 4px 8px; min-width: 0; min-height: 16px;
    font-weight: 500; text-transform: none; letter-spacing: 0px;
}
QPushButton:hover { background: #303744; }
QPushButton:checked { background: #394557; border-color: #75869d; }
QPushButton:disabled { color: #616b79; background: #20252e; border-color: #2c3440; }
QPushButton#review_icon_button, QPushButton#review_text_button { border: none; background: transparent; color: #9aa4b3; }
QPushButton#review_icon_button { padding: 0; }
QPushButton#review_collapse { background: transparent; border: none; color: #8d9aaf; font: 22px monospace; padding: 0; }
QPushButton#review_icon_button:checked { background: #394557; border: 1px solid #75869d; }
QFrame#review_drawing_tools { background: #141922; border: none; border-radius: 6px; }
QPushButton#review_annotate:checked { background: #342c2e; border-color: #804b3b; color: #ffb48f; }
QPushButton#review_icon_button:hover, QPushButton#review_text_button:hover { background: #303744; }
QPushButton#review_icon_button[action="delete"]:hover { color: #ff7676; border: 1px solid #ff7676; }
QPushButton#review_icon_button[action="edit"]:hover { color: #ff8758; border: 1px solid #ff8758; }
QPushButton#review_frame_badge, QPushButton#review_version_button {
    color: #ffb88f; background: #29252b; border: none; font: 10px monospace; padding: 4px 7px;
}
QPushButton#review_swatch { padding: 0; min-width: 0; min-height: 0; border: 1px solid transparent; border-radius: 7px; }
QPushButton#review_swatch:checked { border: 2px solid #dce4ef; }
QFrame#review_composer { background: #1d2129; border: none; border-top: 1px solid #39414e; }
QFrame#review_composer_actions { border: none; border-top: 1px solid #2b2f36; border-radius: 0; }
QPushButton#review_save_task { background: #ff8758; color: #1f1611; border: 1px solid #ff8758; font-weight: 700; padding: 7px 15px; }
QPushButton#review_save_task:hover { background: #ffa17c; }
QPushButton#review_save_task:disabled { background: #7b5445; border-color: #7b5445; color: #a69b95; }
"""


class SidebarCombo(QComboBox):
    """Keep the dropdown affordance visible under the application's theme packs."""
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#b5c0d0'), 1.2))
        x, y = self.width() - 12, self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 3, y - 1))


class CommentCard(QFrame):
    """Match web card-body navigation without interrupting selected note text."""
    def __init__(self, activate):
        super().__init__()
        self._activate = activate
        self._origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and self._origin is not None
                and (event.position() - self._origin).manhattanLength() < 4
                and not any(label.hasSelectedText() for label in self.findChildren(QLabel))):
            self._origin = None
            self._activate()
            event.accept()
            return
        self._origin = None
        super().mouseReleaseEvent(event)


def icon(name):
    paths = {
        'edit': 'M4 14 14 4l3 3L7 17l-4 1 1-4Zm8-8 3 3',
        'delete': 'M3 5h14M7 5V2h6v3M5 5l1 13h8l1-13M8 8v7m4-7v7',
        'arrow': 'M4 16 16 4M5 4h11v11', 'line': 'M4 16 16 4',
        'rect': 'M3 4h14v12H3Z', 'pen': 'm4 13 9-9 3 3-9 9-4 1 1-4Zm7-7 3 3',
        'undo': 'M7 4 3 8l4 4M3 8h9a5 5 0 0 1 0 10',
        'redo': 'm13 4 4 4-4 4m4-4H8a5 5 0 0 0 0 10',
    }
    result = QIcon()
    hover = '#ff7676' if name == 'delete' else '#ff8758' if name == 'edit' else '#ffffff'
    for mode, state, colour in ((QIcon.Mode.Normal, QIcon.State.Off, '#a8b0bf'),
                               (QIcon.Mode.Active, QIcon.State.Off, hover),
                               (QIcon.Mode.Normal, QIcon.State.On, '#ffffff'),
                               (QIcon.Mode.Active, QIcon.State.On, '#ffffff'),
                               (QIcon.Mode.Disabled, QIcon.State.Off, '#596170')):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="{paths[name]}" fill="none" stroke="{colour}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        pixmap = QPixmap(40, 40)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, 20, 20))
        painter.end()
        result.addPixmap(pixmap, mode, state)
    return result


def icon_button(page, name, tip, fn, layout):
    button = page._button('', fn, layout)
    button.setObjectName('review_icon_button')
    button.setProperty('action', name)
    button.setIcon(icon(name))
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(26, 26)
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    return button


def build_sidebar(page, statuses):
    # Keep detailed task fields beside shot details, as in the web view.
    shot_scroll = page.left
    page.left = QTabWidget()
    page.left.setMinimumWidth(240)
    detail_scroll = QScrollArea()
    detail_scroll.setWidgetResizable(True)
    page.task_details = QWidget()
    page.task_details.setObjectName('timeline_task_details')
    extras = QVBoxLayout(page.task_details)
    extras.setContentsMargins(16, 16, 16, 16)
    extras.setSpacing(10)
    page.draft_label = QLabel('New task')
    page.draft_label.setWordWrap(True)
    extras.addWidget(page.draft_label)
    page._button('Return to draft shot', page.return_to_draft, extras)
    page.title_preset = SidebarCombo()
    page.title_preset.addItems(['Task presets…'] + TITLE_PRESETS)
    page.title_preset.activated.connect(lambda i: page.title.setText(page.title_preset.itemText(i)) if i else None)
    extras.addWidget(page.title_preset)
    page._button('Notes: idk', lambda: page.notes.setPlainText(NOTES_PRESETS[0]), extras)
    details = QFormLayout()
    page.range_start, page.range_end = QSpinBox(), QSpinBox()
    for label, control in (('Shot frame / range start', page.range_start), ('Range end (inclusive)', page.range_end)):
        control.setRange(-2147483647, 2147483647)
        details.addRow(label, control)
    extras.addLayout(details)
    page._button('Set start here', lambda: page.set_range_here(False), extras)
    page._button('Set end here', lambda: page.set_range_here(True), extras)
    fields = QFormLayout()
    page.task_status = SidebarCombo()
    for value, label in statuses:
        page.task_status.addItem(label, value)
    page.artist = SidebarCombo()
    page.artist.addItem('Unassigned', None)
    page.budget = QDoubleSpinBox()
    page.budget.setRange(0, 999.99)
    page.budget.setDecimals(2)
    page.priority = QSpinBox()
    page.priority.setRange(1, 10)
    page.priority.setValue(5)
    for label, control in (('Status', page.task_status), ('Artist', page.artist), ('Budget hours', page.budget), ('Priority', page.priority)):
        fields.addRow(label, control)
    extras.addLayout(fields)
    for attr, label, values, control in (('budget_preset', 'Budget presets…', BUDGET_PRESETS, page.budget),
                                       ('priority_preset', 'Priority presets…', PRIORITY_PRESETS, page.priority)):
        combo = SidebarCombo()
        combo.addItem(label)
        for value in values:
            combo.addItem(str(value), value)
        combo.activated.connect(lambda i, c=combo, target=control: target.setValue(c.itemData(i)) if i else None)
        setattr(page, attr, combo)
        extras.addWidget(combo)
    extras.addStretch()
    detail_scroll.setWidget(page.task_details)
    page.left.addTab(detail_scroll, 'Task details')
    page.left.addTab(shot_scroll, 'Shot details')
    page.splitter.insertWidget(0, page.left)
    page.left_toggle.setText('Task / shot details')
    page.left.hide()

    page.right = QWidget()
    page.right.setObjectName('timeline_review_sidebar')
    page.right.setMinimumWidth(350)
    right = QVBoxLayout(page.right)
    right.setContentsMargins(0, 0, 0, 0)
    right.setSpacing(0)
    header = QFrame()
    header.setObjectName('review_sidebar_header')
    heading = QVBoxLayout(header)
    heading.setContentsMargins(16, 17, 16, 13)
    row = QHBoxLayout()
    title = QLabel('Comments & tasks')
    title.setObjectName('review_sidebar_title')
    row.addWidget(title)
    page.comment_count = QLabel('0')
    page.comment_count.setObjectName('review_task_count')
    row.addWidget(page.comment_count)
    row.addStretch()
    collapse = page._button('›', page.right.hide, row)
    collapse.setObjectName('review_collapse')
    collapse.setFixedWidth(24)
    collapse.setToolTip('Collapse comments and tasks panel')
    heading.addLayout(row)
    page.comment_filter = SidebarCombo()
    page.comment_filter.setObjectName('review_comment_filter')
    page.comment_filter.addItems(['Current shot', 'All shots'])
    heading.addWidget(page.comment_filter)
    right.addWidget(header)
    page.comments = QScrollArea()
    page.comments.setWidgetResizable(True)
    page.comments.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    right.addWidget(page.comments, 1)
    page.form_scroll = QScrollArea()
    page.form_scroll.setWidgetResizable(True)
    page.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page.form_scroll.setFixedHeight(332)
    composer = QFrame()
    composer.setObjectName('review_composer')
    form = QVBoxLayout(composer)
    form.setContentsMargins(15, 14, 15, 12)
    form.setSpacing(9)
    page.scope = SidebarCombo()
    page.scope.addItems(['Whole shot', 'Current frame', 'From current frame to shot end', 'Custom frame range'])
    page.scope.setFixedHeight(34)
    page.scope.activated.connect(lambda i: show_task_details(page) if i == 3 else None)
    form.addWidget(page.scope)
    page.title = QLineEdit()
    page.title.setMaxLength(50)
    page.title.setPlaceholderText('Add a comment or task…')
    page.title.setFixedHeight(36)
    form.addWidget(page.title)
    page.notes = QPlainTextEdit()
    page.notes.setPlaceholderText('More detail (optional)')
    page.notes.setFixedHeight(68)
    form.addWidget(page.notes)
    tools = QHBoxLayout()
    tools.setSpacing(3)
    page.annotate_button = page._button('Annotate', lambda: page.tool.setCurrentIndex(
        0 if page.tool.currentData() else page.tool.findData(getattr(page, '_last_drawing_tool', 'pen'))), tools)
    page.annotate_button.setObjectName('review_annotate')
    page.annotate_button.setCheckable(True)
    page.tool = QComboBox(page)
    page.tool.hide()
    for label, value in (('Drawing off', None), ('Pen', 'pen'), ('Arrow', 'arrow'), ('Line', 'line'), ('Rectangle', 'rect')):
        page.tool.addItem(label, value)
    page.draw_buttons = {}
    drawing_group = QFrame()
    drawing_group.setObjectName('review_drawing_tools')
    drawing_icons = QHBoxLayout(drawing_group)
    drawing_icons.setContentsMargins(2, 2, 2, 2)
    drawing_icons.setSpacing(2)
    tools.addWidget(drawing_group)
    for tool, index in (('arrow', 2), ('line', 3), ('rect', 4), ('pen', 1)):
        button = icon_button(page, tool, {'rect': 'Rectangle', 'pen': 'Pen'}.get(tool, tool.title()),
                             lambda i=index: page.tool.setCurrentIndex(i), drawing_icons)
        button.setFixedSize(24, 26)
        button.setCheckable(True)
        page.draw_buttons[tool] = button
    tools.addStretch()
    page.swatch_buttons = {}
    for name, colour in (('Pink', '#f553e6'), ('Yellow', '#ffd400'), ('Green', '#00ca88'), ('Orange', '#ff6b35')):
        button = page._button('', partial(page.set_colour, colour), tools)
        button.setObjectName('review_swatch')
        button.setFixedSize(16, 16)
        button.setCheckable(True)
        button.setToolTip(name)
        button.setStyleSheet(f'background-color: {colour};')
        page.swatch_buttons[colour] = button
    page.colour_button = page._button('', page.choose_colour, tools)
    page.colour_button.setFixedSize(24, 24)
    page.colour_button.setToolTip('Custom drawing colour')
    form.addLayout(tools)
    history = QHBoxLayout()
    history.setSpacing(5)
    page.stroke_size = SidebarCombo()
    for name, size in (('Thin', 2), ('Medium', 3), ('Thick', 6)):
        page.stroke_size.addItem(name, size)
    page.stroke_size.setCurrentIndex(1)
    page.stroke_size.setFixedWidth(96)
    history.addWidget(page.stroke_size)
    page.undo_button = icon_button(page, 'undo', 'Undo drawing', page.picture.undo, history)
    page.redo_button = icon_button(page, 'redo', 'Redo drawing', page.redo_drawing, history)
    page.clear_drawing_button = page._button('Clear drawings', page.clear_drawing, history)
    history.addStretch()
    form.addLayout(history)
    page.done_button = page._button('Done drawing', page.done_drawing, form)
    page.done_button.hide()
    form.addStretch()
    actions_widget = QFrame()
    actions_widget.setObjectName('review_composer_actions')
    actions = QHBoxLayout(actions_widget)
    actions.setContentsMargins(0, 10, 0, 0)
    actions.addStretch()
    page.clear_button = page._button('Clear', page.cancel_task, actions)
    page.clear_button.setObjectName('review_text_button')
    page.save_button = page._button('Create Task', page.save_task, actions)
    page.save_button.setObjectName('review_save_task')
    form.addWidget(actions_widget)
    page.form_scroll.setWidget(composer)
    right.addWidget(page.form_scroll)
    page.splitter.addWidget(page.right)
    page.splitter.setSizes([240, 900, 365])
    page.splitter.setStretchFactor(1, 1)
    set_widget_style(page.right, SIDEBAR_STYLE)
    set_widget_style(page.task_details, SIDEBAR_STYLE)


def sync_drawing_controls(page):
    tool = page.tool.currentData()
    if tool:
        page._last_drawing_tool = tool
    for value, button in page.draw_buttons.items():
        button.setChecked(value == (tool or getattr(page, '_last_drawing_tool', 'pen')))
    page.annotate_button.setChecked(bool(tool))
    page.done_button.setVisible(bool(tool))
    page.form_scroll.setFixedHeight(366 if tool else 332)
    page.undo_button.setEnabled(bool(page.picture.shapes))
    page.redo_button.setEnabled(bool(page.picture.redo_shapes))
    page.clear_drawing_button.setEnabled(bool(page.picture.shapes))
    for colour, button in page.swatch_buttons.items():
        button.setChecked(colour == page.picture.color)
    page.colour_button.setStyleSheet(f'background-color: {page.picture.color}; border: 1px solid #8493a8; padding: 2px;')


def show_task_details(page):
    page.left.setCurrentIndex(0)
    page.left.show()


def task_card(page, cut, task, statuses):
    card = CommentCard(partial(page.select_task, cut, task))
    card.setObjectName('review_comment_card')
    card.setProperty('selected', bool(page.selected_task and page.selected_task[1]['id'] == task['id']))
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    box = QVBoxLayout(card)
    box.setContentsMargins(14, 15, 14, 15)
    box.setSpacing(10)
    metadata = task.get('frameio_metadata') or {}
    comment = metadata.get('comment') or {}
    author = (comment.get('author') or {}).get('name') or 'Shotbox task'
    created = comment.get('inserted_at') or task.get('created', '')
    try:
        created = datetime.fromisoformat(created.replace('Z', '+00:00')).astimezone().strftime('%d/%m/%Y, %H:%M:%S')
    except (ValueError, AttributeError):
        pass
    heading = QHBoxLayout()
    avatar = QLabel(author[:2].upper())
    avatar.setObjectName('review_avatar')
    avatar.setFixedSize(30, 30)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    heading.addWidget(avatar)
    author_info = QVBoxLayout()
    author_info.setSpacing(2)
    author_label = QLabel(author)
    author_label.setObjectName('review_comment_author')
    author_label.setTextFormat(Qt.TextFormat.PlainText)
    author_info.addWidget(author_label)
    date = QLabel(created)
    date.setObjectName('review_comment_date')
    author_info.addWidget(date)
    heading.addLayout(author_info, 1)
    for name, tip, fn in (('edit', 'Edit task', partial(page.edit_task, cut, task)),
                          ('delete', 'Delete task', partial(page.delete_task, cut, task))):
        icon_button(page, name, tip, fn, heading).setEnabled(task['id'] not in page._pending_tasks)
    box.addLayout(heading)
    ref = task.get('frame_reference')
    reference = f'Frames {ref[0]}–{ref[1]}' if isinstance(ref, list) else f'Frame {ref}' if ref is not None else 'Whole shot'
    badge_row = QHBoxLayout()
    badge = page._button(f'{cut.shot["title"]} · {reference}' + (' · Drawing' if task.get('annotations') else ''),
                         partial(page.select_task, cut, task), badge_row)
    badge.setObjectName('review_frame_badge')
    badge_row.addStretch()
    box.addLayout(badge_row)
    version = task.get('reviewed_version')
    if version is not None:
        different = version != cut.media.get('version')
        note = QLabel(f'Reviewed on v{version}' + (' · Different render displayed' if different else ''))
        note.setObjectName('review_version_note')
        note.setWordWrap(True)
        box.addWidget(note)
        if different:
            version_row = QHBoxLayout()
            load = page._button(f'Load v{version}', partial(page.load_task_version, cut, task), version_row)
            load.setObjectName('review_version_button')
            version_row.addStretch()
            box.addLayout(version_row)
    if not task.get('notes') or not task['notes'].startswith(task['title']):
        title = QLabel(task['title'])
        title.setObjectName('review_comment_title')
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        box.addWidget(title)
    if task.get('notes'):
        notes = QLabel(task['notes'])
        notes.setObjectName('review_comment_notes')
        notes.setWordWrap(True)
        notes.setTextFormat(Qt.TextFormat.PlainText)
        notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(notes)
    fields = QFrame()
    fields.setObjectName('review_card_fields')
    row = QHBoxLayout(fields)
    row.setContentsMargins(0, 10, 0, 0)
    row.setSpacing(10)
    for field, label, options in (('status', 'Status', statuses),
                                  ('artist', 'Artist', [(page.artist.itemData(i), page.artist.itemText(i)) for i in range(page.artist.count())])):
        column = QVBoxLayout()
        column.setSpacing(5)
        caption = QLabel(label)
        caption.setObjectName('review_field_label')
        column.addWidget(caption)
        combo = SidebarCombo()
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo.setAccessibleName(f'{label} for {task["title"]}')
        for value, text in options:
            combo.addItem(text, value)
        value = task.get(field)
        if combo.findData(value) < 0:
            combo.addItem(str(value), value)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        combo.setEnabled(task['id'] not in page._pending_tasks)
        combo.activated.connect(partial(page._quick_field, cut.shot['id'], task, field, combo))
        column.addWidget(combo)
        row.addLayout(column, 1)
    box.addWidget(fields)
    for reply in metadata.get('replies') or []:
        if isinstance(reply, dict):
            label = QLabel(f"{(reply.get('author') or {}).get('name', 'Reviewer')}: {reply.get('text', '')}")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            box.addWidget(label)
    return card
