# SIAS Agentic Colab Studio

**ID:** Studio Google Colab untuk cerita sains pendek dari **ilustrasi diam +
narasi alami** — bukan video AI. Mode awal `plan` **gratis** (nol panggilan API).

**EN:** A Google Colab production system for short science stories built from
**still illustrations + natural narration** — image-first, never video-first.
Default `plan` mode is **free** (zero paid API calls).

## Open in Colab

Upload / open `notebooks/SIAS_Agentic_Colab_Control_Center.ipynb` in Google
Colab (CPU runtime is enough — GPU never required for the API-first flow),
then follow the numbered sections 1→17. Indonesian UI by default; set
`UI_LANG="en"` in the first cell to switch.

Three source modes: `github_repository` (the init cell clones this repo),
`standalone` (pip install), `uploaded_zip` (extract both
`scientific-illustrated-audio-studio*/` directories side by side).

## What a first-time user does

setup → Drive (optional) → add secrets (🔑 Colab Secrets: `BFL_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`; `HF_*` optional) → enter a topic →
**free planning + storyboard review** → small provider **canary** → **style
lock** + manual approval → **4-scene pilot** → watch it → unlock →
**production** → final QC → **export ZIP**. No Git/Pydantic/FFmpeg knowledge
needed; every manifest and command stays inspectable for advanced users.

## Safety model

Every paid operation requires ALL of: `ARM_PAID_CALLS=True` + the secret +
run-mode allowance + available budget + previous gate passed + an explicit
confirmation checkbox. `BudgetGuardian` hard-stops at the caps (images /
vision / TTS chars / Higgsfield). Failures always show stage, provider, model,
request id, recoverability, and the next action — never "something went wrong".

## Architecture

`sias_colab` (this repo dir) is the Colab layer — supervisor DAG with 20
single-responsibility agents, episode state + Drive resume, canary, bilingual
dashboard (ipywidgets + text fallback), optional Higgsfield adapter — on top of
the proven `sias` engine (`../scientific-illustrated-audio-studio`, 52 tests).
Audio is the timeline source of truth; FFmpeg is the reference renderer
(Remotion optional, lazy, license-respected). See `docs/architecture.md` +
`docs/adr/`.

## Validation

`make all` → ruff + pytest (17 tests: unit, mocked provider contracts incl.
Higgsfield, notebook JSON/parse/keyless-plan-execution) + notebook validation +
top-to-bottom plan execution with zero keys + FFmpeg smoke render (watermarked
placeholders that production QC provably rejects).

## Docs

`docs/colab_user_guide.md` (ID+EN) · architecture · provider_contracts ·
repository_compatibility (+ `repo_sources.lock.json`, `THIRD_PARTY_NOTICES.md`)
· style_canon · story_grammar · qc_rubrics · cost_controls · troubleshooting.
