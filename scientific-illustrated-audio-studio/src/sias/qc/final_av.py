"""Final AV QC: file/stream/duration integrity + blank frames."""

from __future__ import annotations

from ..exceptions import RenderError
from ..render.validator import detect_black_frames, validate_final
from ..schemas import QCCheck


def check_final_av(
    video_path: str,
    narration_duration_s: float,
    tolerance_s: float = 0.08,
    check_black: bool = True,
) -> list[QCCheck]:
    checks: list[QCCheck] = []
    try:
        info = validate_final(video_path, narration_duration_s, tolerance_s)
        checks.append(QCCheck(check_id="file_integrity", level="final", status="PASS", detail=f"{info['size_bytes']} bytes"))
        checks.append(QCCheck(check_id="streams", level="final", status="PASS", detail="audio+video present"))
        checks.append(
            QCCheck(
                check_id="duration_integrity",
                level="final",
                status="PASS",
                detail=f"|Δ|={info['duration_delta_s']}s <= {tolerance_s}s",
            )
        )
    except RenderError as exc:
        checks.append(QCCheck(check_id="final_av", level="final", status="FAIL", detail=str(exc)))
        return checks
    if check_black:
        black = detect_black_frames(video_path)
        if black:
            checks.append(QCCheck(check_id="blank_frames", level="final", status="FAIL", detail=f"black frames at {black}"))
        else:
            checks.append(QCCheck(check_id="blank_frames", level="final", status="PASS"))
    return checks
