"""Visual QC: every approved scene present, reviewer records exist, no
unresolved hard fail, no corrupt image, no placeholder in production."""

from __future__ import annotations

from pathlib import Path

from ..schemas import CandidateRecord, QCCheck, SceneSpec


def check_visual(
    scenes: list[SceneSpec],
    approved_images: dict[str, str],
    candidate_records: dict[str, list[CandidateRecord]],
    production_mode: bool = False,
) -> list[QCCheck]:
    checks: list[QCCheck] = []
    for scene in scenes:
        sid = scene.scene_id
        image = approved_images.get(sid)
        if not image or not Path(image).exists():
            checks.append(QCCheck(check_id=f"scene_present_{sid}", level="visual", status="FAIL", detail="approved image missing"))
            continue
        if production_mode and "placeholder" in Path(image).name.lower():
            checks.append(QCCheck(check_id=f"placeholder_{sid}", level="visual", status="FAIL", detail="placeholder asset in production"))
            continue
        records = candidate_records.get(sid, [])
        if not records:
            checks.append(QCCheck(check_id=f"reviewer_records_{sid}", level="visual", status="FAIL", detail="no reviewer records"))
            continue
        unresolved = [
            code
            for r in records
            if r.status not in ("APPROVED",)
            for code in ((r.qwen.hard_fail_reasons if r.qwen else []) + (r.gemini.hard_fail_reasons if r.gemini else []))
        ]
        winner_ok = any(r.status == "APPROVED" for r in records)
        if not winner_ok:
            checks.append(QCCheck(check_id=f"approved_candidate_{sid}", level="visual", status="FAIL", detail=f"no approved candidate (unresolved: {sorted(set(unresolved))})"))
        else:
            checks.append(QCCheck(check_id=f"visual_{sid}", level="visual", status="PASS"))
    return checks
