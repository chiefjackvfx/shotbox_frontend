"""Validate named workflow presets without capturing input or machine-local paths."""
from __future__ import annotations

import math
from typing import Any

from .temporal import TEMPORAL_MODES


INTEGER_RANGES = {
    "frame_skip": (0, 10000), "frame_cap": (0, 1000000),
    "generation_width": (32, 1920), "generation_height": (32, 1088),
    "seed": (0, 2147483647), "padding": (1, 8),
    "first_frame": (0, 2147483647),
    "source_start": (0, 2147483647), "source_end": (0, 2147483647),
}
CHOICES = {
    "source_color": {"auto", "linear-srgb", "srgb", "acescg"},
    "precision": {"half", "float"}, "existing": {"version-up", "overwrite"},
    "offload": {"cpu", "disk", "none"},
    "temporal_mode": set(TEMPORAL_MODES),
}


def validated_preset(value: Any) -> dict[str, Any]:
    """Drop unsupported or malformed values, leaving current settings intact."""
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, (minimum, maximum) in INTEGER_RANGES.items():
        item = value.get(key)
        if item is None and key in {"first_frame", "source_start", "source_end"}:
            if key in value:
                clean[key] = None
        elif isinstance(item, int) and not isinstance(item, bool) and minimum <= item <= maximum:
            clean[key] = item
    for key, choices in CHOICES.items():
        if isinstance(value.get(key), str) and value[key] in choices:
            clean[key] = value[key]
    fps = value.get("fps")
    if isinstance(fps, (int, float)) and not isinstance(fps, bool) and math.isfinite(fps) and 1 <= fps <= 120:
        clean["fps"] = float(fps)
    if isinstance(value.get("suffix"), str):
        clean["suffix"] = value["suffix"]
    if "suffix" in clean:
        from .export import validate_suffix
        try:
            validate_suffix(clean["suffix"])
        except ValueError:
            clean.pop("suffix")
    if isinstance(value.get("low_vram"), bool):
        clean["low_vram"] = value["low_vram"]
    start, end = clean.get("source_start"), clean.get("source_end")
    if start is not None and end is not None and start > end:
        clean.pop("source_start", None)
        clean.pop("source_end", None)
    return clean


def validated_presets(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        name: validated_preset(options)
        for name, options in value.items()
        if isinstance(name, str) and name.strip() and len(name) <= 120 and isinstance(options, dict)
    }
