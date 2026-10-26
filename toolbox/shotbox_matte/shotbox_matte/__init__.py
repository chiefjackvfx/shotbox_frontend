"""ShotBox Matte: native LTX Alpha Gen for EXR sequences.

Importing this package never initializes Qt, CUDA, or a model.
"""

import os


if os.name == "nt" and os.environ.get("SHOTBOX_PORTABLE_DIR"):
    from ._windows import configure_dlls

    configure_dlls(os.environ["SHOTBOX_PORTABLE_DIR"])

__version__ = "0.1.0"
