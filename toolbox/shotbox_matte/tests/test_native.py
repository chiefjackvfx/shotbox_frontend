import contextlib
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from shotbox_matte.models import ModelConfig
from shotbox_matte.native import NativeAlphaEngine, _raw_matte_from_pixels, _torch_only_diffvae, _validate_frames


@pytest.mark.parametrize("count", [2, 20, 146, 153])
def test_invalid_model_length_rejected(count):
    with pytest.raises(ValueError, match="8n\\+1"):
        _validate_frames(np.zeros((count, 32, 32, 3), np.float32), 24)


def test_valid_limit_and_bad_geometry():
    assert _validate_frames(np.zeros((145, 32, 64, 3), np.float32), 24) == (145, 32, 64)
    with pytest.raises(ValueError, match="divisible by 32"):
        _validate_frames(np.zeros((9, 33, 64, 3), np.float32), 24)
    with pytest.raises(ValueError, match="1920x1088"):
        _validate_frames(np.zeros((1, 32, 1952, 3), np.float32), 24)


def test_extended_native_length_requires_explicit_full_range_opt_in():
    frames = np.zeros((257, 32, 32, 3), np.float32)
    with pytest.raises(ValueError, match="up to 145"):
        _validate_frames(frames, 24)
    assert _validate_frames(frames, 24, force_full_range=True) == (257, 32, 32)
    for count in [250, 258]:
        with pytest.raises(ValueError, match="8n\\+1 frames"):
            _validate_frames(np.zeros((count, 32, 32, 3), np.float32), 24, force_full_range=True)
    for count in [265, 281, 1001]:
        assert _validate_frames(np.zeros((count, 32, 32, 3), np.float32), 24,
                                force_full_range=True) == (count, 32, 32)
    with pytest.raises(ValueError, match="divisible by 32"):
        _validate_frames(np.zeros((257, 33, 32, 3), np.float32), 24, force_full_range=True)


@pytest.mark.parametrize("value", [np.nan, np.inf, -0.1, 1.1])
def test_untreated_invalid_rgb_rejected(value):
    frames = np.full((1, 32, 32, 3), value, np.float32)
    with pytest.raises(ValueError, match="display RGB"):
        _validate_frames(frames, 24)


def test_raw_decode_range_mapping_preserves_overshoot():
    class Pixels:
        def float(self):
            return np.array([-1.4, -1, 0, 1, 1.6], np.float32)

    np.testing.assert_allclose(_raw_matte_from_pixels(Pixels()), [-0.2, 0, 0.5, 1, 1.3], atol=1e-6)


def test_torch_fallback_capabilities_restore_after_failure(monkeypatch):
    leaf_name = "ltx_core.model.video_vae.transformer"
    for name in ("ltx_core", "ltx_core.model", "ltx_core.model.video_vae", leaf_name):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    leaf = sys.modules[leaf_name]
    sentinel = object()
    leaf.swiglu = SimpleNamespace(_TRITON_AVAILABLE=True)
    leaf.row_rms_norm = SimpleNamespace(triton=sentinel)
    leaf.stage5_tail = SimpleNamespace(triton=sentinel)
    with pytest.raises(RuntimeError, match="cancelled"):
        with _torch_only_diffvae():
            assert leaf.swiglu._TRITON_AVAILABLE is False
            assert leaf.row_rms_norm.triton is None
            assert leaf.stage5_tail.triton is None
            raise RuntimeError("cancelled")
    assert leaf.swiglu._TRITON_AVAILABLE is True
    assert leaf.row_rms_norm.triton is sentinel
    assert leaf.stage5_tail.triton is sentinel


def mock_native(monkeypatch, output):
    import shotbox_matte.native as native

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True, is_bf16_supported=lambda: True,
                             empty_cache=lambda: None, OutOfMemoryError=type("GPUOOM", (RuntimeError,), {})),
        inference_mode=contextlib.nullcontext,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(native, "validate_models", lambda _config: [])
    captured = {}

    class Tensor:
        def detach(self):
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return output

    class Pipeline:
        image_conditioner = SimpleNamespace()

        def stream_chunks(self, **kwargs):
            captured.update(kwargs)

            def generate():
                yield SimpleNamespace(video=Tensor())

            return generate(), output.shape[0], None

    monkeypatch.setattr(native, "_make_pipeline", lambda *_args: Pipeline())
    return captured


