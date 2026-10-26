"""Native host regression for immutable RGBA publication; synthetic mattes only."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
from shotbox_nuke import cache, capture, precomp  # noqa: E402


def close(a, b):
    assert abs(a - b) < 1e-6, (a, b)


def run():
    nuke.addFormat('16 12 0 0 16 12 1 ShotBoxPrecompSmoke')
    nuke.Layer('precompaux', ['precompaux.depth'])
    base = Path(tempfile.mkdtemp(prefix='shotbox-precomp-host-'))
    report = []
    for start, end, bounds in ((1001, 1003, [0, 0, 16, 12]), (-2, 0, [3, 2, 12, 10]),
                               (0, 1, [-3, -2, 20, 15]), (7, 9, [3, 2, 12, 10])):
        nuke.Layer('precompaux', ['precompaux.depth'])
        attempt = base / ('case%d' % start)
        attempt.mkdir()
        original = nuke.nodes.Constant()
        original['format'].setValue('ShotBoxPrecompSmoke')
        original['color'].setValue([0.123456789, -0.23456789, 1.3456789, 0.9])
        add = nuke.nodes.AddChannels(inputs=[original])
        add['channels'].setValue('precompaux')
        expr = nuke.nodes.Expression(inputs=[add])
        expr['channel0'].setValue('rgba.red')
        expr['expr0'].setValue('r + frame * 0.0001')
        expr['channel1'].setValue('precompaux.depth')
        expr['expr1'].setValue('42.1234567')
        crop = nuke.nodes.Crop(inputs=[expr])
        crop['box'].setValue(bounds)
        crop['reformat'].setValue(False)
        crop['intersect'].setValue(False)
        if start == 7:
            crop['box'].setExpression('frame - 7 + 3', 0)
        evaluated = expr if start == 1001 else crop
        group = nuke.nodes.NoOp(inputs=[evaluated])
        snap = {**capture.input_geometry(group, start, end), 'start': start, 'end': end,
                'count': end - start + 1, 'view': 'main'}
        original_boxes = {}
        original_values = {}
        for frame in range(start, end + 1):
            nuke.frame(frame)
            box = evaluated.bbox()
            original_boxes[frame] = [box.x(), box.y(), box.x() + box.w(), box.y() + box.h()]
            original_values[frame] = [evaluated.sample(channel, 5.5, 5.5)
                                      for channel in ('red', 'green', 'blue', 'precompaux.depth')]
        nuke.frame(99)
        selected_before = [node.fullName() for node in nuke.selectedNodes()]
        capture.capture(group, attempt, snap)
        assert nuke.frame() == 99
        assert [node.fullName() for node in nuke.selectedNodes()] == selected_before
        snap['source_files'] = [cache.file_record(attempt / ('source_v001/source.%04d.exr' % i), attempt)
                                for i in range(1, snap['count'] + 1)]
        matte_dir = attempt / 'matte'
        matte_dir.mkdir()
        synthetic = nuke.nodes.Constant()
        synthetic['format'].setValue('ShotBoxPrecompSmoke')
        synthetic['color'].setValue([0.27182818, 0.27182818, 0.27182818, 1])
        write = nuke.nodes.Write(inputs=[synthetic])
        write['file_type'].setValue('exr')
        write['file'].setValue(str(matte_dir / 'matte.%04d.exr'))
        capture.configure_write(write, channels='rgb')
        nuke.execute(write, 1, snap['count'])
        manifest = {'capture': snap, 'outputs': [cache.file_record(matte_dir / ('matte.%04d.exr' % i), attempt)
                                               for i in range(1, snap['count'] + 1)]}
        # Change the live input after capture. Publication must retain original RGB.
        original['color'].setValue([9, 8, 7, 0])
        result = precomp.publish(attempt, manifest, 'precomp_v01.%04d.exr')
        read = nuke.nodes.Read()
        read['file'].setValue(str(attempt / result['pattern']))
        read['first'].setValue(start)
        read['last'].setValue(end)
        read['raw'].setValue(True)
        source = nuke.nodes.Read()
        capture.configure_read(source, attempt / 'source_v001/source.%04d.exr', snap)
        for frame in range(start, end + 1):
            nuke.frame(frame)
            assert precomp._bbox(read) == original_boxes[frame], (frame, precomp._bbox(read), original_boxes[frame])
            assert precomp._bbox(source) == original_boxes[frame], (frame, precomp._bbox(source), original_boxes[frame])
            for channel in ('red', 'green', 'blue', 'precompaux.depth'):
                for x, y in ((5.5, 5.5), (bounds[0] + 0.5, bounds[1] + 0.5), (bounds[2] - 0.5, bounds[3] - 0.5)):
                    close(read.sample(channel, x, y), source.sample(channel, x, y))
            for channel, expected in zip(('red', 'green', 'blue', 'precompaux.depth'), original_values[frame]):
                close(read.sample(channel, 5.5, 5.5), expected)
            close(read.sample('alpha', 5.5, 5.5), 0.27182818)
        assert result['first'] == start and result['last'] == end
        assert len(result['files']) == snap['count']
        assert result['state'] == 'complete'
        assert 'precompaux.depth' in result['channels'], result['channels']
        try:
            precomp.publish(attempt, manifest, result['pattern'])
        except RuntimeError as exc:
            assert 'already contains' in str(exc)
        else:
            raise AssertionError('Publication replaced existing frames')
        source_path = attempt / snap['source_files'][0]['path']
        with source_path.open('ab') as stream:
            stream.write(b'changed')
        try:
            precomp.publish(attempt, manifest, 'precomp_v02.%04d.exr')
        except RuntimeError as exc:
            assert 'snapshot changed' in str(exc)
        else:
            raise AssertionError('Publication accepted modified immutable source')
        assert not list(attempt.glob('precomp_v02.*.exr'))
        report.append({'range': [start, end], 'bounds': bounds, 'result': result})
        nuke.scriptClear()
    print(json.dumps({'status': 'passed', 'fixture': 'synthetic host mattes, no inference', 'cases': report}, indent=2))


run()
