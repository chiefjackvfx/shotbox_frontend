"""Opt-in native Nuke checks; never copies media or writes beside source files.

Run with the desktop Python environment:
  python test/timeline_native_acceptance.py --folder /path/to/example --nuke /path/to/Nuke
Requires a folder with one matching XML/OTIO pair and usable media timecodes.
Imports each file independently and validates every resulting shot.
Resolve-render equivalence still requires an editorial reference export.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))


def inside_nuke(request_path):
    import nuke
    import create_nk

    requests = json.loads(Path(request_path).read_text())
    checked = 0
    for data in requests:
        nuke.scriptClear(ignoreUnsavedChanges=True)
        request = create_nk.GenerateNkRequest.from_dict(data)
        create_nk._TemplateEditor(nuke).edit(request)
        assert int(nuke.root()['first_frame'].value()) == request.frame_first
        assert request.frame_first == 1001
        assert int(nuke.root()['last_frame'].value()) == request.frame_last
        viewer = nuke.toNode('Viewer1')
        assert viewer['frame_range'].value() == f'{request.cut_first}-{request.cut_last}'
        assert viewer['frame_range_lock'].value()
        info_label = nuke.toNode('BackdropINFO')['label'].value()
        assert f'edit: {request.edit_inpoint}-{request.edit_outpoint}' in info_label
        for line in request.shot_metadata_lines:
            assert line in info_label, f'Missing shot metadata in info backdrop: {line}'
        for index, timing in enumerate(request.plate_timings, 1):
            read = nuke.toNode(f'Read{index}')
            warp = nuke.toNode(f'Retime{index}')
            assert warp.Class() == ('Retime' if abs(timing['speed'] - 1) < 1e-9 else 'TimeWarp')
            assert warp.input(0) is read
            assert int(read['last'].value()) == timing['frames']
            if timing.get('input_colourspace'):
                assert read['colorspace'].value() == create_nk.map_input_colorspace(timing['input_colourspace'])
            # Verify complete generated scripts can decode first/last source
            # samples, then use frame-coded pixels to measure the exact mapping.
            for frame in (request.frame_first, request.cut_first, request.cut_last, request.frame_last):
                expected = timing['cut_start'] + 1 + (frame - request.cut_first) * timing['speed']
                assert 1 - 1e-6 <= expected <= timing['frames'] + 1e-6
                if warp.Class() == 'TimeWarp':
                    assert abs(warp['lookup'].valueAt(frame) - expected) < 1e-6
                nuke.frame(frame)
                value = nuke.sample(warp, 'red', 100, 100)
                assert value == value, 'Non-finite decoded media sample'
                assert not read.error(), f'{read.name()}: media decoding failed'
                assert not warp.error(), f'{warp.name()}: timing sample failed'
            constant = nuke.nodes.Constant()
            constant['color'].setExpression('frame', 0)
            warp.setInput(0, constant)
            for frame in (request.frame_first, request.cut_first, request.frame_last):
                nuke.frame(frame)
                expected = timing['cut_start'] + 1 + (frame - request.cut_first) * timing['speed']
                assert abs(nuke.sample(warp, 'red', 100, 100) - expected) < 0.0001
            warp.setInput(0, read)
            nuke.delete(constant)
            checked += 1
    print(f'TIMELINE_ACCEPTANCE: {len(requests)} scripts; {checked} plate mappings passed')


def run(folder, nuke_path):
    from app_cache import cache_directory, temporary_cache_task
    from import_xml_v2 import NukeScriptGenerator
    from timeline_import import discover, prepare_import, probe_media, read_timeline

    @temporary_cache_task
    def validate():
        discovery = discover(folder)
        xmls = [p for p in discovery.timelines if Path(p).suffix.lower() == '.xml']
        otios = [p for p in discovery.timelines if Path(p).suffix.lower() == '.otio']
        if len(xmls) != 1 or len(otios) != 1:
            raise ValueError('Acceptance folder must contain exactly one XML/OTIO pair')
        documents = [read_timeline(xmls[0]), read_timeline(otios[0])]
        signatures = {doc.path: Path(doc.path).read_bytes() for doc in documents}
        media = {p: probe_media(p) for p in discovery.media}
        with tempfile.TemporaryDirectory(prefix='timeline-native-', dir=cache_directory('temp')) as temp:
            requests = []
            generator = NukeScriptGenerator()
            for source_index, doc in enumerate(documents):
                prepared = prepare_import(doc, media, handles=None, auto_colourspace=True)
                if prepared.errors:
                    raise ValueError('\n'.join(prepared.errors))
                for shot in prepared.shots:
                    shot.name = f'shot{shot.index:03d}'
                    directory = Path(temp, str(source_index), shot.name)
                    for clip in shot.clips:
                        clip.copied_filepath = clip.filepath  # Test references only; no media copying.
                    request = generator._build_create_nk_request(shot, str(directory),
                                                                 str(directory / 'scripts' / 'test.nk'), 'Acceptance')
                    requests.append(request.to_dict())
            request_path = Path(temp, 'requests.json')
            request_path.write_text(json.dumps(requests))
            result = subprocess.run([nuke_path, '-t', str(Path(__file__).resolve()),
                                     '--inside-nuke', str(request_path)], timeout=600)
            if result.returncode:
                raise RuntimeError(f'Native acceptance failed: exit {result.returncode}')
        for path, before in signatures.items():
            assert Path(path).read_bytes() == before
    validate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder')
    parser.add_argument('--nuke')
    parser.add_argument('--inside-nuke')
    args = parser.parse_args()
    if args.inside_nuke:
        inside_nuke(args.inside_nuke)
    elif args.folder and args.nuke:
        run(args.folder, args.nuke)
    else:
        parser.error('Specify --folder and --nuke')
