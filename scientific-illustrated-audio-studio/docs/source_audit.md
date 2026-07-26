# Source audit

Date: 2026-07-26. Working directory: `youtube-automation` repository +
session uploads.

## Files inspected

| Source | Status | Use |
|---|---|---|
| `Scientific_Illustrated_Audio_Studio_Blueprint_v1.md` | **ABSENT** | not available; proceeded from the production blueprint (`CLAUDE_FABLE_SIAS_PRODUCTION_BLUEPRINT.md`), which embeds the product direction, style canon, story grammar and safety constraints |
| `Scientific_Illustrated_Audio_Studio_Blueprint_v1.ipynb` | **ABSENT** | scaffold built fresh from the production blueprint's contracts |
| `Scientific_Motion_Studio_v10_Agentic_Production_FINAL.ipynb` (+ LINEAR variant, repo root) | present | mined for reusable utility *patterns* only |
| `what-happens-if-it-rains-nonstop-for-one-*.rar` parts | **INCOMPLETE** | uploads contain `*.zip.001`/`.002` split-archive parts (not RAR); the final part is missing, so the archive cannot be safely extracted. Prior failure diagnostics were instead taken from the uploaded `error_report*.json` (BFL `Request Moderated` crash; `npm install` exit 1 crash) |

## Reusable legacy components (patterns adopted, code rewritten)

- input-hash + output-SHA-256 stage manifests and resume rules (`sias.manifests`, `sias.cache`);
- atomic file writes and JSON quarantine discipline (`sias.filesystem`);
- typed exceptions with stage context; failed manifests on error;
- secret registration + log redaction (`sias.logging_utils`);
- injectable provider transports so all tests run without live keys;
- moderation is an explicit provider failure (BFL adapter), never a silent retry loop.

## Rejected legacy components (video-first — removed by design)

- temporal video backends (WAN/SkyReels), motion DSL, skeletal rigs, SAM2
  segmentation, Remotion FK executor, reference-video style extraction,
  per-scene "beauty frame → motion" architecture, silent fallbacks of any kind.
- mandatory reference-video assumption: SIAS takes only a topic + audience.

## Assumptions

- BFL flux-2 endpoint family (`flux-2-pro` etc.) accepts `prompt/seed/width/
  height/reference_images` — payload isolated in `providers/bfl.py`; VERIFY
  against current BFL docs before live use.
- OpenAI `gpt-4o-mini-tts` voice `cedar` and `whisper-1` verbose_json word
  timestamps — configurable, not guaranteed current.
- OpenRouter model slugs are resolved at runtime from the live catalog
  (`providers/model_resolver.py`), never hard-coded.

## Unresolved uncertainties

- Exact current BFL flux-2 request/response field names (submit/poll shapes).
- Whether `gpt-4o-mini-tts` supports the configured voice at run time.
- Loudness normalization targets may need per-platform tuning.
