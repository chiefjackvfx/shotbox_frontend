"""Presentation for Import Timeline; parsing and import actions stay on the page."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
    QScrollArea, QSplitter, QStackedWidget, QStyle, QVBoxLayout, QWidget,
)


def _label(text, role='muted'):
    label = QLabel(text)
    label.setProperty('role', role)
    label.setWordWrap(role in ('muted', 'loading', 'importStatus'))
    return label


def _card(step, title):
    card = QFrame()
    card.setProperty('role', 'card')
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)
    heading = QHBoxLayout()
    number = _label(step, 'step')
    number.setFixedWidth(28)
    heading.addWidget(number)
    heading.addWidget(_label(title, 'sectionTitle'))
    heading.addStretch()
    layout.addLayout(heading)
    return card, layout, heading


def _field(text, widget):
    layout = QVBoxLayout()
    layout.setSpacing(5)
    layout.addWidget(_label(text, 'fieldLabel'))
    layout.addWidget(widget)
    return layout


def arrange_timeline_page(page):
    """Rearrange the shared import controls without replacing their behavior."""
    root = page.layout()
    controls = {
        page.shoNametextbox, page.shotNumberPadSpinbox, page.updateBtn,
        page.colourspaceComboBox, page.detect_colourspace_check, page.colourspace_detection_label,
        page.btnSelecRoot, page.lableRoot, page.spinboxHandles, page.detect_handles_check,
        page.btnSelectXML, page.btnMakeThumbs, page.btnShotTable, page.btnNukeAll,
        page.checkBoxAddToDB, page.checkBoxCreateTasks, page.checkBoxSkipActivityLogging,
        page.btnMakeShot, page.tableWidgetTemp, page.folder_edit, page.cancel_scan_button,
        page.loading_label, page.loading_progress, page.timeline_combo, page.accept_handles_button,
        page.validation_text, page.timeline_name_edit,
    }

    def clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.layout():
                clear(item.layout())
                item.layout().deleteLater()
            elif item.widget() and item.widget() not in controls:
                item.widget().hide()
                item.widget().deleteLater()

    clear(root)
    page.setObjectName('import_timeline_page')
    ui_dir = Path(__file__).parent / 'ui'
    page.setStyleSheet((ui_dir / 'import_timeline.qss').read_text(encoding='utf-8')
                      .replace('@TIMELINE_UI@', ui_dir.as_posix()))
    root.setContentsMargins(20, 18, 20, 16)
    root.setSpacing(14)

    header = QHBoxLayout()
    titles = QVBoxLayout()
    titles.setSpacing(4)
    titles.addWidget(_label('Import Timeline', 'pageTitle'))
    titles.addWidget(_label('Turn a media-managed edit into ready-to-work shots.'))
    header.addLayout(titles, 1)
    header.addWidget(_label('DRT / XML / OTIO', 'formatBadge'))
    header.addSpacing(12)
    header.addWidget(page.btnMakeShot)
    root.addLayout(header)

    content_splitter = QSplitter(Qt.Orientation.Vertical)
    content_splitter.setChildrenCollapsible(False)
    root.addWidget(content_splitter, 1)
    setup_scroll = QScrollArea()
    setup_scroll.setObjectName('timeline_setup_scroll')
    setup_scroll.setWidgetResizable(True)
    setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
    setup_scroll.setMinimumHeight(160)
    setup_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    setup_content = QWidget()
    setup_content.setObjectName('timeline_setup_content')
    setup = QVBoxLayout(setup_content)
    setup.setContentsMargins(0, 0, 0, 0)
    setup.setSpacing(12)
    setup_scroll.setWidget(setup_content)
    content_splitter.addWidget(setup_scroll)

    source, source_layout, _ = _card('01', 'Choose your timeline')
    source.setObjectName('timeline_source_card')
    source_layout.addWidget(_label('Drop a media-managed folder anywhere on this page, or browse to one.'))
    folder_row = QHBoxLayout()
    folder_row.addWidget(page.folder_edit, 1)
    folder_row.addWidget(page.btnSelectXML)
    page.btnSelectXML.setIcon(page.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
    page.folder_edit.setPlaceholderText('Folder containing your clips and DRT / XML / OTIO timeline')
    source_layout.addLayout(folder_row)
    timelines = _field('Timeline to import · choose DRT, XML or OTIO', page.timeline_combo)
    page.timeline_combo.setMinimumWidth(0)
    page.timeline_combo.setPlaceholderText('Select a folder first')
    source_layout.addLayout(timelines)
    loading = QHBoxLayout()
    page.loading_label.setProperty('role', 'loading')
    loading.addWidget(page.loading_label, 1)
    page.loading_progress.setFixedHeight(5)
    loading.addWidget(page.loading_progress)
    loading.addWidget(page.cancel_scan_button)
    page.cancel_scan_button.hide()
    source_layout.addLayout(loading)
    setup.addWidget(source)

    settings, settings_layout, settings_heading = _card('02', 'Import settings')
    settings_heading.addWidget(page.updateBtn)
    page.updateBtn.setText('Apply settings')
    settings_layout.addLayout(_field('Timeline name', page.timeline_name_edit))
    settings_grid = QGridLayout()
    settings_grid.setHorizontalSpacing(24)
    settings_grid.setVerticalSpacing(10)
    naming = QHBoxLayout()
    naming.setSpacing(12)
    naming.addLayout(_field('Shot prefix', page.shoNametextbox))
    naming.addLayout(_field('Padding', page.shotNumberPadSpinbox))
    naming.addStretch()
    page.shoNametextbox.setMaximumWidth(160)
    page.shoNametextbox.setMinimumWidth(90)
    page.shotNumberPadSpinbox.setFixedWidth(75)
    settings_grid.addLayout(naming, 0, 0)
    colour = QVBoxLayout()
    colour.setSpacing(6)
    page.detect_colourspace_check.setText('Detect input colour space')
    colour.addWidget(page.detect_colourspace_check)
    colour.addWidget(page.colourspaceComboBox)
    page.colourspaceComboBox.setMinimumWidth(0)
    page.colourspace_detection_label.setProperty('role', 'muted')
    page.colourspace_detection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    colour.addWidget(page.colourspace_detection_label)
    settings_grid.addLayout(colour, 0, 1, 2, 1)
    handles = QHBoxLayout()
    handles.addWidget(page.detect_handles_check)
    handles.addWidget(page.spinboxHandles)
    page.spinboxHandles.setFixedWidth(85)
    handles.addStretch()
    settings_grid.addLayout(handles, 1, 0)
    settings_grid.setColumnStretch(0, 1)
    settings_grid.setColumnStretch(1, 1)
    settings_layout.addLayout(settings_grid)
    destination = QHBoxLayout()
    destination.addWidget(_label('Destination', 'fieldLabel'))
    page.lableRoot.setText('Choose where imported shots will be created')
    page.lableRoot.setProperty('role', 'muted')
    page.lableRoot.setWordWrap(True)
    page.lableRoot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    destination.addWidget(page.btnSelecRoot)
    destination.addWidget(page.lableRoot, 1)
    settings_layout.addLayout(destination)
    options = QHBoxLayout()
    options.setSpacing(20)
    for check in (page.checkBoxAddToDB, page.checkBoxCreateTasks, page.checkBoxSkipActivityLogging):
        options.addWidget(check)
    options.addStretch()
    settings_layout.addLayout(options)
    setup.addWidget(settings)

    review, review_layout, review_heading = _card('03', 'Review matches')
    review.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    review.setMinimumHeight(270)
    page.review_summary_label = _label('No timeline loaded', 'muted')
    page.review_summary_label.setWordWrap(False)
    review_heading.addWidget(page.review_summary_label)
    tools = QHBoxLayout()
    tools.setSpacing(8)
    tools.addWidget(page.accept_handles_button)
    tools.addStretch()
    tools.addWidget(page.btnShotTable)
    review_layout.addLayout(tools)

    page.review_stack = QStackedWidget()
    empty = QWidget()
    empty_layout = QVBoxLayout(empty)
    empty_layout.setContentsMargins(20, 20, 20, 20)
    empty_layout.addStretch()
    icon = QLabel()
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setPixmap(page.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon).pixmap(36, 36))
    empty_layout.addWidget(icon)
    for text, role in [('Your timeline starts here', 'emptyTitle'),
                       ('Choose a folder to review local clips, timing and handles before importing.', 'muted')]:
        label = _label(text, role)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(label)
    empty_layout.addStretch()
    page.review_stack.addWidget(empty)
    table = page.tableWidgetTemp
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().hide()
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    table.horizontalHeader().setMinimumSectionSize(55)
    # Keep the match and validation columns together; detailed timing follows.
    header = table.horizontalHeader()
    for position, column in enumerate((0, 1, 3, 4, 12, 13, 5, 2, 6, 7, 8, 9, 10, 11)):
        header.moveSection(header.visualIndex(column), position)
    page.review_stack.addWidget(table)
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(page.review_stack)
    page.validation_text.setObjectName('timeline_validation')
    page.validation_text.setPlaceholderText('Import checks and any issues will appear here.')
    page.validation_text.setMinimumHeight(50)
    page.validation_text.setMaximumHeight(120)
    splitter.addWidget(page.validation_text)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    splitter.setSizes([320, 70])
    review_layout.addWidget(splitter, 1)
    content_splitter.addWidget(review)
    content_splitter.setStretchFactor(0, 0)
    content_splitter.setStretchFactor(1, 1)
    content_splitter.setSizes([450, 350])

    footer = QHBoxLayout()
    page.import_status_label = _label('Choose a folder to begin', 'importStatus')
    page.import_status_label.setProperty('tone', 'neutral')
    footer.addWidget(page.import_status_label, 1)
    page.btnNukeAll.setProperty('role', 'primary')
    page.btnNukeAll.setMinimumWidth(180)
    footer.addWidget(page.btnNukeAll)
    root.addLayout(footer)
    # Retained only for compatibility with the shared XML page's signal setup.
    page.btnMakeThumbs.hide()
