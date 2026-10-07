"""Native render discovery and floating point EXR playback for Review."""

from pathlib import Path
import re
import threading

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import QImage


def discover_renders(base_path, folders):
    """List render versions using Shotbox's existing EXR sequence metadata."""
    if not base_path:
        return []
    directory = Path(folders.convert_path(base_path)) / "renders" / "comp"
    if not directory.is_dir():
        return []
    options = []
    for path in directory.iterdir():
        match = re.search(r"_v(\d+)(?:\.[^.]+)?$", path.name, re.IGNORECASE)
        version = int(match.group(1)) if match else -1
        if path.is_file() and path.suffix.lower() in {".mov", ".mp4", ".m4v"}:
            options.append({"render_path": str(path), "display_name": path.name,
                            "type": "mov", "version": version})
        elif path.is_dir() and version >= 0:
            info = folders._exr_sequence_info(directory, path, version)
            if info:
                options.append(info)
    return sorted(options, key=lambda item: (item["version"], item["type"] == "exr",
                                            item["display_name"]), reverse=True)


class EXRSequenceReader:
    """Read matching numbered frames without reducing HDR pixels to eight bits."""

    def __init__(self, first_path, fps=24.0, sequence_path=None):
        path = Path(first_path)
        name = Path(sequence_path).name if sequence_path else path.name
        token = r"(#+)" if sequence_path else r"(\d+)"
        match = re.match(rf"^(.*?){token}(\.exr)$", name, re.IGNORECASE)
        if not match:
            raise ValueError("EXR render filenames must end in a frame number.")
        prefix, frame_text, suffix = match.groups()
        self._directory = path.parent
        self._prefix, self._padding, self._suffix = prefix, len(frame_text), suffix
        pattern = re.compile(rf"^{re.escape(prefix)}(\d{{{len(frame_text)}}}){re.escape(suffix)}$",
                             re.IGNORECASE)
        self.frames = {}
        for candidate in path.parent.iterdir():
            numbered = pattern.match(candidate.name)
            if numbered and candidate.is_file():
                self.frames[int(numbered.group(1))] = candidate
        if not self.frames:
            raise FileNotFoundError(f"No EXR frames found: {path}")
        self.first_frame = min(self.frames)
        self.last_frame = max(self.frames)
        self.total_frames = self.last_frame - self.first_frame + 1
        self.fps = float(fps)
        self.duration_ms = int(self.total_frames * 1000 / self.fps)

    def read_frame(self, index):
        import OpenEXR
        import Imath

        number = self.first_frame + index
        path = self.frames.get(number)
        if path is None:
            candidate = self._directory / f"{self._prefix}{number:0{self._padding}d}{self._suffix}"
            if candidate.is_file():
                path = candidate
                self.frames[number] = path
        if path is None:
            raise FileNotFoundError(f"Render frame {number} is missing.")
        source = OpenEXR.InputFile(str(path))
        try:
            header = source.header()
            data_window = header["dataWindow"]
            width = data_window.max.x - data_window.min.x + 1
            height = data_window.max.y - data_window.min.y + 1
            names = header["channels"]
            if all(channel in names for channel in ("R", "G", "B")):
                channels = ["R", "G", "B"]
            else:
                prefixes = sorted(name[:-1] for name in names if name.endswith("R"))
                channels = next(([prefix + c for c in "RGB"] for prefix in prefixes
                                 if all(prefix + c in names for c in "RGB")), None)
                if not channels:
                    raise ValueError(f"No RGB channels found in {path.name}.")
            pixels = None
            # The modern binding releases Python's lock while decoding. Use it
            # for ordinary RGB(A) images so workers really can run concurrently
            # and Qt stays responsive. Avoid loading every AOV/part in VFX EXRs.
            if set(names) <= {"R", "G", "B", "A"} and hasattr(OpenEXR, "File"):
                with OpenEXR.File(str(path), header_only=True) as metadata:
                    single_part = len(metadata.parts) == 1
                if single_part:
                    with OpenEXR.File(str(path)) as image:
                        grouped = image.channels()
                        rgb = grouped.get("RGB", grouped.get("RGBA"))
                        if rgb is not None:
                            pixels = np.array(rgb.pixels[..., :3], dtype=np.float32,
                                              order="C", copy=True)
            if pixels is None:
                pixel_type = Imath.PixelType(Imath.PixelType.FLOAT)
                # Read RGB together: separate channel() calls decompress the
                # same compressed scanlines three times.
                buffers = source.channels(channels, pixel_type)
                planes = [np.frombuffer(buffer, dtype=np.float32).reshape(height, width)
                          for buffer in buffers]
                pixels = np.stack(planes, axis=-1)
            # Keep cropped render data in its original display-window position.
            display = header["displayWindow"]
            full_width = display.max.x - display.min.x + 1
            full_height = display.max.y - display.min.y + 1
            if data_window != display:
                full = np.zeros((full_height, full_width, 3), dtype=np.float32)
                x, y = data_window.min.x - display.min.x, data_window.min.y - display.min.y
                x0, y0 = max(0, x), max(0, y)
                x1, y1 = min(full_width, x + width), min(full_height, y + height)
                if x1 > x0 and y1 > y0:
                    full[y0:y1, x0:x1] = pixels[y0-y:y1-y, x0-x:x1-x]
                pixels = full
            return pixels
        finally:
            source.close()

    def close(self):
        """Each background frame read owns and closes its own file handle."""


