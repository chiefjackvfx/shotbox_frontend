from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media_colourspace import camera_identity, camera_note_lines, detect_input_colourspace


@pytest.mark.parametrize('model,expected', [
    ('ALEXA Mini LF', 'ARRI LogC3 (EI800)'), ('ALEXA Mini', 'ARRI LogC3 (EI800)'),
    ('ALEXA 35', 'ARRI LogC4'), ('ALEXA 35 Xtreme', 'ARRI LogC4'), ('ALEXA 265', 'ARRI LogC4'),
])
def test_known_camera_model_inference_is_labelled(model, expected):
    result = detect_input_colourspace({'format.company_name': 'ARRI', 'format.product_name': model,
                                      'video.color_space': 'gbr', 'video.color_transfer': 'reserved', 'video.color_primaries': 'reserved'})
    assert result.colourspace == expected
    assert result.source == 'camera'
    assert 'Inferred' in result.reason


def test_explicit_encoding_overrides_camera_inference():
    result = detect_input_colourspace({'company_name': 'ARRI', 'product_name': 'ALEXA Mini LF', 'gamma': 'LogC4'})
    assert result.colourspace == 'ARRI LogC4'
    assert result.source == 'metadata'


def test_conflicting_explicit_metadata_needs_manual_choice():
    result = detect_input_colourspace({'format.input_colorspace': 'LogC3', 'stream.input_colorspace': 'LogC4'})
    assert result.colourspace is None
    assert result.source == 'conflict'


@pytest.mark.parametrize('metadata', [
    {}, {'filename': 'ARRI_ALEXA_Mini_LF_LogC4.mxf'}, {'camera_make': 'Sony', 'camera_model': 'VENICE'},
    {'company_name': 'ARRI', 'product_name': 'Unknown camera'},
    {'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF', 'input_colorspace': 'Unsupported custom space'},
    {'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF', 'video.color_transfer': 'bt709'},
])
def test_unknown_or_unsupported_metadata_is_not_guessed(metadata):
    assert detect_input_colourspace(metadata).colourspace is None


@pytest.mark.parametrize('metadata', [
    {'gamma': 'LogC3', 'exposure_index': '1600'}, {'input_colorspace': 'ARRI LogC3 (EI400)'},
])
def test_non_800_exposure_needs_matching_manual_transform(metadata):
    assert detect_input_colourspace(metadata).colourspace is None


def test_logc3_missing_exposure_is_visible_assumption():
    result = detect_input_colourspace({'gamma': 'LogC3'})
    assert result.colourspace == 'ARRI LogC3 (EI800)'
    assert 'EI800 assumed' in result.reason


def test_sony_requires_explicit_gamut_and_gamma():
    assert detect_input_colourspace({'gamma': 'S-Log3'}).colourspace is None
    result = detect_input_colourspace({'gamma': 'S-Log3', 'gamut': 'S-Gamut3.Cine'})
    assert result.colourspace == 'Input - Sony - S-Log3 - Venice S-Gamut3.Cine'


def test_explicit_srgb_transfer_overrides_camera_model():
    result = detect_input_colourspace({'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF',
                                      'video.color_space': 'bt709', 'video.color_transfer': 'iec61966-2-1', 'video.color_primaries': 'bt709'})
    assert result.colourspace == 'sRGB Encoded Rec.709 (sRGB)'
    assert result.source == 'metadata'


def test_quicktime_camera_tags_and_software_names():
    assert camera_identity({'format.com.apple.quicktime.make': 'Sony', 'format.com.apple.quicktime.model': 'VENICE 2'}) == {'make': 'Sony', 'model': 'VENICE 2'}
    assert camera_identity({'company_name': 'Blackmagic Design', 'product_name': 'DaVinci Resolve'})['model'] == ''


def test_camera_notes_are_distinct_per_camera_and_label_secondary_layers():
    arri = {'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF'}
    sony = {'camera_make': 'Sony', 'camera_model': 'VENICE 2'}
    lines = camera_note_lines([(1, arri), (2, arri), (3, sony)])
    assert lines == ['Camera: ARRI ALEXA Mini LF', 'V3 Camera: Sony VENICE 2']
    assert camera_note_lines([(1, {})]) == []


def test_different_lenses_on_same_camera_are_kept_and_duplicate_notes_omitted():
    primary = {'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF',
               'lens_model': 'TRIBE7 TRIBE 7 BLACKWINGS 37 T 1', 'focal_length_mm': '37'}
    secondary = dict(primary, lens_model='TRIBE 7 BLACKWINGS 47 1155', focal_length_mm='47.5')
    lines = camera_note_lines([(1, primary), (2, secondary), (3, primary)])
    assert lines == ['Camera: ARRI ALEXA Mini LF',
                     'Lens: TRIBE7 TRIBE 7 BLACKWINGS 37 T 1', 'Focal length: 37 mm',
                     'V2 Lens: TRIBE 7 BLACKWINGS 47 1155', 'V2 Focal length: 47.5 mm']
    assert camera_note_lines([(2, {'lens_model': 'Unknown lens', 'focal_length_mm': '0'})]) == ['V2 Lens: Unknown lens']
    assert camera_note_lines([(1, {'focal_length_mm': 'NaN'})]) == []


def test_known_embedded_ei_is_not_labelled_as_assumed():
    result = detect_input_colourspace({'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF', 'exposure_index': '800'})
    assert 'EI800 metadata' in result.reason
    assert 'assumed' not in result.reason
