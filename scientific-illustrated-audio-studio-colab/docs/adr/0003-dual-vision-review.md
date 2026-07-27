# ADR-0003 — Dual vision review (Qwen structural + Gemini editorial)
Status: accepted.
Context: one judge model is blind to its own biases; anatomy and editorial quality are different skills.
Decision: two reviewers with distinct rubrics via OpenRouter; weighted consensus; hard-fail veto; disagreement → REVIEW_REQUIRED (human), not auto-reject. Model slugs resolved from the live catalog.
Alternatives: single reviewer; fixed slugs — rejected.
Consequences: 2× vision cost per candidate, bounded by budget caps.
