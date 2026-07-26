"""Source guard — no claim ships unsourced without a caveat."""

from __future__ import annotations

from ..schemas import QCCheck, ResearchPack


def validate_pack(pack: ResearchPack) -> list[QCCheck]:
    checks: list[QCCheck] = []
    for claim in pack.claims:
        if not claim.sources and not claim.caveat:
            checks.append(
                QCCheck(
                    check_id=f"claim_unsourced_{claim.claim_id}",
                    level="story",
                    status="FAIL",
                    detail=f"{claim.claim_id} has no sources and no caveat",
                )
            )
        elif not claim.sources:
            checks.append(
                QCCheck(
                    check_id=f"claim_caveat_only_{claim.claim_id}",
                    level="story",
                    status="WARN",
                    detail=f"{claim.claim_id} relies on a caveat; add a source before production",
                )
            )
        else:
            checks.append(
                QCCheck(check_id=f"claim_sourced_{claim.claim_id}", level="story", status="PASS")
            )
    return checks
