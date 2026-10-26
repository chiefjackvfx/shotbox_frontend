"""Fresh interpreter checks for the Nuke/backend runtime boundary."""
from pathlib import Path
import subprocess
import sys


PLUGIN = Path(__file__).resolve().parents[3] / "plugins" / "nuke"


def run_isolated(code):
    result = subprocess.run([sys.executable, "-I", "-c", code, str(PLUGIN)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


GUARD = """
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class RejectRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'shotbox_matte', 'torch', 'numpy', 'OpenImageIO', 'ltx_core',
                                      'ltx_pipelines', 'PyQt6', 'PySide6', 'PySide2'}:
            raise AssertionError('Forbidden host import: ' + fullname)
sys.meta_path.insert(0, RejectRuntime())
"""


def test_all_host_modules_import_without_nuke_qt_or_backend():
    run_isolated(GUARD + """
import importlib
for module in ('protocol', 'cache', 'state', 'freshness', 'lifecycle', 'render', 'node',
               'preferences', 'process', 'controller', 'capture', 'precomp', 'precomp_paths', 'naming', 'readback', 'ui', 'qt_compat'):
    importlib.import_module('shotbox_nuke.' + module)
assert 'nuke' not in sys.modules
""")


def test_headless_init_registers_idempotently_without_qt_or_backend():
    run_isolated(GUARD + """
import runpy
import types
callbacks = []
nuke = types.ModuleType('nuke')
nuke.env = {'gui': False}
nuke.allNodes = lambda **kwargs: []
for name in ('BeforeRender', 'BeforeFrameRender', 'AfterRender', 'OnScriptLoad', 'OnScriptClose',
             'OnCreate', 'OnDestroy', 'KnobChanged'):
    setattr(nuke, 'add' + name, lambda callback, _name=name, **kwargs: callbacks.append((_name, callback)))
    setattr(nuke, 'remove' + name, lambda *args, **kwargs: None)
sys.modules['nuke'] = nuke
runpy.run_path(sys.argv[1] + '/init.py')
count = len(callbacks)
assert count == 8, callbacks
runpy.run_path(sys.argv[1] + '/init.py')
assert len(callbacks) == count
assert any(name == 'BeforeRender' for name, _ in callbacks)
assert 'shotbox_nuke.controller' not in sys.modules
assert 'shotbox_nuke.process' not in sys.modules
""")


def test_headless_render_entry_does_not_import_backend_or_spawn_processes():
    run_isolated(GUARD + """
import shotbox_nuke.render as render
import shotbox_nuke.cache as cache
import shotbox_nuke.freshness as freshness
assert callable(render.farm_preflight)
assert callable(cache.validate_cache)
assert callable(freshness.fingerprint)
assert 'shotbox_nuke.controller' not in sys.modules
assert 'shotbox_nuke.process' not in sys.modules
""")
