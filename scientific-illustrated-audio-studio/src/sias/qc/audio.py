"""Audio QC: silence, loudness, catchphrase count, transcript similarity."""

from __future__ import annotations

from ..audio.silence import wav_stats
from ..audio.transcription import transcript_similarity
from ..schemas import QCCheck
from ..story.catchphrase import count_occurrences


def check_audio(
    audio_path: str,
    approved_text: str,
    transcript_text: str,
    catchphrase: str,
    silent_threshold_dbfs: float = -55.0,
    similarity_threshold: float = 0.85,
    target_lufs: float | None = None,
    measured_lufs: float | None = None,
) -> list[QCCheck]:
    checks: list[QCCheck] = []
    stats = wav_stats(audio_path)
    if stats["peak_dbfs"] <= silent_threshold_dbfs:
        checks.append(QCCheck(check_id="audio_silent", level="audio", status="FAIL", detail=f"peak {stats['peak_dbfs']} dBFS"))
    else:
        checks.append(QCCheck(check_id="audio_not_silent", level="audio", status="PASS"))
    if stats["clipping"]:
        checks.append(QCCheck(check_id="audio_clipping", level="audio", status="WARN", detail="peak at full scale"))
    n = count_occurrences(transcript_text or approved_text, catchphrase)
    if n != 1:
        checks.append(QCCheck(check_id="catchphrase_once", level="audio", status="FAIL", detail=f"catchphrase count {n} != 1"))
    else:
        checks.append(QCCheck(check_id="catchphrase_once", level="audio", status="PASS"))
    if transcript_text:
        sim = transcript_similarity(approved_text, transcript_text)
        status = "PASS" if sim >= similarity_threshold else "FAIL"
        checks.append(QCCheck(check_id="transcript_similarity", level="audio", status=status, detail=f"similarity {sim:.3f}"))
    if target_lufs is not None and measured_lufs is not None:
        drift = abs(measured_lufs - target_lufs)
        checks.append(
            QCCheck(
                check_id="loudness_target",
                level="audio",
                status="PASS" if drift <= 2.0 else "WARN",
                detail=f"integrated {measured_lufs} LUFS vs target {target_lufs}",
            )
        )
    return checks
