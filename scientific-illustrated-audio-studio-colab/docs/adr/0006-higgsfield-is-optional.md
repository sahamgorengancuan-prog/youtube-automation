# ADR-0006 — Higgsfield is optional
Status: accepted.
Context: Higgsfield offers image models, live model/cost discovery, and a Virality Predictor; but its credentials are extra cost/complexity, and its video models could pull SIAS toward video-first.
Decision: HiggsfieldAdapter is opt-in (enabled:false; virality predictor separately false). BFL remains primary imaging. SIAS runs fully without HF_* credentials. Video generation is never used in the core pipeline.
Alternatives: HF as co-primary — rejected (two mandatory image providers, unclear failure semantics).
Consequences: HF users get fallback imaging + post-render analysis; everyone else sees zero difference.
