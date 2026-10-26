"""Run with /opt/Nuke17.1v2/Nuke17.1 -t smoke.py.

Native Nuke fixtures exercise the host, not GPU inference. Every matte here is
an explicitly synthetic, float32 host-written fixture, never worker evidence.
"""
import json
import math
import platform
from pathlib import Path
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import nuke  # noqa: E402
from shotbox_nuke import cache, capture, freshness, lifecycle, node, render  # noqa: E402
from shotbox_nuke.protocol import FrameMap  # noqa: E402


def close(actual, expected):
    assert math.isclose(actual, expected, abs_tol=1e-5), (actual, expected)


def fixture(group, directory, start, end):
    """A real host-captured image + synthetic cache; no inference claimed."""
    group['sb_start'].setValue(start)
    group['sb_end'].setValue(end)
    group['sb_color'].setValue('linear-srgb')
    geometry = capture.input_geometry(group, start, end)
    count = end - start + 1
    snapshot = {**geometry, 'start': start, 'end': end, 'count': count, 'view': 'main'}
    attempt = cache.allocate_attempt(directory, group['sb_uuid'].value())
    before = freshness.fingerprint(group)
    assert not before['unresolved'], before
    capture.capture(group, attempt, snapshot)
    assert freshness.fingerprint(group)['digest'] == before['digest']
    output = attempt / 'capture_matte_v001'
    output.mkdir()
    owned = []
    with capture.preserve_host():
        try:
            black = nuke.nodes.Constant()
            owned.append(black)
            black['format'].setValue('ShotBoxSmoke')
            matte = nuke.nodes.Expression(inputs=[black])
            owned.append(matte)
            for key in ('expr0', 'expr1', 'expr2'):
                matte[key].setValue('frame == 1 ? -0.1 : 1.25')
            write = nuke.nodes.Write(inputs=[matte])
            owned.append(write)
            write['file_type'].setValue('exr')
            write['datatype'].setValue('32 bit float')
            write['raw'].setValue(True)
            write['file'].setValue(str(output / 'capture_matte_v001.%04d.exr'))
            nuke.execute(write, 1, count, 1, views=['main'], continueOnError=False)
        finally:
            for temporary in reversed(owned):
                nuke.delete(temporary)
    worker = attempt / 'worker.json'
    cache.atomic_write_json(worker, {'host_test_fixture': 'Synthetic host matte; inference not run'})
    backend = attempt / 'backend_jobs/host-fixture/_shotbox_job.json'
    frames = ['capture_matte_v001.%04d.exr' % index for index in range(1, count + 1)]
    cache.atomic_write_json(backend, {'status': 'complete', 'frame_count': count, 'frames': frames,
                                    'test_fixture': 'Nuke synthetic fixture, not a ShotBox inference result'})
    first_capture = attempt / 'capture_v001/capture.0001.exr'
    first_output = output / frames[0]
    dims = {key: geometry[key] for key in ('width', 'height')}
    manifest = {'schema_version': 1, 'integration_version': node.VERSION,
        'node_id': group['sb_uuid'].value(), 'attempt_id': attempt.name,
        'state': 'complete', 'created_at': cache.utc_now(), 'original_attempt_root': str(attempt),
        'worker_path': 'worker.json', 'worker_sha256': cache.sha256_file(worker),
        'capture': {**snapshot, 'fps': nuke.root()['fps'].value(),
                    'files': [cache.file_record(attempt / ('capture_v001/capture.%04d.exr' % i), attempt, geometry=dims)
                              for i in range(1, count + 1)]},
        'frame_map': FrameMap(start, end).as_dict(), 'fingerprint': before,
        'settings': {'source_color': 'linear-srgb'}, 'host': capture.host_identity(),
        'worker_exit': 0, 'outputs': [cache.file_record(output / name, attempt, geometry=dims) for name in frames],
        'validation': {'valid': True, 'fixture': 'synthetic native host matte; no inference'},
        'backend_manifest': backend.relative_to(attempt).as_posix(),
        'result': {'status': 'complete', 'frame_count': count, 'output_dir': str(output),
            'first_source': str(first_capture), 'first_output': str(first_output),
            'manifest': str(backend), 'job_dir': str(backend.parent),
            'source_preview': str(backend.parent / 'source.png'), 'matte_preview': str(backend.parent / 'matte.png')}}
    path = attempt / 'integration.json'
    cache.atomic_write_json(path, manifest)
    cache.validate_cache(path)
    node.activate(group, path)
    return path



