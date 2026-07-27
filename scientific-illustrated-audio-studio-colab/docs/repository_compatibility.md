# Repository compatibility

Method: licenses fetched from raw.githubusercontent; commits pinned via
`git ls-remote HEAD` (GitHub REST API blocked by this build environment's
egress proxy). Nothing was cloned; nothing live-integrated. Details + pins in
`repo_sources.lock.json`; licenses summarized in THIRD_PARTY_NOTICES.md.

Colab compatibility summary:
- openai-python: installs cleanly (requirements-colab.txt) — mandatory.
- higgsfield-client: pure-python SDK, Colab-safe — OPTIONAL, lazy.
- higgsfield cli: binary/npm distribution; used only if the user installs it;
  output JSON parsed defensively.
- flux2: local mode needs a large GPU + weights download → optional, explicit
  confirmation + VRAM check required; DEFAULT COLAB IS API-FIRST.
- remotion: needs Node — never installed by default; ffmpeg stays baseline;
  custom license reviewed before any commercial default (hence optional).
- whisperX / silero-vad: optional; default alignment uses OpenAI timestamps.

Risks: provider payload drift (all shapes isolated in adapters, marked
live_verified:false until a real canary); Higgsfield endpoint naming may
differ from the documented surface — the adapter is the single fix point.