def test_real_adapter_request_contract_forces_empty_prompt_and_raw_output(monkeypatch):
    output = np.full((9, 32, 64, 3), 1.25, np.float32)
    output[0, 0, 0] = -0.2
    captured = mock_native(monkeypatch, output)
    result = list(NativeAlphaEngine(ModelConfig()).infer(
        np.zeros_like(output), fps=23.976, seed=4,
    ))
    assert captured["width"] == 128 and captured["height"] == 64
    assert captured["prompt"] == "" and captured["skip_stage_2"] is True
    assert captured["enhance_prompt"] is False
    assert captured["video_conditioning"] == []  # tensor hook supplies the reference
    assert captured["num_frames"] == 9
    assert "tiling_config" not in captured  # retain native halo-aware decoder AUTO_TILING
    np.testing.assert_array_equal(np.stack(result), output)


def test_prompt_is_always_empty_for_existing_callers(monkeypatch):
    frames = np.zeros((1, 32, 32, 3), np.float32)
    captured = mock_native(monkeypatch, frames)
    list(NativeAlphaEngine(ModelConfig()).infer(frames, fps=24, seed=0))
    assert captured["prompt"] == ""


def test_full_range_adapter_requests_257_frames_in_one_model_pass(monkeypatch):
    output = np.full((257, 32, 32, 3), 1.25, np.float32)
    captured = mock_native(monkeypatch, output)
    events = []
    result = list(NativeAlphaEngine(ModelConfig()).infer(
        np.zeros_like(output), fps=24, seed=0, force_full_range=True,
        progress=lambda *event: events.append(event),
    ))
    assert captured["num_frames"] == 257
    assert "chunk_config" not in captured  # Upstream default requests one full window.
    assert any(stage == "warning" and "Experimental full range" in message
               for stage, _current, _total, message in events)
    np.testing.assert_array_equal(np.stack(result), output)


@pytest.mark.parametrize("prompt", ["Foreground person", None])
def test_prompt_keyword_is_not_accepted_before_loading_dependencies(monkeypatch, prompt):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(TypeError, match="unexpected keyword argument 'prompt'"):
        list(NativeAlphaEngine(ModelConfig()).infer(np.zeros((1, 32, 32, 3), np.float32),
                                                  fps=24, seed=0, prompt=prompt))


def test_returned_geometry_is_checked(monkeypatch):
    mock_native(monkeypatch, np.zeros((9, 64, 128, 3), np.float32))
    with pytest.raises(RuntimeError, match="unexpected geometry"):
        list(NativeAlphaEngine(ModelConfig()).infer(np.zeros((9, 32, 64, 3), np.float32), fps=24, seed=0))


def test_cuda_unavailable_has_clear_error(monkeypatch):
    mock_native(monkeypatch, np.zeros((1, 32, 32, 3), np.float32))
    monkeypatch.setattr(sys.modules["torch"].cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="CUDA is unavailable"):
        list(NativeAlphaEngine(ModelConfig()).infer(np.zeros((1, 32, 32, 3), np.float32), fps=24, seed=0))


def test_cancelled_before_model_loading(monkeypatch):
    from shotbox_matte.core import CancelledError

    # Even the CUDA dependency must not be touched after an initial cancel.
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(CancelledError):
        list(NativeAlphaEngine(ModelConfig()).infer(
            np.zeros((1, 32, 32, 3), np.float32), fps=24, seed=0, cancelled=lambda: True,
        ))


def test_cancellation_between_decoded_frames(monkeypatch):
    from shotbox_matte.core import CancelledError

    mock_native(monkeypatch, np.zeros((9, 32, 32, 3), np.float32))
    stop = False
    iterator = NativeAlphaEngine(ModelConfig()).infer(
        np.zeros((9, 32, 32, 3), np.float32), fps=24, seed=0, cancelled=lambda: stop,
    )
    assert next(iterator).shape == (32, 32, 3)
    stop = True
    with pytest.raises(CancelledError):
        next(iterator)
