"""Nuke -t test/denoise_timing_acceptance.py: cut/retime integration, no profiling."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke
import app_cache
from denoise_template import TEMPLATE, prepare


@app_cache.temporary_cache_task
def main():
    with tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp'), prefix='denoise-timing-') as directory:
        root = Path(directory)
        source = root / 'source.####.exr'
        nuke.scriptClear(ignoreUnsavedChanges=True)
        constant = nuke.nodes.Constant()
        constant['format'].setValue('square_256')
        constant['color'].setExpression('frame / 100', 0)
        write = nuke.nodes.Write(inputs=[constant], file=str(source), file_type='exr')
        write['raw'].setValue(True)
        nuke.execute(write, 1, 30)
        for speed in (1, .5, 2):
            nuke.scriptClear(ignoreUnsavedChanges=True)
            read = nuke.nodes.Read(file=str(source), first=1, last=30)
            warp = nuke.nodes.TimeWarp(inputs=[read])
            warp['lookup'].setExpression(f'10.25 + (frame - 1006) * {speed}')
            warp['filter'].setValue('none')
            viewer = nuke.nodes.Viewer(inputs=[warp])
            viewer['frame_range'].setValue('1006-1011')
            viewer['frame_range_lock'].setValue(True)
            nuke.root()['first_frame'].setValue(1001)
            nuke.root()['last_frame'].setValue(1016)
            script = root / 'shot_v001.nk'
            nuke.scriptSaveAs(str(script), overwrite=True)
            original = script.read_bytes()
            nuke.scriptClear(ignoreUnsavedChanges=True)
            nuke.scriptOpen(str(TEMPLATE))
            request = dict(source=str(source), timing_script=str(script), script=str(TEMPLATE),
                           fps=24, colourspace='ACEScg', output_pattern=str(root / 'dn.####.exr'))
            result = prepare(nuke, request)
            assert (result['render_first'], result['render_last'], result['fps']) == (1006, 1011, 24)
            assert script.read_bytes() == original
            prepared = root / 'prepared.nk'
            nuke.scriptSaveAs(str(prepared), overwrite=True)
            nuke.scriptClear(ignoreUnsavedChanges=True)
            nuke.scriptOpen(str(prepared))
            resized = nuke.toNode('Reformat1')
            assert (resized.width(), resized.height()) == (256, 256)
            assert int(nuke.root()['first_frame'].value()) == 1006
            assert int(nuke.root()['last_frame'].value()) == 1011
            for frame in range(1006, 1012):
                expected = int(10.25 + (frame - 1006) * speed) / 100
                actual = resized.sample('red', 128, 128, 1, 1, frame)
                assert abs(actual - expected) < .001, (speed, frame, actual, expected)
            print(f'DENOISE_TIMING_OK speed={speed} range=1006-1011')


if __name__ == '__main__':
    main()
