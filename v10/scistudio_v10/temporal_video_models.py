"""WAN 2.2 and SkyReels-V2 image-to-video temporal backends (organic motion).

These are real diffusers image-to-video adapters for shots that need organic,
non-rigid motion (fluids, cloth, smoke, crowds) that the deterministic compositor
and the cutout rig cannot fake. They are heavy and GPU-only:

* **WanVideoBackend** — WAN 2.2 (``Wan-AI/Wan2.2-TI2V-5B-Diffusers`` by default) via
  ``diffusers`` ``WanImageToVideoPipeline``.
* **SkyReelsVideoBackend** — SkyReels-V2 (``Skywork/SkyReels-V2-I2V-1.3B-540P``) via
  ``diffusers`` ``SkyReelsV2DiffusionForcingImageToVideoPipeline``.

Both are honestly gated: ``available()`` is True only with a CUDA GPU **and**
``diffusers``/``torch`` importable **and** the backend explicitly enabled. Without
those, the temporal router falls back to the deterministic compositor, so the
pipeline still runs offline — but no organic AI motion is fabricated. Model
weights are large (multi-GB) and are downloaded on first use; the invocation uses
``enable_model_cpu_offload`` + fp16/bf16 so it fits a single large GPU.

This module deliberately contains the real invocation path; "it renders" is
verifiable only on a big-GPU runtime with the weights available, which is stated
rather than claimed here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .schemas import TemporalRequest, TemporalResult
from .utils import ensure_dir


def _has_cuda() -> bool:
    try:
        import torch  # type: ignore[import-not-found]

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _has_diffusers() -> bool:
    try:
        return importlib.util.find_spec("diffusers") is not None
    except Exception:
        return False


class _BaseVideoBackend:
    """Shared image-to-video diffusers backend. Subclasses set the model id and
    the pipeline class; everything else (gating, memory management, export) is
    shared."""

    backend_id = "video-model"
    pipeline_symbol = ""  # e.g. "WanImageToVideoPipeline"
    default_model = ""

    def __init__(self, config: dict[str, Any], root: str | Path, execution_mode: str = "development"):
        self.config = config or {}
        self.root = ensure_dir(root)
        self.execution_mode = str(execution_mode).lower()
        self._pipe = None

    # -- capability gating --------------------------------------------------
    def available(self) -> bool:
        if not self.config.get("enable", False):
            return False
        return _has_cuda() and _has_diffusers()

    def supports(self, request: TemporalRequest) -> bool:
        return request.complexity in {"organic", "deformation", "articulated"}

    # -- pipeline ------------------------------------------------------------
    def _model_id(self) -> str:
        return str(self.config.get("model", self.default_model))

    def _load(self):
        if self._pipe is not None:
            return self._pipe
        import diffusers  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]

        pipeline_cls = getattr(diffusers, self.pipeline_symbol)
        dtype = torch.bfloat16 if self.config.get("dtype", "bf16") == "bf16" else torch.float16
        pipe = pipeline_cls.from_pretrained(self._model_id(), torch_dtype=dtype)
        # Single-GPU memory management: offload weights to CPU between stages and
        # (optionally) slice VAE decoding so long clips fit.
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pipe = pipe.to("cuda")
        try:
            pipe.vae.enable_slicing()
            pipe.vae.enable_tiling()
        except Exception:
            pass
        self._pipe = pipe
        return pipe

    def generate(self, request: TemporalRequest) -> TemporalResult:
        from diffusers.utils import export_to_video, load_image  # type: ignore[import-not-found]

        output = Path(request.output_path or self.root / f"{request.scene_id}.mp4")
        ensure_dir(output.parent)
        pipe = self._load()
        image = load_image(request.beauty_start)
        prompt = request.motion_prompt or "natural, physically plausible motion consistent with the scene"
        num_frames = int(self.config.get("num_frames", max(17, min(81, request.duration_frames))))
        kwargs: dict[str, Any] = {
            "image": image,
            "prompt": prompt,
            "num_frames": num_frames,
            "num_inference_steps": int(self.config.get("num_inference_steps", 40)),
            "guidance_scale": float(self.config.get("guidance_scale", 5.0)),
        }
        seed = self.config.get("seed")
        if seed is not None:
            import torch  # type: ignore[import-not-found]

            kwargs["generator"] = torch.Generator(device="cuda").manual_seed(int(seed))
        result = pipe(**kwargs)
        frames = result.frames[0]
        export_to_video(frames, str(output), fps=request.fps)
        return TemporalResult(
            scene_id=request.scene_id,
            backend_id=self.backend_id,
            output_path=str(output),
            deterministic=False,
            metadata={
                "model": self._model_id(),
                "pipeline": self.pipeline_symbol,
                "num_frames": num_frames,
            },
        )


class WanVideoBackend(_BaseVideoBackend):
    backend_id = "wan-2.2-i2v"
    pipeline_symbol = "WanImageToVideoPipeline"
    default_model = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"


class SkyReelsVideoBackend(_BaseVideoBackend):
    backend_id = "skyreels-v2-i2v"
    pipeline_symbol = "SkyReelsV2DiffusionForcingImageToVideoPipeline"
    default_model = "Skywork/SkyReels-V2-I2V-1.3B-540P"


def build_video_backends(config: dict[str, Any], root: str | Path, execution_mode: str) -> list[Any]:
    """Instantiate the enabled organic-video backends (may be empty). Each is
    gated by its own ``enable`` flag inside the temporal config."""
    root = Path(root)
    temporal = config.get("temporal", {}) if config else {}
    out: list[Any] = []
    wan_cfg = temporal.get("wan", {})
    if wan_cfg.get("enable", False):
        out.append(WanVideoBackend(wan_cfg, root / "wan", execution_mode))
    sky_cfg = temporal.get("skyreels", {})
    if sky_cfg.get("enable", False):
        out.append(SkyReelsVideoBackend(sky_cfg, root / "skyreels", execution_mode))
    return out
