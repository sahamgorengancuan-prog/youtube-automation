"""Streamlit WebUI.

Uses the same :class:`NotebookRunController` as the notebook Control Center —
one implementation of validation, preflight, the paid-run gate and execution.
Plan-only is the safe default; failures show short redacted errors.
"""

from __future__ import annotations

import json
from pathlib import Path

from .notebook_runner import (
    ACTION_DESCRIPTIONS,
    LIVE_ACTIONS,
    PAID_CONFIRMATION_PHRASE,
    NotebookRunController,
    PipelineAction,
    PipelineRunRequest,
    RunMonitorState,
)
from .preflight import VIDEO_SUFFIXES
from .security import redact_secrets, redacted_exception_text


def main() -> None:
    try:
        import streamlit as st
    except ImportError as exc:
        raise RuntimeError("Install streamlit to use the WebUI") from exc

    st.set_page_config(page_title="Scientific Motion Studio V10", layout="wide")
    st.title("Scientific Motion Studio V10")

    workspace = st.sidebar.text_input("Workspace", "./scientific_motion_studio_v10")
    controller = NotebookRunController(workspace)

    st.sidebar.subheader("Secrets")
    for name, status in controller.secrets.status().items():
        st.sidebar.write(f"{'✓' if status == 'Configured' else '✕'} {name}: {status}")

    config_source = st.sidebar.radio("Config source", ["Import JSON file", "Paste JSON"])
    config: dict = {}
    if config_source == "Import JSON file":
        uploaded = st.sidebar.file_uploader("Config JSON", type=["json"])
        if uploaded is not None:
            config = json.loads(uploaded.read().decode("utf-8"))
    else:
        pasted = st.sidebar.text_area("Config JSON", "{}")
        try:
            config = json.loads(pasted or "{}")
        except json.JSONDecodeError as exc:
            st.sidebar.error(f"Invalid JSON: {exc}")

    topic = st.text_area("Topic", "What happens if it rains nonstop for one year?")
    upload = st.file_uploader("Reference video", type=[s.lstrip(".") for s in VIDEO_SUFFIXES])
    reference_path = st.text_input("…or existing reference path")
    if upload is not None:
        target = controller.paths["base"] / "uploads" / upload.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(upload.read())
        reference_path = str(target)
        st.info(f"Uploaded to {target}")

    action = st.selectbox(
        "Action",
        [
            PipelineAction.environment_check,
            PipelineAction.config_validation,
            PipelineAction.quick_smoke,
            PipelineAction.plan_only,
            PipelineAction.live_generation,
            PipelineAction.full_production_render,
            PipelineAction.resume_job,
            PipelineAction.package_artifacts,
        ],
        format_func=lambda a: a.value.replace("_", " ").title(),
    )
    st.caption(ACTION_DESCRIPTIONS[action])
    job_id = st.text_input("Job ID (optional)")

    confirm = phrase = ""
    if action in LIVE_ACTIONS:
        st.warning("This action performs PAID provider calls.")
        confirm = st.checkbox("I understand this action may create paid API requests.")
        phrase = st.text_input(f"Type {PAID_CONFIRMATION_PHRASE!r} to confirm")

    if st.button("Run preflight"):
        request = PipelineRunRequest(
            action=action, topic=topic, reference_video=reference_path, config=config, job_id=job_id or None
        )
        report = controller.run_preflight(request)
        summary = report["summary"]
        st.write(
            f"PASS {summary['PASS']} | WARNING {summary['WARNING']} | "
            f"FAIL {summary['FAIL']} | SKIPPED {summary['SKIPPED']}"
        )
        st.dataframe(report["checks"])

    if st.button("Run action", type="primary"):
        request = PipelineRunRequest(
            action=action,
            topic=topic,
            reference_video=reference_path,
            config=config,
            job_id=job_id or None,
            confirm_paid_run=bool(confirm),
            paid_confirmation_phrase=phrase,
        )
        monitor = RunMonitorState()
        progress_bar = st.progress(0.0, text="Waiting for first stage…")

        def on_progress(stage_id: str, event: str, record: dict) -> None:
            monitor.callback(stage_id, event, record)
            snapshot = monitor.snapshot()
            progress_bar.progress(min(1.0, snapshot["progress"]), text=f"{stage_id} ({event})")

        try:
            result = (
                controller.dispatch(request, progress_callback=on_progress)
                if action
                in {
                    PipelineAction.plan_only,
                    PipelineAction.live_generation,
                    PipelineAction.full_production_render,
                    PipelineAction.resume_job,
                }
                else controller.dispatch(request)
            )
            st.success("Completed")
            with st.expander("Result (redacted)"):
                st.json(redact_secrets(result))
            video = result.get("video") if isinstance(result, dict) else ""
            if video and Path(video).exists():
                st.video(video)
        except Exception as exc:
            st.error(f"[{type(exc).__name__}] {redacted_exception_text(exc, 400)}")
            for item in getattr(exc, "field_errors", []) or []:
                st.write(f"• {item['field']}: {item['message']}")


if __name__ == "__main__":
    main()
