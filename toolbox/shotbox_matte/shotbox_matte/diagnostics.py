"""Classify visible diagnostics while retaining unfiltered raw worker logs."""
from __future__ import annotations


def log_level(message: str) -> str:
    lower = message.casefold()
    if any(part in lower for part in ("traceback (most recent call last)", "generation failed", "fatal error", "runtimeerror:", "error:")):
        return "error"
    if (("matplotlib" in lower and any(word in lower for word in ("not available", "not installed", "could not", "not found")))
            or ("natten" in lower and any(word in lower for word in ("not available", "not installed", "could not", "not found", "fallback", "falling back")))):
        return "optional"
    if any(part in lower for part in ("warning", "leaked ", "leaks detected")):
        return "warning"
    return "info"
