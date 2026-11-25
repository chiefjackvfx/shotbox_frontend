"""Run with Nuke -t to check OG cuts/retimes using frame-coded pixels."""
from pathlib import Path
import sys
import math
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_cache
import nuke
from nuke_headless_tasks import _PreviewTemplateEditor, get_preview_template_path


@app_cache.temporary_cache_task
def main():
    with tempfile.TemporaryDirectory(prefix='preview-timing-', dir=app_cache.cache_directory('temp')) as temp:
        directory = Path(temp)
        source = directory / 'source.%04d.exr'
        nuke.scriptClear(ignoreUnsavedChanges=True)
        constant = nuke.nodes.Constant()
        constant['format'].setValue(nuke.addFormat('6048 4032 1 preview_camera_6k'))
        constant['color'].setExpression('frame', 0)
        write = nuke.nodes.Write(inputs=[constant], file=str(source), file_type='exr')
        nuke.execute(write, 1, 40)
        for speed, offset in ((1, 11), (0.5, 11.25), (2, 5.5)):
            nuke.scriptClear(ignoreUnsavedChanges=True)
            read = nuke.nodes.Read(file=str(source), first=1, last=40)
            if speed == 1:
                mapping = nuke.nodes.Retime(inputs=[read])
                for key, value in (('input.first', offset), ('input.first_lock', True),
                                   ('input.last', 40), ('input.last_lock', True),
                                   ('output.first', 1004), ('output.first_lock', True),
                                   ('output.last', 1033), ('output.last_lock', False),
                                   ('before', 'continue'), ('after', 'continue'),
                                   ('speed', 1), ('filter', 'none')):
                    mapping[key].setValue(value)
            else:
                mapping = nuke.nodes.TimeWarp(inputs=[read])
                mapping['lookup'].setExpression(f'{offset} + (frame - 1004) * {speed}')
                mapping['filter'].setValue('none')
            viewer = nuke.nodes.Viewer(inputs=[mapping])
            viewer['frame_range'].setValue('1004-1009')
            viewer['frame_range_lock'].setValue(True)
            nuke.root()['first_frame'].setValue(1001)
            nuke.root()['last_frame'].setValue(1012)
            script = directory / 'shot_v001.nk'
            nuke.scriptSaveAs(str(script), overwrite=1)
            nuke.scriptClear(ignoreUnsavedChanges=True)
            first, last = _PreviewTemplateEditor(nuke).render(
                template_path=str(get_preview_template_path()), input_path=str(source),
                output_path=str(directory / 'preview.mp4'), shot_name='shot', project='test',
                artist='test', colourspace='ACEScg', fps=60, quality='low',
                timing_script=str(script), prepare_scene=str(directory / 'prepared.nk'))
            assert (first, last) == (1001, 1006), (first, last)
            # Deadline reopens the prepared scene; the timing must be self-contained.
            nuke.scriptClear(ignoreUnsavedChanges=True)
            nuke.scriptOpen(str(directory / 'prepared.nk'))
            output = nuke.toNode('Retime1')
            assert output.input(0).name() == 'Reformat2'
            assert (output.width(), output.height()) == (1620, 1080)
            write = nuke.toNode('WriteCompMP4')
            assert (write.width(), write.height()) == (1620, 1200)
            for frame in range(first, last + 1):
                nuke.frame(frame)
                expected = offset + (frame - first) * speed
                # filter=none selects the preceding source sample for fractional frames.
                actual = output.sample('red', 100, 100, 1, 1, frame)
                assert abs(actual - math.floor(expected)) < 0.0001, (speed, frame, actual, expected)
            assert not output.error()
            # Exercise the actual encoder, not only scene preparation.
            nuke.execute(write, first, last)
            assert (directory / 'preview.mp4').stat().st_size > 0
            print(f'OG_PREVIEW_TIMING_OK speed={speed} cut={first}-{last}')

        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptOpen(str(get_preview_template_path()))
        resize = nuke.toNode('Reformat2')
        write = nuke.toNode('WriteCompMP4')
        for label, source_format, expected_width in (
            ('widescreen', '2048 858 1', 2578),
            ('portrait', '1080 1920 1', 608),
            ('anamorphic', '1440 1080 1.3333333333333333', 1920),
        ):
            source = nuke.nodes.Constant()
            source['format'].setValue(nuke.addFormat(f'{source_format} {label}'))
            resize.setInput(0, source)
            assert (resize.width(), resize.height()) == (expected_width, 1080), label
            assert resize.format().pixelAspect() == 1, label
            assert (write.width(), write.height()) == (expected_width, 1200), label
            movie = directory / f'{label}.mp4'
            write['file'].setValue(str(movie))
            nuke.execute(write, 1001, 1001)
            assert movie.stat().st_size > 0
            print(f'PREVIEW_ASPECT_OK {label} {expected_width}x1080')


if __name__ == '__main__':
    main()
