"""Pinned Lightricks inference adapter: float RGB tensors in, raw float matte out.

All torch and LTX imports are lazy so CLI planning and EXR operations remain
usable without CUDA, Qt, or the optional native inference environment.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import numpy as np

from .models import ModelConfig, validate_models
from .temporal import MAX_MODEL_FRAMES, MAX_SELECTED_FRAMES, padded_frame_count

LTX_REVISION = "e98f6d927d83c92ef2be6aec76c8440b19306e39"
Progress = Callable[[str, int, int, str], None]


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        from .core import CancelledError

        raise CancelledError("Generation cancelled")


def _raw_matte_from_pixels(pixels: Any) -> Any:
    """Undo the VAE's range encoding without clipping overshoot or adding gamma."""
    return (pixels.float() + 1.0) * 0.5


@contextmanager
def _torch_only_diffvae() -> Iterator[None]:
    """Use upstream's torch kernels even when a nonfunctional Triton imports.

    Upstream checks only whether Triton imports, which does not prove that its
    C launcher can compile (Python development headers can be absent). Keep
    the original torch implementations of its MLP, norm and output tail. These
    process-local capability gates are restored even after cancellation/error.
    """
    from ltx_core.model.video_vae.transformer import row_rms_norm, stage5_tail, swiglu

    gates = ((swiglu, "_TRITON_AVAILABLE", False), (row_rms_norm, "triton", None), (stage5_tail, "triton", None))
    previous = [(module, name, getattr(module, name)) for module, name, _ in gates]
    try:
        for module, name, value in gates:
            setattr(module, name, value)
        yield
    finally:
        for module, name, value in previous:
            setattr(module, name, value)


def _validate_frames(frames: np.ndarray, fps: float, *, force_full_range: bool = False) -> tuple[int, int, int]:
    if not isinstance(frames, np.ndarray) or frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError("Native inference requires float RGB frames shaped [T,H,W,3]")
    count, height, width, _ = frames.shape
    limit = padded_frame_count(MAX_SELECTED_FRAMES) if force_full_range else MAX_MODEL_FRAMES
    if not 1 <= count <= limit or (count - 1) % 8:
        raise ValueError(f"Alpha Gen requires 8n+1 frames up to {limit}, received {count}; pad/limit before inference")
    if min(height, width) < 32 or height % 32 or width % 32:
        raise ValueError(f"Alpha Gen requires spatial dimensions divisible by 32; received {width}x{height}")
    if width > 1920 or height > 1088:
        raise ValueError(f"Alpha Gen supports at most 1920x1088 padded pixels; received {width}x{height}")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("FPS must be a finite positive number")
    if frames.dtype != np.float32 or not np.isfinite(frames).all() or frames.min() < 0 or frames.max() > 1:
        raise ValueError("Conditioning must be finite display RGB float32 in [0,1]; convert scene-linear EXRs first")
    return count, height, width


class _ProgressStage:
    """Decorate the real DiffusionStage, retaining the upstream Euler sampler."""

    def __init__(self, stage: Any, loop: Callable[..., Any]) -> None:
        self.stage, self.loop = stage, loop

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stage, name)

    def with_loras(self, loras: Any) -> "_ProgressStage":
        return _ProgressStage(self.stage.with_loras(loras), self.loop)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["loop"] = self.loop
        return self.stage(*args, **kwargs)


