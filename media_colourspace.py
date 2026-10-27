"""Input-transform suggestions and camera identity from media metadata."""
from dataclasses import dataclass
import math
import re

from colourspace_defaults import COLOURSPACE_LIST


def _normal(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


def _fields(metadata):
    result = {}
    for key, value in metadata.items():
        key = re.sub(r'^(format|stream|video)\.', '', key)
        result.setdefault(_normal(key), []).append(' '.join(str(value).split()))
    return result


def camera_identity(metadata):
    fields = _fields(metadata)
    def first(*keys):
        return next((value for key in keys for value in fields.get(key, []) if value), '')
    make = first('cameramake', 'cameramanufacturer', 'manufacturer', 'make', 'comapplequicktimemake')
    model = first('cameramodel', 'model', 'comapplequicktimemodel')
    company = first('companyname')
    if not make and _normal(company) in ('arri', 'arriag', 'sony', 'sonycorporation', 'canon', 'canoninc', 'blackmagicdesign', 'panasonic', 'nikon', 'fujifilm', 'reddigitalcinema'):
        make = company
    if not model and make:
        product = first('productname')
        if any(word in _normal(product) for word in ('alexa', 'amira', 'venice', 'cinealta', 'eos', 'ursa', 'pocketcinema', 'komodo', 'raptor', 'lumix')):
            model = product
    if not make and _normal(model).startswith(('alexa', 'arri', 'amira')):
        make = 'ARRI'
    return {'make': make, 'model': model}


def lens_identity(metadata):
    fields = _fields(metadata)
    def first(*keys):
        return next((value for key in keys for value in fields.get(key, []) if value), '')
    lens = first('lensmodel', 'lensname', 'comarricameralensmodel', 'comapplequicktimelensmodel')
    focal = first('focallengthmm', 'focallength', 'comarricameralensfocallength')
    result = {'lens_model': lens} if lens else {}
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:mm)?', focal, re.IGNORECASE)
    if match and 0 < float(match[1]) <= 10000:
        result['focal_length_mm'] = f'{float(match[1]):g}'
    return result


@dataclass(frozen=True)
class ColourspaceDetection:
    colourspace: str | None
    source: str
    reason: str


