"""Keep per-job app state inside each test's temporary workspace."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_job_storage(tmp_path, monkeypatch):
    # The environment also isolates real CLI/desktop worker subprocesses.
    monkeypatch.setenv("SHOTBOX_JOB_DIR", str(tmp_path / "app-job-data"))


@pytest.fixture
def make_mxf():
    """Mux genuine MPEG-2 4:2:2 MXF with distinguishable constant RGB frames."""
    from pathlib import Path
    import numpy as np

    av = pytest.importorskip("av")

    def create(path: Path, count=7, fps=25) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(path), "w", format="mxf") as container:
            stream = container.add_stream("mpeg2video", rate=fps)
            stream.width, stream.height, stream.pix_fmt = 720, 576, "yuv422p"
            stream.bit_rate = 10_000_000
            stream.codec_context.gop_size = 1
            for index in range(count):
                values = np.full((576, 720, 3), 20 + (index * 30) % 210, np.uint8)
                frame = av.VideoFrame.from_ndarray(values, format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return path

    return create
