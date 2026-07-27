# Architecture

Two layers:
1. **Engine** — the `sias` package (../scientific-illustrated-audio-studio):
   schemas, story/style systems, BFL/OpenAI/OpenRouter adapters, dual-vision
   consensus, alignment (audio = timeline source of truth), FFmpeg renderer,
   QC. 52 tests of its own.
2. **Colab layer** — `sias_colab`: bootstrap (3 source modes), StudioConfig,
   EpisodeState (Drive-persistable, resume), BudgetGuardian + 6-condition paid
   gate, supervisor DAG with 20 single-responsibility agents, hook v2 +
   Retention Critic (one bounded revision), watermark-placeholder QC, canary,
   optional Higgsfield adapter, bilingual dashboard, Studio facade.

Pipeline: topic → claims → hook tournament (3 kinds) → 8 beats → retention
critique → spoken draft → scene specs → style lock (machine PASS + human
APPROVED) → candidate tournament → Qwen structural + Gemini editorial →
consensus/bounded repair → approved library → ONE TTS track → transcription →
word alignment → deterministic FFmpeg composition → final AV QC → MP4 + SRT +
manifests + reports.

Failure behavior: typed exceptions with stage context, FAILED manifests, no
fake artifacts, no silent fallback; paid agents refuse when un-armed; offline
adapters refuse rather than fabricate. Cache reuse requires PASS + input-hash
+ artifact SHA-256 (file existence is never enough). State resumes after a
runtime disconnect from `episodes/<id>/state.json`.
