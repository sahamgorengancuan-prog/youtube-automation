# ADR-0007 — API-first Colab mode
Status: accepted.
Context: Colab CPU runtimes are the accessibility baseline; local FLUX weights need big GPUs + downloads.
Decision: the default flow calls provider APIs only; no model weights auto-download; GPU never required. Local FLUX.2 is an optional path gated by VRAM detection, shown download size, and explicit confirmation.
Alternatives: local-first — rejected (excludes CPU users, slow cold start, license complexity).
Consequences: needs API keys for live runs; plan/render_only remain fully free/offline.
