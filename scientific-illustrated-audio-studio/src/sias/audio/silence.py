"""WAV integrity: duration, peak/RMS dBFS, silence and clipping detection.
Effectively-silent audio is a hard failure, never a warning."""

from __future__ import annotations

import audioop
import math
import wave
from pathlib import Path
from typing import Any

from ..exceptions import AssetIntegrityError, SilentAudioError


def wav_stats(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() or p.stat().st_size < 128:
        raise AssetIntegrityError(f"audio file missing or too small: {p}", stage="audio")
    try:
        with wave.open(str(p), "rb") as wf:
            n_frames = wf.getnframes()
            rate = wf.getframerate()
            width = wf.getsampwidth()
            channels = wf.getnchannels()
            frames = wf.readframes(n_frames)
    except wave.Error as exc:
        raise AssetIntegrityError(f"audio not decodable as WAV: {p} ({exc})", stage="audio") from exc
    duration = n_frames / float(rate) if rate else 0.0
    peak = audioop.max(frames, width) if frames else 0
    rms = audioop.rms(frames, width) if frames else 0
    full_scale = float(2 ** (8 * width - 1) - 1)
    peak_dbfs = 20 * math.log10(peak / full_scale) if peak > 0 else -120.0
    rms_dbfs = 20 * math.log10(rms / full_scale) if rms > 0 else -120.0
    return {
        "duration_s": round(duration, 3),
        "rate": rate,
        "channels": channels,
        "peak_dbfs": round(peak_dbfs, 2),
        "rms_dbfs": round(rms_dbfs, 2),
        "clipping": peak >= full_scale - 1,
    }


def assert_not_silent(path: str | Path, threshold_dbfs: float = -55.0) -> dict[str, Any]:
    stats = wav_stats(path)
    if stats["peak_dbfs"] <= threshold_dbfs:
        raise SilentAudioError(
            f"audio effectively silent (peak {stats['peak_dbfs']} dBFS <= {threshold_dbfs})",
            stage="audio",
        )
    return stats
