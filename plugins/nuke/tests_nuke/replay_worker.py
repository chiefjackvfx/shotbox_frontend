"""Test-only CPU replay engine; actual CLI/export, no new model inference.

Previously verified native matte EXRs are resampled into the model raster and
fed through normal spatial restoration/export. This is workflow evidence, not
a new GPU inference or a bit-exact replay of the original full-size mattes.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "toolbox" / "shotbox_matte"))


def main():
    import numpy as np
    import shotbox_matte.native as native
    from shotbox_matte.cli import main as cli_main
    from shotbox_matte.core import JobOptions, plan_job
    from shotbox_matte.imaging import prepare_spatial, read_exr, read_image_window

    worker_path, source_path = map(Path, sys.argv[1:])
    options = JobOptions(**json.loads(worker_path.read_text()))
    plan = plan_job(options)
    source = json.loads(source_path.read_text())
    assert source["state"] == "complete"
    assert source["settings"]["source_color"] == options.source_color
    assert source["capture"]["count"] == plan.selected_count
    assert len(plan.chunks) == 1, "This replay fixture accepts one short temporal chunk only"
    source_root = source_path.parent
    matte_paths = [source_root / record["path"] for record in source["outputs"]]
    for current, previous in zip(plan.selected_files, source["capture"]["files"]):
        current_window = read_image_window(current)
        previous_window = read_image_window(source_root / previous["path"])
        assert current_window == previous_window
        current_pixels = read_exr(current)
        previous_pixels = read_exr(source_root / previous["path"])
        assert np.array_equal(current_pixels, previous_pixels), "Replay fixture input differs from the verified native source capture"
    provenance = {"fixture": "CPU replay of previously verified native GPU outputs; no inference in this attempt",
                  "source_manifest": str(source_path.resolve()), "source_gui_report": "docs/nuke-precomp-gui.json",
                  "resampling": "full-size cached matte -> fitted/padded model raster -> normal CLI full-size restoration/export",
                  "spatial": plan.spatial.to_dict(),
                  "source_outputs": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in matte_paths]}
    worker_path.with_name("replay_fixture.json").write_text(json.dumps(provenance, indent=2))

    class ReplayEngine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            assert frames.shape[0] == plan.chunks[0].padded_count
            prepared = []
            for path in matte_paths:
                pixels = read_exr(path)
                prepared.append(prepare_spatial(pixels, plan.spatial))
            for index in range(frames.shape[0]):
                if kwargs["cancelled"]():
                    from shotbox_matte.core import CancelledError
                    raise CancelledError("Replay cancelled")
                yield prepared[min(index, len(prepared) - 1)].copy()

    native.NativeAlphaEngine = ReplayEngine
    return cli_main(["--worker", str(worker_path)])


if __name__ == "__main__":
    raise SystemExit(main())
