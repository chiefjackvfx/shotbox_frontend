"""Stable OCIO viewing transform for camera-plate thumbnails."""
from functools import lru_cache


CONFIG_NAME = 'studio-config-v4.0.0_aces-v2.0_ocio-v2.5'
DISPLAY = 'sRGB - Display'
VIEW = 'ACES 2.0 - SDR 100 nits (Rec.709)'


@lru_cache(maxsize=32)
def thumbnail_display_processor(input_colourspace):
    """Convert a selected IDT to display RGB; never guess an unknown space."""
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        raise RuntimeError('OpenColorIO is required for colour-managed thumbnails; update Shotbox dependencies.') from exc
    config = ocio.Config.CreateFromBuiltinConfig(CONFIG_NAME)
    config.validate()
    source = config.getColorSpace(input_colourspace)
    if source is None:
        raise ValueError(f'Thumbnail input transform is not available in the ACES config: {input_colourspace}')
    transform = ocio.DisplayViewTransform(src=source.getName(), display=DISPLAY, view=VIEW)
    return config.getProcessor(transform).getDefaultCPUProcessor()
