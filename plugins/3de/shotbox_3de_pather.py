# 3DE4.script.name:    Path swap
# 3DE4.script.version: v1.3
# 3DE4.script.gui:     Main Window::ShotBox
# 3DE4.script.comment: Normalize camera, 3D model and texture paths for the current OS.

"""Normalize 3DE camera, 3D model and texture paths for the current OS."""

from __future__ import annotations

import platform
import re
from typing import Any

try:
    import tde4  # type: ignore
except ImportError:  # pragma: no cover - only available inside 3DEqualizer
    tde4 = None


ROOT_PAIRS = [
    ("Z:/", "/Volumes/projects/"),
]

MODEL_PATH_SKIP_VALUES = {
    "",
    None,
    "<primitive>",
    "<USD Reference>",
}


def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def join_root(root: str, remainder: str) -> str:
    root = root.rstrip("/")
    if remainder:
        return root + "/" + remainder.lstrip("/")
    return root + "/"


def render_path(path_norm: str, target_style: str) -> str:
    if target_style == "windows":
        return path_norm.replace("/", "\\")
    return path_norm


def swap_root(path: str) -> tuple[str | None, str | None]:
    """Map either known root to this OS, never toggle based on the input."""
    system = platform.system()
    if system not in ("Windows", "Linux", "Darwin"):
        return None, None
    target_style = "windows" if system == "Windows" else "linux"
    path_norm = normalize_path(path)

    for win_root, linux_root in ROOT_PAIRS:
        target_root = win_root if target_style == "windows" else linux_root
        for source_root, case_sensitive in ((win_root, False), (linux_root, True)):
            source_norm = normalize_path(source_root).rstrip("/")
            comparable_path = path_norm if case_sensitive else path_norm.casefold()
            comparable_root = source_norm if case_sensitive else source_norm.casefold()
            if comparable_path == comparable_root or comparable_path.startswith(comparable_root + "/"):
                remainder = path_norm[len(source_norm):].lstrip("/")
                return join_root(normalize_path(target_root), remainder), target_style

    return None, None


def has_sequence_padding(filename: str) -> bool:
    return ("####" in filename) or (re.search(r"%0\d+d", filename) is not None)


def force_sequence_padding(path_norm: str) -> str:
    dirname, separator, filename = path_norm.rpartition("/")

    if has_sequence_padding(filename):
        return path_norm

    filename, count = re.subn(
        r"(^|[^\d])(\d+)(\.[^.]+)$",
        lambda match: match.group(1) + "####" + match.group(3),
        filename,
    )

    if count == 0:
        return path_norm

    if separator:
        return dirname + separator + filename
    return filename


def swap_camera_path(path: str, camera_type: str | None = "SEQUENCE") -> str | None:
    swapped_norm, target_style = swap_root(path)
    if swapped_norm is None:
        return None

    # Reference cameras point to literal still filenames, including any digits.
    if camera_type == "SEQUENCE":
        swapped_norm = force_sequence_padding(swapped_norm)
    return render_path(swapped_norm, target_style)


def swap_asset_path(path: str) -> str | None:
    """Normalize the root on a 3D model or texture path. No sequence padding —
    these are typically single static files, not image sequences."""
    swapped_norm, target_style = swap_root(path)
    if swapped_norm is None:
        return None

    return render_path(swapped_norm, target_style)


# Backwards-compat alias for anything that imported the old name
swap_model_path = swap_asset_path


def set_model_path(tde4_module: Any, pgroup_id: Any, model_id: Any, path: str) -> bool:
    if hasattr(tde4_module, "set3DModelFilepath"):
        tde4_module.set3DModelFilepath(pgroup_id, model_id, path)
        return True

    if hasattr(tde4_module, "importOBJ3DModel"):
        return bool(tde4_module.importOBJ3DModel(pgroup_id, model_id, path))

    return False


def get_model_texture(tde4_module: Any, pgroup_id: Any, model_id: Any) -> str | None:
    """Read a 3D model's texture filename, or None if this 3DE build doesn't
    expose a texture API."""
    if hasattr(tde4_module, "get3DModelUVTextureMap"):
        return tde4_module.get3DModelUVTextureMap(pgroup_id, model_id)
    return None


def set_model_texture(tde4_module: Any, pgroup_id: Any, model_id: Any, path: str) -> bool:
    if hasattr(tde4_module, "set3DModelUVTextureMap"):
        tde4_module.set3DModelUVTextureMap(pgroup_id, model_id, path)
        return True
    return False


def build_summary(
    camera_message: str,
    model_updates: list[str],
    model_skips: list[str],
    texture_updates: list[str],
    texture_skips: list[str],
) -> str:
    summary = [
        camera_message,
        "3D models updated: {}".format(len(model_updates)),
        "Textures updated:  {}".format(len(texture_updates)),
    ]

    if model_updates:
        preview = model_updates[:8]
        summary.append("Changed models:\n" + "\n".join(preview))
        if len(model_updates) > len(preview):
            summary.append("...and {} more model(s).".format(len(model_updates) - len(preview)))

    if texture_updates:
        preview = texture_updates[:8]
        summary.append("Changed textures:\n" + "\n".join(preview))
        if len(texture_updates) > len(preview):
            summary.append("...and {} more texture(s).".format(len(texture_updates) - len(preview)))

    if model_skips:
        preview = model_skips[:8]
        summary.append("Skipped models: {}\n{}".format(len(model_skips), "\n".join(preview)))
        if len(model_skips) > len(preview):
            summary.append("...and {} more skipped model(s).".format(len(model_skips) - len(preview)))

    if texture_skips:
        preview = texture_skips[:8]
        summary.append("Skipped textures: {}\n{}".format(len(texture_skips), "\n".join(preview)))
        if len(texture_skips) > len(preview):
            summary.append("...and {} more skipped texture(s).".format(len(texture_skips) - len(preview)))

    return "\n\n".join(summary)


