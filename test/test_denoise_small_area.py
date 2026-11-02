"""Only the qualified Neat small-area confirmation may be accepted."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import app_cache
from nuke_denoise_display import PrivateDisplay, DenoiseCancelled


class SmallAreaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp'))
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.display = PrivateDisplay(self.directory, lambda: False, tools={'unused': True})
        self.display._load_templates()
        self.display.clicked_auto = True
        self.window = mock.Mock(id=42)
        self.window.get_wm_transient_for.return_value.get_wm_name.return_value = 'Neat Video 6.1.2 Studio'
        self.pixels = np.full((194, 456, 3), 71, dtype=np.uint8)
        for name, x, y in [('small_area_warning', 78, 10), ('use_small_area', 199, 155)]:
            template = self.display.templates[name]
            h, w = template.shape[:2]
            self.pixels[y:y+h, x:x+w] = template
        self.display.screenshot = mock.Mock(side_effect=lambda _: self.pixels)
        self.display._click = mock.Mock()

    def test_accepts_verified_warning_once_without_confirming_profile(self):
        with mock.patch('nuke_denoise_display.time.monotonic', return_value=10) as clock:
            self.assertFalse(self.display._prepare_dialog(self.window, 'Confirm'))
            self.assertFalse(self.display._prepare_dialog(self.window, 'Confirm'))
            self.display._click.assert_called_once_with(self.window, (272, 168))
            clock.return_value = 13
            self.assertTrue(self.display._prepare_dialog(self.window, 'Confirm'))
        self.assertFalse(self.display.applied)
        self.assertFalse((self.directory / 'profile-confirmed').exists())
        self.assertTrue((self.directory / 'small-area-confirmation.png').exists())

    def test_requires_both_body_and_button(self):
        for region in [(slice(10, 72), slice(78, 445)), (slice(155, 182), slice(199, 345))]:
            with self.subTest(region=region):
                pixels = self.pixels.copy()
                pixels[region] = 0
                self.assertIsNone(self.display._small_area_button(pixels))
        self.assertIsNone(self.display._small_area_button(np.zeros((5, 5, 3), dtype=np.uint8)))

    def test_unrelated_title_owner_or_profiling_stage_is_not_accepted(self):
        self.assertTrue(self.display._prepare_dialog(self.window, 'Warning'))
        self.display.clicked_auto = False
        self.assertTrue(self.display._prepare_dialog(self.window, 'Confirm'))
        self.display.clicked_auto = True
        self.window.get_wm_transient_for.return_value.get_wm_name.return_value = 'Nuke'
        self.assertTrue(self.display._prepare_dialog(self.window, 'Confirm'))
        self.display._click.assert_not_called()

    def test_cancellation_prevents_click(self):
        self.display.cancelled = lambda: True
        with self.assertRaises(DenoiseCancelled):
            self.display._prepare_dialog(self.window, 'Confirm')
        self.display._click.assert_not_called()

    def test_small_area_ready_variant_requires_acknowledgement_and_adjust_ready(self):
        pixels = np.zeros((130, 500, 3), dtype=np.uint8)
        for name, x, y in [('build_profile_small_area', 44, 66), ('adjust_ready', 231, 31)]:
            template = self.display.templates[name]
            h, w = template.shape[:2]
            pixels[y:y+h, x:x+w] = template
        self.assertFalse(self.display._profile_is_ready(pixels))
        self.display.small_area_acknowledged = (42, 10)
        self.assertTrue(self.display._profile_is_ready(pixels))
        pixels[31:48, 231:372] = 0
        self.assertFalse(self.display._profile_is_ready(pixels))
