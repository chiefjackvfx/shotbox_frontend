import contextlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from shotbox_matte.media import (
    IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS, default_source_color,
    probe_video, read_frame, read_image, read_source_frames,
)
from shotbox_matte.sequences import discover_sequences, select_frame_indices


HAS_AV = importlib.util.find_spec("av") is not None
HAS_OIIO = importlib.util.find_spec("OpenImageIO") is not None


def make_video(path: Path, count=7, codec="libx264", pixel_format="yuv420p", high_depth=False):
    import av
    with av.open(str(path), "w") as container:
        stream = container.add_stream(codec, rate=25)
        stream.width, stream.height, stream.pix_fmt = 16, 16, pixel_format
        for index in range(count):
            if high_depth:
                pixels = np.full((16, 16, 3), 2000 + index * 9000, dtype=np.uint16)
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb48le")
            else:
                pixels = np.full((16, 16, 3), 20 + index * 25, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class MediaPolicyTests(unittest.TestCase):
    def test_explicit_allowlist_and_color_defaults(self):
        self.assertEqual(VIDEO_EXTENSIONS, {".mov", ".mp4", ".mxf"})
        self.assertEqual(IMAGE_EXTENSIONS, {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dpx", ".bmp"})
        for suffix in (".gif", ".avi", ".webm", ".mkv", ".txt"):
            self.assertNotIn(suffix, SUPPORTED_EXTENSIONS)
            with self.assertRaisesRegex(ValueError, "unsupported"):
                read_frame(Path("source" + suffix))
        self.assertEqual(default_source_color("source.EXR"), "linear-srgb")
        self.assertEqual(default_source_color("source.mov"), "srgb")
        self.assertEqual(default_source_color("source.MXF"), "srgb")
        self.assertEqual(default_source_color("source.PNG"), "srgb")

    def test_probe_counts_decode_when_metadata_count_is_unknown(self):
        stream = SimpleNamespace(codec_context=SimpleNamespace(width=0, height=0),
                                 frames=0, average_rate=None, base_rate=None)
        container = SimpleNamespace(streams=SimpleNamespace(video=[stream]),
                                    decode=lambda _stream: iter([SimpleNamespace(width=48, height=32)] * 5))
        fake_av = SimpleNamespace(open=lambda _path: contextlib.nullcontext(container))
        with patch("shotbox_matte.media._pyav", return_value=fake_av):
            info = probe_video("input.mov")
        self.assertEqual((info.width, info.height, info.count, info.fps), (48, 32, 5, None))

    def test_audio_only_movie_fails_clearly(self):
        container = SimpleNamespace(streams=SimpleNamespace(video=[]))
        fake_av = SimpleNamespace(open=lambda _path: contextlib.nullcontext(container))
        with patch("shotbox_matte.media._pyav", return_value=fake_av):
            with self.assertRaisesRegex(ValueError, "no video stream"):
                probe_video("input.mp4")

    @unittest.skipUnless(HAS_OIIO, "OpenImageIO is not installed")
    def test_common_images_use_float_rgb_and_natural_selection(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for number in (10, 1, 2):
                Image.fromarray(np.full((8, 12, 3), number, dtype=np.uint8)).save(folder / f"plate.{number}.png")
            sequence = discover_sequences(folder)[0]
            self.assertEqual(sequence.frame_numbers, (1, 2, 10))
            frames = list(read_source_frames(sequence, select_frame_indices(sequence.count, 1)))
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[0].dtype, np.float32)
            np.testing.assert_allclose(frames[0], 1 / 255, atol=1e-7)
            np.testing.assert_allclose(frames[1], 10 / 255, atol=1e-7)
            np.testing.assert_array_equal(read_frame(sequence.files[0]), read_image(sequence.files[0]))


@unittest.skipUnless(HAS_AV, "PyAV is not installed")
class VideoTests(unittest.TestCase):
    def test_mp4_probe_skip_cap_and_video_output_stem(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shot_v003.mp4"
            make_video(path)
            info = probe_video(path)
            self.assertEqual((info.width, info.height, info.count, info.fps), (16, 16, 7, 25.0))
            sequence = discover_sequences(path)[0]
            self.assertEqual(sequence.media_type, "video")
            self.assertEqual(sequence.video_path, path)
            self.assertEqual(sequence.output_source, path.with_suffix(""))
            self.assertEqual(sequence.first_frame, 1001)
            self.assertEqual(len(sequence.files), 7)
            self.assertEqual(sequence.frame_numbers, (None,) * 7)
            indices = select_frame_indices(sequence.count, 2, 2)
            self.assertEqual(indices, (0, 3))
            frames = list(read_source_frames(sequence, indices))
            self.assertEqual(len(frames), 2)
            np.testing.assert_array_equal(frames[0], read_frame(path, 0))
            np.testing.assert_array_equal(frames[1], read_frame(path, 3))
            self.assertGreater(float(frames[1].mean()), float(frames[0].mean()))
            self.assertEqual(sequence.to_dict()["fps"], 25.0)

    def test_mov_preserves_sixteen_bit_decode_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plate.mov"
            make_video(path, count=4, codec="png", pixel_format="rgb48be", high_depth=True)
            result = read_frame(path, 2)
            self.assertEqual(result.dtype, np.float32)
            np.testing.assert_allclose(result, 20000 / 65535, atol=1e-7)
            self.assertGreater(abs(float(result[0, 0, 0]) * 255 - round(float(result[0, 0, 0]) * 255)), .01)

    def test_missing_frame_and_invalid_indices_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plate.mov"
            make_video(path, count=2, codec="png", pixel_format="rgb24")
            with self.assertRaisesRegex(ValueError, "ended before requested frame"):
                read_frame(path, 3)
            sequence = discover_sequences(path)[0]
            for indices in ((-1,), (1, 0), (0, 0), (2,)):
                with self.assertRaises(ValueError):
                    list(read_source_frames(sequence, indices))

    def test_video_loading_obeys_cancellation(self):
        from shotbox_matte.core import CancelledError
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plate.mp4"
            make_video(path)
            sequence = discover_sequences(path)[0]
            state = {"cancelled": False}
            frames = read_source_frames(sequence, (0, 1, 2), lambda: state["cancelled"])
            next(frames)
            state["cancelled"] = True
            with self.assertRaises(CancelledError):
                next(frames)


if __name__ == "__main__":
    unittest.main()
