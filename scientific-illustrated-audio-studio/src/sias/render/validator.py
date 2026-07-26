"""Render validation via ffprobe: streams, duration, size, blank frames. A tiny
MP4 or an existing filename is never proof of a successful render."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..exceptions import RenderError

MIN_VIDEO_BYTES = 20_000


def probe(path: str | Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RenderError(f"ffprobe failed: {proc.stderr[-300:]}", stage="validate")
    return json.loads(proc.stdout or "{}")


def validate_final(
    video_path: str | Path,
    narration_duration_s: float,
    tolerance_s: float = 0.08,
    min_bytes: int = MIN_VIDEO_BYTES,
) -> dict[str, Any]:
    p = Path(video_path)
    if not p.exists():
        raise RenderError(f"output missing: {p}", stage="validate")
    size = p.stat().st_size
    if size < min_bytes:
        raise RenderError(f"output suspiciously small ({size} bytes)", stage="validate")
    info = probe(p)
    streams = info.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_video:
        raise RenderError("no video stream in output", stage="validate")
    if not has_audio:
        raise RenderError("no audio stream in output", stage="validate")
    duration = float(info.get("format", {}).get("duration", 0.0))
    delta = abs(duration - narration_duration_s)
    if delta > tolerance_s:
        raise RenderError(
            f"duration mismatch: video {duration:.3f}s vs narration {narration_duration_s:.3f}s "
            f"(|Δ|={delta:.3f}s > {tolerance_s}s)",
            stage="validate",
        )
    return {
        "size_bytes": size,
        "duration_s": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "duration_delta_s": round(delta, 4),
    }


_BLACK_RE = re.compile(r"black_start:(\d+(?:\.\d+)?)")


def detect_black_frames(video_path: str | Path, min_black_s: float = 0.4, ffmpeg: str = "ffmpeg") -> list[float]:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video_path), "-vf", f"blackdetect=d={min_black_s}:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    return [float(x) for x in _BLACK_RE.findall(proc.stderr)]
