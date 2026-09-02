from __future__ import annotations

import sys
from pathlib import Path
import unittest


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from colourspace_defaults import COLOURSPACE_LIST, DEFAULT_COLOURSPACE


class ColourspaceDefaultsTests(unittest.TestCase):
    def test_presets_are_immutable_ordered_and_unique(self):
        self.assertIsInstance(COLOURSPACE_LIST, tuple)
        self.assertEqual(len(COLOURSPACE_LIST), 9)
        self.assertEqual(len(COLOURSPACE_LIST), len(set(COLOURSPACE_LIST)))

    def test_default_is_a_known_preset(self):
        self.assertIn(DEFAULT_COLOURSPACE, COLOURSPACE_LIST)


if __name__ == "__main__":
    unittest.main()
