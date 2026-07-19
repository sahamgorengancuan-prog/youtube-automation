"""Notebook-native Control Center (ipywidgets).

``launch_control_center()`` renders a tabbed production console inside
Jupyter/Colab. Every button dispatches through the shared
:class:`NotebookRunController`, so notebook, Streamlit and headless usage all
run identical logic. When ``ipywidgets`` is unavailable the function returns
a :class:`HeadlessControlCenter` with the same controller and printed
instructions instead of crashing — safe for plain ``python -c`` imports and
CI.

Safety: rendering the Control Center performs **no network calls**. Paid
actions stay disabled until the confirmation checkbox is ticked and the
phrase ``RUN LIVE`` is typed, and they still pass the controller's gate.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .config_models import BFL_ALLOWED_MODELS
from .notebook_runner import (
    ACTION_DESCRIPTIONS,
    CONFIG_PRESETS,
    LIVE_ACTIONS,
    PAID_CONFIRMATION_PHRASE,
    CancellationToken,
    NotebookRunController,
    PipelineAction,
    PipelineRunRequest,
    RunMonitorState,
    generate_job_id,
)
from .preflight import VIDEO_SUFFIXES
from .security import redact_secrets, redacted_exception_text
from .utils import ffprobe_duration, load_json

STATUS_ICONS = {
    "PASS": "✓ PASS",
    "WARNING": "⚠ WARNING",
    "FAIL": "✕ FAIL",
    "SKIPPED": "○ SKIPPED",
    "RUNNING": "● RUNNING",
}

_CSS = """<style>
.scs-card {display:inline-block;border:1px solid #b8c1c6;border-radius:8px;
  padding:8px 14px;margin:4px;min-width:130px;font-family:sans-serif;font-size:12px;}
