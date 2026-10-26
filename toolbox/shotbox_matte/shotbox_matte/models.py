"""Local-only discovery and lightweight validation of the native LTX split pack."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping


DEFAULT_MODEL_ROOT = Path(__file__).resolve().parent.parent / "models"


MODEL_FILES = {
    "transformer": "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors",
    "text_encoder": "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
    "video_vae": "vae/ltx-2.5-video-vae-bf16.safetensors",
    "audio_vae": "vae/ltx-2.5-audio-vae-bf16.safetensors",
    "spatial_upsampler": "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
    "alpha_lora": "loras/ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors",
}


@dataclass(frozen=True)
class ModelConfig:
    transformer: Path | None = None
    text_encoder: Path | None = None
    video_vae: Path | None = None
    audio_vae: Path | None = None
    spatial_upsampler: Path | None = None
    alpha_lora: Path | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            object.__setattr__(self, item.name, Path(value).expanduser() if value else None)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "ModelConfig":
        unknown = set(values) - set(MODEL_FILES)
        if unknown:
            raise ValueError(f"Unknown model setting(s): {', '.join(sorted(unknown))}")
        return cls(**{name: Path(value).expanduser() if value else None for name, value in values.items()})

    def to_dict(self) -> dict[str, str | None]:
        return {name: str(value) if (value := getattr(self, name)) is not None else None for name in MODEL_FILES}


def discover_models(root: Path) -> ModelConfig:
    """Find the exact released bf16 pack, retaining its existing file locations.

    Accepts either a split-pack model directory, its parent, or a flat
    directory containing the six files. Does not recurse through arbitrary
    storage, copy files, consult another application's settings, or contact the network.
    Missing slots remain None so the interface can explain and configure them.
    """
    root = Path(root).expanduser().resolve()
    directories = (root / "models", root) if (root / "models").is_dir() else (root,)
    found: dict[str, Path | None] = {}
    for name, relative in MODEL_FILES.items():
        candidates = [candidate for directory in directories for candidate in (directory / relative, directory / Path(relative).name)]
        found[name] = next((path.resolve() for path in candidates if path.is_file()), None)
    return ModelConfig(**found)


def _read_header(path: Path) -> dict[str, Any]:
    """Read only safetensors' JSON header, never any multi-gigabyte weights."""
    with path.open("rb") as handle:
        length_bytes = handle.read(8)
        if len(length_bytes) != 8:
            raise ValueError("truncated safetensors header")
        length = struct.unpack("<Q", length_bytes)[0]
        if length < 2 or length > 32 * 1024 * 1024 or length > path.stat().st_size - 8:
            raise ValueError("invalid safetensors header length")
        header = json.loads(handle.read(length))
    if not isinstance(header, dict):
        raise ValueError("safetensors header is not an object")
    metadata = header.get("__metadata__", {})
    if not isinstance(metadata, dict):
        raise ValueError("invalid safetensors metadata")
    data_size = path.stat().st_size - 8 - length
    for key, entry in header.items():
        if key == "__metadata__":
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"invalid tensor descriptor: {key}")
        offsets = entry.get("data_offsets", [])
        if (len(offsets) != 2 or not all(isinstance(value, int) for value in offsets)
                or not 0 <= offsets[0] <= offsets[1] <= data_size):
            raise ValueError(f"truncated or invalid tensor data: {key}")
    return header


def validate_models(config: ModelConfig) -> list[str]:
    """Return actionable local model errors; an empty list means headers passed.

    This verifies the embedded Gemma tokenizer/config and component metadata.
    It is intentionally not a full checksum or GPU allocation test.
    """
    errors: list[str] = []
    for name, expected in MODEL_FILES.items():
        path = getattr(config, name)
        if path is None:
            errors.append(f"Missing {name}: choose existing {Path(expected).name}")
            continue
        if not path.is_file():
            errors.append(f"Missing {name} file: {path}")
            continue
        if path.suffix.lower() != ".safetensors":
            errors.append(f"{name}: native LTX requires a bf16 .safetensors file: {path}")
            continue
        if any(marker in path.name.lower() for marker in ("int8", "nvfp4", "fp8", "convrot")):
            errors.append(f"{name}: quantized ComfyUI weights are unsupported; choose the native bf16 file: {path}")
            continue
        try:
            header = _read_header(path)
            tensors = {key: entry for key, entry in header.items() if key != "__metadata__"}
            if not tensors or not any(entry.get("dtype") == "BF16" for entry in tensors.values()):
                errors.append(f"{name}: no bf16 model tensors found in {path}")
            if any(entry.get("dtype", "").startswith(("F8", "F4")) for entry in tensors.values()):
                errors.append(f"{name}: quantized tensor format is unsupported: {path}")
            metadata = header.get("__metadata__", {})
            if name in {"transformer", "video_vae", "audio_vae"}:
                if not str(metadata.get("model_version", "")).startswith("2.5"):
                    errors.append(f"{name}: expected LTX 2.5 model_version metadata in {path}")
                component_config = json.loads(metadata.get("config", "{}"))
                if not isinstance(component_config, dict) or not component_config:
                    errors.append(f"{name}: missing embedded native model configuration in {path}")
                elif name == "video_vae" and component_config.get("vae", {}).get("_class_name") != "CausalDiffusionVAE":
                    errors.append(f"video_vae: expected the LTX-2.5 diffusion video VAE, not the conv variant: {path}")
                elif name == "transformer" and "transformer" not in component_config:
                    errors.append(f"transformer: missing transformer configuration in {path}")
                elif name == "audio_vae" and "audio_vae" not in component_config:
                    errors.append(f"audio_vae: missing audio_vae configuration in {path}")
            if name == "text_encoder":
                gemma = json.loads(metadata.get("gemma_config", "{}"))
                if not isinstance(gemma, dict) or gemma.get("gemma_version") != "gemma4-12b-ltx-v1":
                    errors.append(f"text_encoder: expected embedded gemma4-12b-ltx-v1 configuration in {path}")
                for asset in ("tokenizer_json", "hf_asset__tokenizer_config.json", "hf_asset__processor_config.json"):
                    fallback = asset.removeprefix("hf_asset__")
                    if asset not in header and (asset == "tokenizer_json" or fallback not in metadata):
                        errors.append(f"text_encoder: missing embedded {asset}; use the complete released bf16 pack: {path}")
            if name == "alpha_lora":
                if not str(metadata.get("model_version", "")).startswith("2.5"):
                    errors.append(f"alpha_lora: expected the released LTX 2.5 Alpha Gen adapter: {path}")
                for factor in ("reference_downscale_factor", "reference_temporal_scale_factor"):
                    if int(metadata.get(factor, 1)) != 1:
                        errors.append(f"alpha_lora: Alpha Gen requires {factor}=1: {path}")
                if not any("lora_A.weight" in key for key in tensors):
                    errors.append(f"alpha_lora: no IC-LoRA tensors found in {path}")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"Cannot validate {name} ({path}): {exc}")
    return errors
