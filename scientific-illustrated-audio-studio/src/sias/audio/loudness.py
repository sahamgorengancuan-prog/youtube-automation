"""Loudness: EBU R128 measurement + loudnorm command via ffmpeg."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..exceptions import RenderError

_I_RE = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_PEAK_RE = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")


def measure_loudness(audio_path: str | Path, ffmpeg: str = "ffmpeg") -> dict[str, float]:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(audio_path), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = proc.stderr
    integrated = _I_RE.findall(out)
    peak = _PEAK_RE.findall(out)
    if proc.returncode != 0 or not integrated:
        raise RenderError(f"loudness measurement failed: {out[-300:]}", stage="loudness")
    return {
        "integrated_lufs": float(integrated[-1]),
        "true_peak_dbfs": float(peak[-1]) if peak else 0.0,
    }


def loudnorm_cmd(in_path: str | Path, out_path: str | Path, target_lufs: float = -16.0, true_peak_db: float = -1.5, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-i",
        str(in_path),
        "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11",
        str(out_path),
    ]
