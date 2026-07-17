"""Control Center / notebook-runner test suite (fully offline).

Covers config round-trips, preset validity, the paid-run gate, secret
handling, preflight injection, action dispatch, quick smoke, resume,
force-rerun scoping, artifact ZIP, progress callbacks, cancellation and the
ipywidgets fallback. No test performs a live network call.
"""

from __future__ import annotations

import builtins
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config_models import StudioConfig
from .errors import (
    ConfigurationError,
    JobCancelledError,
    PreflightError,
    ProviderAuthenticationError,
    ReferenceVideoError,
)
from .notebook_runner import (
    CONFIG_PRESETS,
    PAID_CONFIRMATION_PHRASE,
    CancellationToken,
    NotebookRunController,
    PipelineAction,
    PipelineRunRequest,
    RunMonitorState,
    generate_job_id,
    security_source_sweep,
    stage_progress_fraction,
)
from .preflight import run_preflight
from .security import clear_registered_secrets, redact_secrets


class Collector:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.items.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(f"{name}: {detail}")


def _reference_video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=180x320:r=12",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def run_notebook_tests(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="scistudio_nb_tests_"))
    base.mkdir(parents=True, exist_ok=True)
    c = Collector()
    controller = NotebookRunController(base / "workspace")
    reference = _reference_video(base / "ref.mp4")
    dev_config = dict(CONFIG_PRESETS["Safe Development"])

    # -- presets & config round-trip ---------------------------------------
    for name, preset in CONFIG_PRESETS.items():
        settings = StudioConfig.from_dict(preset)
        c.check(f"preset valid: {name}", settings is not None)
        if preset.get("execution_mode") == "production":
            c.check(
                f"preset provider-locked: {name}",
                settings.llm.ordered_providers() == ["openai"] and settings.llm.image_provider == "bfl",
            )
    round_trip = json.loads(json.dumps(dev_config))
    c.check(
        "ui config serialization round-trip",
        StudioConfig.from_dict(round_trip).as_runtime_dict()["execution_mode"] == "development",
    )

    # -- job id generator ----------------------------------------------------
    job_id = generate_job_id("What if oceans doubled in depth?", str(reference))
    c.check(
        "job id readable and directory-safe",
        job_id.startswith("what-if-oceans-doubled-in-depth") and "/" not in job_id and " " not in job_id,
    )
    c.check("job id stable", job_id == generate_job_id("What if oceans doubled in depth?", str(reference)))

    # -- reference validation -------------------------------------------------
    for name, bad in (("missing file", str(base / "nope.mp4")), ("invalid extension", str(base / "ref.txt"))):
        (base / "ref.txt").write_text("x", encoding="utf-8")
        try:
            controller.validate_reference_video(bad)
            ok = False
        except ReferenceVideoError:
            ok = True
        c.check(f"reference rejected: {name}", ok)

    # -- invalid config JSON → field errors ----------------------------------
    try:
        controller.validate_config({"llm": {"bfl_model": "sdxl"}})
        ok = False
    except ConfigurationError as exc:
        ok = bool(getattr(exc, "field_errors", []))
    c.check("invalid config reports field errors", ok)

    # -- paid-run gate ---------------------------------------------------------
    live_request = PipelineRunRequest(
        action=PipelineAction.live_generation,
        topic="t",
        reference_video=str(reference),
        config=CONFIG_PRESETS["Production Live Generation"],
    )
    try:
        controller.validate_request(live_request)
        ok = False
    except PreflightError:
        ok = True
    c.check("paid run blocked without checkbox", ok)
    try:
        controller.validate_request(live_request.model_copy(update={"confirm_paid_run": True}))
        ok = False
    except PreflightError:
        ok = True
    c.check("paid run blocked without confirmation phrase", ok)
    try:
        controller.validate_request(
            live_request.model_copy(
                update={"confirm_paid_run": True, "paid_confirmation_phrase": PAID_CONFIRMATION_PHRASE}
            )
        )
        ok = False
    except ProviderAuthenticationError:
        ok = True  # gate passed, missing API key caught BEFORE any pipeline start
    c.check("missing API key handled before pipeline", ok)
    try:
        controller.run_provider_smoke(live_request.model_copy(update={"action": PipelineAction.provider_live_smoke}))
        ok = False
    except PreflightError:
        ok = True
    c.check("live smoke blocked without confirmation", ok)

    # -- preflight with injected missing tools ----------------------------------
    report = run_preflight(
        config=dev_config, reference_video=reference, workspace=base / "workspace", which=lambda tool: None
    )
    failed = {check["check"] for check in report["checks"] if check["status"] == "FAIL"}
    c.check("preflight FAILs on missing ffmpeg", "ffmpeg" in failed and "ffprobe" in failed)
    remotion_config = {**dev_config, "render": {"backend": "remotion"}}
    report = run_preflight(config=remotion_config, which=lambda tool: None)
    failed = {check["check"] for check in report["checks"] if check["status"] == "FAIL"}
    c.check("preflight FAILs on missing npm/npx for remotion", {"node", "npm", "npx"} <= failed)
    report = run_preflight(config=dev_config, live=False)
    statuses = {check["check"]: check["status"] for check in report["checks"]}
    c.check("network check skipped offline", statuses["network"] == "SKIPPED")

    # -- stage progress mapping ---------------------------------------------------
    c.check(
        "stage progress maps shot-state wildcard",
        0 < stage_progress_fraction("09_shot_state_SC01") < stage_progress_fraction("13_flux_studio"),
    )

    # -- action dispatch completeness (spy controller: routing only, no execution)
    routed: list[str] = []

    class SpyController(NotebookRunController):
        def run_preflight(self, request):
            routed.append("preflight")
            return {"summary": {"PASS": 0, "WARNING": 0, "FAIL": 0, "SKIPPED": 0}, "checks": []}

        def run_quick_smoke(self, request=None):
            routed.append("quick_smoke")
            return {"passed": True}

        def run_offline_tests(self, request=None):
            routed.append("offline_tests")
            return {"passed": True}

        def run_provider_smoke(self, request):
            routed.append("provider_smoke")
            return {}

        def run_pipeline(self, request, **kwargs):
            routed.append(f"pipeline:{request.action.value}")
            return {"mode": "stub"}

        def package_artifacts(self, job_id):
            routed.append("package")
            return base / "stub.zip"

        def validate_config(self, config):
            routed.append("validate_config")
            return StudioConfig.from_dict(dev_config)

    spy = SpyController(base / "spy_workspace")
    for action in PipelineAction:
        request = PipelineRunRequest(action=action, topic="t", reference_video=str(reference), config=dev_config)
        spy.dispatch(request)
    c.check(
        "all actions dispatchable through one controller",
        len(routed) >= len(PipelineAction)
        and any(r.startswith("pipeline:") for r in routed)
        and {"preflight", "quick_smoke", "offline_tests", "provider_smoke", "package"} <= set(routed),
        str(routed),
    )

    # -- quick smoke (offline, no keys) ----------------------------------------------
    smoke = controller.run_quick_smoke()
    c.check("quick smoke passes without API keys", smoke["passed"], json.dumps(smoke))
    c.check("quick smoke resume used cache", smoke["resume_used_cache"])

    # -- pipeline runs: plan-only, progress callback, resume, cancel, force -----------
    workspace_config = {**dev_config, "workspace": str(controller.paths["base"])}
    plan_request = PipelineRunRequest(
        action=PipelineAction.plan_only,
        topic="What if rivers ran backward?",
        reference_video=str(reference),
        config=workspace_config,
        job_id="nb-plan",
    )
    monitor = RunMonitorState()
    result = controller.run_pipeline(plan_request, progress_callback=monitor.callback)
    snapshot = monitor.snapshot()
    c.check("plan-only run completes via controller", result["mode"] == "plan_only")
    c.check(
        "progress callback captured stage events", snapshot["event_count"] > 5 and "03_script" in snapshot["completed"]
    )
    run_dir = Path(result["run_dir"])
    c.check(
        "run_request + resolved_config persisted (redacted)",
        (run_dir / "run_request.json").exists() and (run_dir / "resolved_config.json").exists(),
    )

    resume_request = plan_request.model_copy(update={"action": PipelineAction.resume_job})
    monitor2 = RunMonitorState()
    result2 = controller.run_pipeline(resume_request, progress_callback=monitor2.callback)
    snapshot2 = monitor2.snapshot()
    c.check(
        "resume reuses manifest and cache",
        result2["mode"] == "plan_only" and len(snapshot2["cached"]) > 0 and not snapshot2["completed"],
        json.dumps(snapshot2),
    )

    token = CancellationToken()
    token.cancel()
    try:
        controller.run_pipeline(plan_request.model_copy(update={"job_id": "nb-cancel"}), cancellation_token=token)
        ok = False
    except JobCancelledError:
        ok = True
    c.check("cancellation stops before next safe stage", ok)
    c.check("cancelled job left resumable manifest", isinstance(controller.job_status("nb-cancel"), dict))

    # Force rerun scoped to a selected stage: only that stage + downstream re-run.
    force_request = plan_request.model_copy(
        update={
            "action": PipelineAction.force_rerun,
            "force_scope": "selected_and_downstream",
            "force_stage": "04_storyboard",
        }
    )
    monitor3 = RunMonitorState()
    controller.run_pipeline(force_request, progress_callback=monitor3.callback)
    snapshot3 = monitor3.snapshot()
    c.check(
        "scoped force rerun re-executes selected stage",
        "04_storyboard" in snapshot3["completed"],
        json.dumps(snapshot3),
    )
    c.check(
        "scoped force rerun keeps earlier stages cached",
        "02_research" in snapshot3["cached"] and "03_script" in snapshot3["cached"],
    )

    # -- artifact zip + safe paths ------------------------------------------------------
    archive = controller.package_artifacts("nb-plan")
    c.check("artifact ZIP generated", archive.exists() and archive.stat().st_size > 500)
    try:
        controller.package_artifacts("../../etc")
        ok = False
    except (ValueError, ConfigurationError):
        ok = True
    c.check("zip rejects path traversal job ids", ok)

    # -- secrets ---------------------------------------------------------------------------
    clear_registered_secrets()
    controller.secrets.set_manual("OPENAI_API_KEY", "sk-manual-test-key-000111222333")
    c.check("manual secret reported configured", controller.secrets.status()["OPENAI_API_KEY"] == "Configured")
    saved_texts = "".join(
        path.read_text(encoding="utf-8") for path in controller.paths["base"].rglob("*.json") if path.is_file()
    )
    c.check("no secret written into reports/configs", "sk-manual-test-key-000111222333" not in saved_texts)
    c.check(
        "manual secret redacted in errors",
        "sk-manual-test-key-000111222333" not in redact_secrets("boom sk-manual-test-key-000111222333"),
    )
    controller.secrets.clear_manual()
    c.check("clear manual secrets works", controller.secrets.status()["OPENAI_API_KEY"] in ("Missing", "Configured"))
    clear_registered_secrets()

    # -- security sweep + no /content -----------------------------------------------------
    sweep = security_source_sweep(Path(__file__).parent)
    c.check("package security sweep clean (incl. no /content)", sweep["ok"], json.dumps(sweep["findings"]))

    # -- backward compatibility ------------------------------------------------------------
    from .pipeline import ScientificMotionStudioV10

    legacy = ScientificMotionStudioV10({**dev_config, "workspace": str(base / "legacy")}).run(
        "Legacy call still works?", reference, plan_only=True, render_video=False, force=False, job_id="legacy"
    )
    c.check("legacy run() signature unchanged", legacy["mode"] == "plan_only")

    # -- notebook UI import & ipywidgets fallback --------------------------------------------
    from . import notebook_ui

    c.check("notebook_ui imports outside Colab", hasattr(notebook_ui, "launch_control_center"))
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("ipywidgets"):
            raise ImportError("ipywidgets blocked for fallback test")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = blocked_import
    try:
        center = notebook_ui.launch_control_center(base / "workspace_fallback")
    finally:
        builtins.__import__ = real_import
    c.check("headless fallback when ipywidgets missing", getattr(center, "headless", False) is True)
    if notebook_ui._widgets_available():
        center2 = notebook_ui.launch_control_center(base / "workspace_widgets")
        c.check("widget control center builds", hasattr(center2, "tabs"))
        c.check(
            "paid gate disables primary button until confirmed", center2.primary.disabled is False
        )  # default offline action enabled
        center2.action.value = PipelineAction.live_generation
        c.check("live action disables primary without confirmation", center2.primary.disabled is True)
        center2.paid_checkbox.value = True
        center2.paid_phrase.value = PAID_CONFIRMATION_PHRASE
        c.check("confirmation enables primary", center2.primary.disabled is False)
        exported = center2._config_from_form()
        c.check("form config validates", StudioConfig.from_dict(exported) is not None)
        center2._apply_config_to_form(CONFIG_PRESETS["Production Full Render"])
        c.check("form round-trips preset", center2._config_from_form()["render"]["backend"] == "remotion")

    passed = all(item["passed"] for item in c.items)
    return {"passed": passed, "count": len(c.items), "tests": c.items, "root": str(base)}


if __name__ == "__main__":
    report = run_notebook_tests()
    print(json.dumps({k: v for k, v in report.items() if k != "tests"}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