.scs-card b {display:block;font-size:11px;color:#475157;text-transform:uppercase;}
.scs-warn {border-left:4px solid #d8483e;background:#fff6f5;padding:6px 10px;
  margin:6px 0;font-family:sans-serif;font-size:13px;}
.scs-ok {border-left:4px solid #2e77a6;background:#f4f9fc;padding:6px 10px;
  margin:6px 0;font-family:sans-serif;font-size:13px;}
</style>"""


def _widgets_available() -> bool:
    try:
        import ipywidgets  # noqa: F401
        import IPython.display  # noqa: F401

        return True
    except ImportError:
        return False


class HeadlessControlCenter:
    """Fallback returned when ipywidgets is missing: same controller,
    programmatic access, printed guidance — never a crash."""

    def __init__(self, controller: NotebookRunController):
        self.controller = controller
        self.headless = True

    def instructions(self) -> str:
        return (
            "ipywidgets is not installed, so the visual Control Center is unavailable.\n"
            "Install it with: pip install ipywidgets\n"
            "Headless equivalent:\n"
            "  from scistudio_v10.notebook_runner import NotebookRunController, "
            "PipelineRunRequest, PipelineAction\n"
            "  controller = NotebookRunController(workspace)\n"
            "  controller.dispatch(PipelineRunRequest(action=PipelineAction.environment_check))"
        )


def launch_control_center(
    workspace: str | Path = "./scientific_motion_studio_v10",
    *,
    controller: NotebookRunController | None = None,
):
    """Render the Control Center; returns the ControlCenter object.

    Safe under ``Run all``: only widgets are constructed — no provider calls.
    """
    controller = controller or NotebookRunController(workspace)
    if not _widgets_available():
        center = HeadlessControlCenter(controller)
        print(center.instructions())
        return center
    center = ControlCenter(controller)
    center.display()
    return center


class ControlCenter:
    """The interactive widget application."""

    def __init__(self, controller: NotebookRunController):
        import ipywidgets as w

        self.w = w
        self.controller = controller
        self.state: dict[str, Any] = {
            "config": dict(CONFIG_PRESETS["Safe Development"]),
            "last_result": None,
            "preflight": None,
        }
        self.monitor = RunMonitorState()
        self.cancellation: CancellationToken | None = None
        self._run_thread: threading.Thread | None = None
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w = self.w
        self.cards = w.HTML()
        self.out_project = w.Output()
        self.out_execution = w.Output()
        self.out_secrets = w.Output()
        self.out_preflight = w.Output()
        self.out_monitor = w.Output()
        self.out_results = w.Output()
        self.out_config = w.Output()

        # -- Tab 1: Project
        self.topic = w.Textarea(
            description="Topic",
            placeholder="Scientific question, e.g. What happens if it rains nonstop for one year?",
            layout=w.Layout(width="95%", height="60px"),
        )
        self.reference = w.Text(
            description="Reference", placeholder="path/to/reference.mp4", layout=w.Layout(width="70%")
        )
        self.job_id = w.Text(
            description="Job ID", placeholder="(auto from topic + reference)", layout=w.Layout(width="70%")
        )
        self.upload = w.FileUpload(accept=",".join(VIDEO_SUFFIXES), multiple=False, description="Upload video")
        upload_button = w.Button(description="Save uploaded file", icon="save")
        use_existing = w.Button(description="Use path above", icon="check")
        drive_button = w.Button(description="Mount Google Drive", icon="cloud")
        upload_button.on_click(self._on_save_upload)
        use_existing.on_click(lambda _b: self._describe_reference())
        drive_button.on_click(self._on_mount_drive)
        tab_project = w.VBox(
            [
                self.topic,
                self.reference,
                self.job_id,
                w.HBox([self.upload, upload_button, use_existing, drive_button]),
                self.out_project,
            ]
        )

        # -- Tab 2: Execution
        self.action = w.Dropdown(
            description="Action",
            options=[(a.value.replace("_", " ").title(), a) for a in PipelineAction],
            value=PipelineAction.environment_check,
            layout=w.Layout(width="60%"),
        )
        self.action_help = w.HTML()
        self.force_scope = w.Dropdown(
            description="Force scope", options=["all", "failed_and_downstream", "selected_and_downstream"], value="all"
        )
        self.force_stage = w.Text(description="Force stage", placeholder="e.g. 13_flux_studio")
        self.primary = w.Button(
            description="Run selected action", button_style="primary", icon="play", layout=w.Layout(width="240px")
        )
        self.stop_button = w.Button(description="Stop after current safe stage", icon="stop", disabled=True)
        self.action.observe(self._on_action_change, names="value")
        self.primary.on_click(self._on_run)
        self.stop_button.on_click(self._on_stop)
        tab_execution = w.VBox(
            [
                self.action,
                self.action_help,
                w.HBox([self.force_scope, self.force_stage]),
                w.HBox([self.primary, self.stop_button]),
                self.out_execution,
            ]
        )

        # -- Tab 3: Providers & Secrets
        self.secret_inputs = {
            name: w.Password(description=name, placeholder="paste value (memory only)", layout=w.Layout(width="60%"))
            for name in ("OPENAI_API_KEY", "BFL_API_KEY", "UPLOAD_POST_TOKEN")
        }
        refresh_secrets = w.Button(description="Refresh Secret Status", icon="refresh")
        load_colab = w.Button(description="Load from Colab Secrets", icon="download")
        clear_manual = w.Button(description="Clear Manual Secrets", icon="trash")
        apply_manual = w.Button(description="Apply manual values", icon="key")
        refresh_secrets.on_click(lambda _b: self._render_secret_status())
        load_colab.on_click(lambda _b: self._render_secret_status())
        clear_manual.on_click(self._on_clear_secrets)
        apply_manual.on_click(self._on_apply_secrets)
        tab_secrets = w.VBox(
            [
                w.HTML(
                    "<div class='scs-ok'>Secrets are resolved from Colab Secrets → environment "
                    "variables → the fields below. Values stay in memory, are registered for "
                    "redaction, and are never written to configs, logs or notebook output.</div>"
                ),
                *self.secret_inputs.values(),
                w.HBox([apply_manual, refresh_secrets, load_colab, clear_manual]),
                self.out_secrets,
            ]
        )

        # -- Tab 4: Model & Generation
        self.f_openai_model = w.Text(description="OpenAI model", value="gpt-5-mini")
        self.f_openai_vision = w.Text(description="Vision model", placeholder="(defaults to OpenAI model)")
        self.f_effort = w.Dropdown(description="Effort", options=["minimal", "low", "medium", "high"], value="low")
        self.f_max_tokens = w.BoundedIntText(description="Max tokens", value=8000, min=256, max=200000)
        self.f_retries = w.BoundedIntText(description="Retries", value=4, min=1, max=10)
        self.f_retry_delay = w.BoundedFloatText(description="Retry delay", value=1.0, min=0.05, max=30.0)
        self.f_bfl_model = w.Dropdown(
            description="BFL model", options=sorted(BFL_ALLOWED_MODELS), value="flux-kontext-pro"
        )
        self.f_aspect = w.Dropdown(description="Aspect", options=["9:16", "16:9", "1:1", "4:5", "3:4"], value="9:16")
        self.f_format = w.Dropdown(description="Format", options=["png", "jpeg"], value="png")
        self.f_upsampling = w.Checkbox(description="Prompt upsampling", value=False, indent=False)
        self.f_safety = w.BoundedIntText(description="Safety tol.", value=2, min=0, max=6)
        self.f_candidates = w.BoundedIntText(description="Candidates", value=4, min=1, max=12)
        self.f_seed_stride = w.BoundedIntText(description="Seed stride", value=9973, min=1, max=10**6)
        self.f_revisions = w.BoundedIntText(description="Max revisions", value=3, min=1, max=10)
        self.f_vision_director = w.Checkbox(description="Require vision director", value=True, indent=False)
        self.f_research = w.Checkbox(description="Research search", value=True, indent=False)
        self.f_audio = w.Checkbox(description="Audio enabled", value=True, indent=False)
        self.f_audio_plan = w.Checkbox(description="Build audio in plan mode", value=False, indent=False)
        tab_model = w.VBox(
            [
                w.HTML("<b>OpenAI (reasoning + vision)</b>"),
                self.f_openai_model,
                self.f_openai_vision,
                self.f_effort,
                self.f_max_tokens,
                w.HBox([self.f_retries, self.f_retry_delay]),
                w.HTML("<b>BFL FLUX Kontext (image generation)</b>"),
                self.f_bfl_model,
                w.HBox([self.f_aspect, self.f_format]),
                w.HBox([self.f_upsampling, self.f_safety]),
                w.HTML("<b>Generation policy</b>"),
                w.HBox([self.f_candidates, self.f_seed_stride]),
                w.HBox([self.f_revisions, self.f_vision_director]),
                w.HBox([self.f_research, self.f_audio, self.f_audio_plan]),
            ]
        )

        # -- Tab 5: Temporal & Rendering
        self.f_temporal = w.Checkbox(description="Temporal enabled", value=True, indent=False)
        self.f_temporal_artic = w.Checkbox(description="Use for articulated motion", value=False, indent=False)
        self.f_backend_pref = w.Text(
            description="Backend pref",
            value="sketch-controlled-video, deterministic-compositor",
            layout=w.Layout(width="70%"),
        )
        self.f_temporal_argv = w.Text(
            description="Command argv",
            placeholder='JSON list, e.g. ["/usr/bin/model", "--in", "{start}"]',
            layout=w.Layout(width="70%"),
        )
        self.f_temporal_timeout = w.BoundedFloatText(description="Cmd timeout", value=1800.0, min=1.0, max=86400.0)
        self.f_allow_shell = w.Checkbox(description="Allow shell (unsafe)", value=False, indent=False)
        self.f_shell_override = w.Checkbox(description="Security override for unsafe shell", value=False, indent=False)
        self.f_render_backend = w.Dropdown(
            description="Render", value="pil", options=["remotion", "preview", "pil", "deterministic"]
        )
        self.f_npm_install = w.Checkbox(description="Install npm deps", value=True, indent=False)
        self.f_crf = w.BoundedIntText(description="CRF", value=18, min=0, max=51)
        self.f_render_timeout = w.BoundedIntText(description="Render timeout", value=2400, min=60, max=24000)
        self.f_publish = w.Checkbox(description="Publishing enabled", value=False, indent=False)
        self.f_publish_provider = w.Dropdown(
            description="Publisher", options=["local-archive", "upload-post"], value="local-archive"
        )
        self.f_archive_dir = w.Text(description="Archive dir", placeholder="(default: run dir/published)")
        self.f_endpoint = w.Text(description="Endpoint", placeholder="https://…")
        self.f_allowed_hosts = w.Text(description="Allowed hosts", placeholder="api.example.com, *.example.com")
        self.render_warnings = w.HTML()
        for widget in (self.f_allow_shell, self.f_publish, self.f_render_backend):
            widget.observe(lambda _c: self._render_render_warnings(), names="value")
        tab_temporal = w.VBox(
            [
                self.f_temporal,
                self.f_temporal_artic,
                self.f_backend_pref,
                self.f_temporal_argv,
                w.HBox([self.f_temporal_timeout, self.f_allow_shell, self.f_shell_override]),
                w.HTML("<b>Rendering</b>"),
                w.HBox([self.f_render_backend, self.f_npm_install]),
                w.HBox([self.f_crf, self.f_render_timeout]),
                w.HTML("<b>Publishing</b>"),
                w.HBox([self.f_publish, self.f_publish_provider]),
                self.f_archive_dir,
                self.f_endpoint,
                self.f_allowed_hosts,
                self.render_warnings,
            ]
        )

        # -- Tab 6: Advanced Configuration
        self.preset = w.Dropdown(description="Preset", options=list(CONFIG_PRESETS), value="Safe Development")
        self.mode = w.Dropdown(description="Mode", options=["development", "test", "production"], value="development")
        apply_preset = w.Button(description="Reset to preset", icon="undo")
        form_to_json = w.Button(description="Generate JSON from form", icon="arrow-down")
        json_to_form = w.Button(description="Load form from JSON", icon="arrow-up")
        validate_button = w.Button(description="Validate config", icon="check", button_style="info")
        export_button = w.Button(description="Export config JSON", icon="save")
        import_button = w.Button(description="Import config JSON file", icon="folder-open")
        diff_button = w.Button(description="Diff vs preset defaults", icon="exchange")
        self.json_editor = w.Textarea(
            layout=w.Layout(width="95%", height="220px"), placeholder="Advanced JSON config editor"
        )
        self.import_path = w.Text(description="Import path", placeholder="workspace/configs/config.json")
        apply_preset.on_click(self._on_apply_preset)
        form_to_json.on_click(lambda _b: self._form_to_json(confirm=True))
        json_to_form.on_click(lambda _b: self._json_to_form())
        validate_button.on_click(lambda _b: self._validate_config_clicked())
        export_button.on_click(self._on_export_config)
        import_button.on_click(self._on_import_config)
        diff_button.on_click(self._on_diff_config)
        tab_config = w.VBox(
            [
                w.HBox([self.preset, self.mode, apply_preset]),
                w.HBox([form_to_json, json_to_form, validate_button, diff_button]),
                self.json_editor,
                w.HBox([self.import_path, import_button, export_button]),
                self.out_config,
            ]
        )

        # -- Tab 7: Preflight
        preflight_button = w.Button(description="Run preflight", button_style="info", icon="search")
        rerun_button = w.Button(description="Rerun preflight", icon="refresh")
        install_button = w.Button(description="Install missing Python packages", icon="wrench")
        download_button = w.Button(description="Write preflight_report.json", icon="download")
        preflight_button.on_click(lambda _b: self._run_preflight_clicked())
        rerun_button.on_click(lambda _b: self._run_preflight_clicked())
        install_button.on_click(self._on_install_missing)
        download_button.on_click(self._on_download_preflight)
        tab_preflight = w.VBox(
            [w.HBox([preflight_button, rerun_button, install_button, download_button]), self.out_preflight]
        )

        # -- Tab 8: Run Monitor
        self.progress = w.FloatProgress(
            value=0.0, min=0.0, max=1.0, description="Progress", layout=w.Layout(width="70%")
        )
        self.monitor_html = w.HTML("<i>No run yet. Choose an action in the Execution tab.</i>")
        tab_monitor = w.VBox([self.progress, self.monitor_html, self.out_monitor])

        # -- Tab 9: Results
        results_refresh = w.Button(description="Refresh results", icon="refresh")
        zip_button = w.Button(description="ZIP run directory", icon="file-archive")
        compare_a = w.Text(description="Compare A", placeholder="job id")
        compare_b = w.Text(description="Compare B", placeholder="job id")
        compare_button = w.Button(description="Compare manifests", icon="exchange")
        results_refresh.on_click(lambda _b: self._render_results())
        zip_button.on_click(self._on_zip)
        compare_button.on_click(lambda _b: self._compare_manifests(compare_a.value, compare_b.value))
        tab_results = w.VBox(
            [w.HBox([results_refresh, zip_button]), w.HBox([compare_a, compare_b, compare_button]), self.out_results]
        )

        self.tabs = w.Tab(
            children=[
                tab_project,
                tab_execution,
                tab_secrets,
                tab_model,
                tab_temporal,
                tab_config,
                tab_preflight,
                tab_monitor,
                tab_results,
            ]
        )
        for index, title in enumerate(
            [
                "Project",
                "Execution",
                "Providers & Secrets",
                "Model & Generation",
                "Temporal & Rendering",
                "Advanced Config",
                "Preflight & Tests",
                "Run Monitor",
                "Results & Artifacts",
            ]
        ):
            self.tabs.set_title(index, title)

        # Paid-run gate widgets (shown above tabs, next to primary action)
        self.paid_checkbox = w.Checkbox(
            description="I understand this action may create paid API requests.",
            value=False,
            indent=False,
            layout=w.Layout(width="60%"),
        )
        self.paid_phrase = w.Text(
            description="Type", placeholder=PAID_CONFIRMATION_PHRASE, layout=w.Layout(width="40%")
        )
        self.paid_box = w.HBox([self.paid_checkbox, self.paid_phrase])
        self.paid_box.layout.display = "none"
        self.paid_checkbox.observe(lambda _c: self._update_primary_enabled(), names="value")
        self.paid_phrase.observe(lambda _c: self._update_primary_enabled(), names="value")

        self._on_action_change(None)
        self._render_cards()
        self._render_secret_status()
        self._render_render_warnings()
        self._form_to_json(confirm=False)

    def display(self) -> None:
        from IPython.display import HTML, display

        display(HTML(_CSS))
        display(self.cards)
        display(self.paid_box)
        display(self.tabs)

    # ------------------------------------------------------------- helpers
    def _card(self, title: str, value: str) -> str:
        return f"<span class='scs-card'><b>{title}</b>{value}</span>"

    def _render_cards(self) -> None:
        import shutil as _shutil

        env = "✓" if _shutil.which("ffmpeg") else "✕ ffmpeg missing"
        secrets = self.controller.secrets.status()
        configured = sum(1 for v in secrets.values() if v == "Configured")
        reference = Path(self.reference.value).name if self.reference.value else "—"
        job = self.job_id.value or (
            generate_job_id(self.topic.value, self.reference.value)
            if self.topic.value and self.reference.value
            else "—"
        )
        last = self.state.get("last_result")
        preflight = self.state.get("preflight")
        self.cards.value = _CSS + "".join(
            [
                self._card("Environment", env),
                self._card("Configuration", self.mode.value),
                self._card("Secrets", f"{configured}/3 configured"),
                self._card("Reference", reference),
                self._card("Current Job", job),
                self._card("Last Result", (last or {}).get("mode", "—") if isinstance(last, dict) else "—"),
                self._card(
                    "Preflight",
                    f"{preflight['summary']['FAIL']} fail / {preflight['summary']['WARNING']} warn"
                    if preflight
                    else "not run",
                ),
            ]
        )

    def _describe_reference(self) -> None:
        with self.out_project:
            self.out_project.clear_output()
            try:
                path = self.controller.validate_reference_video(self.reference.value)
                duration = ffprobe_duration(path)
                print(
                    f"{STATUS_ICONS['PASS']}  {path.name} | {path.suffix} | "
                    f"{path.stat().st_size / 1024:.0f} KiB | {duration:.1f}s"
                )
                try:
                    from IPython.display import Video, display

                    display(Video(str(path), embed=False, width=200))
                except Exception:
                    print("(inline preview unavailable in this frontend)")
            except Exception as exc:
                print(f"{STATUS_ICONS['FAIL']}  {redacted_exception_text(exc, 300)}")
        self._render_cards()

    def _on_save_upload(self, _button) -> None:
        with self.out_project:
            self.out_project.clear_output()
            value = self.upload.value
            items = list(value.values()) if isinstance(value, dict) else list(value)
            if not items:
                print("No file selected in the upload widget.")
                return
            item = items[0]
            name = item.get("name") if isinstance(item, dict) else getattr(item, "name", "upload.mp4")
            content = item.get("content") if isinstance(item, dict) else getattr(item, "content", b"")
            if isinstance(content, memoryview):
                content = content.tobytes()
            if isinstance(content, dict):
                content = content.get("content", b"")
            target = self.controller.paths["base"] / "uploads" / str(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(content))
            self.reference.value = str(target)
            print(f"Saved upload to {target}")
        self._describe_reference()

    def _on_mount_drive(self, _button) -> None:
        with self.out_project:
            self.out_project.clear_output()
            try:
                from google.colab import drive  # type: ignore[import-not-found]

                drive.mount("/gdrive")
                print("Google Drive mounted at /gdrive.")
            except ImportError:
                print("Google Drive mounting is only available inside Google Colab.")
            except Exception as exc:
                print(f"Drive mount failed: {redacted_exception_text(exc, 200)}")

    # -------------------------------------------------------------- config
    def _config_from_form(self) -> dict[str, Any]:
        argv: list[str] = []
        if self.f_temporal_argv.value.strip():
            try:
                parsed = json.loads(self.f_temporal_argv.value)
                if isinstance(parsed, list):
                    argv = [str(x) for x in parsed]
            except json.JSONDecodeError:
                argv = [self.f_temporal_argv.value]
        # Two-tier vision: Qwen VL (OpenRouter) primary + Gemini escalation when
        # their keys exist; otherwise fall back to OpenAI vision.
        vision_order = []
        if self.controller.secrets.available("OPENROUTER_API_KEY"):
            vision_order.append("openrouter")
        if self.controller.secrets.available("GEMINI_API_KEY"):
            vision_order.append("gemini")
        if not vision_order:
            vision_order = ["openai"]
        config: dict[str, Any] = {
            "workspace": str(self.controller.paths["base"]),
            "execution_mode": self.mode.value,
            "research_search": {"enabled": self.f_research.value},
            "llm": {
                "provider_order": ["openai"]
                if self.mode.value == "production"
                else (["openai"] if self.controller.secrets.available("OPENAI_API_KEY") else []),
                "vision_provider_order": vision_order,
                "openrouter_vision_model": "qwen/qwen-2.5-vl-72b-instruct",
                "gemini_vision_model": "gemini-2.5-flash",
                "openai_model": self.f_openai_model.value,
                "openai_vision_model": self.f_openai_vision.value,
                "openai_reasoning_effort": self.f_effort.value,
                "openai_max_output_tokens": int(self.f_max_tokens.value),
                "image_provider": "bfl",
                "bfl_model": self.f_bfl_model.value,
                "bfl_aspect_ratio": self.f_aspect.value,
                "bfl_output_format": self.f_format.value,
                "bfl_prompt_upsampling": bool(self.f_upsampling.value),
                "bfl_safety_tolerance": int(self.f_safety.value),
                "retry": {"max_attempts": int(self.f_retries.value), "base_delay_s": float(self.f_retry_delay.value)},
            },
            "candidate_tournament": {
                "candidate_count": int(self.f_candidates.value),
                "seed_stride": int(self.f_seed_stride.value),
            },
            "flux_studio": {
                "maximum_director_revisions": int(self.f_revisions.value),
                "require_vision_director": bool(self.f_vision_director.value),
            },
            "audio": {"enabled": bool(self.f_audio.value), "build_in_plan_mode": bool(self.f_audio_plan.value)},
            "temporal": {
                "enabled": bool(self.f_temporal.value),
                "use_for_articulated": bool(self.f_temporal_artic.value),
                "backend_preference": [x.strip() for x in self.f_backend_pref.value.split(",") if x.strip()],
                "sketch_backend": {
                    "argv": argv,
                    "timeout_s": float(self.f_temporal_timeout.value),
                    "allow_shell": bool(self.f_allow_shell.value),
                    "security_override_unsafe_shell": bool(self.f_shell_override.value),
                },
            },
            "render": {
                "backend": self.f_render_backend.value,
                "install_dependencies": bool(self.f_npm_install.value),
                "crf": int(self.f_crf.value),
                "render_timeout": int(self.f_render_timeout.value),
            },
            "publishing": {
                "enabled": bool(self.f_publish.value),
                "provider": self.f_publish_provider.value,
                "archive_dir": self.f_archive_dir.value,
                "endpoint": self.f_endpoint.value,
                "allowed_hosts": [x.strip() for x in self.f_allowed_hosts.value.split(",") if x.strip()],
            },
        }
        return config

    def _apply_config_to_form(self, config: dict[str, Any]) -> None:
        llm = config.get("llm") or {}
        self.mode.value = str(config.get("execution_mode", "development"))
        self.f_openai_model.value = str(llm.get("openai_model", "gpt-5-mini"))
        self.f_openai_vision.value = str(llm.get("openai_vision_model", ""))
        self.f_effort.value = str(llm.get("openai_reasoning_effort", "low"))
        self.f_max_tokens.value = int(llm.get("openai_max_output_tokens", 8000))
        retry = llm.get("retry") or {}
        self.f_retries.value = int(retry.get("max_attempts", 4))
        self.f_retry_delay.value = float(retry.get("base_delay_s", 1.0))
        if str(llm.get("bfl_model", "flux-kontext-pro")) in BFL_ALLOWED_MODELS:
            self.f_bfl_model.value = str(llm.get("bfl_model", "flux-kontext-pro"))
        aspect = str(llm.get("bfl_aspect_ratio", "9:16"))
        if aspect not in list(self.f_aspect.options):
            self.f_aspect.options = [*self.f_aspect.options, aspect]
        self.f_aspect.value = aspect
        self.f_format.value = str(llm.get("bfl_output_format", "png"))
        self.f_upsampling.value = bool(llm.get("bfl_prompt_upsampling", False))
        self.f_safety.value = int(llm.get("bfl_safety_tolerance", 2))
        tournament = config.get("candidate_tournament") or {}
        self.f_candidates.value = int(tournament.get("candidate_count", 4))
        self.f_seed_stride.value = int(tournament.get("seed_stride", 9973))
        flux = config.get("flux_studio") or {}
        self.f_revisions.value = int(flux.get("maximum_director_revisions", 3))
        self.f_vision_director.value = bool(flux.get("require_vision_director", True))
        self.f_research.value = bool((config.get("research_search") or {}).get("enabled", True))
        audio = config.get("audio") or {}
        self.f_audio.value = bool(audio.get("enabled", True))
        self.f_audio_plan.value = bool(audio.get("build_in_plan_mode", False))
        temporal = config.get("temporal") or {}
        self.f_temporal.value = bool(temporal.get("enabled", True))
        self.f_temporal_artic.value = bool(temporal.get("use_for_articulated", False))
        self.f_backend_pref.value = ", ".join(
            temporal.get("backend_preference", []) or ["sketch-controlled-video", "deterministic-compositor"]
        )
        sketch = temporal.get("sketch_backend") or {}
        self.f_temporal_argv.value = json.dumps(sketch.get("argv", [])) if sketch.get("argv") else ""
        self.f_temporal_timeout.value = float(sketch.get("timeout_s", 1800.0))
        self.f_allow_shell.value = bool(sketch.get("allow_shell", False))
        self.f_shell_override.value = bool(sketch.get("security_override_unsafe_shell", False))
        render = config.get("render") or {}
        self.f_render_backend.value = str(render.get("backend", "pil"))
        self.f_npm_install.value = bool(render.get("install_dependencies", True))
        self.f_crf.value = int(render.get("crf", 18))
        self.f_render_timeout.value = int(render.get("render_timeout", 2400))
        publishing = config.get("publishing") or {}
        self.f_publish.value = bool(publishing.get("enabled", False))
        self.f_publish_provider.value = str(publishing.get("provider", "local-archive"))
        self.f_archive_dir.value = str(publishing.get("archive_dir", ""))
        self.f_endpoint.value = str(publishing.get("endpoint", ""))
        self.f_allowed_hosts.value = ", ".join(publishing.get("allowed_hosts", []) or [])
        self._render_render_warnings()

    def _form_to_json(self, confirm: bool) -> None:
        config = self._config_from_form()
        if confirm and self.json_editor.value.strip():
            try:
                current = json.loads(self.json_editor.value)
            except json.JSONDecodeError:
                current = None
            if current is not None and current != config:
                with self.out_config:
                    self.out_config.clear_output()
                    print(
                        "JSON editor content differs from the form. Press the button again "
                        "within this session to overwrite it."
                    )
                if self.state.get("pending_overwrite") != True:  # noqa: E712
                    self.state["pending_overwrite"] = True
                    return
        self.state["pending_overwrite"] = False
        self.state["config"] = config
        self.json_editor.value = json.dumps(config, indent=2)
        self._render_cards()

    def _json_to_form(self) -> None:
        with self.out_config:
            self.out_config.clear_output()
            try:
                config = json.loads(self.json_editor.value or "{}")
                self.controller.validate_config(config)
                self._apply_config_to_form(config)
                self.state["config"] = config
                print(f"{STATUS_ICONS['PASS']}  JSON loaded into the form.")
            except Exception as exc:
                self._print_config_error(exc)
        self._render_cards()

    def _print_config_error(self, exc: Exception) -> None:
        print(f"{STATUS_ICONS['FAIL']}  {redacted_exception_text(exc, 300)}")
        for item in getattr(exc, "field_errors", []) or []:
            print(f"   • {item['field']}: {item['message']}")

    def _validate_config_clicked(self) -> None:
        with self.out_config:
            self.out_config.clear_output()
            try:
                config = json.loads(self.json_editor.value or "{}")
                settings = self.controller.validate_config(config)
                print(f"{STATUS_ICONS['PASS']}  Valid ({settings.execution_mode.value} mode).")
                print(json.dumps(redact_secrets(settings.as_runtime_dict()), indent=2)[:2500])
            except Exception as exc:
                self._print_config_error(exc)

    def _on_apply_preset(self, _button) -> None:
        preset = dict(CONFIG_PRESETS[self.preset.value])
        self._apply_config_to_form(preset)
        self.state["config"] = preset
        self.json_editor.value = json.dumps(preset, indent=2)
        with self.out_config:
            self.out_config.clear_output()
            print(f"Preset {self.preset.value!r} applied to form and JSON editor.")
        self._render_cards()

    def _on_export_config(self, _button) -> None:
        with self.out_config:
            self.out_config.clear_output()
            config = redact_secrets(json.loads(self.json_editor.value or "{}"))
            target = self.controller.paths["configs"] / "control_center_config.json"
            target.write_text(json.dumps(config, indent=2), encoding="utf-8")
            print(f"Exported (redacted) config to {target}")

    def _on_import_config(self, _button) -> None:
        with self.out_config:
            self.out_config.clear_output()
            path = Path(self.import_path.value)
            if not path.is_file():
                print(f"{STATUS_ICONS['FAIL']}  File not found: {path}")
                return
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                self.controller.validate_config(config)
                self.json_editor.value = json.dumps(config, indent=2)
                self._apply_config_to_form(config)
                self.state["config"] = config
                print(f"{STATUS_ICONS['PASS']}  Imported {path}")
            except Exception as exc:
                self._print_config_error(exc)

    def _on_diff_config(self, _button) -> None:
        with self.out_config:
            self.out_config.clear_output()
            base = CONFIG_PRESETS[self.preset.value]
            try:
                current = json.loads(self.json_editor.value or "{}")
            except json.JSONDecodeError as exc:
                print(f"{STATUS_ICONS['FAIL']}  JSON invalid: {exc}")
                return

            def walk(prefix: str, a: Any, b: Any) -> None:
                if isinstance(a, dict) or isinstance(b, dict):
                    keys = sorted(set((a or {}).keys()) | set((b or {}).keys()))
                    for key in keys:
                        walk(f"{prefix}.{key}" if prefix else str(key), (a or {}).get(key), (b or {}).get(key))
                elif a != b:
                    print(f"  {prefix}: preset={a!r} → current={b!r}")

            print(f"Differences vs preset {self.preset.value!r}:")
            walk("", base, current)

    # ------------------------------------------------------------ execution
    def _on_action_change(self, _change) -> None:
        action = self.action.value
        self.action_help.value = (
            f"<div class='{'scs-warn' if action in LIVE_ACTIONS else 'scs-ok'}'>{ACTION_DESCRIPTIONS[action]}</div>"
        )
        self.paid_box.layout.display = "" if action in LIVE_ACTIONS else "none"
        self._update_primary_enabled()

    def _update_primary_enabled(self) -> None:
        action = self.action.value
        if action in LIVE_ACTIONS:
            gate = self.paid_checkbox.value and self.paid_phrase.value.strip() == PAID_CONFIRMATION_PHRASE
            self.primary.disabled = not gate
            self.primary.tooltip = "" if gate else "Tick the paid-run checkbox and type the confirmation phrase."
        else:
            self.primary.disabled = self._run_thread is not None and self._run_thread.is_alive()
            self.primary.tooltip = ""

    def _current_request(self) -> PipelineRunRequest:
        try:
            config = json.loads(self.json_editor.value or "{}") or self._config_from_form()
        except json.JSONDecodeError:
            config = self._config_from_form()
        return PipelineRunRequest(
            action=self.action.value,
            topic=self.topic.value,
            reference_video=self.reference.value,
            config=config,
            job_id=self.job_id.value or None,
            force_scope=self.force_scope.value,
            force_stage=self.force_stage.value,
            confirm_paid_run=self.paid_checkbox.value,
            paid_confirmation_phrase=self.paid_phrase.value,
        )

    def _on_run(self, _button) -> None:
        request = self._current_request()
        with self.out_execution:
            self.out_execution.clear_output()
            if request.action in LIVE_ACTIONS:
                print("Request summary (review before the paid run):")
                print(json.dumps(self.controller.request_summary(request), indent=2))
            print(f"{STATUS_ICONS['RUNNING']}  {request.action.value} …")
        if request.action in self._non_pipeline_actions():
            self._run_sync(request)
        else:
            self._run_async(request)

    @staticmethod
    def _non_pipeline_actions() -> set[PipelineAction]:
        return {
            PipelineAction.environment_check,
            PipelineAction.config_validation,
            PipelineAction.quick_smoke,
            PipelineAction.full_offline_tests,
            PipelineAction.provider_live_smoke,
            PipelineAction.package_artifacts,
        }

    def _run_sync(self, request: PipelineRunRequest) -> None:
        with self.out_execution:
            try:
                result = self.controller.dispatch(request)
                if request.action == PipelineAction.environment_check:
                    self.state["preflight"] = result
                    self._render_preflight(result)
                print(f"{STATUS_ICONS['PASS']}  done")
                print(json.dumps(redact_secrets(result), indent=2, default=str)[:4000])
            except Exception as exc:
                print(f"{STATUS_ICONS['FAIL']}  {redacted_exception_text(exc, 400)}")
                for item in getattr(exc, "field_errors", []) or []:
                    print(f"   • {item['field']}: {item['message']}")
        self._render_cards()

    def _run_async(self, request: PipelineRunRequest) -> None:
        self.monitor = RunMonitorState()
        self.cancellation = CancellationToken()
        self.stop_button.disabled = False
        self.primary.disabled = True

        def worker() -> None:
            try:
                result = self.controller.dispatch(
                    request, progress_callback=self._progress_event, cancellation_token=self.cancellation
                )
                self.state["last_result"] = result
                with self.out_monitor:
                    print(f"{STATUS_ICONS['PASS']}  completed: mode={result.get('mode')}")
                self._render_results()
            except Exception as exc:
                with self.out_monitor:
                    print(f"{STATUS_ICONS['FAIL']}  {type(exc).__name__}: {redacted_exception_text(exc, 400)}")
                    snapshot = self.monitor.snapshot()
                    if snapshot["failed"]:
                        print(f"   failed stage: {snapshot['failed'][-1]}")
                    print("   The job is resumable: choose 'Resume Job' with the same Job ID.")
            finally:
                self.stop_button.disabled = True
                self._update_primary_enabled()
                self._render_cards()

        self._run_thread = threading.Thread(target=worker, daemon=True)
        self._run_thread.start()

    def _progress_event(self, stage_id: str, event: str, record: dict[str, Any]) -> None:
        self.monitor.callback(stage_id, event, record)
        snapshot = self.monitor.snapshot()
        self.progress.value = snapshot["progress"]
        self.monitor_html.value = (
            f"<b>Stage:</b> {snapshot['current_stage'] or '—'} ({event}) | "
            f"<b>elapsed:</b> {snapshot['elapsed_s']}s | "
            f"<b>completed:</b> {len(snapshot['completed'])} | "
            f"<b>cached:</b> {len(snapshot['cached'])} | "
            f"<b>retries:</b> {snapshot['retries']} | "
            f"<b>failed:</b> {len(snapshot['failed'])}"
        )

    def _on_stop(self, _button) -> None:
        if self.cancellation is not None:
            self.cancellation.cancel()
            with self.out_monitor:
                print("Stop requested — the run will halt after the current safe stage.")

    # ------------------------------------------------------------ preflight
    def _run_preflight_clicked(self) -> None:
        request = self._current_request()
        report = self.controller.run_preflight(request)
        self.state["preflight"] = report
        self._render_preflight(report)
        self._render_cards()

    def _render_preflight(self, report: dict[str, Any]) -> None:
        with self.out_preflight:
            self.out_preflight.clear_output()
            summary = report["summary"]
            print(
                f"PASS {summary['PASS']} | WARNING {summary['WARNING']} | "
                f"FAIL {summary['FAIL']} | SKIPPED {summary['SKIPPED']}"
            )
            for check in report["checks"]:
                icon = STATUS_ICONS.get(check["status"], check["status"])
                line = f"{icon:12s} {check['check']}: {check['detail']}"
                if check["recommendation"]:
                    line += f"  → {check['recommendation']}"
                print(line)

    def _on_install_missing(self, _button) -> None:
        import subprocess
        import sys as _sys

        with self.out_preflight:
            report = self.state.get("preflight")
            if not report:
                print("Run preflight first.")
                return
            missing = [
                c["check"].split(":", 1)[1]
                for c in report["checks"]
                if c["check"].startswith(("required_package:", "optional_package:"))
                and c["status"] in ("FAIL", "WARNING")
            ]
            if not missing:
                print("No missing Python packages.")
                return
            print("Installing:", ", ".join(missing))
            subprocess.run([_sys.executable, "-m", "pip", "install", "--quiet", *missing], check=False)
            print("Done — rerun preflight to confirm.")

    def _on_download_preflight(self, _button) -> None:
        with self.out_preflight:
            path = self.controller.paths["reports"] / "preflight_report.json"
            print(f"Preflight report path: {path} (exists={path.exists()})")

    # -------------------------------------------------------------- results
    def _render_results(self) -> None:
        with self.out_results:
            self.out_results.clear_output()
            result = self.state.get("last_result")
            job = self.job_id.value or (result or {}).get("run_dir", "")
            if not result and not job:
                print("Empty state: no run yet. Run an action first, or enter a Job ID and refresh.")
                return
            if not result and self.job_id.value:
                manifest = self.controller.job_status(self.job_id.value)
                if manifest is None:
                    print(f"No manifest for job {self.job_id.value!r}.")
                    return
                result = {
                    "run_dir": str(self.controller.paths["jobs"] / self.job_id.value),
                    "mode": manifest.get("status"),
                }
            print(f"mode: {result.get('mode')} | run_dir: {result.get('run_dir')}")
            run_dir = Path(result.get("run_dir", ""))
            manifest = load_json(run_dir / "job_manifest.json")
            if isinstance(manifest, dict):
                stages = manifest.get("stages", {})
                resumed = [s for s, r in stages.items() if r.get("status") == "completed"]
                print(
                    f"manifest status: {manifest.get('status')} | stages: {len(stages)} "
                    f"| completed/cached-eligible: {len(resumed)}"
                )
            for key in (
                "job_manifest",
                "reference_board",
                "drawing_briefs",
                "shot_states",
                "candidate_plans",
                "beauty_frames",
                "semantic_contracts",
                "animation_plans",
                "temporal_results",
                "video",
            ):
                value = result.get(key)
                if value:
                    exists = Path(value).exists()
                    print(f"  {key}: {value} ({'exists' if exists else 'missing'})")
            provenance = load_json(run_dir / "provenance_manifest.json")
            if provenance:
                print("provenance providers:", provenance.get("providers"))
            warnings = result.get("warnings") or []
            for warning in warnings:
                print(f"  ⚠ {warning}")
            video = result.get("video")
            if video and Path(video).exists():
                try:
                    from IPython.display import Video, display

                    display(Video(str(video), embed=False, width=240))
                except Exception:
                    print("(video preview unavailable in this frontend)")

    def _on_zip(self, _button) -> None:
        with self.out_results:
            try:
                job = self.job_id.value or generate_job_id(self.topic.value, self.reference.value)
                archive = self.controller.package_artifacts(job)
                print(f"{STATUS_ICONS['PASS']}  ZIP written: {archive}")
            except Exception as exc:
                print(f"{STATUS_ICONS['FAIL']}  {redacted_exception_text(exc, 300)}")

    def _compare_manifests(self, job_a: str, job_b: str) -> None:
        with self.out_results:
            self.out_results.clear_output()
            manifest_a = self.controller.job_status(job_a)
            manifest_b = self.controller.job_status(job_b)
            if not manifest_a or not manifest_b:
                print("Both job ids must have manifests.")
                return
            stages = sorted(set(manifest_a.get("stages", {})) | set(manifest_b.get("stages", {})))
            print(f"{'stage':32s} {job_a[:14]:14s} {job_b[:14]:14s}")
            for stage in stages:
                status_a = manifest_a.get("stages", {}).get(stage, {}).get("status", "—")
                status_b = manifest_b.get("stages", {}).get(stage, {}).get("status", "—")
                marker = "" if status_a == status_b else "  ← differs"
                print(f"{stage:32s} {status_a:14s} {status_b:14s}{marker}")

    # -------------------------------------------------------------- secrets
    def _render_secret_status(self) -> None:
        with self.out_secrets:
            self.out_secrets.clear_output()
            required = self.controller.required_secrets(self.action.value)
            for name, status in self.controller.secrets.status(required).items():
                icon = {
                    "Configured": STATUS_ICONS["PASS"],
                    "Missing": STATUS_ICONS["FAIL"],
                    "Not required for selected mode": STATUS_ICONS["SKIPPED"],
                }[status]
                print(f"{icon:12s} {name}: {status}")
        self._render_cards()

    def _on_apply_secrets(self, _button) -> None:
        for name, widget in self.secret_inputs.items():
            if widget.value:
                self.controller.secrets.set_manual(name, widget.value)
                widget.value = ""
        self._render_secret_status()

    def _on_clear_secrets(self, _button) -> None:
        self.controller.secrets.clear_manual()
        self._render_secret_status()

    # ------------------------------------------------------------- warnings
    def _render_render_warnings(self) -> None:
        import shutil as _shutil

        warnings = []
        if self.f_allow_shell.value:
            warnings.append(
                "allow_shell=True is an unsafe opt-in; production rejects it without the security override."
            )
        if self.f_publish.value:
            warnings.append("Publishing is enabled — the final video will leave this runtime.")
        if self.f_render_backend.value == "remotion" and not all(_shutil.which(t) for t in ("node", "npm", "npx")):
            warnings.append(
                "Remotion selected but node/npm/npx is missing — install Node.js or switch to the 'pil' backend."
            )
        if self.mode.value == "production" and self.action.value in LIVE_ACTIONS:
            warnings.append("Production live run selected: paid provider calls after confirmation.")
        self.render_warnings.value = (
            "".join(f"<div class='scs-warn'>⚠ {text}</div>" for text in warnings)
            or "<div class='scs-ok'>No configuration warnings.</div>"
        )
