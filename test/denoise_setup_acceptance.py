"""Run with Nuke -t test/denoise_setup_acceptance.py; requires installed Stamps."""
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke
import stamps
from app_cache import cache_directory, temporary_cache_task
from create_nk import GenerateNkRequest, _TemplateEditor, get_runtime_template_path


@temporary_cache_task
def main():
    with tempfile.TemporaryDirectory(dir=cache_directory('temp'), prefix='denoise-setup-') as directory:
        root = Path(directory)
        source = root / 'source.####.exr'
        constant = nuke.nodes.Constant()
        constant['format'].setValue('square_256')
        constant['color'].setExpression('frame / 100', 0)
        write = nuke.nodes.Write(inputs=[constant], file=str(source), file_type='exr')
        write['raw'].setValue(True)
        nuke.execute(write, 1, 30)
        nuke.scriptClear()
        for index in (1, 2, 3):
            for frame in range(1, 31):
                shutil.copyfile(root / f'source.{frame:04}.exr', root / f'plate{index}.{frame+1000}.exr')
        request = GenerateNkRequest(
            template_path=str(get_runtime_template_path()), script_path=str(root / 'check.nk'),
            shot_name='check', duration=10, frame_last=1010,
            primary_plate_path=str(source), extra_plate_paths=[str(source), str(source)],
            dn_exr_file=str(root / 'legacy.####.exr'), comp_mov_file=str(root / 'comp.mov'),
            comp_exr_file=str(root / 'comp.####.exr'), preview_mp4_file=str(root / 'preview.mp4'),
            cut_duration=6, head_handles=2, tail_handles=2,
            plate_timings=[dict(frames=30, cut_start=10.25, speed=0.5)] * 3,
            denoise_plates=[dict(source=str(root / 'source.0001.exr'), plate=f'plate_{i:02}',
                                 read_index=i, file=str(root / f'plate{i}.####.exr')) for i in (1, 2, 3)],
        )
        _TemplateEditor(nuke).edit(request)
        nuke.scriptClear()
        nuke.scriptOpen(request.script_path)
        for index in (1, 2, 3):
            read = nuke.toNode(f'ReadDN{index}')
            assert read['colorspace'].value() == 'ACES - ACEScg'
            assert (read['first'].value(), read['last'].value()) == (1001, 1030)
            retime = nuke.toNode(f'RetimeDN{index}')
            assert retime.Class() == 'Retime'
            assert retime.input(0) is read
            assert retime['input.first'].value() == 1001
            assert retime['output.first'].value() == 1001
            assert retime['speed'].value() == 1
            assert not retime['time'].hasExpression()
            assert retime.ypos() - read.ypos() == 180
        for index in (2, 3):
            anchor, stamp = nuke.toNode(f'Anchor_DN{index}'), nuke.toNode(f'Stamp_DN{index}')
            assert anchor.input(0) is nuke.toNode(f'RetimeDN{index}')
            assert anchor.ypos() - anchor.input(0).ypos() == 180
            assert stamp.ypos() - anchor.ypos() == 180
            assert stamp['anchor'].value() == anchor.name()
            assert stamps.findAnchorsByTitle(f'degrain_plate_{index:02}') == [anchor]
            stamp.setInput(0, None)
            assert stamps.wiredReconnect(stamp)
            assert stamp.input(0) is anchor
            assert stamp in stamps.anchorWireds(anchor)
            assert not stamp.dependent(nuke.INPUTS | nuke.HIDDEN_INPUTS)
        primary_anchor = stamps.findAnchorsByTitle('degrain')[0]
        assert primary_anchor.input(0) is nuke.toNode('RetimeDN1')
        assert nuke.toNode('Stamp5').input(0) is primary_anchor
        print('DENOISE_SETUP_ACCEPTANCE_OK: three plates, source discovery, plain Retimes, spacing, Stamps reconnection, reopen')
        nuke.scriptClear()


if __name__ == '__main__':
    main()
