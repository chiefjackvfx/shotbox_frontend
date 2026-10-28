"""Run with Nuke -t test/reformat_acceptance.py."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke
from create_nk import GenerateNkRequest, _TemplateEditor, compute_target_root_format

for width, height in ((1920, 1080), (2048, 858), (2160, 8192), (4096, 2160),
                      (6048, 4032), (8192, 2160), (6000, 6000)):
    nuke.scriptClear()
    fmt = nuke.addFormat(f'{width} {height} 1 Source_{width}_{height}')
    source = nuke.nodes.Constant(format=fmt.name())
    reformat = nuke.nodes.Reformat(name='Reformat3', inputs=[source])
    target_width, target_height = compute_target_root_format(width, height)
    editor = _TemplateEditor(nuke)
    editor.request = GenerateNkRequest(format_width=target_width, format_height=target_height)
    editor._update_reformats()
    assert not reformat['box_fixed'].value()
    assert bool(reformat['disable'].value()) == (width <= 4096)
    assert (reformat.width(), reformat.height()) == (target_width, target_height), (
        width, height, reformat.width(), reformat.height(), target_width, target_height)
print('REFORMAT_ACCEPTANCE_OK: native sizes, 4096 cap, portrait, square and widescreen')
