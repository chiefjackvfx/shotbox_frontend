"""Float EXR I/O, explicit display preparation, and reversible image geometry."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import math


SOURCE_COLORS = ("linear-srgb", "srgb", "acescg")


def _oiio():
    try:
        import OpenImageIO as oiio
    except ImportError as exc:
        raise RuntimeError("OpenImageIO is required for EXR input/output. Install ShotBox Matte's declared dependencies.") from exc
    return oiio


@dataclass(frozen=True)
class ImageWindow:
    """A flat image's data window and display window, in source pixel coordinates.

    Width/height describe the stored raster. Display windows can be smaller than
    the data window (overscan) or larger (a cropped plate); neither changes the
    raster sent to inference. Negative origins are valid.
    """
    x: int
    y: int
    width: int
    height: int
    full_x: int
    full_y: int
    full_width: int
    full_height: int
    pixel_aspect_ratio: float = 1.0

    def __post_init__(self):
        names = ("x", "y", "width", "height", "full_x", "full_y", "full_width", "full_height")
        if any(not isinstance(getattr(self, name), int) or isinstance(getattr(self, name), bool) for name in names):
            raise ValueError("Image data/display windows must use integer coordinates and dimensions.")
        if min(self.width, self.height, self.full_width, self.full_height) < 1:
            raise ValueError("Image data/display window dimensions must be positive.")
        if not isinstance(self.pixel_aspect_ratio, (int, float)) or not math.isfinite(self.pixel_aspect_ratio) or self.pixel_aspect_ratio <= 0:
            raise ValueError("Image pixel aspect ratio must be finite and positive.")

    def to_dict(self) -> dict:
        return asdict(self)


def _window_from_spec(spec, path: Path | str) -> ImageWindow:
    if spec.deep:
        raise ValueError(f"Deep EXRs are unsupported: {path}")
    if spec.depth != 1 or spec.full_depth != 1 or spec.z != 0 or spec.full_z != 0:
        raise ValueError(f"3D/volume images are unsupported; expected flat 2D data/display windows: {path}")
    return ImageWindow(int(spec.x), int(spec.y), int(spec.width), int(spec.height),
                       int(spec.full_x), int(spec.full_y), int(spec.full_width), int(spec.full_height),
                       float(spec.get_float_attribute("PixelAspectRatio", 1.0)))


def read_image_window(path: Path | str) -> ImageWindow:
    """Read only the header, preserving data/display origins and pixel aspect."""
    oiio = _oiio()
    reader = oiio.ImageInput.open(str(path))
    if reader is None:
        raise RuntimeError(f"Cannot open image {path}: {oiio.geterror()}")
    try:
        return _window_from_spec(reader.spec(), path)
    finally:
        reader.close()


def read_dimensions(path: Path | str) -> tuple[int, int]:
    window = read_image_window(path)
    return window.width, window.height


def read_exr(path: Path | str):
    """Read RGB float pixels with OIIO; also used for supported image sequences."""
    import numpy as np
    oiio = _oiio()
    reader = oiio.ImageInput.open(str(path))
    if reader is None:
        raise RuntimeError(f"Cannot open image {path}: {oiio.geterror()}")
    try:
        spec = reader.spec()
        _window_from_spec(spec, path)
        names = list(spec.channelnames)
        channels = {name.casefold(): index for index, name in enumerate(names)}
        rgb = [channels.get(c) for c in ("r", "g", "b")]
        if any(i is None for i in rgb):
            # A sole named layer is useful; ambiguous multilayer files need an explicit extraction.
            layers = {name.rsplit(".", 1)[0] for name in names if "." in name}
            matches = [[channels.get(f"{layer}.{c}".casefold()) for c in ("r", "g", "b")]
                       for layer in sorted(layers)]
            matches = [indices for indices in matches if all(i is not None for i in indices)]
            if len(matches) == 1:
                rgb = matches[0]
            elif "y" in channels:
                rgb = [channels["y"]] * 3
            else:
                raise ValueError(f"Image needs unambiguous R/G/B or Y channels: {path}; found {names}")
        pixels = reader.read_image(format=oiio.FLOAT)
        if pixels is None:
            raise RuntimeError(f"Cannot read image {path}: {reader.geterror()}")
        pixels = np.asarray(pixels, dtype=np.float32).reshape(spec.height, spec.width, spec.nchannels)
        result = np.ascontiguousarray(pixels[..., rgb], dtype=np.float32)
        if not np.isfinite(result).all():
            raise ValueError(f"Source image contains NaN or infinite values: {path}")
        return result
    finally:
        reader.close()


def read_alpha_exr(path: Path | str):
    """Decode a single flat A channel as finite float data without colour work."""
    import numpy as np
    oiio = _oiio()
    reader = oiio.ImageInput.open(str(path))
    if reader is None:
        raise RuntimeError(f"Cannot read alpha image {path}: {oiio.geterror()}")
    try:
        spec = reader.spec()
        _window_from_spec(spec, path)
        if spec.nchannels != 1 or list(spec.channelnames) != ["A"]:
            raise ValueError(f"Expected exactly one alpha A channel: {path}")
        pixels = reader.read_image(format=oiio.FLOAT)
        if pixels is None:
            raise RuntimeError(f"Cannot decode alpha image {path}: {reader.geterror()}")
        result = np.ascontiguousarray(np.asarray(pixels, dtype=np.float32).reshape(spec.height, spec.width))
        if not np.isfinite(result).all():
            raise ValueError(f"Alpha image contains NaN or infinite values: {path}")
        return result
    finally:
        reader.close()


def _acescg_to_linear_srgb():
    """Derive AP1/D60 -> Rec.709/D65 from chromaticities with Bradford adaptation."""
    import numpy as np

    def xyz(xy):
        x, y = xy
        return np.array([x / y, 1.0, (1 - x - y) / y])

    def primaries(points, white):
        p = np.array([xyz(point) for point in points]).T
        return p @ np.diag(np.linalg.solve(p, xyz(white)))

    d60, d65 = (0.32168, 0.33767), (0.3127, 0.3290)
    ap1 = primaries(((0.713, 0.293), (0.165, 0.830), (0.128, 0.044)), d60)
    rec709 = primaries(((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), d65)
    bradford = np.array([[.8951, .2664, -.1614], [-.7502, 1.7135, .0367], [.0389, -.0685, 1.0296]])
    adapt = np.linalg.inv(bradford) @ np.diag((bradford @ xyz(d65)) / (bradford @ xyz(d60))) @ bradford
    return (np.linalg.inv(rec709) @ adapt @ ap1).astype(np.float32)


def prepare_color(pixels, source_color: str = "linear-srgb"):
    """Make bounded display RGB for the model; this is never used on exported mattes."""
    import numpy as np
    values = np.asarray(pixels, dtype=np.float32)
    if values.ndim < 3 or values.shape[-1] != 3 or not np.isfinite(values).all():
        raise ValueError("Expected finite RGB source pixels.")
    if source_color not in SOURCE_COLORS:
        raise ValueError(f"Unsupported source colour {source_color!r}; choose {', '.join(SOURCE_COLORS)}.")
    if source_color == "acescg":
        values = values @ _acescg_to_linear_srgb().T
    values = np.clip(values, 0.0, 1.0)
    if source_color != "srgb":
        values = np.where(values <= 0.0031308, values * 12.92, 1.055 * np.power(values, 1 / 2.4) - 0.055)
    return np.ascontiguousarray(values, dtype=np.float32)


@dataclass(frozen=True)
class SpatialPlan:
    source_width: int
    source_height: int
    resized_width: int
    resized_height: int
    width: int
    height: int
    left: int
    top: int

    def to_dict(self):
        return asdict(self)


def spatial_plan(width: int, height: int, target_width: int, target_height: int) -> SpatialPlan:
    if any(not isinstance(n, int) or n < 1 for n in (width, height, target_width, target_height)):
        raise ValueError("Source and generation dimensions must be positive integers.")
    if target_width > 1920 or target_height > 1088:
        raise ValueError("Alpha Gen supports generation bounds up to 1920 x 1088; lower the generation resolution.")
    scale = min(target_width / width, target_height / height)
    rw, rh = max(1, round(width * scale)), max(1, round(height * scale))
    pw, ph = ((rw + 31) // 32) * 32, ((rh + 31) // 32) * 32
    return SpatialPlan(width, height, rw, rh, pw, ph, (pw - rw) // 2, (ph - rh) // 2)


def resize_float(pixels, width: int, height: int):
    """Resize independent float channels: Pillow F mode preserves values outside [0,1]."""
    import numpy as np
    from PIL import Image
    values = np.asarray(pixels, dtype=np.float32)
    if values.shape[:2] == (height, width):
        return np.ascontiguousarray(values)
    return np.stack([np.asarray(Image.fromarray(values[..., c]).resize((width, height), Image.Resampling.BILINEAR))
                     for c in range(values.shape[-1])], axis=-1).astype(np.float32)


def prepare_spatial(pixels, plan: SpatialPlan):
    import numpy as np
    if pixels.shape[:2] != (plan.source_height, plan.source_width):
        raise ValueError(f"Sequence frame size changed: expected {plan.source_width} x {plan.source_height}, got {pixels.shape[1]} x {pixels.shape[0]}.")
    values = resize_float(pixels, plan.resized_width, plan.resized_height)
    return np.pad(values, ((plan.top, plan.height - plan.resized_height - plan.top),
                           (plan.left, plan.width - plan.resized_width - plan.left), (0, 0)), mode="edge")


def restore_spatial(pixels, plan: SpatialPlan):
    import numpy as np
    values = np.asarray(pixels, dtype=np.float32)
    if values.shape[:2] != (plan.height, plan.width):
        raise ValueError(f"Native inference returned {values.shape[:2]}, expected {(plan.height, plan.width)}; refusing an incorrect crop.")
    if values.ndim == 2:
        values = values[..., None]
    # The reference converts the red matte channel to a mask. Export equal R/G/B values.
    values = np.repeat(values[..., :1], 3, axis=-1)
    values = values[plan.top:plan.top + plan.resized_height, plan.left:plan.left + plan.resized_width]
    return resize_float(values, plan.source_width, plan.source_height)


def write_exr(path: Path | str, pixels, precision: str = "half", *, window: ImageWindow | None = None,
              channels: str = "rgb") -> None:
    import numpy as np
    oiio = _oiio()
    if precision not in ("half", "float"):
        raise ValueError("EXR precision must be 'half' or 'float'.")
    if channels not in ("rgb", "alpha"):
        raise ValueError("EXR channels must be 'rgb' or 'alpha'.")
    values = np.ascontiguousarray(pixels, dtype=np.float32)
    if values.ndim != 3 or values.shape[-1] != 3 or not np.isfinite(values).all():
        raise ValueError("Output matte must contain finite RGB values.")
    if precision == "half" and np.any(np.abs(values) > 65504):
        raise ValueError("Matte exceeds half-float range; export full-float EXRs.")
    if channels == "alpha":
        if not np.array_equal(values[..., 0], values[..., 1]) or not np.array_equal(values[..., 0], values[..., 2]):
            raise ValueError("Alpha-only output requires equal RGB matte channels; refusing to average colour values.")
        values = np.ascontiguousarray(values[..., :1])
    h, w, _ = values.shape
    if window is not None and (window.width, window.height) != (w, h):
        raise ValueError(f"EXR raster is {w} x {h}, but its data window is {window.width} x {window.height}.")
    spec = oiio.ImageSpec(w, h, values.shape[-1], oiio.HALF if precision == "half" else oiio.FLOAT)
    if window is not None:
        spec.x, spec.y = window.x, window.y
        spec.full_x, spec.full_y = window.full_x, window.full_y
        spec.full_width, spec.full_height = window.full_width, window.full_height
        spec.attribute("PixelAspectRatio", float(window.pixel_aspect_ratio))
    spec.channelnames = ["A"] if channels == "alpha" else ["R", "G", "B"]
    if channels == "alpha":
        spec.alpha_channel = 0
    spec.attribute("compression", "zip")
    writer = oiio.ImageOutput.create(str(path))
    if writer is None:
        raise RuntimeError(f"Cannot create EXR writer for {path}: {oiio.geterror()}")
    try:
        if not writer.open(str(path), spec):
            raise RuntimeError(f"Cannot open EXR output {path}: {writer.geterror()}")
        if not writer.write_image(values):
            raise RuntimeError(f"Cannot write EXR {path}: {writer.geterror()}")
    finally:
        closed = writer.close()
    if closed is False:
        raise RuntimeError(f"Cannot finish EXR {path}: {writer.geterror()}")


def write_preview(path: Path | str, pixels) -> None:
    import numpy as np
    from PIL import Image
    # The PNG is a display aid only. Clipping here never changes EXR data.
    preview = Image.fromarray(np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8))
    preview.thumbnail((960, 540))
    preview.save(path)
