import json
import struct
from pathlib import Path

from shotbox_matte.models import MODEL_FILES, ModelConfig, discover_models, validate_models


def test_cli_defaults_to_models_beside_the_application():
    from shotbox_matte.cli import build_parser
    from shotbox_matte.models import DEFAULT_MODEL_ROOT

    assert DEFAULT_MODEL_ROOT == Path(__file__).resolve().parent.parent / "models"
    assert build_parser().parse_args([]).model_root == DEFAULT_MODEL_ROOT


def write_pack(path: Path, role: str) -> None:
    configurations = {
        "transformer": {"transformer": {}},
        "video_vae": {"vae": {"_class_name": "CausalDiffusionVAE"}},
        "audio_vae": {"audio_vae": {}},
    }
    metadata = {"model_version": "2.5.0", "config": json.dumps(configurations.get(role, {"component": True}))}
    header = {"weights": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}}
    if role == "text_encoder":
        metadata["gemma_config"] = '{"gemma_version":"gemma4-12b-ltx-v1"}'
        for key in ("tokenizer_json", "hf_asset__tokenizer_config.json", "hf_asset__processor_config.json"):
            header[key] = {"dtype": "U8", "shape": [0], "data_offsets": [2, 2]}
    if role == "alpha_lora":
        header["block.lora_A.weight"] = header.pop("weights")
    header["__metadata__"] = metadata
    payload = json.dumps(header).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(payload)) + payload + b"\x00\x00")


def test_discovers_existing_pack_without_copying(tmp_path):
    for role, relative in MODEL_FILES.items():
        write_pack(tmp_path / "models" / relative, role)
    config = discover_models(tmp_path)
    assert not validate_models(config)
    assert config.transformer == (tmp_path / "models" / MODEL_FILES["transformer"]).resolve()
    assert len(list(tmp_path.rglob("*.safetensors"))) == 6
    assert ModelConfig.from_dict(config.to_dict()) == config


def test_discovery_does_not_choose_quantized_variant(tmp_path):
    quantized = tmp_path / MODEL_FILES["transformer"].replace("bf16", "nvfp4")
    write_pack(quantized, "transformer")
    assert discover_models(tmp_path).transformer is None
    errors = validate_models(ModelConfig(transformer=quantized))
    assert any("quantized" in error for error in errors)


def test_missing_embedded_tokenizer_is_actionable(tmp_path):
    path = tmp_path / "encoder.safetensors"
    write_pack(path, "transformer")
    errors = validate_models(ModelConfig(text_encoder=path))
    assert any("missing embedded tokenizer_json" in error for error in errors)


def test_truncated_weights_detected_by_header(tmp_path):
    path = tmp_path / "model.safetensors"
    write_pack(path, "transformer")
    path.write_bytes(path.read_bytes()[:-1])
    errors = validate_models(ModelConfig(transformer=path))
    assert any("truncated or invalid tensor data" in error for error in errors)


def test_unknown_config_keys_do_not_silently_drop_paths():
    import pytest

    with pytest.raises(ValueError, match="Unknown model setting"):
        ModelConfig.from_dict({"transformr": "wrong"})