def run_path_swap(tde4_module: Any) -> dict[str, Any]:
    camera_messages: list[str] = []
    camera_changed = False
    model_updates: list[str] = []
    model_skips: list[str] = []
    texture_updates: list[str] = []
    texture_skips: list[str] = []

    if hasattr(tde4_module, "getCameraList"):
        camera_ids = tde4_module.getCameraList(0) or []
    else:
        current_camera = tde4_module.getCurrentCamera()
        camera_ids = [current_camera] if current_camera else []
    for cam_id in camera_ids:
        camera_name = (
            tde4_module.getCameraName(cam_id)
            if hasattr(tde4_module, "getCameraName") else str(cam_id)
        )
        camera_type = (
            tde4_module.getCameraType(cam_id)
            if hasattr(tde4_module, "getCameraType") else None
        )
        old_camera_path = tde4_module.getCameraPath(cam_id)

        if old_camera_path:
            new_camera_path = swap_camera_path(old_camera_path, camera_type)

            if new_camera_path is not None and new_camera_path != old_camera_path:
                tde4_module.setCameraPath(cam_id, new_camera_path)
                camera_changed = True
                camera_message = "Camera updated.\nOld:\n{}\nNew:\n{}".format(
                    old_camera_path,
                    new_camera_path,
                )
            elif new_camera_path == old_camera_path:
                camera_message = "Camera skipped.\nPath already matches the current OS."
            else:
                camera_message = (
                    "Camera skipped.\nPath does not start with a known Windows/Linux root:\n{}".format(
                        old_camera_path
                    )
                )
        else:
            camera_message = "Camera skipped.\nCamera has no image path set."

        camera_messages.append("{} [{}]\n{}".format(
            camera_name, camera_type or "unknown type", camera_message
        ))

    camera_message = "\n\n".join(camera_messages) or "Camera skipped.\nNo cameras found."

    for pgroup_id in tde4_module.getPGroupList(0) or []:
        pgroup_name = tde4_module.getPGroupName(pgroup_id)

        for model_id in tde4_module.get3DModelList(pgroup_id, 0) or []:
            model_name = tde4_module.get3DModelName(pgroup_id, model_id)
            label = "{} / {}".format(pgroup_name, model_name)

            # ─── Model file (OBJ etc) ───
            old_model_path = tde4_module.get3DModelFilepath(pgroup_id, model_id)

            if old_model_path not in MODEL_PATH_SKIP_VALUES and not str(old_model_path).startswith("<"):
                new_model_path = swap_asset_path(old_model_path)

                if new_model_path is None:
                    model_skips.append(label + " [unknown root]")
                elif new_model_path != old_model_path:
                    if set_model_path(tde4_module, pgroup_id, model_id, new_model_path):
                        model_updates.append(label)
                    else:
                        model_skips.append(label + " [update failed]")

            # ─── Texture / UV map ───
            old_texture = get_model_texture(tde4_module, pgroup_id, model_id)

            if old_texture is None:
                # API not exposed in this 3DE version — nothing to do.
                continue

            if old_texture in MODEL_PATH_SKIP_VALUES or str(old_texture).startswith("<"):
                continue

            new_texture = swap_asset_path(old_texture)

            if new_texture is None:
                texture_skips.append(label + " [unknown root]")
                continue

            if new_texture == old_texture:
                continue

            if set_model_texture(tde4_module, pgroup_id, model_id, new_texture):
                texture_updates.append(label)
            else:
                texture_skips.append(label + " [update failed]")

    return {
        "camera_changed": camera_changed,
        "camera_message": camera_message,
        "model_updates": model_updates,
        "model_skips": model_skips,
        "texture_updates": texture_updates,
        "texture_skips": texture_skips,
    }


def main(tde4_module: Any | None = None) -> dict[str, Any]:
    active_tde4 = tde4_module or tde4
    if active_tde4 is None:
        raise RuntimeError("tde4 is unavailable. Run this inside 3DEqualizer or pass a compatible module.")

    results = run_path_swap(active_tde4)

    nothing_changed = (
        not results["camera_changed"]
        and not results["model_updates"]
        and not results["texture_updates"]
    )
    title = "Swap Paths - No Changes" if nothing_changed else "Swap Paths"

    message = build_summary(
        results["camera_message"],
        results["model_updates"],
        results["model_skips"],
        results["texture_updates"],
        results["texture_skips"],
    )

    if hasattr(active_tde4, "postQuestionRequester"):
        active_tde4.postQuestionRequester(title, message, "OK")
    else:
        print(title)
        print(message)

    return results


if __name__ == "__main__":
    main()
