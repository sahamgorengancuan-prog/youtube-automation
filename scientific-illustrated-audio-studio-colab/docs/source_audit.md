# Source audit — SIAS Agentic Colab Studio (v2)

Date: 2026-07-27. Branch: `claude/scientific-animation-studio-mvp-65rpn4`
(session working branch, not protected; nothing on main touched).

## Files inspected

| Source | Status | Decision |
|---|---|---|
| `scientific-illustrated-audio-studio/` (package `sias`, 52 passing tests) | **PRESENT** | RETAIN as the engine. `sias_colab` imports it; no core duplication. |
| `CLAUDE_FABLE_SIAS_PRODUCTION_BLUEPRINT.md` (uploads) | present | conceptual source of truth for canon/grammar/QC (already encoded in `sias`) |
| `Scientific_Illustrated_Audio_Studio_Blueprint_v1.md` / `.ipynb` | **ABSENT** | not invented; proceeded from the production blueprint + v2 master prompt |
| `SIAS_Agentic_Colab_Control_Center.ipynb` / `SIAS_Agentic_Colab_Guide.md` | **ABSENT** | built fresh here |
| `Scientific_Motion_Studio_v10_*` notebooks | present | video-first architecture REJECTED for SIAS; retained learnings: hash manifests, budget gates, moderation-as-explicit-failure, npm-failure lesson (ffmpeg baseline) |
| Rain-job archive (`*.zip.001/.002`) | incomplete (final part missing) | not extracted; failure diagnostics from uploaded error reports |

## Useful components retained (from `sias`)

config validation; 20 schemas; 8-beat grammar + validators; hook scoring;
catchphrase rules; style bible + reference hierarchy + 10-block prompt
compiler; BFL/OpenAI/OpenRouter adapters (injectable transports); dual-vision
consensus + hard-fail veto + bounded repair; silence gates; word-timestamp
alignment (audio = timeline); FFmpeg renderer + validator; run modes +
production lock; QC roll-up; 52 tests.

## Duplicated components avoided

`sias_colab` re-exports rather than copies. The only intentional additions:
supervisor DAG, episode state/resume, BudgetGuardian (adds Higgsfield +
transcription tracking), scale-first hook + retention critic, canary
orchestration, watermarked-placeholder QC, ipywidgets UI, Higgsfield adapter.

## Broken assumptions found

- GitHub REST API unreachable through the egress proxy (raw + ls-remote work) —
  third-party pinning done via ls-remote.
- `black-forest-labs/skills` has no README/LICENSE at `main/` root paths —
  recorded as such; guidance translated from BFL API patterns already encoded
  in `sias.providers.bfl` (submit→poll→download, moderation explicit).
- Colab `google.colab` is unavailable here — notebook is written and TESTED in
  non-Colab fallback mode (Drive/userdata guarded); Colab-specific paths are
  exercised only structurally.

## Video-first components rejected

Temporal diffusion backends, skeletal animation, frame interpolation,
reference-video extraction, Remotion-as-default (optional only), any silent
fallback.

## Provider payloads requiring live confirmation

BFL flux-2 submit/poll field names; OpenAI `gpt-4o-mini-tts` voice list;
OpenRouter current Qwen/Gemini slugs (resolver queries live catalog);
Higgsfield client endpoints + CLI output schema. All isolated in adapters,
all mock-tested, all marked `live_verified: false`.

## Reusable tests

The `sias` suite (52) continues to run unchanged. The v2 suite adds
supervisor/state/budget-guardian/higgsfield/notebook/watermark tests.

## Architectural conflicts + migration decisions

None destructive: v1 stays canonical for CLI/library use; v2 adds the Colab
product layer on top. If the two ever diverge, `sias` remains the single
engine of record (DECISIONS.md #2).
