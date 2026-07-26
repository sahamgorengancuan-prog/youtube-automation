"""Conservative repair: local corrections only, bounded attempts, then a human
decision — never an unbounded regeneration loop."""

from __future__ import annotations

from ..exceptions import RepairLimitError
from ..schemas import CandidateRecord, RepairRequest

MAX_REPAIR_ATTEMPTS = 2

PRESERVE_CLAUSES = [
    "preserve character identity",
    "preserve correct anatomy",
    "preserve palette",
    "preserve paper texture",
    "preserve layout where correct",
    "preserve scientific objects",
    "alter only the diagnosed failures",
    "do not add characters",
    "do not add decorative text",
    "do not redesign the entire image",
]


def build_repair_request(record: CandidateRecord, attempt: int) -> RepairRequest:
    if attempt > MAX_REPAIR_ATTEMPTS:
        raise RepairLimitError(
            f"scene {record.scene_id}: repair attempt {attempt} exceeds the limit "
            f"({MAX_REPAIR_ATTEMPTS}); status = HUMAN_DECISION_REQUIRED",
            stage="repair",
        )
    hard = list((record.qwen.hard_fail_reasons if record.qwen else []) + (record.gemini.hard_fail_reasons if record.gemini else []))
    fixes = list(
        dict.fromkeys(
            (record.qwen.repair_instructions if record.qwen else [])
            + (record.gemini.repair_instructions if record.gemini else [])
        )
    )
    return RepairRequest(
        scene_id=record.scene_id,
        source_candidate_id=record.candidate_id,
        hard_fail_codes=hard,
        visible_failures=fixes,
        required_corrections=fixes,
        preserve_regions=["all regions not named in required_corrections"],
        forbidden_changes=["new characters", "decorative text", "full redesign"],
        repair_attempt=attempt,
    )


def repair_prompt(request: RepairRequest) -> str:
    return (
        f"CONSERVATIVE LOCAL EDIT for scene {request.scene_id} "
        f"(attempt {request.repair_attempt}/{MAX_REPAIR_ATTEMPTS}).\n"
        "Required corrections: " + "; ".join(request.required_corrections or ["none listed"]) + ".\n"
        "Contract: " + "; ".join(PRESERVE_CLAUSES) + "."
    )


def status_after_failure(attempt: int) -> str:
    return "HUMAN_DECISION_REQUIRED" if attempt >= MAX_REPAIR_ATTEMPTS else "REPAIR"
