# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ProviderRequestError: … transport not configured` | Offline mode: expected. For live runs wire a transport + API key (see provider_contracts.md). |
| `BFL_API_KEY missing` | Export the key; `sias audit` shows key presence without printing values. |
| BFL polling timeout | Raise `timeout_s` on poll; check BFL status page; request id is in the manifest. |
| `AssetIntegrityError: image suspiciously small/corrupt` | Provider returned junk; the pipeline refuses it — regenerate; never reuse the artifact. |
| `ProviderSchemaError` from a reviewer | Model returned non-JSON; retry once, then switch model via the resolver; malformed reviews are never accepted. |
| Model resolver failure | Family prefix has no image-capable model in the catalog; adjust `vision.*_family_prefix` or preferences. |
| `SilentAudioError` | TTS produced silence — hard failure by design; check voice/model config and re-synthesize. |
| Bad alignment (overlaps/gaps) | Transcript diverges from script; check transcript_similarity; re-TTS or fix the script; alignment refuses impossible gaps. |
| `ffmpeg`/`ffprobe` missing | Install FFmpeg; `sias audit` reports availability. |
| Final MP4 too small / missing streams | RenderError with captured ffmpeg output; inspect `render/work/*`; a tiny file is never treated as success. |
| Duration mismatch | video vs narration Δ > tolerance: check trailing silence, re-run alignment; tolerance is `render.duration_tolerance_s`. |
| Blank frames detected | blackdetect flagged intervals; inspect the offending clip; usually a corrupt source image. |