def extra_host_checks(group, grade, base):
    original_path = group['sb_active_manifest'].value()
    owned = []
    try:
        # Retiming is evaluated upstream at the original Nuke timeline frame.
        offset = nuke.nodes.TimeOffset(inputs=[grade])
        owned.append(offset)
        offset['time_offset'].setValue(2)
        group.setInput(0, offset)
        path = fixture(group, base, -2, 0)
        retimed_path = path
        read = nuke.nodes.Read()
        owned.append(read)
        read['file'].setValue(str(path.parent / 'capture_v001/capture.%04d.exr'))
        read['first'].setValue(1)
        read['last'].setValue(3)
        read['raw'].setValue(True)
        for timeline in (-2, -1, 0):
            for x, y in ((0.5, 0.5), (15.5, 0.5), (0.5, 11.5), (15.5, 11.5)):
                nuke.frame(timeline)
                expected = [offset.sample(channel, x, y) for channel in ('red', 'green', 'blue')]
                nuke.frame(timeline + 3)
                for channel, value in zip(('red', 'green', 'blue'), expected):
                    close(read.sample(channel, x, y), value)
        # A tight bbox is black outside, with the full input format preserved.
        cropped = nuke.nodes.Crop(inputs=[grade])
        owned.append(cropped)
        cropped['box'].setValue([3, 2, 12, 10])
        cropped['reformat'].setValue(False)
        cropped['intersect'].setValue(True)
        group.setInput(0, cropped)
        path = fixture(group, base, 0, 0)
        read['file'].setValue(str(path.parent / 'capture_v001/capture.%04d.exr'))
        read['first'].setValue(1)
        read['last'].setValue(1)
        nuke.frame(1)
        close(read.sample('red', 0.5, 0.5), 0)
        close(read.sample('blue', 15.5, 11.5), 0)
        assert (read.width(), read.height()) == (16, 12)
        nuke.frame(0)
        close(group.sample('red', 5, 5), cropped.sample('red', 5, 5))
        assert [(getattr(group.bbox(), key)()) for key in ('x', 'y', 'w', 'h')] == [(getattr(cropped.bbox(), key)()) for key in ('x', 'y', 'w', 'h')]
        # Auxiliary channels and source alpha do not alter capture RGB.
        nuke.Layer('fixtureaux', ['fixtureaux.depth'])
        add = nuke.nodes.AddChannels(inputs=[grade])
        owned.append(add)
        add['channels'].setValue('fixtureaux')
        auxiliary = nuke.nodes.Expression(inputs=[add])
        owned.append(auxiliary)
        auxiliary['channel0'].setValue('fixtureaux.depth')
        auxiliary['expr0'].setValue('42')
        group.setInput(0, auxiliary)
        fixture(group, base, 0, 0)
        nuke.frame(0)
        close(group.sample('fixtureaux.depth', 5, 5), 42)
        close(group.sample('red', 5, 5), auxiliary.sample('red', 5, 5))
        # The actual Nuke file-knob OutputContext API resolves sequences.
        group.setInput(0, read)
        group['sb_start'].setValue(1)
        group['sb_end'].setValue(1)
        current = freshness.fingerprint(group)
        assert not current['unresolved'], current['unresolved']
        assert current['dependencies'], 'Read sequence identity was not recorded'
        media = Path(current['dependencies'][0]['path'])
        original_stat = media.stat()
        import os
        os.utime(media, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1000000))
        assert freshness.fingerprint(group)['digest'] != current['digest']
        os.utime(media, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        # File identities include source frames outside the capture timeline.
        read['file'].setValue(str(retimed_path.parent / 'capture_v001/capture.%04d.exr'))
        read['first'].setValue(1)
        read['last'].setValue(3)
        shifted = nuke.nodes.TimeOffset(inputs=[read])
        owned.append(shifted)
        shifted['time_offset'].setValue(2)
        group.setInput(0, shifted)
        group['sb_start'].setValue(5)
        group['sb_end'].setValue(5)
        nuke.frame(5)
        expected = shifted.sample('red', 5, 5)
        nuke.frame(3)
        close(expected, read.sample('red', 5, 5))
        source_fingerprint = freshness.fingerprint(group)
        assert not source_fingerprint['unresolved'], source_fingerprint
        consumed = retimed_path.parent / 'capture_v001/capture.0003.exr'
        assert str(consumed) in [entry['path'] for entry in source_fingerprint['dependencies']]
        consumed_stat = consumed.stat()
        os.utime(consumed, ns=(consumed_stat.st_atime_ns, consumed_stat.st_mtime_ns + 1000000))
        assert freshness.fingerprint(group)['digest'] != source_fingerprint['digest']
        os.utime(consumed, ns=(consumed_stat.st_atime_ns, consumed_stat.st_mtime_ns))
        # The selected custom OCIO file identity participates in provenance.
        config_knob = nuke.root()['customOCIOConfigPath']
        original_config = config_knob.toScript()
        config = base / 'custom-config.ocio'
        config.write_bytes(Path('/opt/Nuke17.1v2/plugins/OCIOConfigs/configs/nuke-default/config.ocio').read_bytes())
        try:
            config_knob.setValue(str(config))
            config_fingerprint = freshness.fingerprint(group)
            assert not config_fingerprint['unresolved'], config_fingerprint
            assert str(config) in [entry['path'] for entry in config_fingerprint['dependencies']]
            config_stat = config.stat()
            os.utime(config, ns=(config_stat.st_atime_ns, config_stat.st_mtime_ns + 1000000))
            assert freshness.fingerprint(group)['digest'] != config_fingerprint['digest']
        finally:
            config_knob.fromScript(original_config)
        # Socket order is image-affecting even when the dependency set is equal.
        extra = nuke.nodes.Constant()
        owned.append(extra)
        extra['format'].setValue('ShotBoxSmoke')
        extra['color'].setValue([0.1, 0.2, 0.3, 1])
        merge = nuke.nodes.Merge2(inputs=[grade, extra])
        owned.append(merge)
        group.setInput(0, merge)
        ordered = freshness.fingerprint(group)
        merge.setInput(0, extra)
        merge.setInput(1, grade)
        assert freshness.fingerprint(group)['digest'] != ordered['digest']
        downstream = node.create()
        owned.append(downstream)
        downstream.setInput(0, group)
        downstream['sb_start'].setValue(5)
        downstream['sb_end'].setValue(5)
        downstream_before = freshness.fingerprint(downstream)
        group['sb_output'].setValue('Matte only')
        assert freshness.fingerprint(downstream)['digest'] != downstream_before['digest']
        group['sb_output'].setValue('Original RGB + generated alpha')
        previous_attempt = group['sb_active_attempt'].value()
        group['sb_active_attempt'].setValue('changed-cache-identity')
        assert freshness.fingerprint(downstream)['digest'] != downstream_before['digest']
        group['sb_active_attempt'].setValue(previous_attempt)
        # Nuke omits Root from expression dependencies; custom references and
        # recursively referenced root knobs still affect the fingerprint.
        group.setInput(0, grade)
        no_reference = freshness.fingerprint(group)
        gain = nuke.Double_Knob('shotGain', 'Shot gain fixture')
        other_gain = nuke.Double_Knob('shotOtherGain', 'Recursive gain fixture')
        gain.setValue(2)
        other_gain.setValue(2)
        nuke.root().addKnob(gain)
        nuke.root().addKnob(other_gain)
        prior_multiply = grade['multiply'].toScript()
        try:
            assert freshness.fingerprint(group)['digest'] == no_reference['digest'], 'Unreferenced root knob changed provenance'
            grade['multiply'].setExpression('root.shotGain')
            expression_before = freshness.fingerprint(group)
            assert not expression_before['unresolved'], expression_before
            nuke.frame(77)
            assert freshness.fingerprint(group)['digest'] == expression_before['digest'], 'GUI frame changed provenance'
            gain.setValue(3)
            assert freshness.fingerprint(group)['digest'] != expression_before['digest']
            gain.setExpression('root.shotOtherGain')
            recursive = freshness.fingerprint(group)
            other_gain.setValue(4)
            assert freshness.fingerprint(group)['digest'] != recursive['digest']
            grade['multiply'].setExpression('root.shotMissingGain')
            assert any('shotMissingGain' in reason for reason in freshness.fingerprint(group)['unresolved'])
        finally:
            grade['multiply'].fromScript(prior_multiply)
            nuke.root().removeKnob(gain)
            nuke.root().removeKnob(other_gain)
        # Reject square-pixel violations before capture.
        nonsquare = nuke.nodes.Constant()
        owned.append(nonsquare)
        nonsquare['format'].setValue(nuke.addFormat('16 12 2 ShotBoxNonSquare').name())
        group.setInput(0, nonsquare)
        try:
            capture.input_geometry(group, 0, 0)
            raise AssertionError('Non-square pixels accepted')
        except ValueError as exc:
            assert 'square' in str(exc), exc
        # Native render cancellation must restore proxy/frame and owned nodes.
        group.setInput(0, grade)
        attempt = cache.allocate_attempt(base, group['sb_uuid'].value())
        previous_active = group['sb_active_manifest'].value()
        cancel_calls = []
        def cancel_capture():
            if nuke.thisNode().name().startswith('ShotBoxCaptureWrite') and nuke.frame() >= -1:
                cancel_calls.append(nuke.frame())
                nuke.cancel()
        nuke.addBeforeFrameRender(cancel_capture)
        nuke.root()['proxy'].setValue(True)
        nuke.frame(9)
        before_nodes = {item.fullName() for item in nuke.allNodes(recurseGroups=True)}
        try:
            try:
                capture.capture(group, attempt, {'start': -2, 'end': 0, 'count': 3,
                    'view': 'main', 'box': [0, 0, 16, 12]})
                raise AssertionError('Native cancelled capture completed')
            except RuntimeError as exc:
                assert 'cancel' in str(exc).lower() or 'incomplete' in str(exc).lower(), exc
            assert cancel_calls, 'Native cancel callback did not run'
            assert group['sb_active_manifest'].value() == previous_active
            assert nuke.root()['proxy'].value() is True
            assert nuke.frame() == 9
            assert before_nodes == {item.fullName() for item in nuke.allNodes(recurseGroups=True)}
        finally:
            nuke.removeBeforeFrameRender(cancel_capture)
            nuke.root()['proxy'].setValue(False)
    finally:
        group.setInput(0, grade)
        group['sb_start'].setValue(0)
        group['sb_end'].setValue(0)
        node.activate(group, original_path)
        for temporary in reversed(owned):
            nuke.delete(temporary)
    return ['retimed evaluated capture', 'four-corner orientation', 'tight bbox black fill',
            'original bbox preservation', 'auxiliary channel preservation', 'Read sequence media identities',
            'media mtime change stale detection', 'retimed Read tracks consumed frames outside capture range', 'custom OCIO config identity', 'indexed socket topology changes', 'upstream ShotBox view/cache identity changes', 'custom/recursive root expression dependencies', 'unreferenced root controls and GUI frame ignored', 'non-square pixels rejected', 'native capture cancellation restores host']


def run():
    assert nuke.NUKE_VERSION_STRING == '17.1v2', nuke.NUKE_VERSION_STRING
    assert not nuke.env.get('nc') and not nuke.env.get('indie'), 'Commercial Nuke required'
    lifecycle.register()
    lifecycle.register()
    nuke.root()['first_frame'].setValue(-2)
    nuke.root()['last_frame'].setValue(3)
    nuke.root()['format'].setValue(nuke.addFormat('16 12 1 ShotBoxSmoke').name())
    source = nuke.nodes.Constant(name='FixtureSource')
    source['format'].setValue('ShotBoxSmoke')
    source['color'].setValue([0.25, 0.5, 0.75, 0.3])
    expression = nuke.nodes.Expression(inputs=[source], name='FixtureCoordinates')
    expression['expr0'].setValue('frame + x/16')
    expression['expr1'].setValue('y/12')
    expression['expr2'].setValue('x < 8 ? 0.25 : 0.75')
    grade = nuke.nodes.Grade(inputs=[expression])
    grade['multiply'].setValue(2)
    group = node.create()
    group.setInput(0, grade)
    lifecycle.reconcile()
    original_id = group['sb_uuid'].value()
    base = Path(tempfile.mkdtemp(prefix='shotbox-nuke-17.1v2-'))
    cases = []
    for start, end in ((1001, 1003), (-2, 0), (0, 0)):
        nuke.frame(7)
        nuke.root()['proxy'].setValue(True)
        selected_before = sorted(n.fullName() for n in nuke.selectedNodes())
        path = fixture(group, base, start, end)
        assert nuke.root()['proxy'].value() is True
        assert nuke.frame() == 7
        assert sorted(n.fullName() for n in nuke.selectedNodes()) == selected_before
        nuke.root()['proxy'].setValue(False)
        render.validate_node(group)
        for frame in (start - 1, start, end, end + 1):
            nuke.frame(frame)
            close(group.sample('red', 2, 3), grade.sample('red', 2, 3))
            close(group.sample('green', 2, 3), grade.sample('green', 2, 3))
            close(group.sample('blue', 2, 3), grade.sample('blue', 2, 3))
            expected = 0 if frame < start or frame > end else (-0.1 if frame == start else 1.25)
            close(group.sample('alpha', 2, 3), expected)
        group['sb_output'].setValue('Matte only')
        nuke.frame(start)
        close(group.sample('red', 2, 3), -0.1)
        close(group.sample('green', 2, 3), -0.1)
        close(group.sample('blue', 2, 3), -0.1)
        close(group.sample('alpha', 2, 3), 1)
        group['sb_output'].setValue('Original RGB + generated alpha')
        before = freshness.fingerprint(group)
        grade['xpos'].setValue(333)
        grade['label'].setValue('Cosmetic')
        group['sb_output'].setValue('Matte only')
        assert freshness.fingerprint(group)['digest'] == before['digest']
        group['sb_output'].setValue('Original RGB + generated alpha')
        grade['multiply'].setValue(3)
        assert freshness.fingerprint(group)['digest'] != before['digest']
        try:
            render.validate_node(group)
            raise AssertionError('Stale cache accepted')
        except RuntimeError as exc:
            assert 'stale' in str(exc), exc
        grade['multiply'].setValue(2)
        cases.append({'range': [start, end], 'manifest': str(path)})
    extra_passed = extra_host_checks(group, grade, base)
    assert group['sb_uuid'].value() == original_id
    script = base / 'cached.nk'
    write = nuke.nodes.Write(name='FixtureWrite', inputs=[group])
    write['file'].setValue(str(base / 'render.%04d.exr'))
    write['file_type'].setValue('exr')
    nuke.scriptSaveAs(str(script), overwrite=1)
    report = {'host': capture.host_identity(), 'platform': platform.platform(), 'fixture': 'synthetic Nuke matte; no inference',
              'passed': ['idempotent registration', 'positive/negative/zero frame mapping', 'capture host-state restore',
                         'RGB preservation', 'raw out-of-unit matte values', 'zero alpha outside range', 'matte-only view',
                         'cosmetic edits ignored', 'upstream grade stale detection', 'Nuke cache decoding'] + extra_passed,
              'cases': cases, 'saved_script': str(script)}
    (base / 'smoke-report.json').write_text(json.dumps(report, indent=2))
    print('SHOTBOX_SMOKE_PASS ' + json.dumps(report))
    return report


def inspect_native(manifest_path):
    """Inspect an actual worker cache against its frozen captures in Nuke.

    This reconstructs the input from the snapshot, so it deliberately does not
    claim the original live-graph fingerprint matches this reconstructed graph.
    """
    report = cache.validate_cache(manifest_path, checksums=True)
    render.decode_outputs(report)
    manifest = report['manifest']
    captured = manifest['capture']
    nuke.root()['format'].setValue(nuke.addFormat('%d %d 1 ShotBoxNative' %
        (captured['width'], captured['height'])).name())
    source = nuke.nodes.Read()
    source['file'].setValue(str(Path(manifest_path).parent / 'capture_v001/capture.%04d.exr'))
    source['first'].setValue(1)
    source['last'].setValue(captured['count'])
    source['raw'].setValue(True)
    source['frame_mode'].setValue('expression')
    source['frame'].setValue('frame - (%d) + 1' % captured['start'])
    group = node.create()
    group.setInput(0, source)
    group['sb_start'].setValue(captured['start'])
    group['sb_end'].setValue(captured['end'])
    group['sb_color'].setValue('linear-srgb')
    node.activate(group, manifest_path)
    values = []
    for frame in range(captured['start'] - 1, captured['end'] + 2):
        nuke.frame(frame)
        for x, y in ((0.5, 0.5), (captured['width'] - 0.5, captured['height'] - 0.5),
                     (captured['width'] / 2, captured['height'] / 2)):
            for channel in ('red', 'green', 'blue'):
                close(group.sample(channel, x, y), source.sample(channel, x, y))
            alpha = group.sample('alpha', x, y)
            expected = group.node('SBCache').sample('red', x, y) if captured['start'] <= frame <= captured['end'] else 0
            close(alpha, expected)
            assert math.isfinite(alpha)
            values.append(alpha)
    temporal = json.loads((Path(manifest_path).parent / manifest['worker_path']).read_text()).get('temporal_mode', 'unknown')
    result = {'host': capture.host_identity(), 'manifest': str(Path(manifest_path).resolve()),
        'temporal_mode': temporal,
        'source': 'actual native worker cache; input reconstructed from frozen Nuke captures',
        'frame_count': captured['count'],
        'passed': ['full cache SHA-256 validation', 'Nuke image decode and geometry', 'real worker matte frame map',
                   'original snapshot RGB equality', 'alpha equals raw worker red', 'zero alpha outside range'],
        'sampled_alpha_min': min(values), 'sampled_alpha_max': max(values),
        'quality_review': 'Not performed; tiny synthetic coordinate fixture is not representative footage',
        'freshness': 'Original live graph not reconstructed; no current-input freshness claim'}
    destination = PACKAGE.parents[1] / 'toolbox/shotbox_matte/docs/nuke-native-host.json'
    previous = json.loads(destination.read_text()) if destination.exists() else {'cases': {}}
    if 'cases' not in previous:
        previous = {'cases': {'frames_3': previous}}
    previous['cases']['frames_%d_%s' % (captured['count'], temporal)] = result
    destination.write_text(json.dumps(previous, indent=2) + '\n')
    print('SHOTBOX_NATIVE_HOST_PASS ' + json.dumps(result))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--native-manifest':
        inspect_native(sys.argv[2])
    else:
        run()
