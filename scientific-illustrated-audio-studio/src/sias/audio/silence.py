"""WAV integrity: duration, peak/RMS dBFS, silence and clipping detection.
Effectively-silent audio is a hard failure, never a warning."""

from __future__ import annotations

import audioop
import math
from pathlib import Path
from typing import Any

from ..exceptions import AssetIntegrityError, SilentAudioError
from .wavio import open_wav


def wav_stats(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() or p.stat().st_size < 128:
        raise AssetIntegrityError(f"audio file missing or too small: {p}", stage="audio")
    with open_wav(p) as wf:  # tolerates FFmpeg's WAVE_FORMAT_EXTENSIBLE output
        header_frames = wf.getnframes()
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        frames = wf.readframes(header_frames)

    # Duration MUST come from the PCM actually present, not the header: streamed
    # WAVs (e.g. OpenAI TTS) ship a placeholder data-chunk size (0xFFFFFFFF),
    # which makes `wave` report 2**31-1 frames — a ~24.8h phantom duration.
    frame_size = max(1, width * channels)
    actual_frames = len(frames) // frame_size
    header_mismatch = header_frames != actual_frames
    duration = actual_frames / float(rate) if rate else 0.0
    peak = audioop.max(frames, width) if frames else 0
    rms = audioop.rms(frames, width) if frames else 0
    full_scale = float(2 ** (8 * width - 1) - 1)
    peak_dbfs = 20 * math.log10(peak / full_scale) if peak > 0 else -120.0
    rms_dbfs = 20 * math.log10(rms / full_scale) if rms > 0 else -120.0
    return {
        "duration_s": round(duration, 3),
        "actual_frames": actual_frames,
        "header_frames": header_frames,
        "header_mismatch": header_mismatch,
        "rate": rate,
        "channels": channels,
        "peak_dbfs": round(peak_dbfs, 2),
        "rms_dbfs": round(rms_dbfs, 2),
        "clipping": peak >= full_scale - 1,
    }


def assert_plausible_duration(measured_s: float, expected_s: float,
                              tolerance_factor: float = 3.0, hard_cap_s: float = 900.0) -> None:
    """Refuse an obviously wrong narration duration BEFORE it reaches the
    renderer. Catches streamed-WAV header phantoms and truncated audio."""
    if measured_s <= 0:
        raise AssetIntegrityError("narration duration is zero", stage="audio")
    if measured_s > hard_cap_s:
        raise AssetIntegrityError(
            f"narration duration {measured_s:.1f}s exceeds the {hard_cap_s:.0f}s hard cap "
            "(a streamed-WAV header can report a phantom length — measure real PCM bytes)",
            stage="audio",
        )
    if expected_s > 0 and not (expected_s / tolerance_factor <= measured_s <= expected_s * tolerance_factor):
        raise AssetIntegrityError(
            f"narration duration {measured_s:.1f}s is implausible versus the ~{expected_s:.1f}s "
            f"script estimate (>{tolerance_factor}x off) — TTS output looks wrong",
            stage="audio",
        )


def assert_not_silent(path: str | Path, threshold_dbfs: float = -55.0) -> dict[str, Any]:
    stats = wav_stats(path)
    if stats["peak_dbfs"] <= threshold_dbfs:
        raise SilentAudioError(
            f"audio effectively silent (peak {stats['peak_dbfs']} dBFS <= {threshold_dbfs})",
            stage="audio",
        )
    return stats
