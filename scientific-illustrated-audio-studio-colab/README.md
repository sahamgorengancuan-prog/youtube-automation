# SIAS Agentic Colab Studio

**ID:** Studio Google Colab untuk cerita sains pendek dari **ilustrasi diam +
narasi alami** — bukan video AI. Mode awal `plan` **gratis** (nol panggilan API).

**EN:** A Google Colab production system for short science stories built from
**still illustrations + natural narration** — image-first, never video-first.
Default `plan` mode is **free** (zero paid API calls).

## 💎 One-Click FULL (satu notebook terintegrasi, sekali Run All)

`notebooks/SIAS_One_Click.ipynb` — SATU notebook: isi topik → **Runtime ▸ Run
all** → **10 tahap** berjalan otomatis dalam satu lintasan, termasuk seluruh
lapisan Diamond:

1. routing snapshot (hardware + backend primer/fallback, cek anti-self-approval)
2. plan (riset → hook tournament → 8 beat → retention critic → scene specs)
3. ilustrasi + **flag konsistensi** (palette/struktur vs style anchor)
4. narasi SATU trek (OpenAI TTS live) / audio bed (preview)
5. alignment kata + **jeda hening pra-reveal**
6. **audio checks** (clipping / silence / stereo) + premaster −16 LUFS (live)
7. render FFmpeg + **subtitle SRT & ASS Diamond** (chunk 2–6 kata, gaya
   Default/Emphasis/Reveal/SciTerm)
8. QC teknis (stream, durasi, blank frame, tolak placeholder di mode live)
9. **Diamond Editorial Gate** — 5 pilar + **6 gerbang persetujuan manusia**
10. manifest + ekspor ZIP

Tanpa API key tetap selesai end-to-end sebagai **PREVIEW** ber-watermark
(0 biaya); dengan key + `RUN_LIVE=True` → episode live penuh (BFL + OpenAI TTS
+ review Qwen VL 32B & Gemini 2.5 Flash). Status Diamond menahan `PASS` sampai
Anda menyetujui 6 gerbang manusia (`HUMAN_GATES_APPROVED=True`) — tidak ada
metrik otomatis yang bisa menggantikannya.

Verified: keyless nbclient Run-All → 0 errors, QC PASS, Diamond
HUMAN_GATES_PENDING, MP4 + SRT + ASS + ZIP dihasilkan.

Notebook 17-bagian (`SIAS_Agentic_Colab_Control_Center.ipynb`) tetap tersedia
untuk kontrol bertahap dengan gerbang persetujuan manusia.

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
