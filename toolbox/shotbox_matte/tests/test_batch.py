from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from shotbox_matte.batch import BatchDiscovery, discover_batch_sources
from shotbox_matte.imaging import write_exr


@pytest.fixture
def image_sequence():
    pytest.importorskip("OpenImageIO")

    def create(folder: Path, stem="plate", suffix=".exr", numbers=(1001, 1002)):
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for number in numbers:
            path = folder / f"{stem}.{number}{suffix}"
            if suffix.casefold() == ".exr":
                write_exr(path, np.full((8, 12, 3), .18, np.float32))
            else:
                from PIL import Image

                Image.fromarray(np.full((8, 12, 3), 100, np.uint8)).save(path)
            files.append(path)
        return files

    return create


@pytest.fixture
def movie():
    av = pytest.importorskip("av")

    def create(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(path), "w") as container:
            stream = container.add_stream("png" if path.suffix.casefold() == ".mov" else "libx264", rate=24)
            stream.width, stream.height = 16, 16
            stream.pix_fmt = "rgb24" if path.suffix.casefold() == ".mov" else "yuv420p"
            for number in range(3):
                frame = av.VideoFrame.from_ndarray(np.full((16, 16, 3), number * 80, np.uint8), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return path

    return create


def test_multiple_movies_and_nested_exr_folders_are_distinct_sources(tmp_path, image_sequence, movie):
    first = image_sequence(tmp_path / "parent" / "shot2_v001")
    second = image_sequence(tmp_path / "parent" / "nested" / "shot10_v001")
    pngs = image_sequence(tmp_path / "parent" / "nested" / "shot3_v001", suffix=".png")
    mov = movie(tmp_path / "movies" / "clip2.mov")
    mp4 = movie(tmp_path / "movies" / "clip10.mp4")
    result = discover_batch_sources([mov, mp4, tmp_path / "parent"])
    assert isinstance(result, BatchDiscovery)
    assert not result.errors and result.duplicate_count == 0
    assert [source.video_path or source.files[0] for source in result.sequences] == [mov, mp4, pngs[0], second[0], first[0]]
    assert [source.media_type for source in result.sequences] == ["video", "video", "sequence", "sequence", "sequence"]
    assert [source.count for source in result.sequences] == [3, 3, 2, 2, 2]


def test_many_frames_overlapping_folders_and_repeated_movies_deduplicate(tmp_path, image_sequence, movie):
    frames = image_sequence(tmp_path / "parent" / "shot_v001", numbers=(1001, 1002, 1003))
    mov = movie(tmp_path / "parent" / "clip.mov")
    selected = [frames[1], frames[2], tmp_path / "parent", frames[0].parent, mov, mov]
    result = discover_batch_sources(selected)
    assert not result.errors
    assert [source.video_path or source.files[0] for source in result.sequences] == [frames[0], mov]
    # Repeated explicit frames/overlapping scans each represent one repeated
    # source; the three frame files in a single folder scan are grouped once.
    assert result.duplicate_count == 5
    assert discover_batch_sources([frames[0].parent]).duplicate_count == 0


def test_corrupt_sources_do_not_discard_readable_peers(tmp_path, image_sequence, movie):
    png = image_sequence(tmp_path, "good", ".png")
    exr = image_sequence(tmp_path, "other", ".exr")
    mov = movie(tmp_path / "valid.mov")
    (tmp_path / "bad.1001.exr").write_bytes(b"damaged EXR")
    (tmp_path / "broken.mp4").write_bytes(b"damaged MP4")
    (tmp_path / "ignored.avi").write_bytes(b"unsupported")
    result = discover_batch_sources([tmp_path])
    assert [source.video_path or source.files[0] for source in result.sequences] == [png[0], exr[0], mov]
    assert len(result.errors) == 2
    assert "bad.1001.exr" in result.errors[0] and "broken.mp4" in result.errors[1]
    assert not any("ignored.avi" in error for error in result.errors)


def test_duplicate_frame_number_error_does_not_hide_other_sequence(tmp_path, image_sequence):
    image_sequence(tmp_path, "ambiguous", numbers=(1,))
    write_exr(tmp_path / "ambiguous.0001.exr", np.full((8, 12, 3), .18, np.float32))
    good = image_sequence(tmp_path, "good", suffix=".png")
    result = discover_batch_sources([tmp_path])
    assert [source.files[0] for source in result.sequences] == [good[0]]
    assert len(result.errors) == 1 and "duplicate frame numbers" in result.errors[0]


def test_missing_unsupported_and_empty_selection_preserve_other_sources(tmp_path, image_sequence):
    frames = image_sequence(tmp_path / "shot")
    empty = tmp_path / "empty"
    empty.mkdir()
    unsupported = tmp_path / "ignored.gif"
    unsupported.write_bytes(b"unsupported")
    result = discover_batch_sources([tmp_path / "missing", frames[0], unsupported, empty])
    assert [source.files[0] for source in result.sequences] == [frames[0]]
    assert len(result.errors) == 3
    assert "does not exist" in result.errors[0]
    assert "Unsupported input" in result.errors[1]
    assert "No supported" in result.errors[2]
    assert discover_batch_sources([]) == BatchDiscovery([], [], 0)


def test_directory_links_hidden_folders_and_staging_are_not_followed(tmp_path, image_sequence):
    parent = tmp_path / "parent"
    visible = image_sequence(parent / "visible")
    image_sequence(parent / ".hidden")
    image_sequence(parent / ".shotbox-partial-test")
    outside = image_sequence(tmp_path / "outside")
    link = parent / "linked"
    try:
        link.symlink_to(outside[0].parent, target_is_directory=True)
        (parent / "cycle").symlink_to(parent, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    result = discover_batch_sources([parent])
    assert [source.files[0] for source in result.sequences] == [visible[0]]
    assert not result.errors
    explicit = discover_batch_sources([link, parent / ".hidden", visible[0]])
    assert [source.files[0] for source in explicit.sequences] == [visible[0]]
    assert len(explicit.errors) == 2
    assert "symlinks are not followed" in explicit.errors[0]
    assert "Hidden folders are skipped" in explicit.errors[1]


def test_resolved_file_alias_deduplicates_existing_source(tmp_path, image_sequence):
    frames = image_sequence(tmp_path / "shot")
    link = tmp_path / "alias.exr"
    try:
        link.symlink_to(frames[1])
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"File symlinks unavailable: {exc}")
    result = discover_batch_sources([link, frames[0]])
    assert [source.files[0] for source in result.sequences] == [frames[0]]
    assert result.duplicate_count == 1 and not result.errors


def test_top_level_selection_order_is_stable_and_inner_names_sort_naturally(tmp_path, image_sequence):
    earlier = image_sequence(tmp_path / "z_selected_first", suffix=".png")
    later2 = image_sequence(tmp_path / "a_selected_second" / "shot2")
    later10 = image_sequence(tmp_path / "a_selected_second" / "shot10")
    selected = [earlier[0].parent, later2[0].parent.parent]
    expected = [earlier[0], later2[0], later10[0]]
    for _ in range(2):
        result = discover_batch_sources(path for path in selected)
        assert [source.files[0] for source in result.sequences] == expected
        assert not result.errors
    assert len(discover_batch_sources(str(earlier[0].parent)).sequences) == 1
    assert len(discover_batch_sources(earlier[0]).sequences) == 1


def test_batch_discovery_import_is_headless():
    script = """
import importlib.abc, sys
class NoHeavyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PyQt', 'torch', 'ltx_', 'shotbox_matte.gui')):
            raise AssertionError('Batch discovery loaded UI or inference: ' + fullname)
sys.meta_path.insert(0, NoHeavyImports())
from shotbox_matte.batch import discover_batch_sources
assert not discover_batch_sources([]).sequences
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
