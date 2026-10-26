"""Check native Qt logging defaults in fresh processes, before Qt initializes."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("rules,metadata_enabled,other_enabled", [
    (None, False, True),
    ("", False, True),
    ("shotbox.other.info=false", False, False),
    ("qt.multimedia.ffmpeg.mediadataholder.info=true", True, True),
    ("qt.multimedia.ffmpeg.mediadataholder.info=true;shotbox.other.info=false", True, False),
])
def test_metadata_dump_is_opt_in_without_hiding_errors(rules, metadata_enabled, other_enabled):
    script = """
import importlib.abc
import json
import runpy
import sys

class BeforeQt(Exception):
    pass
class StopBeforeQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PyQt6':
            raise BeforeQt()
finder = StopBeforeQt()
sys.meta_path.insert(0, finder)
try:
    runpy.run_path('main.py', run_name='imported_main')
except BeforeQt:
    pass
else:
    raise AssertionError('Qt imports were not reached')
finally:
    sys.meta_path.remove(finder)

from PyQt6.QtCore import QLoggingCategory
metadata = QLoggingCategory('qt.multimedia.ffmpeg.mediadataholder')
other = QLoggingCategory('shotbox.other')
print(json.dumps([metadata.isInfoEnabled(), metadata.isWarningEnabled(),
                  metadata.isCriticalEnabled(), other.isInfoEnabled()]))
"""
    env = os.environ.copy()
    env.pop("QT_LOGGING_RULES", None)
    if rules is not None:
        env["QT_LOGGING_RULES"] = rules
    result = subprocess.run([sys.executable, "-B", "-c", script], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [metadata_enabled, True, True, other_enabled]
