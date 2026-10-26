"""Real MPEG-2 MXF files through desktop discovery, previews and batch imports."""
from __future__ import annotations

import os
from pathlib import Path
import time

import pytest


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def wait_until(app, condition, timeout=12):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("MXF desktop operation did not finish")
        time.sleep(.005)
    app.processEvents()


@pytest.fixture
def window(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from shotbox_matte.gui import create_window

    instance = create_window(QSettings(str(tmp_path / "mxf-ui.ini"), QSettings.Format.IniFormat))
    instance.show()
    wait_until(app, lambda: not instance._tasks)
    yield instance
    wait_until(app, lambda: not instance._tasks)
    instance.close()
    app.processEvents()


def test_genuine_mxf_single_source_loads_preview_fps_and_ordinal_bounds(app, window, make_mxf, tmp_path):
    av = pytest.importorskip("av")
    from shotbox_matte.media import read_frame

    path = make_mxf(tmp_path / "plate_v001.mxf", count=7, fps=25)
    with av.open(str(path)) as container:
        assert container.format.name == "mxf"
        assert container.streams.video[0].codec_context.name == "mpeg2video"
    window.load_input(str(path))
    wait_until(app, lambda: window._plan is not None and window._source_preview is not None)
    assert window._plan["source_count"] == 7 and window._plan["source_color"] == "srgb"
    assert window._plan["source_range_mode"] == "ordinal"
    assert window._plan["first_frame"] == 1001
    assert window.fps.value() == 25
    assert (window.source_start.value(), window.source_end.value()) == (1, 7)
    assert "MXF" in window.source_info.text() and "25 FPS detected" in window.source_info.text()
    assert window._source_preview[:2] == (720, 576)
    expected = float(read_frame(path, 0)[0, 0, 0]) * 255
    assert abs(window._source_preview[2][0] - expected) <= 1


def test_mxf_qc_seeks_nonzero_source_frame_instead_of_repeating_first_frame(app, window, make_mxf, tmp_path):
    from shotbox_matte.media import read_frame

    path = make_mxf(tmp_path / "seek.MXF", count=7, fps=25)
    window.load_input(str(path))
    wait_until(app, lambda: window._plan is not None and window.qc_preview._pair is not None)
    qc = window.qc_preview
    first_mean = float(qc._pair["source"].mean())
    assert qc.slider.maximum() == 6
    qc.slider.setValue(4)
    wait_until(app, lambda: qc._pair is not None and float(qc._pair["source"].mean()) > first_mean + 40)
    assert "Source frame 5" in qc.frame_label.text()
    expected = float(read_frame(path, 4).mean()) * 255
    assert abs(float(qc._pair["source"].mean()) - expected) <= 1


def test_multiple_mxf_movies_queue_without_a_single_source(app, window, make_mxf, tmp_path):
    paths = [make_mxf(tmp_path / "first.mxf", count=3), make_mxf(tmp_path / "second.MXF", count=4)]
    window.add_paths_to_queue([str(path) for path in paths])
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert window._input_path == "" and window._process is None
    assert window.workspace_tabs.currentIndex() == 1
    assert {Path(job.options["input_path"]).resolve() for job in window.queue.jobs} == {path.resolve() for path in paths}
    assert all(job.status == "pending" for job in window.queue.jobs)
    assert {window.queue_tree.topLevelItem(i).text(1) for i in range(2)} == {"MXF"}
    assert {window.queue_tree.topLevelItem(i).text(2) for i in range(2)} == {"3", "4"}


@pytest.mark.parametrize("area_name", ["drop_area", "batch_drop_area"])
def test_multiple_mxf_urls_are_accepted_and_queued_from_either_drop_area(app, window, make_mxf, tmp_path, area_name):
    from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent

    paths = [make_mxf(tmp_path / "drop-first.mxf", count=2), make_mxf(tmp_path / "drop-second.MXF", count=3)]
    if area_name == "batch_drop_area":
        window.workspace_tabs.setCurrentIndex(1)
    area = getattr(window, area_name)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    area.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    area.dropEvent(drop)
    assert drop.isAccepted()
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert window.workspace_tabs.currentIndex() == 1 and window._process is None
    assert all(job.status == "pending" for job in window.queue.jobs)


def test_single_and_batch_browse_filters_offer_lowercase_and_uppercase_mxf(app, window, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QFileDialog

    single_filters = []
    batch_filters = []

    def rejected(dialog):
        single_filters.extend(dialog.nameFilters())
        return QDialog.DialogCode.Rejected

    def no_files(*args, **kwargs):
        batch_filters.append(args[3] if len(args) > 3 else kwargs["filter"])
        return [], ""

    monkeypatch.setattr(QFileDialog, "exec", rejected)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", no_files)
    window._browse_input()
    window._browse_queue_files()
    assert any("*.mxf" in value and "*.MXF" in value for value in single_filters)
    assert any("*.mxf" in value and "*.MXF" in value for value in batch_filters)
    assert window._input_path == "" and not window.queue.jobs
