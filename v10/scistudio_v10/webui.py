"""Streamlit WebUI.

Plan-only mode is the safe default. Config files and reference-video paths
are validated before the pipeline starts, results are redacted before
display, and failures show a short structured error — never a raw traceback.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config_models import StudioConfig
from .security import redact_secrets, redacted_exception_text

_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


def _load_config(config_path: str) -> StudioConfig:
    path = Path(config_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return StudioConfig.from_dict(raw)


def main() -> None:
    try:
        import streamlit as st
    except ImportError as exc:
        raise RuntimeError("Install streamlit to use the WebUI") from exc
    from .pipeline import ScientificMotionStudioV10

    st.set_page_config(page_title="Scientific Motion Studio V10", layout="wide")
    st.title("Scientific Motion Studio V10")
    config_path = st.text_input("Config JSON", "config.json")
    topic = st.text_area("Topic", "What happens if it rains nonstop for one year?")
    reference_video = st.text_input("Reference MP4 path")
    plan_only = st.checkbox("Plan only (no paid image generation)", value=True)
    render_video = st.checkbox("Render video", value=False)
    job_id = st.text_input("Job ID (optional)")

    if st.button("Run"):
        try:
            settings = _load_config(config_path)
        except Exception as exc:
            st.error(f"Configuration error: {redacted_exception_text(exc, 300)}")
            return
        reference = Path(reference_video)
        if not reference.exists() or not reference.is_file():
            st.error("Reference video path does not exist or is not a file.")
            return
        if reference.suffix.lower() not in _VIDEO_SUFFIXES:
            st.error(f"Reference video must be one of {sorted(_VIDEO_SUFFIXES)}.")
            return
        progress = st.status("Running pipeline...", expanded=True)
        try:
            studio = ScientificMotionStudioV10(settings)
            result = studio.run(
                topic,
                str(reference),
                plan_only=plan_only,
                render_video=render_video,
                job_id=job_id or None,
            )
            progress.update(label="Completed", state="complete")
            st.success(f"Completed in mode: {result.get('mode', 'unknown')}")
            with st.expander("Result manifest (redacted)"):
                st.json(redact_secrets(result))
            if result.get("video") and Path(result["video"]).exists():
                st.video(result["video"])
        except Exception as exc:
            progress.update(label="Failed", state="error")
            st.error(f"Pipeline failed [{type(exc).__name__}]: {redacted_exception_text(exc, 400)}")


if __name__ == "__main__":
    main()
