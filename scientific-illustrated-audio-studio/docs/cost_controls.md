# Cost controls

## Budget ledger
`sias.budget.BudgetLedger` tracks BFL images, vision reviews, TTS characters,
transcription seconds and repair requests. Before every paid call: estimate →
compare with the configured hard cap → record the decision → abort with
`BudgetExceededError` when `hard_stop_on_budget_exceeded` (default true).

## Caps (configs/default.yaml)
max_image_calls 30 · max_vision_calls 80 · max_tts_characters 9000.

## Pilot-first economics
The 4-scene pilot proves style consistency, anatomy, narration quality,
alignment and AV integrity for a fraction of full-production spend; production
stays locked until `pilot_qc.status == PASS`. Overriding requires an explicit
config flag and is written into the final report.

## No hidden calls
Adapters cannot spend without an injected transport + key; run modes `plan`
and `render_only` structurally forbid paid stages; `--dry-run` prints the plan
(provider, model, est. calls, caps) and executes nothing; repair loops are
bounded at 2 attempts per scene.
