"""Eight-beat story grammar (B01..B08). The emotional logic is locked; scene
count may flex 7–9 while preserving the arc."""

from __future__ import annotations

from typing import Any

from ..schemas import BEAT_ROLES, ResearchPack, StoryBeat

BEAT_TABLE = [
    ("B01", "cold_open", "normal", "impossible"),
    ("B02", "fact_1", "recognition", "concern"),
    ("B03", "fact_2", "concern", "surprise"),
    ("B04", "fact_3", "surprise", "anticipation"),
    ("B05", "explanation", "confusion", "understanding"),
    ("B06", "scale_example", "understanding", "awe"),
    ("B07", "gasp_reveal", "awe", "gasp"),
    ("B08", "payoff", "gasp", "satisfying click"),
]


def build_story_beats(pack: ResearchPack, llm: Any = None) -> list[StoryBeat]:
    if llm is not None:
        data = llm.structured_json(
            system="You are a short-form science storyteller. Curiosity over completeness.",
            prompt=(
                f"Topic: {pack.topic}. Claims: "
                + "; ".join(f"{c.claim_id}: {c.statement}" for c in pack.claims)
                + ' Return JSON {"beats":[{"beat_id","role","summary","narration","claim_refs"}]}'
                " using exactly the roles "
                + ", ".join(BEAT_ROLES)
            ),
            namespace="beats",
        )
        beats = []
        for raw, (bid, role, e_from, e_to) in zip(data.get("beats", []), BEAT_TABLE):
            beats.append(
                StoryBeat(
                    beat_id=raw.get("beat_id", bid),
                    role=raw.get("role", role),
                    summary=raw.get("summary", ""),
                    narration=raw.get("narration", ""),
                    emotional_from=e_from,
                    emotional_to=e_to,
                    claim_refs=list(raw.get("claim_refs", [])),
                )
            )
        return beats

    claim_ids = [c.claim_id for c in pack.claims] or ["C01"]
    topic = pack.topic
    offline_narration = {
        "B01": f"Imagine {topic.rstrip('?').lower()} — and it doesn't stop.",
        "B02": "At first, everything looks almost normal. Almost.",
        "B03": "Then the ground itself stops cooperating.",
        "B04": "And the systems we built quietly hit their limits.",
        "B05": "Here's the mechanism your gut missed.",
        "B06": "Scale it up, and the numbers get absurd fast.",
        "B07": "But the real surprise isn't the water at all.",
        "B08": "So next time it rains, you'll know what's really at stake.",
    }
    beats = []
    for i, (bid, role, e_from, e_to) in enumerate(BEAT_TABLE):
        beats.append(
            StoryBeat(
                beat_id=bid,
                role=role,
                summary=f"{role} beat for {topic}",
                narration=offline_narration[bid],
                emotional_from=e_from,
                emotional_to=e_to,
                claim_refs=[claim_ids[i % len(claim_ids)]] if role.startswith("fact") or role in ("explanation", "gasp_reveal") else [],
            )
        )
    return beats


def validate_beats(beats: list[StoryBeat], min_scenes: int = 7, max_scenes: int = 9) -> list[str]:
    issues: list[str] = []
    if not (min_scenes <= len(beats) <= max_scenes):
        issues.append(f"beat count {len(beats)} outside {min_scenes}..{max_scenes}")
    roles = [b.role for b in beats]
    for required in ("cold_open", "explanation", "gasp_reveal", "payoff"):
        if required not in roles:
            issues.append(f"missing required beat role: {required}")
    if roles and roles[0] != "cold_open":
        issues.append("first beat must be cold_open")
    if roles and roles[-1] != "payoff":
        issues.append("last beat must be payoff")
    if "explanation" in roles and "fact_1" in roles and roles.index("explanation") < roles.index("fact_1"):
        issues.append("explanation must follow the examples/facts")
    return issues
