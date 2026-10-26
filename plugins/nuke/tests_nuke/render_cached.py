"""Explicit cached farm entry point and real headless failure matrix.

/opt/Nuke17.1v2/Nuke17.1 -t render_cached.py --script /project/comp.nk \
    --write Write1 --first=1001 --last=1003

Use --self-test to create native synthetic fixtures and exercise failures. The
external ShotBox interpreter is deliberately unavailable throughout this test.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke
import shotbox_nuke
from shotbox_nuke import lifecycle, render


def rejected(action, text):
    try:
        action()
    except (RuntimeError, ValueError) as exc:
        assert text.lower() in str(exc).lower(), (text, str(exc))
    else:
        raise AssertionError('Expected rejection: ' + text)


def self_test():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from smoke import run
    report = run()
    script = report['saved_script']
    os.environ['SHOTBOX_PYTHON'] = '/deliberately-unavailable-shotbox-python'
    render.farm_preflight(script, 'FixtureWrite', 0, 0)
    write = nuke.toNode('FixtureWrite')
    group = write.input(0)
    nuke.execute(write, 0, 0, 1, views=['main'], continueOnError=False)
    assert not any(name.startswith(('torch', 'shotbox_matte', 'PyQt6')) for name in sys.modules)
    assert 'shotbox_nuke.process' not in sys.modules
    # The native saved graph works in another Nuke process after explicit import.
    path = group['sb_active_manifest'].value()
    group['sb_active_manifest'].setValue('')
    rejected(lambda: render.validate_write(write, 0, 0), 'Not generated')
    rejected(lambda: nuke.execute(write, 0, 0, 1, views=['main'], continueOnError=False), 'Not generated')
    unrelated_source = nuke.nodes.Constant()
    unrelated = nuke.nodes.Write(name='UnrelatedWrite', inputs=[unrelated_source])
    unrelated['file_type'].setValue('exr')
    unrelated['file'].setValue('/tmp/shotbox-unused.exr')
    assert render.validate_write(unrelated, 0, 0) == []
    nuke.execute(unrelated, 0, 0, 1, views=['main'], continueOnError=False)
    group['disable'].setValue(True)
    assert render.validate_write(write, -2, 3) == []
    group['disable'].setAnimated()
    group['disable'].setValueAt(True, 0)
    group['disable'].setValueAt(False, 1)
    assert render.validate_write(write, 0, 0) == []
    rejected(lambda: render.validate_write(write, 0, 1), 'Not generated')
    group['disable'].clearAnimated()
    group['disable'].setValue(False)
    group['sb_active_manifest'].setValue(path)
    manifest = json.loads(Path(path).read_text())
    first_output = Path(path).parent / manifest['outputs'][0]['path']
    content = first_output.read_bytes()
    first_output.unlink()
    rejected(lambda: render.validate_write(write, 0, 0), 'Missing')
    first_output.write_bytes(content)
    grade = nuke.toNode('Grade1')
    grade['multiply'].setValue(4)
    rejected(lambda: render.validate_write(write, 0, 0), 'stale')
    grade['multiply'].setValue(2)
    # A disconnected malformed node has no effect on this Write.
    orphan = shotbox_nuke.create()
    assert render.validate_write(write, 0, 0)
    # UUID collision preserves the existing owner and cache, clears pending.
    orphan['sb_uuid'].setValue(group['sb_uuid'].value())
    lifecycle.reconcile()
    assert orphan['sb_uuid'].value() != group['sb_uuid'].value()
    # Native nested Group traversal follows Output, ignoring disconnected tools.
    wrapper = nuke.nodes.Group(name='NestedWrapper', inputs=[group])
    wrapper.begin()
    try:
        inp = nuke.nodes.Input()
        nuke.nodes.Output(inputs=[inp])
        shotbox_nuke.create()
    finally:
        wrapper.end()
    wrapper.setInput(1, orphan)
    nested = nuke.nodes.Write(name='NestedWrite', inputs=[wrapper])
    nested['file'].setValue(str(Path(script).parent / 'nested.%04d.exr'))
    nested['file_type'].setValue('exr')
    assert render.validate_write(nested, 0, 0)
    nuke.execute(nested, 0, 0, 1, views=['main'], continueOnError=False)
    # Expression dependencies contribute even without an image pipe.
    expression_grade = nuke.nodes.Grade(inputs=[unrelated_source])
    expression_grade['multiply'].setExpression(orphan.fullName() + '.sb_height')
    expression_write = nuke.nodes.Write(inputs=[expression_grade])
    rejected(lambda: render.validate_write(expression_write, 0, 0), 'Not generated')
    # Real copy/paste receives a distinct live identity, retaining cache references.
    for item in nuke.selectedNodes():
        item['selected'].setValue(False)
    group['selected'].setValue(True)
    copy_file = str(Path(script).parent / 'copy.nk')
    nuke.nodeCopy(copy_file)
    pasted = nuke.nodePaste(copy_file)
    lifecycle.reconcile()
    assert pasted['sb_uuid'].value() != group['sb_uuid'].value()
    assert pasted['sb_active_manifest'].value() == group['sb_active_manifest'].value()
    retired = pasted['sb_uuid'].value()
    nuke.Undo.begin('ShotBox delete undo fixture')
    nuke.delete(pasted)
    nuke.Undo.end()
    nuke.undo()
    lifecycle.reconcile()
    assert all(item['sb_uuid'].value() != retired for item in nuke.allNodes(recurseGroups=True)
               if item.knob('sb_uuid') is not None)
    print('SHOTBOX_RENDER_PASS ' + json.dumps({'script': script, 'passed': [
        'saved/reopened cache', 'native production Write', 'no external Python/GPU',
        'no inference/Qt process adapter imports', 'ungenerated render rejects',
        'unrelated Write succeeds', 'static bypass', 'animated bypass over range',
        'missing frame rejects', 'stale input rejects', 'unrelated invalid node ignored',
        'UUID collision reconciliation', 'nested Groups ignore unused invalid tools',
        'expression dependency guards', 'native copy/paste ownership', 'native delete/undo identity retirement']}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--script')
    parser.add_argument('--write')
    parser.add_argument('--first', type=int)
    parser.add_argument('--last', type=int)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.script or not args.write or args.first is None or args.last is None:
        parser.error('--script, --write, --first and --last are required')
    render.farm_preflight(args.script, args.write, args.first, args.last)
    nuke.execute(nuke.toNode(args.write), args.first, args.last, 1,
                 views=nuke.views(), continueOnError=False)
    print('SHOTBOX_FARM_RENDER_PASS')


if __name__ == '__main__':
    main()
