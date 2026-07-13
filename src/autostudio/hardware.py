from __future__ import annotations

import gc
import os
import random
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class HardwareProfile:
    accelerator: str
    device_name: str
    total_vram_gb: float
    compute_capability: str
    supports_bf16: bool
    recommended_dtype: str
    recommended_quantization: str

    def to_dict(self) -> dict:
        return asdict(self)


def detect_hardware() -> HardwareProfile:
    """Auto-detect T4/L4/A100/CPU. Chooses bf16 on Ampere+, else fp16, else fp32."""
    try:
        import torch
    except Exception:
        return HardwareProfile("cpu", "CPU", 0.0, "n/a", False, "float32", "none")
    if not torch.cuda.is_available():
        return HardwareProfile("cpu", "CPU", 0.0, "n/a", False, "float32", "none")
    index = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(index)
    major, minor = torch.cuda.get_device_capability(index)
    total = props.total_memory / (1024 ** 3)
    bf16 = bool(torch.cuda.is_bf16_supported())
    return HardwareProfile(
        "cuda", props.name, round(total, 2), f"{major}.{minor}", bf16,
        "bfloat16" if bf16 else "float16", "4bit",
    )


def seed_everything(seed: int) -> None:
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def release_gpu_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass
