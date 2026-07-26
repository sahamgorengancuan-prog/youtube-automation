# Architecture

## System diagram

topic → research → hooks → beats → spoken script → scene specs → style lock
→ candidates (BFL) → dual vision review (Qwen structural + Gemini editorial via
OpenRouter) → consensus/repair → approved scene library → one TTS track (OpenAI)
→ transcription + word alignment → scene timings → FFmpeg deterministic render
→ final AV QC → package (MP4 + SRT + manifest + QC report).

## Stage dependencies

See `src/sias/pipeline/stages.py::PARENTS`. Every stage writes a
`StageManifest` (input hash, artifact path + SHA-256, provider/model, costs,
parents, warnings/errors). Resume reuses a stage only when status=PASS AND
input hash matches AND the artifact's SHA-256 matches (`sias.cache`).

## Provider boundaries

One adapter module per provider under `src/sias/providers/`; request payload
shapes exist nowhere else. Adapters take an injectable transport; without one,
every call raises `ProviderRequestError` — offline can never silently spend or
silently fake.

## Source-of-truth rules

1. Audio duration is the timeline source of truth (ADR-0002).
2. Style lock precedes all production scenes (ADR-0004).
3. The consensus selector may never approve past a reviewer hard-fail.
4. FFmpeg output is the validation reference even if Remotion is added later
   (ADR-0005).

## Failure behavior

Typed exceptions (`sias.exceptions`), stage-context messages, failed manifests
written on error, no fake success artifacts, no broad except-pass. Hard
failures include silent audio, corrupt/tiny outputs, missing streams, duration
mismatch beyond tolerance, unresolved hard-fail codes, placeholder assets in
production.
