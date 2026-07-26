"""TTS orchestration: ONE full narration track by default (per-act fallback);
independent per-scene TTS is forbidden unless explicitly configured. Post-TTS
integrity checks are hard gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..exceptions import ConfigurationError
from ..schemas import SpokenScript
from .silence import assert_not_silent


def synthesize_narration(
    script: SpokenScript,
    adapter: Any,
    out_path: str | Path,
    model: str = "gpt-4o-mini-tts",
    voice: str = "cedar",
    per_scene: bool = False,
    silent_threshold_dbfs: float = -55.0,
) -> dict[str, Any]:
    """Synthesize the full narration as one track and validate it. Returns
    {path, stats}. Per-scene mode raises unless explicitly enabled in config —
    and even then the caller must pass per_scene=True deliberately."""
    if per_scene:
        raise ConfigurationError(
            "per-scene TTS is forbidden by default (one full narration track); "
            "set audio.generate_per_scene=true only for a special production reason",
            stage="tts",
        )
    path = adapter.tts(script.full_text, out_path, model=model, voice=voice)
    stats = assert_not_silent(path, silent_threshold_dbfs)
    return {"path": str(path), "stats": stats}
