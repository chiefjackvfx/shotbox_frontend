import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest

from shotbox_matte.imaging import ImageWindow, prepare_color, prepare_spatial, read_dimensions, read_exr, read_image_window, resize_float, restore_spatial, spatial_plan, write_exr


class ImageTests(unittest.TestCase):
    def test_linear_values_get_display_transfer_and_gamut_clip(self):
        values = np.array([[[0.18, -0.1, 2.0]]], dtype=np.float32)
        result = prepare_color(values, "linear-srgb")
        np.testing.assert_allclose(result, [[[0.46135613, 0.0, 1.0]]], atol=1e-6)
        np.testing.assert_allclose(prepare_color(values, "srgb"), [[[0.18, 0, 1]]])

    def test_acescg_neutral_stays_neutral_under_white_adaptation(self):
        np.testing.assert_allclose(prepare_color(np.full((2, 3, 3), .18, dtype=np.float32), "acescg"),
                                   0.46135613, atol=2e-5)

    def test_fit_pad_crop_and_resize_without_stretch(self):
        plan = spatial_plan(100, 50, 96, 96)
        self.assertEqual((plan.resized_width, plan.resized_height, plan.width, plan.height), (96, 48, 96, 64))
        source = np.zeros((50, 100, 3), dtype=np.float32)
        source[..., 0] = 1.25
        prepared = prepare_spatial(source, plan)
        self.assertEqual(prepared.shape, (64, 96, 3))
        # Deliberately poison spatial padding to prove that it is removed.
        prepared[:plan.top] = -10
        prepared[plan.top + plan.resized_height:] = 10
        result = restore_spatial(prepared, plan)
        self.assertEqual(result.shape, source.shape)
        np.testing.assert_allclose(result, 1.25)

    def test_float_resize_preserves_values_outside_display_range(self):
        source = np.full((2, 2, 3), -0.25, dtype=np.float32)
        np.testing.assert_array_equal(resize_float(source, 8, 8), -0.25)

    @unittest.skipUnless(importlib.util.find_spec("OpenImageIO"), "OpenImageIO is not installed")
    def test_float_exr_roundtrip_preserves_raw_values_and_zip_rgb(self):
        import OpenImageIO as oiio
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matte.exr"
            source = np.repeat(np.array([[[-.25], [1.25], [.123456]]], dtype=np.float32), 3, axis=-1)
            write_exr(path, source, "float")
            np.testing.assert_array_equal(read_exr(path), source)
            reader = oiio.ImageInput.open(str(path))
            try:
                self.assertEqual(list(reader.spec().channelnames), ["R", "G", "B"])
                self.assertEqual(reader.spec().get_string_attribute("compression"), "zip")
            finally:
                reader.close()

    @unittest.skipUnless(importlib.util.find_spec("OpenImageIO"), "OpenImageIO is not installed")
    def test_half_exr_roundtrip_and_overflow_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matte.exr"
            source = np.full((2, 3, 3), 1.25, dtype=np.float32)
            write_exr(path, source, "half")
            np.testing.assert_array_equal(read_exr(path), source)
            with self.assertRaisesRegex(ValueError, "half-float range"):
                write_exr(path, source * 100000, "half")


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("window", [
    ImageWindow(30, 40, 16, 12, 0, 0, 100, 80),
    ImageWindow(-8, -5, 16, 12, 0, 0, 8, 6, 1.25),
    ImageWindow(-30, 40, 16, 12, -50, 20, 100, 80),
])
@pytest.mark.parametrize("precision", ["half", "float"])
def test_real_exr_windows_preserve_crop_overscan_origins_and_pixel_aspect(tmp_path, window, precision):
    pytest.importorskip("OpenImageIO")
    pixels = np.arange(16 * 12 * 3, dtype=np.float32).reshape(12, 16, 3) / 16
    path = tmp_path / "window.exr"
    write_exr(path, pixels, precision, window=window)
    assert read_image_window(path) == window
    assert read_dimensions(path) == (16, 12)
    np.testing.assert_array_equal(read_exr(path), pixels)


def test_exr_refuses_mismatched_raster_and_window_before_writing(tmp_path):
    pytest.importorskip("OpenImageIO")
    path = tmp_path / "wrong.exr"
    with pytest.raises(ValueError, match="data window is 16 x 12"):
        write_exr(path, np.zeros((5, 8, 3), np.float32), window=ImageWindow(30, 40, 16, 12, 0, 0, 100, 80))
    assert not path.exists()


def test_three_dimensional_image_header_is_rejected(monkeypatch):
    from types import SimpleNamespace
    from shotbox_matte import imaging

    reader = SimpleNamespace(spec=lambda: SimpleNamespace(deep=False, depth=2, full_depth=2, z=0, full_z=0), close=lambda: None)
    monkeypatch.setattr(imaging, "_oiio", lambda: SimpleNamespace(ImageInput=SimpleNamespace(open=lambda _path: reader)))
    with pytest.raises(ValueError, match="3D/volume images are unsupported"):
        read_image_window("volume.exr")
    with pytest.raises(ValueError, match="3D/volume images are unsupported"):
        read_exr("volume.exr")
