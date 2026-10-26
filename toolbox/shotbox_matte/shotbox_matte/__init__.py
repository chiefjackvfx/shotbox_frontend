"""ShotBox Matte: native LTX Alpha Gen for EXR sequences.

Importing this package never initializes Qt, CUDA, or a model.
"""

import os


if os.name == "nt":
    from ._windows import configure_dlls

    configure_dlls()

__version__ = "0.1.0"
