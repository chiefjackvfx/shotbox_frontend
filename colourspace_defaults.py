"""Shared colour-space presets used by the ShotBox frontend."""

from __future__ import annotations


COLOURSPACE_LIST: tuple[str, ...] = (
    "ARRI LogC3 (EI800)",
    "ARRI LogC4",
    "Input - Sony - Linear - Venice S-Gamut3.Cine",
    "Input - Sony - S-Log3 - Venice S-Gamut3.Cine",
    "Input - Canon - Curve - Canon-Log3",
    "Input - RED - REDLog3G10 - REDWideGamutRGB",
    "color_picking",
    "sRGB Encoded Rec.709 (sRGB)",
    "ACES - ACEScg",
)

DEFAULT_COLOURSPACE = COLOURSPACE_LIST[1] 