def detect_input_colourspace(metadata):
    fields = _fields(metadata)
    input_fields = _fields({key: value for key, value in metadata.items() if not key.startswith('video.')})
    colour_keys = {'inputcolorspace', 'inputcolourspace', 'sourcecolorspace', 'sourcecolourspace',
                   'colorspace', 'colourspace', 'gamma', 'cameragamma', 'transferfunction',
                   'logcurve', 'gamut', 'colorgamut', 'colourgamut'}
    values = [value for key, items in input_fields.items() if key in colour_keys for value in items]
    values = [value for value in values if _normal(value) not in ('', 'unknown', 'unspecified', 'reserved', 'gbr')]
    combined = ' '.join(_normal(value) for value in values)
    exposure = next((value for key in ('exposureindex', 'ei', 'cameraei') for value in fields.get(key, [])), '')
    if not exposure:
        exposure_match = re.search(r'ei(\d+)', combined)
        exposure = exposure_match[1] if exposure_match else ''
    matches = set()
    presets = {_normal(preset): preset for preset in COLOURSPACE_LIST}
    for value in values:
        normal = _normal(value)
        if normal in presets:
            matches.add(presets[normal])
        if 'logc4' in normal:
            matches.add('ARRI LogC4')
        if 'logc3' in normal:
            matches.add('ARRI LogC3 (EI800)')
        if normal in ('acescg', 'acesap1linear'):
            matches.add('ACES - ACEScg')
        if normal in ('srgb', 'iec6196621'):
            matches.add('sRGB Encoded Rec.709 (sRGB)')
    if 'slog3' in combined and 'sgamut3cine' in combined:
        matches.add('Input - Sony - S-Log3 - Venice S-Gamut3.Cine')
    if len(matches) > 1 or (matches and 'logc' in combined and any(_normal(v) in ('rec709', 'bt709', 'gamma24') for v in values)):
        return ColourspaceDetection(None, 'conflict', 'Conflicting input colour-space metadata; choose a transform')
    if matches:
        selected = next(iter(matches))
        if selected == 'ARRI LogC3 (EI800)':
            if exposure and _normal(exposure) not in ('800', 'ei800'):
                return ColourspaceDetection(None, 'unknown', f'LogC3 detected with EI {exposure}; choose the matching OCIO transform')
            return ColourspaceDetection(selected, 'metadata', 'LogC3 metadata; EI800 assumed' if not exposure else 'LogC3 and EI800 metadata')
        return ColourspaceDetection(selected, 'metadata', 'Explicit input colour-space metadata')
    if values:
        return ColourspaceDetection(None, 'unknown', f'Unrecognised input colour-space metadata: {", ".join(values)}')
    transfer = [value for value in fields.get('colortransfer', []) if _normal(value) not in ('reserved', 'unknown', 'unspecified', '')]
    primaries = fields.get('colorprimaries', [])
    if any(_normal(value) == 'iec6196621' for value in transfer) and any(_normal(value) == 'bt709' for value in primaries):
        return ColourspaceDetection('sRGB Encoded Rec.709 (sRGB)', 'metadata', 'sRGB transfer and BT.709 primaries')
    if transfer:
        return ColourspaceDetection(None, 'unknown', f'Transfer metadata {", ".join(transfer)} needs a matching manual transform')
    identity = camera_identity(metadata)
    model = _normal(identity['model']).removeprefix('arri')
    if _normal(identity['make']) in ('arri', 'arriag'):
        if model.startswith(('alexa35', 'alexa265')):
            return ColourspaceDetection('ARRI LogC4', 'camera', f'Inferred from {identity["model"]}; verify exported encoding')
        if model in {'alexaminilf', 'alexamini', 'alexalf', 'alexasxt', 'alexa65', 'alexaclassic', 'alexaxt', 'amira'}:
            if exposure and _normal(exposure) not in ('800', 'ei800'):
                return ColourspaceDetection(None, 'unknown', f'{identity["model"]} EI {exposure}; choose the matching OCIO transform')
            ei_reason = 'EI800 metadata' if exposure else 'EI800 assumed'
            return ColourspaceDetection('ARRI LogC3 (EI800)', 'camera', f'Inferred from {identity["model"]}; {ei_reason}; verify exported encoding')
    return ColourspaceDetection(None, 'unknown', 'Input transform not identified; choose manually')


def camera_note_lines(clips):
    """Describe cameras, lenses and retimes, labelling secondary plates."""
    seen_cameras, seen_lenses, lines = set(), set(), []
    for track, metadata in sorted(clips, key=lambda item: item[0]):
        make, model = metadata.get('camera_make', ''), metadata.get('camera_model', '')
        prefix = '' if track == 1 else f'V{track} '
        if (make or model) and (make, model) not in seen_cameras:
            camera = model if make and model.casefold().startswith(make.casefold() + ' ') else ' '.join(value for value in (make, model) if value)
            lines.append(f'{prefix}Camera: {camera}')
            seen_cameras.add((make, model))
        lens = lens_identity(metadata)
        identity = (lens.get('lens_model', ''), lens.get('focal_length_mm', ''))
        if any(identity) and identity not in seen_lenses:
            if identity[0]:
                lines.append(f'{prefix}Lens: {identity[0]}')
            if identity[1]:
                lines.append(f'{prefix}Focal length: {identity[1]} mm')
            seen_lenses.add(identity)
        speed = metadata.get('speed', 1.0)
        if isinstance(speed, (int, float)) and math.isfinite(speed) and speed > 0 and abs(speed - 1.0) >= 1e-9:
            lines.append(f'{prefix}Retime: {speed * 100:.9g}%')
    return lines
