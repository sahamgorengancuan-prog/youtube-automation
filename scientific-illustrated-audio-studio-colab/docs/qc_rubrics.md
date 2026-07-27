# QC rubrics

## Qwen — structural review (vision/qwen_reviewer.py)
Focus: anatomy (one head/torso, two arms/legs, connected joints), identity
consistency against the character sheet, composition readability, asset
integrity. Returns strict JSON scores 0..1 + hard-fail codes + repair
instructions. Malformed JSON is rejected (`ProviderSchemaError`).

## Gemini — editorial review (vision/gemini_reviewer.py)
Focus: one hero idea, curiosity pull, label economy (short noun phrases only),
humor that serves the story, safe caption zone. Same strict JSON contract.

## Weights
anatomy 15% · style fidelity 18% · identity 17% · composition 12% · science
accuracy 15% · story clarity 15% · novelty 8%.

## Decision rules
- any hard fail from either reviewer → never approved (REPAIR);
- |qwen − gemini| > disagreement_threshold (0.18) → REVIEW_REQUIRED (tie-break
  + human inspection, not auto-reject);
- consensus ≥ approval_threshold (0.82) → APPROVED; else REPAIR.

## Hard-fail taxonomy
HF_ANATOMY_MISSING_LIMB · HF_ANATOMY_DUPLICATED_LIMB ·
HF_ANATOMY_DISCONNECTED_PART · HF_IDENTITY_WRONG_CHARACTER · HF_STYLE_ABSTRACT
· HF_STYLE_SURREAL · HF_SCIENCE_CONTRADICTION · HF_COMPOSITION_UNREADABLE ·
HF_TEXT_HALLUCINATION · HF_ASSET_CORRUPT

## Final gates
File/stream/duration integrity (|video − narration| ≤ 0.08 s, configurable),
blank-frame detection, silence/clipping/loudness, transcript similarity,
catchphrase exactly once, story timing (hook ≤2 s, first payoff ≤ deadline).
Statuses: PASS / PASS_WITH_WARNINGS / FAIL / HUMAN_DECISION_REQUIRED; publish
only on PASS or a documented human override.
