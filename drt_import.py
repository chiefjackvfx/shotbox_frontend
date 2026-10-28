"""Read Resolve timeline archives without extracting or modifying their contents.

The supported timing encodings are validated narrowly: unknown mappings must not
silently become normal-speed clips. Resolve's private format may evolve.
"""
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zipfile


def _double_pair(value):
    values = struct.unpack('<dd', bytes.fromhex(value))
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Invalid DRT timing value')
    return values


def _time_map(value, source_in, fps):
    """Return source offset in frames and positive constant speed."""
    data = bytes.fromhex(value)
    if len(data) == 9 and data[0] == 2:
        duration = struct.unpack('>d', data[1:])[0]
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Invalid DRT time map')
        return source_in, 1.0
    # Qt QVariantMap: version, entry count, UTF-16 keys and typed values.
    if data[:4] != b'\0\0\0\1':
        raise ValueError('Unsupported DRT retime encoding')
    position = 4
    def take(size):
        nonlocal position
        if size < 0 or position + size > len(data):
            raise ValueError('Truncated DRT retime')
        result = data[position:position + size]
        position += size
        return result
    def integer():
        return int.from_bytes(take(4), 'big')
    count = integer()
    if count > 64:
        raise ValueError('Unsupported DRT retime fields')
    fields = {}
    for _ in range(count):
        key = take(integer()).decode('utf-16-be')
        kind = integer()
        if take(1) != b'\0':
            raise ValueError('Unsupported null DRT retime field')
        if kind == 6:
            item = struct.unpack('>d', take(8))[0]
        elif kind in (10, 12):
            item = take(integer())
        else:
            raise ValueError('Unsupported DRT retime field type')
        if key in fields:
            raise ValueError('Duplicate DRT retime field')
        fields[key] = item
    if position != len(data) or fields.get('DbType') != 'Sm2TimeMap'.encode('utf-16-be'):
        raise ValueError('Unsupported DRT time map')
    points = fields.get('KeyframesBA', b'')
    # Two linear protobuf points. First x is omitted (zero); y is explicit.
    if (len(points) != 32 or points[:4] != b'\x80\x0a\x09\x11'
            or points[12:15] != b'\x0a\x12\x09' or points[23] != 0x11):
        raise ValueError('Only forward constant-speed DRT retimes are supported')
    y0 = struct.unpack('<d', points[4:12])[0]
    x1 = struct.unpack('<d', points[15:23])[0]
    y1 = struct.unpack('<d', points[24:32])[0]
    if not all(math.isfinite(v) for v in (y0, x1, y1)) or x1 <= 0:
        raise ValueError('Invalid DRT retime points')
    speed = (y1 - y0) / x1
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError('Only forward constant-speed DRT retimes are supported')
    return y0 * fps + source_in * speed, speed


def read_drt(path):
    from timeline_import import TimelineDocument, TimelineClip, fingerprint, _validate_structure
    try:
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if m.filename.endswith('.xml')]
            if len(members) > 256 or sum(m.file_size for m in members) > 64 * 1024 * 1024:
                raise ValueError('DRT XML exceeds supported archive size')
            roots = []
            for member in members:
                text = archive.read(member).decode('utf-8-sig')
                if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                    raise ValueError('DRT XML declarations are unsupported')
                # Resolve writes C++ class names, which are not valid XML QNames.
                text = re.sub(r'(<\/?[\w]+)::', r'\1__', text)
                roots.append(ET.fromstring(text))
        timelines = [t for r in roots for t in r.iter('Sm2Timeline')]
        containers = [r for r in roots if r.tag == 'Sm2SequenceContainer']
        if len(timelines) != 1 or len(containers) != 1:
            raise ValueError('DRT must contain exactly one timeline and sequence container')
        timeline, container = timelines[0], containers[0]
        sequence = timeline.find('Sequence/Sm2Sequence')
        if sequence is None:
            raise ValueError('DRT timeline sequence is missing')
        fps, rate_flags = _double_pair(sequence.findtext('FrameRate', ''))
        origin_seconds, _ = _double_pair(sequence.findtext('MediaExtents', ''))
        if fps <= 0 or rate_flags != 0:
            raise ValueError('Unsupported DRT frame rate encoding')
        origin = origin_seconds * fps
        if abs(origin - round(origin)) > 0.01:
            raise ValueError('Non-integral DRT timeline origin')
        result = TimelineDocument(str(path), timeline.findtext('Name') or Path(path).stem,
                                  fps, [], signature=fingerprint(path))
        for number, track in enumerate(container.findall('VideoTrackVec/Element/Sm2TiTrack'), 1):
            if track.findtext('Sequence') != sequence.get('DbId'):
                raise ValueError('DRT track references a different sequence')
            if track.findtext('Flags', '0') != '0':
                raise ValueError('Unsupported DRT track flags; export XML or OTIO')
            if track.findall('FusionCompHolderItems/Element'):
                raise ValueError('DRT Fusion timeline items require an XML or OTIO export')
            for index, item in enumerate(track.findall('Items/Element')):
                clip = item.find('Sm2TiVideoClip')
                if clip is None:
                    raise ValueError(f'V{number}: unsupported DRT item or transition')
                errors = []
                start = int(clip.findtext('Start')) - round(origin)
                duration = int(clip.findtext('Duration'))
                if start < 0 or duration <= 0:
                    errors.append('Invalid DRT editorial range')
                if clip.findtext('Flags', '0') != '0':
                    errors.append('Unsupported DRT clip flags; export XML or OTIO')
                source_start, speed = None, 1.0
                try:
                    source_fps, flags = _double_pair(clip.findtext('MediaFrameRate', ''))
                    if abs(source_fps - fps) > 0.000001 or flags != 0:
                        raise ValueError('Mixed source/timeline rates require conversion outside this importer')
                    offset, speed = _time_map(clip.findtext('MediaTimemapBA', ''),
                                              float(clip.findtext('In')), fps)
                    source_start = float(clip.findtext('MediaStartTime')) * fps + offset
                    if not math.isfinite(source_start):
                        raise ValueError('Invalid DRT source start')
                except (ValueError, TypeError, struct.error) as exc:
                    errors.append(str(exc))
                    source_start = None
                reference = clip.findtext('MediaFilePath', '')
                if not reference:
                    errors.append('Missing DRT external media reference')
                result.clips.append(TimelineClip(
                    f'{number}:{index}', clip.findtext('Name', 'unnamed'), reference,
                    reference.replace('\\', '/').rsplit('/', 1)[-1],
                    clip.findtext('MediaReelNumber', ''), number, start, duration, fps,
                    source_start, speed, '; '.join(errors)))
        if container.findall('AudioTrackVec/Element/Sm2TiTrack/Items/Element'):
            result.warnings.append('Audio is not imported')
        _validate_structure(result)
        return result
    except (zipfile.BadZipFile, ET.ParseError, UnicodeError, struct.error, TypeError, OSError) as exc:
        raise ValueError(f'Cannot read DRT: {exc}') from exc
