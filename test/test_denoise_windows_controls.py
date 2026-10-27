"""Replay captured Windows controls, including remote-viewer resampling."""
from pathlib import Path
import unittest

import cv2
import numpy as np

from nuke_denoise_controls import WindowsControlMatcher


FIXTURES = Path(__file__).parent / "fixtures/denoise_windows"
CONTROLS = ("auto_profile", "build_profile", "adjust_ready", "apply",
            "profile_check", "profile_check_active", "profile_check_preview")
EXPECTED = ({"auto_profile"}, {"build_profile", "adjust_ready", "apply", "profile_check"},
            {"build_profile", "adjust_ready", "apply", "profile_check_active", "profile_check_preview"})


class WindowsControlRecognitionTests(unittest.TestCase):
    def test_real_captured_states_at_original_and_resampled_scales(self):
        for scale in (1, .8, .93, 1.25, 1.5):
            matcher = WindowsControlMatcher()
            for stage, expected in enumerate(EXPECTED, 1):
                with self.subTest(scale=scale, stage=stage):
                    pixels = cv2.imread(str(FIXTURES / f"stage_{stage}.png"))
                    pixels = cv2.resize(pixels, None, fx=scale, fy=scale,
                                        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                    actual = {name: matcher.match(pixels, name) for name in CONTROLS}
                    self.assertEqual({name for name, point in actual.items() if point}, expected, actual)
                    self.assertAlmostEqual(matcher.scale, scale, delta=.02)
                    # Selected locations must remain within the intended controls.
                    for name, point in actual.items():
                        if point:
                            x, y = point
                            if name == "apply":
                                self.assertTrue(1213*scale < x < 1323*scale)
                                self.assertTrue(1201*scale < y < 1229*scale)
                            elif name in ("auto_profile", "build_profile"):
                                self.assertTrue(10*scale < x < 139*scale)
                                self.assertTrue(76*scale < y < 105*scale)

    def test_disabled_apply_blocks_an_otherwise_finished_profile(self):
        matcher = WindowsControlMatcher()
        initial = cv2.imread(str(FIXTURES / "stage_1.png"))
        self.assertIsNotNone(matcher.match(initial, "auto_profile"))
        checked = cv2.imread(str(FIXTURES / "stage_3.png"))
        checked[-48:, -310:] = initial[-48:, -310:]
        self.assertIsNotNone(matcher.match(checked, "profile_check_active"))
        self.assertIsNotNone(matcher.match(checked, "profile_check_preview"))
        self.assertIsNone(matcher.match(checked, "apply"))

    def test_assist_image_matches_over_different_footage_background(self):
        matcher = WindowsControlMatcher()
        initial = cv2.imread(str(FIXTURES / "stage_1.png"))
        matcher.match(initial, "auto_profile")
        checked = cv2.imread(str(FIXTURES / "stage_3.png"))
        # Change only the non-lettering pixels, retaining the actual text and outline.
        source = matcher.templates["profile_check_preview"]
        opaque = np.all(source >= 220, axis=2) | np.all(source <= 40, axis=2)
        patch = checked[118:135, 890:995]
        patch[~opaque] = (30, 120, 200)
        self.assertIsNotNone(matcher.match(checked, "profile_check_preview"))

    def test_blank_window_and_controls_in_wrong_regions_are_rejected(self):
        matcher = WindowsControlMatcher()
        matcher.scale = 1
        pixels = np.zeros((1240, 1374, 3), dtype=np.uint8)
        self.assertTrue(all(matcher.match(pixels, name) is None for name in CONTROLS))
        pixels = pixels.copy()
        template = matcher.templates["apply"]
        h, w = template.shape[:2]
        pixels[20:20+h, 30:30+w] = template
        self.assertIsNone(matcher.match(pixels, "apply"))


if __name__ == "__main__":
    unittest.main()