def display_image(pixels, processor=None, *, owned=False):
    """Apply OCIO to float RGB before clipping for the screen."""
    # Decoder-owned arrays can be transformed in place. Public callers keep
    # their HDR pixels unchanged unless they explicitly transfer ownership.
    rgb = (pixels if owned and pixels.dtype == np.float32 and
           pixels.flags.c_contiguous and pixels.flags.writeable else
           np.array(pixels, dtype=np.float32, order="C", copy=True))
    if processor is not None:
        processor.applyRGB(rgb)
    np.clip(rgb, 0, 1, out=rgb)
    # clip already handles infinities; avoid allocating three full-size masks.
    np.copyto(rgb, 0, where=np.isnan(rgb))
    np.multiply(rgb, 255, out=rgb)
    np.rint(rgb, out=rgb)
    encoded = rgb.astype(np.uint8)
    height, width, _ = encoded.shape
    return QImage(encoded.data, width, height, encoded.strides[0],
                  QImage.Format.Format_RGB888).copy()


def movie_display_image(frame, processor=None, scale=1):
    """Convert an owned movie frame, optionally reducing pixels before OCIO."""
    pixel_format = "rgb48le" if processor is not None else "rgb24"
    if scale > 1:
        frame = frame.reformat(width=max(1, frame.width // scale),
                               height=max(1, frame.height // scale), format=pixel_format)
    array = frame.to_ndarray(format=pixel_format)
    if processor is not None:
        pixels = array.astype(np.float32)
        pixels *= 1.0 / 65535.0
        return display_image(pixels, processor, owned=True)
    array = np.ascontiguousarray(array)
    height, width, _ = array.shape
    return QImage(array.data, width, height, array.strides[0], QImage.Format.Format_RGB888).copy()


class MovieCacheReader:
    """Own a separate decoder; serialize reads while workers convert in parallel."""

    def __init__(self, path, reader_factory):
        self.path = path
        self.reader_factory = reader_factory
        self._reader = None
        self._lock = threading.Lock()
        self._closed = False

    def read_image(self, index, processor, scale):
        with self._lock:
            try:
                if self._closed:
                    raise RuntimeError("Movie cache reader closed")
                if self._reader is None:
                    self._reader = self.reader_factory()
                    self._reader.open(self.path)
                if self._reader.current_frame_index + 1 == index:
                    frame, actual = self._reader.decode_next()
                else:
                    frame, actual = self._reader.seek_to_frame(index)
                if frame is None or actual != index:
                    raise ValueError(f"Movie frame {index} is unavailable")
            finally:
                if self._closed and self._reader is not None:
                    self._reader.close()
                    self._reader = None
        return movie_display_image(frame, processor, scale)

    def close(self):
        # Switching media must not wait for an in-flight disk read. The reader
        # performing that read closes its container when it releases the lock.
        self._closed = True
        if self._lock.acquire(blocking=False):
            try:
                if self._reader is not None:
                    self._reader.close()
                    self._reader = None
            finally:
                self._lock.release()


class FrameSignals(QObject):
    finished = pyqtSignal(int, int, object, str)


class ReviewFrameTask(QRunnable):
    """Decode and colour-convert one EXR or movie frame outside the GUI thread."""

    def __init__(self, generation, index, reader, processor, scale=1):
        super().__init__()
        self.generation, self.index = generation, index
        self.reader, self.processor = reader, processor
        self.scale = scale
        self.signals = FrameSignals()

    def run(self):
        try:
            if isinstance(self.reader, MovieCacheReader):
                image = self.reader.read_image(self.index, self.processor, self.scale)
            else:
                pixels = self.reader.read_frame(self.index)
                if self.scale > 1:
                    pixels = np.ascontiguousarray(pixels[::self.scale, ::self.scale])
                image = display_image(pixels, self.processor, owned=True)
            error = ""
        except Exception as exc:
            image, error = None, str(exc)
        self.signals.finished.emit(self.generation, self.index, image, error)
