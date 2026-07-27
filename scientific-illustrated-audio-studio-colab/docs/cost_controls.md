# Cost controls

BudgetGuardian extends the engine ledger: BFL images, OpenRouter vision calls,
TTS characters, transcription seconds, repair attempts, Higgsfield calls, and
provider-REPORTED costs. Before every paid call: estimate → compare to hard cap
→ record → abort (`BudgetExceededError`) when `hard_stop_on_budget_exceeded`.

Every paid section additionally passes `assert_paid_call_allowed`, which
requires ALL of: ARM_PAID_CALLS · secret present · run mode allows the stage ·
incremental budget available · previous quality gate passed · explicit user
action. Any missing condition refuses with the exact reasons.

Pilot-first economics: production stays locked until pilot QC = PASS
(4 scenes, one narration track — a fraction of full spend). The canary is even
cheaper: 2 images + 2 reviews + 1 TTS + 1 transcription proves every key works
before style lock. Defaults: 30 images / 80 vision / 9000 TTS chars / 5 HF.
