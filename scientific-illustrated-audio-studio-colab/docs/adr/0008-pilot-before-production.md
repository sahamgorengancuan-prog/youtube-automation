# ADR-0008 — Pilot before production
Status: accepted.
Context: full production spends the whole budget; failures should surface on 4 scenes, not 8+.
Decision: production stays locked until the 4-scene pilot passes ALL gates (style lock machine+human, zero anatomy/science hard fails, non-silent narration, transcript similarity, contiguous timeline, both streams, duration match, blank-frame check). Override requires an explicit flag and is recorded as a warning.
Alternatives: straight-to-production — rejected (the legacy pipeline lost full runs to single failures).
Consequences: slightly slower happy path; drastically cheaper failure path.
