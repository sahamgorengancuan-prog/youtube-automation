# License matrix (Diamond stack)

Method: LICENSE files fetched from raw.githubusercontent on 2026-07-27 (see
repo_sources.lock.json for pins). **Code license ≠ weight license** — weights
are tracked separately in `model_weights.lock.json`; anything not verifiably
commercial-safe stays `default_enabled: false` and is excluded from routing by
the registry (license gate is enforced in code, `diamond/registry.py`).

| Repo | Code license (verified) | Weight status | Default |
|---|---|---|---|
| langchain-ai/langgraph | MIT | n/a | optional orchestration adapter |
| pydantic/pydantic-ai | MIT | n/a | schemas already enforced via pydantic |
| PrefectHQ/prefect | Apache-2.0 | n/a | batch-scale only, never required in Colab |
| QwenLM/Qwen-Image | Apache-2.0 | REVIEW (verify revision) | off |
| stepfun-ai/Step1X-Edit | Apache-2.0 | REVIEW | off |
| VectorSpaceLab/OmniGen2 | Apache-2.0 | REVIEW | off (experimental) |
| Comfy-Org/ComfyUI | **GPL-3.0** | n/a | off — isolated lab only; workflows get translated to pinned adapters |
| HVision-NKU/StoryDiffusion | Apache-2.0 | SDXL ecosystem REVIEW | off (experimental; video part unused) |
| ToTheBeginning/PuLID | Apache-2.0 | InsightFace dep NONCOMMERCIAL | **blocked** for production |
| instantX-research/InstantID | Apache-2.0 | InsightFace dep NONCOMMERCIAL | **blocked** for production |
| facebookresearch/dinov2 | Apache-2.0 (license at repo) | Apache-2.0 | off until installed |
| ssundaram21/dreamsim | MIT | REVIEW | off |
| QwenLM/Qwen3-VL | Apache-2.0 | hosted via OpenRouter (primary QC) | **on (hosted)** |
| OpenGVLab/InternVL | MIT | REVIEW per size | off (second opinion) |
| IDEA-Research/GroundingDINO | Apache-2.0 | Apache-2.0 | off until installed |
| facebookresearch/sam2 | Apache-2.0 | Apache-2.0 | off until installed |
| open-mmlab/mmpose | Apache-2.0 | Apache-2.0 | off until installed |
| tgxs002/HPSv2 | Apache-2.0 | REVIEW | off (tie-break only) |
| resemble-ai/chatterbox | MIT | REVIEW | off (fallback TTS) |
| SWivid/F5-TTS | MIT | **CC-BY-NC-4.0** | **noncommercial — blocked** |
| stepfun-ai/Step-Audio-EditX | Apache-2.0 | REVIEW | off |
| fishaudio/fish-speech | **Research license** | research | experimental_research_only only |
| SYSTRAN/faster-whisper | MIT | REVIEW per CT2 model | off (CPU fallback) |
| jianfch/stable-ts | MIT | n/a | off |
| motion-canvas/motion-canvas | MIT | n/a | off (needs Node) |
| ManimCommunity/manim | MIT | n/a | off |
| Breakthrough/PySceneDetect | BSD-3-Clause | n/a | off |
| SonyResearch/MMAudio | MIT | suitability NOT guaranteed | enabled:false, production_allowed:false |
| libass/libass, FFmpeg | ISC / LGPL-GPL | n/a | **primary subtitle/render path** |
