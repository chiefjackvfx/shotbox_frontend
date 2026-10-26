import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shotbox_matte.sequences import SequenceInfo, discover_sequences, select_frame_indices, select_frames, select_sequence_indices


class SequenceTests(unittest.TestCase):
    def test_natural_order_mixed_groups_and_file_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("beauty.10.exr", "beauty.2.exr", "beauty.1.exr", "depth.1.exr", "still.exr", "note.txt"):
                (folder / name).touch()
            with patch("shotbox_matte.imaging.read_dimensions", return_value=(1920, 1080)):
                sequences = discover_sequences(folder)
                self.assertEqual([s.id for s in sequences], ["beauty.#.exr", "depth.#.exr", "still.exr"])
                self.assertEqual(sequences[0].frame_numbers, (1, 2, 10))
                self.assertEqual(discover_sequences(folder / "beauty.2.exr")[0].files, sequences[0].files)
                self.assertEqual(sequences[-1].first_frame, 1001)

    def test_padding_variation_does_not_split_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "a.9.exr").touch()
            (folder / "a.0010.exr").touch()
            with patch("shotbox_matte.imaging.read_dimensions", return_value=(64, 64)):
                result = discover_sequences(folder)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].frame_numbers, (9, 10))

    def test_duplicate_frame_numbers_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "a.1.exr").touch()
            (folder / "a.0001.exr").touch()
            with self.assertRaisesRegex(ValueError, "duplicate frame"):
                discover_sequences(folder)

    def test_skip_then_cap(self):
        sequence = SequenceInfo("x", Path("."), tuple(Path(f"{i}.exr") for i in range(10)), tuple(range(1001, 1011)), 64, 64)
        paths, numbers = select_frames(sequence, frame_skip=2, frame_cap=3)
        self.assertEqual(numbers, (1001, 1004, 1007))
        self.assertEqual([p.name for p in paths], ["0.exr", "3.exr", "6.exr"])
        with self.assertRaises(ValueError):
            select_frames(sequence, -1)
        self.assertEqual(select_frame_indices(10, 2, 3), (0, 3, 6))

    def test_inclusive_source_numbers_then_skip_then_cap(self):
        sequence = SequenceInfo("x", Path("."), tuple(Path(f"{i}.exr") for i in range(10)), tuple(range(1001, 1011)), 64, 64)
        self.assertEqual(select_sequence_indices(sequence, 1, 3, 1003, 1009), (2, 4, 6))
        self.assertEqual(select_frames(sequence, 1, 3, 1003, 1009)[1], (1003, 1005, 1007))
        self.assertEqual(select_sequence_indices(sequence, source_end=1002), (0, 1))
        self.assertEqual(select_sequence_indices(sequence, source_start=1010), (9,))
        self.assertEqual(sequence.to_dict()["source_range_mode"], "frame-number")
        self.assertEqual((sequence.source_start, sequence.source_end), (1001, 1010))

    def test_range_gaps_and_invalid_empty_ranges(self):
        sequence = SequenceInfo("x", Path("."), tuple(Path(f"{i}.exr") for i in range(4)), (1001, 1003, 1005, 1007), 64, 64)
        self.assertEqual(select_sequence_indices(sequence, source_start=1002, source_end=1006), (1, 2))
        for start, end, message in ((1004, 1003, "must not exceed"), (1000, 1003, "outside"),
                                    (1003, 1008, "outside"), (1002, 1002, "No source frames"),
                                    (1.5, None, "integer"), (True, None, "integer"), (None, "1003", "integer")):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, message):
                select_sequence_indices(sequence, source_start=start, source_end=end)

    def test_movies_and_unnumbered_images_use_one_based_positions(self):
        sequence = SequenceInfo("x.mov", Path("."), (Path("x.mov"),) * 7, (None,) * 7, 64, 64, "video")
        self.assertEqual(sequence.source_range_mode, "ordinal")
        self.assertEqual((sequence.source_start, sequence.source_end), (1, 7))
        self.assertEqual(select_sequence_indices(sequence, 1, 2, 2, 7), (1, 3))
        with self.assertRaisesRegex(ValueError, "1-based frame positions"):
            select_sequence_indices(sequence, source_start=0)
        still = SequenceInfo("still.exr", Path("."), (Path("still.exr"),), (None,), 64, 64)
        self.assertEqual(select_sequence_indices(still, source_start=1, source_end=1), (0,))
        self.assertEqual((still.source_start, still.source_end), (1, 1))

    def test_mixed_images_and_movies_exclude_other_formats(self):
        from shotbox_matte.media import VideoInfo
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("beauty.2.png", "beauty.1.png", "beauty.1.exr", "shot_v002.mov", "source.mp4", "ignored.avi", "ignored.gif"):
                (folder / name).touch()
            with (patch("shotbox_matte.imaging.read_dimensions", return_value=(64, 48)),
                  patch("shotbox_matte.media.probe_video", return_value=VideoInfo(96, 64, 9, 24.0))):
                sequences = discover_sequences(folder)
                self.assertEqual([s.id for s in sequences], ["beauty.#.exr", "beauty.#.png", "shot_v002.mov", "source.mp4"])
                self.assertEqual(sequences[1].frame_numbers, (1, 2))
                video = sequences[2]
                self.assertEqual(video.output_source, folder / "shot_v002")
                self.assertEqual(video.frame_indices, tuple(range(9)))
                self.assertEqual(discover_sequences(folder / "beauty.2.png")[0].id, "beauty.#.png")
                self.assertEqual(discover_sequences(folder / "shot_v002.mov")[0].id, "shot_v002.mov")

    def test_unsupported_selected_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.avi"
            path.touch()
            with self.assertRaisesRegex(ValueError, "unsupported"):
                discover_sequences(path)


if __name__ == "__main__":
    unittest.main()
