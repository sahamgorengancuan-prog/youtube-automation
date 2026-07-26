"""Research claims. Offline (plan mode) produces a clearly-marked draft pack;
live mode uses the OpenAI text adapter for structured claims. Draft claims are
never presented as sourced facts — source_guard flags them."""

from __future__ import annotations

from typing import Any

from ..schemas import ResearchClaim, ResearchPack

_OFFLINE_TEMPLATES = [
    "Under the conditions of '{topic}', the first-order effect appears sooner than intuition expects.",
    "The system involved in '{topic}' has a finite capacity; past saturation, behaviour changes regime.",
    "A second-order consequence of '{topic}' acts on a much larger scale than the visible one.",
]


def build_research_pack(
    topic: str,
    audience: str = "curious general audience, age 15+",
    facts_count: int = 3,
    llm: Any = None,
) -> ResearchPack:
    if llm is not None:
        data = llm.structured_json(
            system=(
                "You are a careful science researcher. Produce verifiable claims with "
                "real sources. Never invent citations."
            ),
            prompt=(
                f"Topic: {topic}\nAudience: {audience}\nReturn JSON "
                '{"claims":[{"claim_id","statement","sources","confidence","caveat"}],'
                f'"summary": "..."}} with exactly {facts_count} claims.'
            ),
            namespace="research",
        )
        claims = [ResearchClaim.model_validate(c) for c in data.get("claims", [])]
        return ResearchPack(topic=topic, audience=audience, claims=claims, summary=data.get("summary", ""))

    claims = [
        ResearchClaim(
            claim_id=f"C{i + 1:02d}",
            statement=t.format(topic=topic),
            sources=[],
            confidence=0.3,
            caveat="OFFLINE-DRAFT: placeholder claim; requires research + real sources before production.",
        )
        for i, t in enumerate(_OFFLINE_TEMPLATES[: max(1, facts_count)])
    ]
    return ResearchPack(
        topic=topic,
        audience=audience,
        claims=claims,
        summary=f"Offline draft research pack for: {topic}",
    )
