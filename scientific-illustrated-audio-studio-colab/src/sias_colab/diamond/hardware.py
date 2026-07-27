"""Hardware-aware routing profiles: cpu / t4_16gb / gpu_24gb / gpu_48gb.
The registry uses the detected profile to pick compatible backends and to
explain why a backend was skipped."""

from __future__ import annotations

import os
from typing import Any

PROFILES = {
    "cpu": "planning, prompts, manifests, subtitles, FFmpeg render, light transcription, API generation",
    "t4_16gb": "adds Chatterbox, Faster-Whisper, DINOv2/DreamSim, GroundingDINO, light VLM (quantized)",
    "gpu_24gb": "adds FP8/quantized image editing, Step1X-fit configs, local candidate evaluation",
    "gpu_48gb": "adds full-size image gen/edit, larger Qwen3-VL, parallel candidates, batch eval",
}


def detect_profile() -> str:
    forced = os.environ.get("SIAS_HW_PROFILE", "")
    if forced in PROFILES:
        return forced
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            if vram_gb >= 44:
                return "gpu_48gb"
            if vram_gb >= 22:
                return "gpu_24gb"
            return "t4_16gb"
    except Exception:
        pass
    return "cpu"


def profile_report() -> dict[str, Any]:
    profile = detect_profile()
    return {"profile": profile, "supports": PROFILES[profile],
            "note": "override with SIAS_HW_PROFILE env var"}
