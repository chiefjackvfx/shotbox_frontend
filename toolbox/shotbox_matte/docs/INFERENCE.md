# Native Alpha Gen inference

ShotBox Matte runs Lightricks' Python LTX implementation directly. Its six model
components live in this project's `models/` directory as independent files.
ShotBox does not import ComfyUI, its nodes, or a server API.

## Verified source and environment

The adapter was written against **LTX-2 revision
`e98f6d927d83c92ef2be6aec76c8440b19306e39`**, release 1.5.0 (2026-10-18).
The API signatures and checkpoint headers were inspected. A real, complete
EXR-to-matte run succeeded on this machine's RTX 5090 (32 GiB, NVIDIA driver
590.48.01) outside the restricted execution sandbox, using the existing local
weights and the installed combination below.

Use Python 3.12 on Linux or Windows with a working NVIDIA CUDA GPU supporting
bf16. The [official PyTorch guide](https://docs.ltx.io/open-source-model/integration-tools/pytorch-api)
lists Python >=3.12, CUDA >=12.7 and PyTorch ~=2.7 as broad requirements.
The pinned source now selects **PyTorch 2.13 / CUDA 13.2** wheels through its UV
indexes; its optional NATTEN extra pins `torch==2.13.0` and the matching
`natten==0.21.7+torch2130cu132`. `ltx-core` requires
`transformers>=5.8.0,<5.15`; upstream identifies **5.14.1** as the newest verified
release. Use a driver compatible with the selected CUDA wheel. These are
upstream's declared combinations, not an end-to-end GPU certification by this
application. Older Torch 2.7 satisfying the broad dependency range alone does
not establish compatibility with the newer Gemma4 encoder or VAE.

The locally installed combination used for the successful inference check is
**Python 3.12.13, torch 2.11.0+cu130, torchvision 0.26.0, torchaudio 2.11.0 and
transformers 5.14.1**. It uses the CUDA 13.0 PyTorch wheels and lies within the
pinned library's declared dependency ranges. The app does not require the newer
optional compiled NATTEN/Blackwell kernels.

The native dependency source is pinned; upstream does not ship a `uv.lock` at
this revision. The frontend's dependency configuration records the immutable
source archives and the tested compatible package versions.

Shotbox installs the pinned inference dependencies into its main frontend
Python 3.12 `venv`. Use the frontend installer/updater and open Matte through
Toolbox or the Precomp Roto action. Generation uses a background process with
the same interpreter; there is no standalone Matte executable or private venv.

## Existing local files

Configure an existing external model directory through Shotbox Settings.
The backend also accepts an explicit model-root path for internal CLI jobs. Its six model
files occupy approximately 67.4 GiB. Discovery accepts this split-pack directory,
its parent, or a flat
directory with the six released filenames:

| Setting | File relative to `models` |
| --- | --- |
| `transformer` | `diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors` |
| `text_encoder` | `text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors` |
| `video_vae` | `vae/ltx-2.5-video-vae-bf16.safetensors` |
| `audio_vae` | `vae/ltx-2.5-audio-vae-bf16.safetensors` |
| `spatial_upsampler` | `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` |
| `alpha_lora` | `loras/ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors` |

All six were copied into the project and verified against the originals using
full SHA-256 checksums. They are independent files, with checksums recorded in
`models/manifest.json`. The text encoder embeds its Hugging Face config, 32 MB tokenizer, chat
template, tokenizer config and processor config. No auxiliary tokenizer or
Gemma directory download is needed for this exact pack. Paths can be overridden
individually or stored as JSON using the setting names above. Relative paths
supplied directly to `ModelConfig` resolve relative to the process directory.

Validation reads JSON headers, metadata and tensor bounds, rather than loading
the large weights. It does not check whole-file hashes. It rejects missing
components, incompatible metadata and the Comfy int8/convrot, FP8 and NVFP4
variants. Use the diffusion video VAE listed above; the separate `video-vae-conv`
pack is not the supported raw-decode path. No models are downloaded
automatically. Hugging Face offline environment flags are enabled before native
inference.

## Exact inference contract

The [Alpha Gen guide](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen)
and [official source](https://github.com/Lightricks/LTX-2/tree/e98f6d927d83c92ef2be6aec76c8440b19306e39)
define the single-stage distilled recipe. ShotBox uses `ModelPaths.from_split`,
`ICLoraPipeline`, a fixed empty prompt, no prompt enhancement, IC-LoRA strength 1.0,
reference strength 1.0 and `skip_stage_2=True`. The adapter always calls
`stream_chunks(prompt="", ...)`; callers cannot supply alternate text.
The desktop and CLI expose no prompt option. Older preference/preset prompt
fields and legacy worker payload text are discarded. Plans and manifests retain
`"prompt": ""` as an explicit record of the fixed recipe.
The duration head is not needed because the padded frame count is explicit.
The spatial upscaler path remains part of the upstream pipeline constructor,
although its second stage never runs. Audio decoding is omitted for a video
matte; the audio VAE path remains configured and validated as required by the
pipeline constructor.

The requested height and width in this upstream pipeline describe the final
two-stage canvas. With stage two skipped, stage one returns **half** of each.
ShotBox therefore requests **twice the padded conditioning height and width**.
For example, a 1920x1080 source padded to 1920x1088 requests 3840x2176 and returns
1920x1088. The larger request is not an unsupported 4K matte: the model's actual
single-stage canvas is 1920x1088. Runtime output geometry and frame counts are
checked before accepting output.

The actual padded canvas is limited to **1920x1088**, dimensions divisible by
32. The padded temporal length must be `8n+1` and at most **145**. At most 121
frames is preferred; valid counts 129, 137 and 145 can be less reliable.
ShotBox extends shot selection to **250 frames** using overlapping segments by
default; the model's officially supported per-pass limit remains unchanged. Existing short shots
that fit the frame and six-second limits retain their single-pass recipe: 20
selected frames still become 25 model frames, then 20 exported frames.

Longer selections use at most 121 model frames per segment, reduced further
when needed to stay within six seconds at the configured FPS. At 24 FPS, a
250-frame selection uses source indices 0–120, 104–224 and 208–249, padding
the last segment from 42 to 49 frames. The shared 17-frame regions blend
linearly from the preceding matte to the following matte, with no value clamp.
The resulting sequence contains exactly 250 frames in source order, preserving
per-frame EXR windows and consecutive export numbering. The job manifest records
each segment's zero-based start, exclusive stop, padded count and blend mode.

Source decoding proceeds sequentially, keeping one prepared RGB segment and
only the shared RGB/matte tails between passes. Each pass releases its native
pipeline before the next one starts. Progress accounts for all model frames,
including overlaps and padding. Cancellation, segment failures and incorrect
decoded counts leave the output uncommitted and preserve an existing sequence.
Selections above 250 fail during planning; restrict the range, cap or skip.

This follows the [official segmenting guidance](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen).
Subject selection remains independent between passes; review join regions for
subject changes and edge differences. The blending/export path has deterministic
automated coverage; 250-frame native GPU matte quality has not been verified.

The optional `temporal_mode="full-range"` (`--temporal-mode full-range`) forces
one model pass over the entire selection. The native adapter receives an explicit
`force_full_range=True` opt-in, raising its application-side padded frame ceiling
from 145 to 257. At 250 selected frames, seven copies of the last source frame
are appended, all 257 frames are requested together, and only the first 250
mattes are exported. Frame-grid, RGB and spatial checks still apply; selections
above 250 remain invalid. No overlap blending or automatic segmented fallback
occurs in this mode. The manifest records the selected mode and experimental flag.

The pinned upstream pipeline's default `chunk_config=None` creates one diffusion
window covering the requested clip. Native VAE tiling and streamed decoding still
follow the existing memory settings; full-range mode disables shot segmentation.
The mode is persisted in desktop settings, named presets and queued-job payloads,
and timing history keeps it separate from segmented work.

Full range bypasses the application's supported frame/time checks; it does not
extend the trained model's supported range. Beyond 145 frames, LTX warns that RGB
can leak into the matte. Longer passes also increase memory demand. Native GPU
execution and matte quality at 257 frames remain unverified; automated tests
verify the single-pass request, opt-in validation and exact 250-frame export
with simulated inference. **Segments** remains the default.

Two small upstream hooks are deliberately isolated in `shotbox_matte/native.py`
and require the pinned revision:

1. `ICLoraPipeline._video_conditionings` is overridden to call the real native
   video encoder and `VideoConditionByReferenceLatent` with in-memory frames.
   This follows the upstream IC-LoRA conditioning mechanism while avoiding its
   file reader's uint8/media conversion. Prepared display RGB float32 `[0,1]` is
   mapped to VAE input `[-1,1]` and cast to bf16; there is no intermediate video,
   duplicated source EXR or lossy codec.
2. The pipeline's video-decoder block uses the real diffusion decoder's
   `_decode_pixels(..., as_fhwc=True)`. Upstream `decode_video` otherwise clamps
   SDR values. ShotBox applies only `(pixels + 1) / 2` to undo VAE range encoding,
   retaining negative/above-one overshoot in float32. It adds no display gamma,
   ACES transform, HDR expansion or clamp. The shared export backend uses the
   red channel replicated into RGB grayscale to match the reference workflow.

The decoder's chunked configuration is installed through upstream
`apply_diffvae_config`/`ModuleOps` with `NAttentionKind.EAGER_SDPA`. During decode,
three optional Triton capability gates for SwiGLU, row RMSNorm and the output
tail are disabled, retaining their existing torch fallback implementations.
Their previous values are restored even after cancellation or an error. This
avoids runtime C launcher compilation while preserving the native model and
sampler. Upstream labels eager SDPA a slower compatibility backend; production
throughput at full resolution has not been established here.

PyTorch inference stays enabled throughout streaming. Progress wraps the
upstream Euler loop's denoiser without replacing its numerical integration.
Cancellation is checked between steps, conditioning and decoded/exported
frames. The desktop worker can terminate a long-running GPU operation; a single
CUDA kernel cannot be interrupted cooperatively from Python.

## Memory options and verification limits

`--offload cpu` (default) uses upstream layer streaming from CPU RAM;
`--offload disk` uses upstream disk streaming; `--offload none` leaves weights on
the GPU. These affect the transformer and text encoder. The app does not
repeat older model versions' memory estimates as LTX-2.5 guarantees.
`--low-vram` additionally tiles reference VAE encoding with 512-pixel spatial
tiles (64-pixel overlaps) and 40-frame temporal tiles (16-frame overlaps), keeping
the full input in CPU memory and moving tiles as needed. Matte decoding always
uses upstream's automatic, memory-aware diffusion-VAE tiling planner. Its halo
requirements differ from those of the reference encoder: the encoder's smaller
overlap settings must not be passed to the diffusion decoder. Tiled VAE results
may differ slightly at seams. Transformer spatial tiling is not enabled by this
flag. Full canvas attention can still run out of memory. Reduce resolution or
selected duration when needed.

The official model card identifies H100/B200-class hardware for upper-bound
full-HD workloads. Neither offloading nor VAE tiling guarantees that a requested
shot fits a particular consumer card. CPU-only and unsupported GPU systems
fail clearly before attempting inference.

The application tests cover local discovery/validation, limits, doubled request
dimensions, fixed empty conditioning and legacy prompt removal, single-stage settings,
cancellation and preservation of raw output using controlled stand-ins. See
[current verification results](VERIFICATION.md) for the final count and platform
coverage. Ruff and compilation checks also pass.

A separate **real-model end-to-end check** loaded the existing bf16 transformer,
Gemma4 encoder, VAE and Alpha Gen adapter. Seven synthetic 83x59 source EXRs
were prepared as nine 128x96 model frames (the native request was 256x192),
denoised through all eight distilled steps with CPU offload and reference-VAE
tiling, decoded through the official eager SDPA fallback, trimmed and exported.
All seven resulting EXRs were independently reopened and confirmed to be 83x59,
RGB grayscale, half float, ZIP compressed and finite (observed matte values
approximately 0.092–0.616). The completed manifest recorded selected length 7,
padded length 9 and a reserved final output location; no partial output or lock
remained. A decoder-only CUDA check also returned nine finite 128x96 raw frames.

OpenImageIO emitted nanobind reference-leak diagnostics during interpreter
shutdown in this environment. The completed run exited with status 0 and its
EXRs passed the independent reopen checks. This binding shutdown warning is a
known observed limitation, not evidence of failed matte export.

These small synthetic checks establish that native weight loading, Gemma4,
CUDA denoising, raw VAE decode and EXR export execute together. They do not
establish subject/edge quality on production footage, full-HD memory capacity,
long-clip temporal consistency, Windows operation or production throughput.
See [VERIFICATION.md](VERIFICATION.md) for the delivery's check record.
