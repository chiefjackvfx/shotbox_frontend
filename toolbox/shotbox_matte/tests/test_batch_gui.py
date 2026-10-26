"""Real Qt interactions for importing batches of movies and EXR sequences."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time

import pytest


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def wait_until(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Batch UI did not reach the expected state")
        time.sleep(.005)
    app.processEvents()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from shotbox_matte.gui import create_window

    monkeypatch.setenv("SHOTBOX_JOB_DIR", str(tmp_path / "app-logs" / "jobs"))
    instance = create_window(QSettings(str(tmp_path / "batch.ini"), QSettings.Format.IniFormat))
    instance.show()
    wait_until(app, lambda: not instance._tasks)
    yield instance
    if instance._is_running():
        instance.cancel()
        wait_until(app, lambda: not instance._is_running(), timeout=15)
    wait_until(app, lambda: not instance._tasks)
    instance.close()
    app.processEvents()


@pytest.fixture
def media(tmp_path):
    np = pytest.importorskip("numpy")
    av = pytest.importorskip("av")
    pytest.importorskip("OpenImageIO")
    from shotbox_matte.imaging import write_exr

    parent = tmp_path / "rushes"
    first = parent / "shotA_v001"
    second = parent / "nested" / "shotB_v001"
    for folder, start, count in ((first, 1001, 3), (second, 2001, 2)):
        folder.mkdir(parents=True)
        for number in range(start, start + count):
            write_exr(folder / f"{folder.name}.{number}.exr", np.full((8, 12, 3), .25, np.float32), "half")
        (folder / "notes.txt").write_text("Sidecars are not queue jobs.")
    clips = parent / "clips"
    clips.mkdir()
    movies = []
    for name, count in (("takeA.mov", 4), ("takeB.mp4", 2)):
        path = clips / name
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=24)
            stream.width, stream.height, stream.pix_fmt = 32, 24, "yuv420p"
            for _ in range(count):
                frame = av.VideoFrame.from_ndarray(np.full((24, 32, 3), 80, np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        movies.append(path)
    return {"parent": parent, "first": first, "second": second, "movies": movies}


def queued_sources(window):
    return {Path(job.options["input_path"]).resolve() for job in window.queue.jobs}


def drop_paths(area, paths):
    from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent

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


def test_batch_workspace_has_visible_queue_and_direct_import_controls(app, window):
    tabs = window.workspace_tabs
    assert [tabs.tabText(index) for index in range(tabs.count())] == ["Single shot", "Batch queue"]
    tabs.setCurrentIndex(1)
    app.processEvents()
    assert window.queue_tree.isVisible()
    assert window.batch_drop_area.isVisible()
    assert window.queue_add_files.isVisible() and window.queue_add_folder.isVisible()
    assert window.queue_scan_folder.isVisible()
    # The batch table can use the workspace, without the old 135px hard cap.
    assert window.queue_tree.maximumHeight() > 135
    headers = [window.queue_tree.headerItem().text(index).casefold() for index in range(4)]
    assert "source" in headers[0] or "shot" in headers[0]
    assert "type" in headers[1] and "frame" in headers[2] and "status" in headers[3]
    assert window._input_path == "" and window._process is None


def test_multiple_movies_can_be_added_before_loading_any_single_shot(app, window, media):
    window.add_paths_to_queue([str(path) for path in media["movies"]])
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert window.workspace_tabs.currentIndex() == 1
    assert queued_sources(window) == {path.resolve() for path in media["movies"]}
    assert all(job.status == "pending" for job in window.queue.jobs)
    assert window._input_path == "" and window._plan is None and window._process is None
    assert {window.queue_tree.topLevelItem(i).text(2) for i in range(2)} == {"2", "4"}
    assert all(window.queue_tree.topLevelItem(i).text(1) for i in range(2))


def test_batch_generation_width_matches_each_source_and_captures_height(app, window, tmp_path, monkeypatch):
    pytest.importorskip("OpenImageIO")
    Image = pytest.importorskip("PIL.Image")
    from shotbox_matte import batch

    paths = []
    for name, size in (("landscape", (32, 18)), ("portrait", (18, 32)), ("wide", (64, 27))):
        path = tmp_path / f"{name}.png"
        Image.new("RGB", size).save(path)
        paths.append(str(path))

    original = batch.discover_batch_sources
    entered, release = threading.Event(), threading.Event()

    def blocked(selected_paths):
        entered.set()
        if not release.wait(8):
            raise RuntimeError("Test did not release batch inspection")
        return original(selected_paths)

    monkeypatch.setattr(batch, "discover_batch_sources", blocked)
    window.generation_height.setValue(1088)
    window.add_paths_to_queue(paths)
    wait_until(app, entered.is_set)
    window.generation_height.setValue(320)
    release.set()
    wait_until(app, lambda: len(window.queue.jobs) == 3 and not window._tasks)
    dimensions = {
        Path(job.options["input_path"]).stem: (job.options["generation_width"], job.options["generation_height"])
        for job in window.queue.jobs
    }
    assert dimensions == {"landscape": (1920, 1080), "portrait": (612, 1088), "wide": (1920, 810)}
    assert window.generation_height.value() == 320


@pytest.mark.parametrize("area_name", ["drop_area", "batch_drop_area"])
def test_multiple_url_drop_accepts_movies_and_exr_folders_together(app, window, media, area_name):
    area = getattr(window, area_name)
    paths = [media["first"], media["movies"][0], media["second"]]
    drop_paths(area, paths)
    wait_until(app, lambda: len(window.queue.jobs) == 3 and not window._tasks)
    assert window.workspace_tabs.currentIndex() == 1
    assert window._input_path == "" and window._process is None
    assert all(job.status == "pending" for job in window.queue.jobs)


def test_batch_drop_of_one_exr_folder_queues_it_without_loading_single_shot(app, window, media):
    window.workspace_tabs.setCurrentIndex(1)
    drop_paths(window.batch_drop_area, [media["first"]])
    wait_until(app, lambda: len(window.queue.jobs) == 1 and not window._tasks)
    assert window._input_path == "" and window.queue.jobs[0].status == "pending"


def test_multi_directory_picker_accepts_every_selected_exr_folder(app, window, media, monkeypatch):
    from PyQt6.QtCore import QItemSelectionModel
    from PyQt6.QtWidgets import QAbstractItemView, QDialog, QFileDialog, QListView, QTreeView

    dialogs = []

    def accepted(dialog):
        dialogs.append(dialog)
        assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        assert dialog.fileMode() == QFileDialog.FileMode.Directory
        file_views = [view for cls in (QListView, QTreeView) for view in dialog.findChildren(cls)
                      if view.objectName() in {"listView", "treeView"}]
        assert file_views
        assert all(view.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection for view in file_views)
        # Exercise Qt's actual filesystem model and selectedFiles implementation.
        # The two sequence folders have different parents, so select the first
        # folder and its sibling nested directory, which contains the second.
        dialog.setDirectory(str(media["parent"]))
        dialog.show()
        view = dialog.findChild(QListView, "listView")
        model = view.model()
        selected = [media["first"], media["second"].parent]
        wait_until(app, lambda: all(model.index(str(path)).isValid() for path in selected))
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        view.selectionModel().clearSelection()
        for path in selected:
            view.selectionModel().select(model.index(str(path)), flags)
        app.processEvents()
        assert set(dialog.selectedFiles()) == {str(path) for path in selected}
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QFileDialog, "exec", accepted)
    window._browse_queue_folders()
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert len(dialogs) == 1 and window.workspace_tabs.currentIndex() == 1
    assert window._process is None


def test_footer_starts_batch_without_a_single_source_even_after_settings_change(app, window, media, monkeypatch):
    calls = []

    def started(options):
        calls.append(options)
        return True

    monkeypatch.setattr(window, "_start_job", started)
    window.add_paths_to_queue([str(path) for path in media["movies"]])
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert window._input_path == "" and window._plan is None
    window.frame_cap.setValue(1)
    window.seed.setValue(99)
    app.processEvents()
    assert window.generate_button.text() == "Start batch"
    assert window.generate_button.isEnabled()
    window.generate_button.click()
    assert len(calls) == 1
    assert calls[0]["input_path"] == str(media["movies"][0])
    assert calls[0]["frame_cap"] == 0 and calls[0]["seed"] == 42
    assert "prompt" not in calls[0]
    assert window.queue.jobs[0].status == "running"
    assert window.queue.jobs[1].status == "pending"


def test_multi_file_picker_passes_all_selected_movies_to_queue(app, window, media, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    calls = []

    def files(*args, **kwargs):
        calls.append((args, kwargs))
        return [str(path) for path in media["movies"]], "MOV / MP4"

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", files)
    window.queue_add_files.click()
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert len(calls) == 1 and window._process is None


def test_parent_scan_adds_nested_exr_sequences_and_movies_once(app, window, media, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(media["parent"]))
    window.queue_scan_folder.click()
    wait_until(app, lambda: len(window.queue.jobs) == 4 and not window._tasks)
    assert window.queue_tree.topLevelItemCount() == 4
    assert sorted(int(window.queue_tree.topLevelItem(i).text(2)) for i in range(4)) == [2, 2, 3, 4]
    assert window.workspace_tabs.currentIndex() == 1 and window._process is None


def test_import_captures_settings_when_clicked_not_when_discovery_completes(app, window, media, monkeypatch):
    from shotbox_matte import batch

    original = batch.discover_batch_sources
    entered, release = threading.Event(), threading.Event()

    def blocked(paths):
        entered.set()
        if not release.wait(8):
            raise RuntimeError("Test did not release batch inspection")
        return original(paths)

    monkeypatch.setattr(batch, "discover_batch_sources", blocked)
    window.seed.setValue(17)
    window.frame_cap.setValue(2)
    window.add_paths_to_queue([str(media["first"]), str(media["second"])])
    wait_until(app, entered.is_set)
    window.seed.setValue(99)
    window.frame_cap.setValue(1)
    release.set()
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert all(job.options["seed"] == 17 for job in window.queue.jobs)
    assert all("prompt" not in job.options for job in window.queue.jobs)
    assert all(job.options["frame_cap"] == 2 for job in window.queue.jobs)
    assert window._input_path == "" and window._process is None


def test_readding_pending_or_active_sources_deduplicates_but_finished_can_be_requeued(app, window, media):
    path = str(media["movies"][0])
    window.add_paths_to_queue([path])
    wait_until(app, lambda: len(window.queue.jobs) == 1 and not window._tasks)
    window.add_paths_to_queue([path, path])
    wait_until(app, lambda: not window._tasks)
    assert len(window.queue.jobs) == 1
    window.queue.jobs[0].status = "running"
    window.add_paths_to_queue([path])
    wait_until(app, lambda: not window._tasks)
    assert len(window.queue.jobs) == 1
    window.queue.jobs[0].status = "complete"
    window.add_paths_to_queue([path])
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert [job.status for job in window.queue.jobs] == ["complete", "pending"]


def test_selecting_all_exr_frames_and_their_folder_adds_one_job_per_sequence(app, window, media):
    frames = sorted(media["first"].glob("*.exr"))
    window.add_paths_to_queue([str(path) for path in [*frames, media["first"], media["second"]]])
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert window.queue_tree.topLevelItemCount() == 2
    assert sorted(int(window.queue_tree.topLevelItem(i).text(2)) for i in range(2)) == [2, 3]


def test_invalid_source_does_not_discard_valid_peers_or_start_workers(app, window, media):
    broken = media["parent"] / "broken.mov"
    broken.write_bytes(b"not a valid movie")
    missing = media["parent"] / "missing"
    window.add_paths_to_queue([str(media["first"]), str(broken), str(missing), str(media["movies"][0])])
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert all(job.status == "pending" for job in window.queue.jobs)
    assert window._process is None
    log = window.log.toPlainText()
    assert "broken.mov" in log and "missing" in log


@pytest.mark.parametrize("area_name", ["drop_area", "batch_drop_area"])
def test_mixed_drag_preserves_valid_movie_and_sequence_when_sidecar_is_selected(app, window, media, area_name):
    paths = [media["movies"][0], media["first"], media["first"] / "notes.txt"]
    drop_paths(getattr(window, area_name), paths)
    wait_until(app, lambda: len(window.queue.jobs) == 2 and not window._tasks)
    assert all(job.status == "pending" for job in window.queue.jobs)
    assert window.workspace_tabs.currentIndex() == 1 and window._process is None
    assert "notes.txt" in window.log.toPlainText()
    assert "error" in window.queue_note.text().casefold()


def test_remote_url_rejects_entire_drag_even_when_a_local_movie_is_included(app, window, media):
    from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(media["movies"][0])), QUrl("https://example.invalid/movie.mov")])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.batch_drop_area.dragEnterEvent(enter)
    assert not enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.batch_drop_area.dropEvent(drop)
    assert not drop.isAccepted()
    app.processEvents()
    assert window.queue.jobs == [] and window._input_path == "" and window._process is None