def _make_pipeline(
    torch: Any, config: ModelConfig, frames: np.ndarray, offload: str,
    low_vram: bool, progress: Progress, cancelled: Callable[[], bool],
) -> Any:
    # These are real interfaces verified at LTX_REVISION. The two private hooks
    # are isolated here: upstream accepts file conditioning and clips its SDR
    # decoder, while our app requires lossless tensors and unclipped EXR output.
    from ltx_core.conditioning import VideoConditionByReferenceLatent
    from ltx_core.loader import LTXV_LORA_COMFY_RENAMING_MAP, LoraPathStrengthAndSDOps
    from ltx_core.loader.module_ops import ModuleOps
    from ltx_core.model.video_vae import DiffusionVideoDecoder
    from ltx_core.model.video_vae.transformer import DiffVAEMode
    from ltx_core.model.video_vae.transformer.apply import apply_diffvae_config
    from ltx_core.model.video_vae.transformer.config import NAttentionKind
    from ltx_core.tiling import DimensionSizeConfig, TileSizeConfig
    from ltx_pipelines.ic_lora import ICLoraPipeline
    from ltx_pipelines.utils.blocks import VideoDecoder
    from ltx_pipelines.utils.gpu_model import gpu_model
    from ltx_pipelines.utils.model_paths import ModelPaths
    from ltx_pipelines.utils.samplers import euler_denoising_loop
    from ltx_pipelines.utils.types import OffloadMode

    # Explicit tiles below are for the convolutional reference encoder only.
    # The diffusion decoder has larger halo requirements and uses upstream's
    # halo-aware AUTO_TILING planner (the stream_chunks default) in every mode.
    # Transformer tiling would change the published Alpha Gen recipe.
    vae_tiles = TileSizeConfig(
        frames=DimensionSizeConfig(tile_size=40, overlap=16),
        height=DimensionSizeConfig(tile_size=512, overlap=64),
        width=DimensionSizeConfig(tile_size=512, overlap=64),
    ) if low_vram else None

    class TensorICLoraPipeline(ICLoraPipeline):
        def _video_conditionings(self, ctx: Any, stage_config: Any, frame_sources: Any) -> Any:
            def make(chunk: Any) -> list[Any]:
                if chunk.video is None or not stage_config.apply_ic_lora:
                    return []
                _check_cancelled(cancelled)
                progress("conditioning", 0, 1, "Encoding prepared RGB frames directly with the native video VAE")

                def encode(encoder: Any) -> list[Any]:
                    start = chunk.layout.start_pixel_frame
                    end = start + chunk.layout.pixel_frames
                    source = torch.from_numpy(np.ascontiguousarray(frames[start:end]))
                    # Native video's normalize_images is RGB/255*2-1. Our input
                    # is already float RGB/1, so perform the same affine mapping
                    # without quantizing through an 8-bit video/image codec.
                    video = source.permute(3, 0, 1, 2).unsqueeze(0).mul(2).sub_(1)
                    # tiled_encode moves just the active tile to the encoder's
                    # device. Keep the full prepared clip in CPU memory here.
                    video = video.to(device="cpu" if vae_tiles is not None else self.device, dtype=self.dtype)
                    latent = encoder.tiled_encode(video, vae_tiles) if vae_tiles is not None else encoder(video)
                    _check_cancelled(cancelled)
                    progress("conditioning", 1, 1, "RGB reference encoded")
                    return [VideoConditionByReferenceLatent(
                        latent=latent, downscale_factor=1, temporal_scale_factor=1, strength=1.0,
                    )]

                return self.image_conditioner(encode)

            return make

    class RawVideoDecoder(VideoDecoder):
        def _prepared_builder(self) -> Any:
            # Install the genuine upstream pure-torch attention backend.
            # CHUNKED_EAGER otherwise still auto-selects Triton when importable.
            recipe = replace(
                self._diffvae_optimization.resolve(), attention=NAttentionKind.EAGER_SDPA,
                natten_backend=None, compile_blocks=False, compile_det_stages=False,
            )
            operation = ModuleOps(
                name="shotbox_diffvae_torch_eager",
                matcher=lambda model: isinstance(model, DiffusionVideoDecoder),
                mutator=lambda model: apply_diffvae_config(model, recipe),
            )
            builder = self._decoder_builder
            return builder.with_module_ops((*builder.module_ops, operation))

        def __call__(self, latent: Any, tiling_config: Any = None, generator: Any = None, *,
                     dtype: Any = None, keyframes: Any = None, device_fn: Any = None) -> Iterator[Any]:
            if keyframes is not None:
                raise RuntimeError("Alpha Gen raw decode does not use generated keyframes")
            _check_cancelled(cancelled)
            progress("decode", 0, frames.shape[0], "Decoding raw floating-point matte pixels")
            build_dtype = self._dtype if dtype is None else dtype
            decoder = self._prepared_builder().build(device=self._device, dtype=build_dtype).eval()
            with gpu_model(decoder, alloc_trim_strategy=self._alloc_trim_strategy), _torch_only_diffvae():
                if not hasattr(decoder, "_decode_pixels"):
                    raise RuntimeError("Raw matte decode requires the released LTX-2.5 diffusion VAE; check the pinned LTX revision and video_vae path")
                raw_chunks = decoder._decode_pixels(
                    latent.to(dtype=build_dtype), tiling_config, generator=generator, as_fhwc=True,
                )
                for pixels in raw_chunks:
                    _check_cancelled(cancelled)
                    yield _raw_matte_from_pixels(pixels)

    model_paths = ModelPaths.from_split(
        transformer_path=str(config.transformer), text_encoder_path=str(config.text_encoder),
        video_vae_path=str(config.video_vae), audio_vae_path=str(config.audio_vae),
    )
    pipe = TensorICLoraPipeline(
        model_paths=model_paths, spatial_upsampler_path=str(config.spatial_upsampler),
        loras=[LoraPathStrengthAndSDOps(str(config.alpha_lora), 1.0, LTXV_LORA_COMFY_RENAMING_MAP)],
        device=torch.device("cuda"), offload_mode=OffloadMode(offload),
        diffvae_optimization=DiffVAEMode.CHUNKED_EAGER,
    )
    if pipe.reference_downscale_factor != 1 or pipe.reference_temporal_scale_factor != 1:
        raise RuntimeError("Expected Alpha Gen IC-LoRA with unscaled RGB conditioning")
    pipe.video_decoder = RawVideoDecoder(
        str(config.video_vae), torch.bfloat16, torch.device("cuda"),
        diffvae_optimization=DiffVAEMode.CHUNKED_EAGER,
    )
    # Alpha Gen emits video only. The pipeline still accepts/validates its
    # required audio-VAE path but need not build an unused audio decoder.
    pipe.audio_decoder = None

    prompt_encoder = pipe.prompt_encoder

    def encode_prompt(*args: Any, **kwargs: Any) -> Any:
        _check_cancelled(cancelled)
        progress("text_encoder", 0, 1, "Encoding prompt with local Gemma4 weights")
        encoded = prompt_encoder(*args, **kwargs)
        _check_cancelled(cancelled)
        progress("text_encoder", 1, 1, "Prompt encoded")
        return encoded

    pipe.prompt_encoder = encode_prompt

    def progress_loop(*, sigmas: Any, video_state: Any, audio_state: Any, transformer: Any, denoiser: Any) -> Any:
        total = len(sigmas) - 1

        def observed_denoiser(model: Any, video: Any, audio: Any, schedule: Any, step: int) -> Any:
            _check_cancelled(cancelled)
            progress("inference", step, total, f"Alpha Gen denoise step {step + 1}/{total}")
            result = denoiser(model, video, audio, schedule, step)
            _check_cancelled(cancelled)
            progress("inference", step + 1, total, f"Completed denoise step {step + 1}/{total}")
            return result

        return euler_denoising_loop(
            sigmas, video_state, audio_state, transformer, observed_denoiser,
        )

    pipe._diffusion_stage = _ProgressStage(pipe._diffusion_stage, progress_loop)
    return pipe


