"""Lightweight terminal diagnostics; never import GUI or inference libraries."""

from __future__ import annotations

from importlib import metadata
import os
import platform
import shutil
import sys
from typing import TextIO

from app_version import APP_VERSION


# Derived from ui/shotbox_logo.png: crop to the alpha bounding box, resize
# to 28 x 14 using area averaging, ignore alpha < 100, then map orange to @
# and charcoal to #. Keep the result here so startup needs no image decoder.
_LOGO = (
    "    @@@@@@@@@###########",
    "  @@@@@@@@@@@#############",
    " @@@@@@@@@@@@####   #######",
    "@@@@@       #####     #####",
    "@@@@@       #####     #####",
    " @@@@@@@    ##############",
    "  @@@@@@@@@ #############",
    "   @@@@@@@@@@##############",
    "       @@@@@@@###     ######",
    "         @@@@@@##      #####",
    "         @@@@@@##     ######",
    " @@@@@@@@@@@@@###   #######",
    "@@@@@@@@@@@@@#############",
    " @@@@@@@@@@ ############",
)
_PACKAGES = (
    ("PyQt6", "PyQt6"),
    ("Qt (bundled)", "PyQt6-Qt6"),
    ("NumPy", "numpy"),
    ("OpenImageIO", "OpenImageIO"),
    ("OpenColorIO", "opencolorio"),
    ("OpenEXR", "OpenEXR"),
    ("PyAV", "av"),
    ("PyTorch", "torch"),
    ("Transformers", "transformers"),
)
_ORANGE = "\x1b[38;2;240;90;32m"
_GREY = "\x1b[37m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _package_version(package: str) -> str:
    try:
        return metadata.version(package) or "unknown"
    except metadata.PackageNotFoundError:
        return "not installed"
    except Exception:
        # Broken package metadata is diagnostic information, not a boot error.
        return "unknown"


def render_banner(color: bool = False, width: int = 100) -> str:
    """Return installed versions and logo, without probing native libraries."""
    def styled(value: str, style: str) -> str:
        return f"{style}{value}{_RESET}" if color else value

    rows = [
        ("Python", platform.python_version()),
        ("OS", f"{platform.system()} / {platform.machine()}"),
        *((label, _package_version(package)) for label, package in _PACKAGES),
    ]
    info = [styled(f"SHOTBOX {APP_VERSION}", _BOLD), "-" * 27]
    info.extend(f"{styled(label.ljust(14), _ORANGE)}{value}" for label, value in rows)

    if width >= 80:
        lines = []
        for index in range(max(len(_LOGO), len(info))):
            logo = _LOGO[index] if index < len(_LOGO) else ""
            if color:
                logo = "".join(
                    styled(char, _ORANGE if char == "@" else _GREY)
                    if char != " " else char
                    for char in logo
                )
            padding = 28 - len(_LOGO[index]) if index < len(_LOGO) else 28
            detail = info[index] if index < len(info) else ""
            lines.append((logo + " " * (padding + 4) + detail).rstrip())
    else:
        lines = info

    lines.extend(("", f"{styled('Python executable:', _ORANGE)} {sys.executable}"))
    if color:
        # Standard terminal palette, aligned with the information column.
        indent = " " * 32 if width >= 80 else ""
        palette = "".join(f"\x1b[{background}m   " for background in range(40, 48))
        lines.extend(("", f"{indent}{palette}{_RESET}"))
    return "\n".join(lines)


def _supports_color(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ or not stream.isatty():
        return False
    term = os.environ.get("TERM", "").lower()
    if term in {"dumb", "unknown"}:
        return False
    if sys.platform == "win32":
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("ANSICON")
            or os.environ.get("ConEmuANSI") == "ON"
            or term.startswith(("xterm", "vt", "ansi", "cygwin", "msys"))
        )
    return True


def print_banner(stream: TextIO | None = None) -> None:
    """Print once when called; console failures must never interrupt boot."""
    try:
        output = sys.stdout if stream is None else stream
        if output is None:
            return
        try:
            width = os.get_terminal_size(output.fileno()).columns
        except (AttributeError, OSError, ValueError):
            width = shutil.get_terminal_size(fallback=(100, 24)).columns
        print(render_banner(color=_supports_color(output), width=width),
              file=output, flush=True)
    except Exception:
        # Includes detached GUI launches, closed pipes and encoding failures.
        return
