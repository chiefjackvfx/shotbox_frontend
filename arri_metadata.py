"""Read preserved ARRI V3 camera headers from MXF metadata, without an SDK.

Offsets and units follow ARRI's legacy Metadata White Paper, pages 55–59:
https://www.arri.com/resource/blob/31912/e30dd1a324dbecbced88fa146d3f88a9/2020-11-arri-metadata-white-paper-en-data.pdf
This reads a header snapshot, not a per-frame lens animation track.
"""
from pathlib import Path
import struct


_MXF_PREFIX = bytes.fromhex('060e2b34')
_ARRI_MAGIC = b'ARRI\x12\x34\x56\x78'
_HEADER_SIZE = 4096
_MAX_METADATA_BYTES = 16 * 1024 * 1024
_MAX_PACKETS = 4096


def _decode_header(header):
    if len(header) != _HEADER_SIZE or not header.startswith(_ARRI_MAGIC):
        return {}
    size, version = struct.unpack_from('<II', header, 8)
    if size != _HEADER_SIZE or version != 3:
        return {}

    def text(offset, length):
        value = header[offset:offset + length].split(b'\0', 1)[0]
        try:
            value = value.decode('ascii')
        except UnicodeDecodeError:
            return ''
        return ' '.join(value.split()) if all(c.isprintable() or c.isspace() for c in value) else ''

    model = text(0x2F8, 20)
    if not model.upper().startswith(('ARRI ', 'ALEXA', 'AMIRA')):
        return {}
    metadata = {'camera_make': 'ARRI', 'camera_model': model.removeprefix('ARRI '),
                'camera_metadata_source': 'Embedded ARRI V3 header snapshot'}
    lens = text(0x398, 32)
    if lens:
        metadata['lens_model'] = lens
    focal = struct.unpack_from('<I', header, 0x37C)[0] / 1000
    if 0 < focal <= 10000:
        metadata['focal_length_mm'] = f'{focal:g}'
    exposure = struct.unpack_from('<I', header, 0x74)[0]
    if 0 < exposure <= 102400:
        metadata['exposure_index'] = str(exposure)
    white_balance = struct.unpack_from('<I', header, 0x5C)[0]
    if 1000 <= white_balance <= 50000:
        metadata['white_balance_kelvin'] = str(white_balance)
    return metadata


def read_embedded_arri_metadata(path, cancel=lambda: False):
    """Read bounded MXF metadata values; skip video essence and unknown headers.

    Only group/metadata KLV values are searched for a validated 4096-byte ARRI
    header. No fixed file offset, filenames, or scans through image data are used.
    Malformed/unsupported metadata is optional and must not prevent an import.
    """
    path = Path(path)
    if path.suffix.lower() != '.mxf':
        return {}
    found = []
    budget = _MAX_METADATA_BYTES
    try:
        file_size = path.stat().st_size
        with path.open('rb') as stream:
            for _ in range(_MAX_PACKETS):
                if cancel():
                    return {}
                key = stream.read(16)
                if len(key) != 16 or not key.startswith(_MXF_PREFIX):
                    break
                length_byte = stream.read(1)
                if not length_byte:
                    break
                length = length_byte[0]
                if length & 0x80:
                    size = length & 0x7F
                    if not 0 < size <= 8:
                        break
                    encoded = stream.read(size)
                    if len(encoded) != size:
                        break
                    length = int.from_bytes(encoded, 'big')
                end = stream.tell() + length
                if end > file_size:
                    break
                # Generic Container essence elements start with category 01.
                # Camera header snapshots belong to the header metadata area.
                if key[4] == 1 and key[8:12] == bytes.fromhex('0d010301'):
                    break
                if key[4] == 2 and _HEADER_SIZE <= length <= budget:
                    data = stream.read(length)
                    budget -= length
                    offset = data.find(_ARRI_MAGIC)
                    while offset >= 0:
                        metadata = _decode_header(data[offset:offset + _HEADER_SIZE])
                        if metadata and metadata not in found:
                            found.append(metadata)
                        offset = data.find(_ARRI_MAGIC, offset + len(_ARRI_MAGIC))
                stream.seek(end)
    except OSError:
        return {}
    # Conflicting snapshots cannot supply a single trustworthy shot value.
    return found[0] if len(found) == 1 else {}
