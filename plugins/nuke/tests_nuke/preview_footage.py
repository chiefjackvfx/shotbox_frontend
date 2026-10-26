"""Create explicit display-only QC sheets and compare full captured RGB.

Run in ShotBox Python, never in Nuke. Source ACEScg is converted using the
backend's existing Rec.709/sRGB preparation; this is not an OCIO show view.
Preview mattes are clamped for display; immutable EXRs are never altered.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "toolbox" / "shotbox_matte"))


def main():
    import numpy as np
    from PIL import Image, ImageDraw
    from shotbox_matte.imaging import prepare_color, read_exr
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    manifest_path = Path(report["attempts"][0]["manifest"])
    manifest = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    args.output.mkdir(parents=True, exist_ok=True)
    capture = manifest["capture"]
    selected = {capture["start"], (capture["start"]+capture["end"])//2, capture["end"]}
    thumbnail_width = 512
    thumbnail_height = round(capture["height"] * thumbnail_width / capture["width"])
    header, gutter = 32, 8
    contact = Image.new("RGB", (thumbnail_width*3 + gutter*4, (thumbnail_height+header)*len(selected)+gutter), "#202125")
    draw = ImageDraw.Draw(contact)
    stats = []
    render_checks = {}
    row = 0
    for index, record in enumerate(capture["files"]):
        frame = capture["start"] + index
        source_path = Path(report["source_pattern"] % frame)
        source = read_exr(source_path)
        captured = read_exr(root / record["path"])
        difference = float(np.max(np.abs(source - captured)))
        if source.shape != captured.shape or not np.array_equal(source, captured):
            raise AssertionError(f"Full captured RGB differs at timeline {frame}: max error {difference}")
        del captured
        matte_path = root / manifest["outputs"][index]["path"]
        matte = read_exr(matte_path)[..., 0]
        cached_render = Path(report["review_script"]).parent / "cached-render" / f"verify.{frame:04d}.exr"
        if frame == capture["start"] and cached_render.is_file():
            import OpenImageIO as oiio
            if not np.array_equal(source, read_exr(cached_render)):
                raise AssertionError("Cached rendered RGB differs from original raw source")
            reader = oiio.ImageInput.open(str(cached_render))
            if reader is None:
                raise RuntimeError("Cannot open cached rendered RGBA image")
            try:
                spec = reader.spec()
                pixels = np.asarray(reader.read_image(format=oiio.FLOAT), dtype=np.float32).reshape(spec.height, spec.width, spec.nchannels)
                names = [name.casefold() for name in spec.channelnames]
                if not np.array_equal(matte, pixels[..., names.index("a")]):
                    raise AssertionError("Cached rendered alpha differs from raw backend matte red")
            finally:
                reader.close()
            del pixels
            render_checks.update(cached_render_full_rgb_bit_exact=True, cached_render_full_alpha_bit_exact=True,
                                 cached_render_compared_frame=frame)
        stats.append({"frame": frame, "capture_rgb_max_abs_error": difference,
                      "matte_min": float(matte.min()), "matte_max": float(matte.max()),
                      "matte_mean": float(matte.mean()), "coverage_above_half": float(np.mean(matte > 0.5))})
        if frame not in selected:
            continue
        display = prepare_color(source, report["source_color"])
        source_image = Image.fromarray(np.uint8(np.clip(display, 0, 1)*255)).resize((thumbnail_width, thumbnail_height), Image.Resampling.LANCZOS)
        matte_image = Image.fromarray(np.uint8(np.clip(matte, 0, 1)*255)).resize((thumbnail_width, thumbnail_height), Image.Resampling.LANCZOS).convert("RGB")
        small = np.asarray(source_image, dtype=np.float32)/255
        alpha = np.asarray(matte_image, dtype=np.float32)[..., :1]/255
        yy, xx = np.indices((thumbnail_height, thumbnail_width))
        checker = np.where(((xx//16 + yy//16) % 2)[..., None], 0.35, 0.18)
        composite = Image.fromarray(np.uint8(np.clip(small*alpha + checker*(1-alpha), 0, 1)*255))
        for column, (title, image) in enumerate((("ACEScg → display sRGB", source_image), ("Raw matte · display clamp", matte_image), ("Matte composite on checker", composite))):
            x, y = gutter + column*(thumbnail_width+gutter), gutter + row*(thumbnail_height+header)
            draw.text((x+4, y+8), f"{frame} · {title}", fill="white")
            contact.paste(image, (x, y+header))
        source_image.save(args.output / f"source.{frame}.png")
        matte_image.save(args.output / f"matte.{frame}.png")
        composite.save(args.output / f"composite.{frame}.png")
        row += 1
        del source, matte, display
    sheet = args.output / "bam030-contact-sheet.png"
    contact.save(sheet)
    qc = {"manifest": str(manifest_path), "frames": stats, "contact_sheet": str(sheet), **render_checks,
          "full_capture_rgb_exact": True, "display_preparation": "existing prepare_color(acescg), Rec.709/sRGB; no show OCIO view",
          "quality": "Three reduced preview frames inspected; full-motion and final edge quality are not approved",
          "manual_visual_review": {"frames": sorted(selected), "scope": "reduced source/matte/checker-composite contact sheet",
              "observations": ["Dominant man is extracted across the inspected frames",
                               "Woman entering at the right is also included, with partial coverage/holes around her upper body",
                               "Edges are soft at generation height 384; inspect at delivery resolution and in motion"],
              "acceptance": "Pipeline/frame alignment verified; final matte quality not accepted"}}
    (args.output / "quality-review.json").write_text(json.dumps(qc, indent=2))
    print(json.dumps({"contact_sheet": str(sheet), "full_capture_rgb_exact": True, "frame_count": len(stats)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
