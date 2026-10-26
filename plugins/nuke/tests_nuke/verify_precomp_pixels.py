"""Compare every precomp pixel against original ACEScg and native matte output.

Run using the external .venv Python, after GUI acceptance and headless reopen.
This inspection uses OpenImageIO; no inference is performed.
"""
import argparse
import json
from pathlib import Path
import traceback

import numpy as np
import OpenImageIO as oiio


def read(path):
    handle = oiio.ImageInput.open(str(path))
    assert handle is not None, oiio.geterror()
    try:
        spec = handle.spec()
        pixels = handle.read_image(format=oiio.FLOAT)
        assert pixels is not None, handle.geterror()
        return pixels, list(spec.channelnames)
    finally:
        handle.close()


def channels(pixels, names, wanted):
    return pixels[..., [names.index(name) for name in wanted]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--reopen-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    reopened = json.loads(args.reopen_report.read_text())
    report = {"inspection": "OpenImageIO float pixel arrays; exact equality, every pixel", "frames": []}
    try:
        assert evidence["status"] == reopened["status"] == "passed"
        for version in evidence["published_versions"]:
            manifest_path = Path(version["manifest"])
            manifest = json.loads(manifest_path.read_text())
            for frame in range(evidence["range"][0], evidence["range"][1] + 1):
                index = frame - evidence["range"][0]
                precomp_path = manifest_path.parent / manifest["precomp"]["files"][index]["path"]
                matte_path = manifest_path.parent / manifest["outputs"][index]["path"]
                plate_path = evidence["source_pattern"] % frame
                plate, plate_channels = read(plate_path)
                precomp, precomp_channels = read(precomp_path)
                matte, matte_channels = read(matte_path)
                assert np.array_equal(channels(precomp, precomp_channels, ["R", "G", "B"]),
                                      channels(plate, plate_channels, ["R", "G", "B"])), (frame, "RGB differs")
                assert np.array_equal(channels(precomp, precomp_channels, ["A"]),
                                      channels(matte, matte_channels, ["R"])), (frame, "Alpha differs")
                assert np.all(channels(precomp, precomp_channels, ["shotboxAux.value"]) == 42), (frame, "Auxiliary channel differs")
                gui_render_path = version["rendered_pattern"] % frame
                gui_render, gui_channels = read(gui_render_path)
                assert set(gui_channels) == set(precomp_channels)
                assert np.array_equal(channels(gui_render, gui_channels, precomp_channels), precomp), (frame, "GUI rendered precomp differs")
                if index == 0:
                    frozen, frozen_channels = read(version["frozen_pattern"] % frame)
                    assert set(frozen_channels) == set(precomp_channels)
                    assert np.array_equal(channels(frozen, frozen_channels, precomp_channels), precomp), "Precomp changed with upstream grade"
                    bypass, bypass_channels = read(version["bypass_pattern"] % frame)
                    assert np.array_equal(channels(bypass, bypass_channels, ["R", "G", "B"]),
                                          channels(plate, plate_channels, ["R", "G", "B"]) * np.float32(0.5)), "Bypass did not render live grade"
                    assert np.all(channels(bypass, bypass_channels, ["shotboxAux.value"]) == 42)
                    if "A" in plate_channels:
                        assert np.array_equal(channels(bypass, bypass_channels, ["A"]), channels(plate, plate_channels, ["A"]))
                rendered_path = None
                if version is evidence["published_versions"][-1]:
                    rendered_path = reopened["rendered_pattern"] % frame
                    rendered, rendered_channels = read(rendered_path)
                    assert set(rendered_channels) == set(precomp_channels)
                    assert np.array_equal(channels(rendered, rendered_channels, precomp_channels), precomp), (frame, "Cached render differs")
                report["frames"].append({"frame": frame, "precomp": str(precomp_path), "matte": str(matte_path),
                                         "source": plate_path, "rendered": rendered_path, "gui_rendered": gui_render_path,
                                         "dimensions": list(precomp.shape[:2][::-1]), "channels": precomp_channels,
                                         "rgb_exact": True, "alpha_exact": True, "auxiliary_exact": True})
        report.update(status="passed", passed=["every RGB pixel of both generated versions is bit exact to source ACEScg EXR",
                                                "every alpha pixel is bit exact to corresponding native backend matte red",
                                                "auxiliary channel preserved at42 over every pixel",
                                                "actual GUI Write output exactly matches published precomp on every frame",
                                                "upstream grade changes preserve frozen precomp; bypass renders changed live RGB and preserves source alpha",
                                                "every channel/pixel in fresh cached farm render is bit exact to published final version"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SHOTBOX_PRECOMP_PIXELS_PASS " + str(args.report))


if __name__ == "__main__":
    main()