class NativeAlphaEngine:
    def __init__(self, config: ModelConfig, offload: str = "cpu", low_vram: bool = False) -> None:
        if offload not in {"none", "cpu", "disk"}:
            raise ValueError("Offload must be one of: none, cpu, disk")
        self.config, self.offload, self.low_vram = config, offload, low_vram

    def infer(self, frames: np.ndarray, *, fps: float, seed: int,
              progress: Progress | None = None,
              cancelled: Callable[[], bool] | None = None,
              force_full_range: bool = False) -> Iterator[np.ndarray]:
        progress = progress or (lambda *_args: None)
        cancelled = cancelled or (lambda: False)
        count, height, width = _validate_frames(frames, fps, force_full_range=force_full_range)
        _check_cancelled(cancelled)
        if force_full_range and (count > MAX_MODEL_FRAMES or (count - 1) / fps > 6.0 + 1e-9):
            progress("warning", 0, 0, f"Experimental full range: requesting {count} model frames in one pass beyond the supported 145-frame / 6-second range; RGB leakage and GPU memory exhaustion are possible.")
        errors = validate_models(self.config)
        if errors:
            raise ValueError("Model configuration is incomplete or incompatible:\n" + "\n".join(errors))
        # Enforce local-only behavior even when another HF dependency has been
        # imported. Our split pack contains its tokenizer and all configuration.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Native inference requires the optional PyTorch/LTX environment; see docs/INFERENCE.md") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("Alpha Gen requires a working NVIDIA CUDA GPU and compatible PyTorch build; CUDA is unavailable")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("The released native Alpha Gen bf16 pack requires an NVIDIA GPU with bf16 support")
        progress("models", 0, 1, "Preparing native LTX-2.5 components from existing local weights")
        pipe = None
        try:
            with torch.inference_mode():
                pipe = _make_pipeline(torch, self.config, frames, self.offload, self.low_vram, progress, cancelled)
                progress("models", 1, 1, "Native model components prepared")
                # Published single-stage contract: CLI/request dimensions are
                # twice stage-one pixel dimensions when skip_stage_2=True.
                # Alpha Gen requires its fixed empty conditioning prompt.
                kwargs: dict[str, Any] = dict(
                    prompt="", seed=seed, height=height * 2, width=width * 2,
                    num_frames=count, frame_rate=fps, images=[], video_conditioning=[],
                    enhance_prompt=False, skip_stage_2=True,
                )
                chunks, _, _ = pipe.stream_chunks(**kwargs)
                emitted = 0
                try:
                    for chunk in chunks:
                        _check_cancelled(cancelled)
                        array = chunk.video.detach().float().cpu().numpy()
                        if array.ndim != 4 or array.shape[1:] != (height, width, 3):
                            raise RuntimeError(f"Native Alpha Gen returned unexpected geometry {array.shape}; expected [T,{height},{width},3]")
                        for frame in array:
                            _check_cancelled(cancelled)
                            emitted += 1
                            if emitted > count:
                                raise RuntimeError("Native Alpha Gen returned more frames than requested")
                            progress("decode", emitted, count, f"Decoded matte frame {emitted}/{count}")
                            yield np.array(frame, dtype=np.float32, copy=True)
                finally:
                    chunks.close()
                if emitted != count:
                    raise RuntimeError(f"Native Alpha Gen returned {emitted} frames; expected {count}")
        except ImportError as exc:
            raise RuntimeError(f"Native LTX dependencies are unavailable or incompatible ({exc}); install revision {LTX_REVISION} as documented in docs/INFERENCE.md") from exc
        except torch.cuda.OutOfMemoryError as exc:
            raise RuntimeError("Alpha Gen ran out of GPU memory. Reduce generation resolution/frame count or use CPU/disk offload and VAE tiling; these options cannot guarantee a fit.") from exc
        finally:
            if pipe is not None:
                pool = getattr(pipe.image_conditioner, "_decode_pool", None)
                if pool is not None:
                    pool.shutdown(wait=False, cancel_futures=True)
            del pipe
            torch.cuda.empty_cache()
