# ADR-0001 — Image-first, not video-first
Status: accepted.
Context: prior Motion Studio pipeline pursued generated/articulated motion; quality and reliability suffered and costs ballooned.
Decision: SIAS produces editorial still illustrations with deterministic camera motion only (hold, ≤4% push/pull, ≤3% pan, label reveal, page turn). No AI video generation, temporal diffusion, character animation, interpolation, lip sync, or reference-video dependency.
Alternatives: WAN/SkyReels I2V; Remotion skeletal animation — rejected as core (cost, failure modes, style drift).
Consequences: motion vocabulary is limited but reliable; product identity rests on illustration + narration quality.
