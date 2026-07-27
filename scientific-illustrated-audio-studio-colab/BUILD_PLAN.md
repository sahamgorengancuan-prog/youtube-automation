# SIAS Agentic Colab Studio — Build Plan

Goal: a user-friendly Google Colab production system (Indonesian-first UI) for
image-first science storytelling, built on the proven `sias` core package,
with an agentic supervisor, optional Higgsfield integration, and hard paid-call
protection. Image-first, never video-first. Audio is the timeline source of truth.

## Milestones (each ends in a commit)

1. **Source audit + scaffold** — git safety check, inspect existing SIAS build
   and third-party repos (license + pinned SHA via raw/ls-remote), scaffold
   `scientific-illustrated-audio-studio-colab/`, progress files, repo lock.
2. **Offline core** — `sias_colab` package: config/state/BudgetGuardian/
   supervisor DAG + 20 agents (delegating to the tested `sias` core), story
   extensions (scale-first hook, retention critic), placeholder-watermark QC.
3. **Provider adapters** — optional Higgsfield adapter (client-style + CLI
   parsing, mock-tested), canary orchestration, re-exported BFL/OpenAI/
   OpenRouter adapters.
4. **Colab UX** — control-center notebook (17 sections, ID/EN toggle,
   ipywidgets dashboard + forms fallback, three source modes, Drive optional),
   two workbench notebooks, notebook build/validate/execute tools.
5. **Tests + smoke render** — unit/provider-contract/notebook/golden suites;
   watermark placeholder smoke render; full validation sequence.
6. **Docs + final audit** — 10 docs, 8 ADRs, THIRD_PARTY_NOTICES,
   VALIDATION_REPORT, KNOWN_LIMITATIONS, final commit + push.

## Reuse strategy

The existing `scientific-illustrated-audio-studio/` (package `sias`, 52 passing
tests) is preserved untouched and REUSED as the engine: `sias_colab` depends on
it for schemas, story/style/vision/audio/render/qc logic and provider adapters,
adding the Colab layer (supervisor, state/resume, budget guardian, UI, canary,
Higgsfield) on top. No core logic is duplicated into notebook cells.

## Working branch

`claude/scientific-animation-studio-mvp-65rpn4` (designated session branch; not
a protected branch — see DECISIONS.md #1).
