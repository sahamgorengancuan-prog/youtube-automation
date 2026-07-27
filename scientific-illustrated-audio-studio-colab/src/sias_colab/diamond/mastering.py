"""Audio layering + mastering (commercial-safe FFmpeg-only default).

Layers: narration · optional licensed music bed · transitions · UI sounds ·
silence (deliberate, before reveals). Generated music/sfx models are NOT used
by default (MusicGen/AudioLDM2 weights non-commercial; MMAudio review-gated).

Targets: narration pre-master ≈ -16 LUFS; final short-form mix ≈ -14 LUFS
(configurable); true peak ≤ -1 dBTP."""

from __future__ import annotations

import audioop
import wave
from pathlib import Path
from typing import Any

from sias.exceptions import RenderError
from sias.render.ffmpeg import run as ffmpeg_run


def narration_premaster_cmd(src: str | Path, out: str | Path, lufs: float = -16.0,
                            true_peak: float = -1.0, ffmpeg: str = "ffmpeg") -> list[str]:
    return [ffmpeg, "-y", "-i", str(src),
            "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA=11", str(out)]


def final_mix_cmd(narration: str | Path, out: str | Path, music: str | Path | None = None,
                  music_duck_db: float = -14.0, lufs: float = -14.0, true_peak: float = -1.0,
                  ffmpeg: str = "ffmpeg") -> list[str]:
    """Mix narration (+ optional licensed music bed ducked under speech) and
    master to the final loudness target."""
    if music is None:
        return [ffmpeg, "-y", "-i", str(narration),
                "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA=11", str(out)]
    return [ffmpeg, "-y", "-i", str(narration), "-i", str(music),
            "-filter_complex",
            (f"[1:a]volume={music_duck_db}dB[m];"
             f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0.5,"
             f"loudnorm=I={lufs}:TP={true_peak}:LRA=11[out]"),
            "-map", "[out]", str(out)]


def master_narration(src: str | Path, out: str | Path, lufs: float = -16.0) -> Path:
    ffmpeg_run(narration_premaster_cmd(src, out, lufs), stage="mastering.premaster")
    if not Path(out).exists():
        raise RenderError("premaster produced no output", stage="mastering")
    return Path(out)


# ---- automated checks (offline, wave-scan) ----------------------------------

def audio_checks(path: str | Path, max_silence_s: float = 2.5, window_s: float = 0.25) -> dict[str, Any]:
    """Clipping, long unexplained silence, stereo imbalance — from raw samples."""
    with wave.open(str(path), "rb") as wf:
        width, channels, rate = wf.getsampwidth(), wf.getnchannels(), wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    full_scale = float(2 ** (8 * width - 1) - 1)
    clipping = audioop.max(frames, width) >= full_scale - 1

    # longest quiet stretch (RMS windows below -50 dBFS)
    window_bytes = int(rate * window_s) * width * channels
    quiet_run = longest_quiet = 0.0
    for i in range(0, max(1, len(frames) - window_bytes), window_bytes):
        rms = audioop.rms(frames[i:i + window_bytes], width)
        if rms < full_scale * 0.003:
            quiet_run += window_s
            longest_quiet = max(longest_quiet, quiet_run)
        else:
            quiet_run = 0.0

    imbalance = 0.0
    if channels == 2:
        left = audioop.tomono(frames, width, 1, 0)
        right = audioop.tomono(frames, width, 0, 1)
        rms_l, rms_r = audioop.rms(left, width) or 1, audioop.rms(right, width) or 1
        imbalance = abs(rms_l - rms_r) / max(rms_l, rms_r)

    return {
        "clipping": clipping,
        "longest_silence_s": round(longest_quiet, 2),
        "silence_ok": longest_quiet <= max_silence_s,
        "stereo_imbalance": round(imbalance, 3),
        "stereo_ok": imbalance <= 0.35,
    }


def insert_pre_reveal_silence(timings, reveal_scene_id: str, hold_s: float = 0.35):
    """Deliberate silence design: widen the gap entering the reveal scene by
    shifting its start later within its own duration (never total duration)."""
    out = [t.model_copy(deep=True) for t in timings]
    for i, timing in enumerate(out):
        if timing.scene_id == reveal_scene_id and i > 0:
            span = timing.end_s - timing.start_s
            shift = min(hold_s, span * 0.2)
            timing.start_s = round(timing.start_s + shift, 3)
            out[i - 1].end_s = timing.start_s
    return out
