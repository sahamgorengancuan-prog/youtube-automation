# Third-party notices

SIAS Colab integrates or references the following (pins + roles in
`repo_sources.lock.json`; none live-verified in this build):

| Repository | License | Use |
|---|---|---|
| higgsfield-ai/skills @27defbaa75ef | MIT | agent-skill reference only |
| higgsfield-ai/cli @8827135df760 | MIT | optional live model/cost discovery (output parsed) |
| higgsfield-ai/higgsfield-client @13dd62a12693 | Apache-2.0 | optional image/analysis SDK path |
| black-forest-labs/skills @d0793c842621 | (see repo) | FLUX prompting/API guidance, translated to code |
| black-forest-labs/flux2 @50fe51627778 | Apache-2.0 (code); weights per-model | inference reference; optional local mode, never auto-downloaded |
| openai/openai-python @d4c151d92ba7 | Apache-2.0 | official SDK (requirements-colab) |
| remotion-dev/remotion @258a191cbaf8 | Remotion License (custom) | OPTIONAL renderer, lazy install only — company license may be required for commercial use |
| m-bain/whisperX | BSD-2-Clause | optional advanced alignment |
| snakers4/silero-vad | MIT (per docs) | optional VAD |

`higgsfield-ai/higgsfield` (training orchestration) is explicitly NOT used.
No model weights, fonts, or third-party code are redistributed in this repo.

## Diamond-stack additions (2026-07-27; details in docs/license_matrix.md + model_weights.lock.json)
langgraph (MIT) · pydantic-ai (MIT) · prefect (Apache-2.0) · Qwen-Image /
Qwen3-VL (Apache-2.0) · Step1X-Edit (Apache-2.0) · OmniGen2 (Apache-2.0) ·
ComfyUI (**GPL-3.0**, isolated lab only) · StoryDiffusion (Apache-2.0) ·
PuLID / InstantID (Apache-2.0 code, **InsightFace noncommercial dep — blocked**)
· dinov2 (Apache-2.0) · dreamsim (MIT) · InternVL (MIT) · GroundingDINO
(Apache-2.0) · SAM2 (Apache-2.0) · mmpose (Apache-2.0) · HPSv2 (Apache-2.0) ·
chatterbox (MIT) · F5-TTS (MIT code, **CC-BY-NC weights — blocked**) ·
Step-Audio-EditX (Apache-2.0) · fish-speech (**research license only**) ·
faster-whisper (MIT) · stable-ts (MIT) · motion-canvas (MIT) · manim (MIT) ·
PySceneDetect (BSD-3) · MMAudio (MIT code, weight suitability not guaranteed —
enabled:false) · libass (ISC). MusicGen/AudioLDM2 weights are noncommercial —
excluded from monetized defaults; use licensed/owned music.
