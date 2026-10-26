"""Compare ShotBox creation with native Nuke node placement and insertion."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import traceback


def start():
    import nuke
    from PySide6 import QtWidgets
    import shotbox_nuke
    from shotbox_nuke import capture, node

    output = Path(os.environ['SHOTBOX_CREATION_REPORT'])
    report = {'host': capture.host_identity(), 'native_gpu_inference': False, 'cases': []}

    def scene(selected, nested):
        nuke.scriptClear(ignoreUnsavedChanges=True)
        parent = nuke.nodes.Group(name='Outer') if nested else nuke.root()
        with parent:
            source = nuke.nodes.NoOp(name='Reference')
            source.setXYpos(1200, 800)
            downstream = nuke.nodes.NoOp(name='Downstream', inputs=[source])
            downstream.setXYpos(1200, 1000)
            for item in nuke.selectedNodes():
                item['selected'].setValue(False)
            source['selected'].setValue(selected)
        nuke.showDag(parent)
        nuke.zoom(1.0, [1200, 850])
        QtWidgets.QApplication.processEvents()
        return parent, source, downstream

    try:
        assert nuke.GUI
        for selected, nested in ((True, False), (False, False), (True, True)):
            parent, source, downstream = scene(selected, nested)
            with parent:
                normal = nuke.createNode('NoOp', inpanel=False)
            expected = {'position': [normal.xpos(), normal.ypos()],
                        'source_connected': normal.input(0) is source,
                        'inserted': downstream.input(0) is normal}
            parent, source, downstream = scene(selected, nested)
            with parent:
                group = shotbox_nuke.create()
                actual = {'position': [group.xpos(), group.ypos()],
                          'source_connected': group.input(0) is source,
                          'inserted': downstream.input(0) is group}
                if selected:
                    assert actual == expected, (selected, nested, actual, expected)
                else:
                    # Nuke cascades successive unselected drops, even across
                    # scriptClear; their coordinates need not repeat exactly.
                    assert actual['position'] != [0, 0], actual
                    assert actual['source_connected'] == expected['source_connected'] is False
                    assert actual['inserted'] == expected['inserted'] is False
                assert nuke.selectedNodes() == [group], [n.fullName() for n in nuke.selectedNodes()]
                assert group.shown(), 'The completed node properties must be open'
                assert group['sb_color'].value() == 'acescg'
                assert group['sb_output'].value() == 'Matte only'
                assert not any(n.name().startswith('NoOp') for n in nuke.allNodes())
                assert len([n for n in nuke.allNodes() if node.is_shotbox(n)]) == 1
                name = group.name()
            if selected and not nested:
                nuke.Undo.undo()
                assert nuke.toNode(name) is None
                assert downstream.input(0) is source
                assert {n.name() for n in nuke.allNodes()} == {'Reference', 'Downstream'}
                nuke.Undo.redo()
                group = nuke.toNode(name)
                assert node.is_shotbox(group) and group.input(0) is source
                assert downstream.input(0) is group
                assert {n.name() for n in nuke.allNodes()} == {'Reference', 'Downstream', name}
                assert [group.xpos(), group.ypos()] == expected['position']
            report['cases'].append({'selected_input': selected, 'nested': nested, 'native': expected,
                                    'shotbox': actual, 'status': 'passed'})
        report.update(status='passed', one_step_undo_redo=True, no_temporary_nodes=True,
                      properties_open=True, acescg_default=True)
    except Exception:
        report.update(status='failed', error=traceback.format_exc())
    finally:
        output.write_text(json.dumps(report, indent=2) + '\n')
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=Path('docs/nuke-creation-gui.json'))
    args = parser.parse_args()
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix='shotbox-creation-gui-') as tmp:
        startup = Path(tmp)
        (startup / 'menu.py').write_text('import runpy\nfrom PySide6 import QtCore\nQtCore.QTimer.singleShot(1500, lambda: runpy.run_path(' + repr(str(here)) + ')["start"]())\n')
        env = dict(os.environ, NUKE_PATH=str(startup) + os.pathsep + str(here.parents[1]),
                   SHOTBOX_CREATION_REPORT=str(report))
        with (startup / 'nuke.log').open('w') as log:
            child = subprocess.run(['/opt/Nuke17.1v2/Nuke17.1', '-q', '-m', '4'], env=env,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=60)
        data = json.loads(report.read_text())
        print(json.dumps({'status': data['status'], 'error': data.get('error'), 'exit_code': child.returncode}))
        if data['status'] != 'passed' or child.returncode:
            print((startup / 'nuke.log').read_text(errors='replace')[-4000:])
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
