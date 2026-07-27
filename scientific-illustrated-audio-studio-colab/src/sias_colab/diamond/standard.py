"""SIAS Diamond Editorial Standard — internal production gates (story/visual/
audio/subtitle/render) + six mandatory human gates. An internal threshold, not
a claim about audience outcomes. No automated metric may override a human
rejection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from sias.schemas import QCCheck

HUMAN_GATES = [
    "hook_approval",
    "style_lock_approval",
    "recurring_character_approval",
    "pilot_approval",
    "audio_approval",
    "final_mobile_viewing_approval",
]


class DiamondReport(BaseModel):
    status: str = "PENDING"  # PASS | FAIL | HUMAN_GATES_PENDING
    pillars: dict[str, str] = Field(default_factory=dict)
    checks: list[QCCheck] = Field(default_factory=list)
    human_gates: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class DiamondGate:
    def __init__(self, first_payoff_deadline_s: float = 10.0, hook_deadline_s: float = 1.5,
                 transcript_similarity_min: float = 0.98):
        self.first_payoff_deadline_s = first_payoff_deadline_s
        self.hook_deadline_s = hook_deadline_s
        self.transcript_similarity_min = transcript_similarity_min

    # ---- pillar evaluators (each returns QCChecks) --------------------------
    def story(self, timings, beats, script_sentences: list[str]) -> list[QCCheck]:
        checks = []
        first_start = timings[0].start_s if timings else 99
        checks.append(QCCheck(check_id="hook_within_1_5s", level="story",
                              status="PASS" if first_start <= self.hook_deadline_s else "FAIL",
                              detail=f"first scene starts at {first_start}s"))
        payoff_time = timings[1].end_s if len(timings) > 1 else 99
        checks.append(QCCheck(check_id="first_payoff_by_10s", level="story",
                              status="PASS" if payoff_time <= self.first_payoff_deadline_s else "WARN",
                              detail=f"second beat ends at {payoff_time}s"))
        texts = [b.narration.strip().lower() for b in beats if b.narration.strip()]
        dup = len(texts) != len(set(texts))
        checks.append(QCCheck(check_id="no_duplicate_claim", level="story",
                              status="FAIL" if dup else "PASS"))
        long_sentences = [s for s in script_sentences if len(s.split()) > 22]
        checks.append(QCCheck(check_id="claim_per_sentence", level="story",
                              status="WARN" if long_sentences else "PASS",
                              detail=f"{len(long_sentences)} overlong sentence(s)"))
        return checks

    def visual(self, scene_reviews: list[dict[str, Any]], compositions: list[str]) -> list[QCCheck]:
        checks = []
        hard = [r for r in scene_reviews if r.get("hard_fail_reasons")]
        checks.append(QCCheck(check_id="zero_unresolved_hard_fails", level="visual",
                              status="FAIL" if hard else "PASS",
                              detail=f"{len(hard)} scene(s) carry hard fails"))
        run = 1
        worst = 1
        for a, b in zip(compositions, compositions[1:]):
            run = run + 1 if a == b else 1
            worst = max(worst, run)
        checks.append(QCCheck(check_id="composition_variety", level="visual",
                              status="FAIL" if worst >= 3 else "PASS",
                              detail=f"longest identical-composition run: {worst}"))
        return checks

    def audio(self, transcript_similarity: float, audio_check: dict[str, Any]) -> list[QCCheck]:
        checks = [QCCheck(check_id="transcript_match_98", level="audio",
                          status="PASS" if transcript_similarity >= self.transcript_similarity_min else "FAIL",
                          detail=f"similarity {transcript_similarity:.3f}")]
        checks.append(QCCheck(check_id="no_clipping", level="audio",
                              status="FAIL" if audio_check.get("clipping") else "PASS"))
        checks.append(QCCheck(check_id="no_long_silence", level="audio",
                              status="WARN" if not audio_check.get("silence_ok", True) else "PASS",
                              detail=f"longest silence {audio_check.get('longest_silence_s')}s"))
        return checks

    def subtitle(self, subtitle_issues: list[str]) -> list[QCCheck]:
        return [QCCheck(check_id="subtitle_chunking", level="subtitle",
                        status="FAIL" if subtitle_issues else "PASS",
                        detail="; ".join(subtitle_issues[:3]))]

    def render(self, final_av_checks: list[QCCheck]) -> list[QCCheck]:
        return final_av_checks

    # ---- roll-up --------------------------------------------------------------
    def evaluate(self, pillar_checks: dict[str, list[QCCheck]],
                 human_approvals: dict[str, str] | None = None) -> DiamondReport:
        report = DiamondReport()
        all_checks: list[QCCheck] = []
        for pillar, checks in pillar_checks.items():
            all_checks.extend(checks)
            fails = [c for c in checks if c.status == "FAIL"]
            report.pillars[pillar] = "FAIL" if fails else "PASS"
        report.checks = all_checks
        approvals = human_approvals or {}
        for gate in HUMAN_GATES:
            report.human_gates[gate] = approvals.get(gate, "PENDING")
        if any(v == "REJECTED" for v in report.human_gates.values()):
            report.status = "FAIL"
            report.notes.append("a human rejection is final — no metric overrides it")
        elif any(v == "FAIL" for v in report.pillars.values()):
            report.status = "FAIL"
        elif any(v != "APPROVED" for v in report.human_gates.values()):
            report.status = "HUMAN_GATES_PENDING"
        else:
            report.status = "PASS"
        return report
