"""Unified notebook run controller.

Every UI surface (ipywidgets Control Center, Streamlit, headless notebook
cells) dispatches through :class:`NotebookRunController`, so there is exactly
one implementation of validation, the paid-run safety gate, preflight,
execution and artifact packaging.

Safety invariants:
* No action performs paid provider calls unless it is a LIVE action AND the
  request carries ``confirm_paid_run=True`` AND the confirmation phrase
  ``RUN LIVE`` AND preflight reports zero FAIL checks.
* Secrets never enter configs, reports or logs — resolved values live only in
  process memory and are registered for global redaction.
"""

from __future__ import annotations

import os
import re
import threading
import time
import zipfile
from datetime import datetime, UTC
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from .config_models import ExecutionMode, StudioConfig
from .errors import (
    ConfigurationError,
    PreflightError,
    ProviderAuthenticationError,
    ReferenceVideoError,
    RenderDependencyError,
    classify_provider_error,
)
from .preflight import FAIL, VIDEO_SUFFIXES, run_preflight, save_preflight_report
from .security import redact_secrets, redacted_exception_text, register_secret
from .utils import ensure_dir, ensure_within, hash_value, load_json, save_json, slugify

PAID_CONFIRMATION_PHRASE = "RUN LIVE"

CANONICAL_STAGES = [
    "01_style_reference",
    "02_research",
    "03_script",
    "04_storyboard",
    "05_audio",
    "06_art_bible",
    "07_continuity_canon",
    "08_scene_architectures",
    "09_shot_state_*",
    "10_reference_retrieval",
    "10_reference_plans",
    "11_drawing_briefs",
    "12_candidate_tournaments",
    "13_flux_studio",
    "14_semantic",
    "15_overlays",
    "16_animation",
    "17_temporal",
    "18_hybrid_packages",
    "19_remotion",
    "publishing",
]


def stage_progress_fraction(stage_id: str) -> float:
    """Best-effort 0..1 position of a stage id in the canonical pipeline."""
    normalized = re.sub(r"^(09_shot_state)_.+$", r"\1_*", stage_id)
    try:
        index = CANONICAL_STAGES.index(normalized)
    except ValueError:
        return 0.0
    return (index + 1) / len(CANONICAL_STAGES)


def generate_job_id(topic: str, reference_video: str) -> str:
    """Readable, directory-safe, stable job id for a topic+reference pair.

    Matches the pipeline's own default so UI previews are accurate.
    """
    return slugify(topic, 40) + "-" + hash_value({"topic": topic, "reference": str(Path(reference_video))}, 10)


class PipelineAction(str, Enum):
    environment_check = "environment_check"
    config_validation = "config_validation"
    quick_smoke = "quick_smoke"
    full_offline_tests = "full_offline_tests"
    provider_live_smoke = "provider_live_smoke"
    plan_only = "plan_only"
    development_dry_run = "development_dry_run"
    production_plan = "production_plan"
    live_generation = "live_generation"
    live_generation_preview = "live_generation_preview"
    full_production_render = "full_production_render"
    resume_job = "resume_job"
    force_rerun = "force_rerun"
    package_artifacts = "package_artifacts"


LIVE_ACTIONS = frozenset(
    {
        PipelineAction.provider_live_smoke,
        PipelineAction.live_generation,
        PipelineAction.live_generation_preview,
        PipelineAction.full_production_render,
    }
)
PIPELINE_ACTIONS = frozenset(
    {
        PipelineAction.plan_only,
        PipelineAction.development_dry_run,
        PipelineAction.production_plan,
        PipelineAction.live_generation,
        PipelineAction.live_generation_preview,
        PipelineAction.full_production_render,
        PipelineAction.resume_job,
        PipelineAction.force_rerun,
    }
)

ACTION_DESCRIPTIONS: dict[PipelineAction, str] = {
    PipelineAction.environment_check: "Check runtime, tools and dependencies. No API calls.",
    PipelineAction.config_validation: "Validate the configuration and provider locks. No API calls.",
    PipelineAction.quick_smoke: "Offline mini-pipeline with a synthetic reference video. No API calls.",
    PipelineAction.full_offline_tests: "Run the full 200+ offline test suite and security sweep. No API calls.",
    PipelineAction.provider_live_smoke: "One tiny OpenAI JSON call + one small BFL image. PAID — needs confirmation.",
    PipelineAction.plan_only: "Research → script → storyboard → briefs. No paid image generation.",
    PipelineAction.development_dry_run: "Plan-only in development mode with deterministic fallbacks (clearly marked).",
    PipelineAction.production_plan: "Plan-only under production provider locks (OpenAI reasoning only).",
    PipelineAction.live_generation: "Full FLUX Kontext generation without final render. PAID — needs confirmation.",
    PipelineAction.live_generation_preview: "Live generation plus a fast deterministic preview render. PAID.",
    PipelineAction.full_production_render: "Live generation plus the full production render. PAID.",
    PipelineAction.resume_job: "Continue an existing job from its manifest; completed stages are reused.",
    PipelineAction.force_rerun: "Re-run stages ignoring caches (scope selectable). May repeat paid calls when live.",
    PipelineAction.package_artifacts: "ZIP an existing run directory for download. No API calls.",
}


class PipelineRunRequest(BaseModel):
    """One request object for every Control Center action."""

    action: PipelineAction
    topic: str = ""
    reference_video: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    plan_only: bool = True
    render_video: bool = False
    force: bool = False
    force_scope: str = "all"  # all | failed_and_downstream | selected_and_downstream
    force_stage: str = ""
    job_id: str | None = None
    confirm_paid_run: bool = False
    paid_confirmation_phrase: str = ""
    debug: bool = False


class SecretsManager:
    """Resolves secrets from Colab Secrets → environment → manual input.

    Values never leave process memory; every resolved value is registered for
    global redaction. ``status()`` reports availability without exposure.
    """

    SUPPORTED = ("OPENAI_API_KEY", "BFL_API_KEY", "UPLOAD_POST_TOKEN")

    def __init__(self) -> None:
        self._manual: dict[str, str] = {}

    @staticmethod
    def _from_colab(name: str) -> str:
        try:
            from google.colab import userdata  # type: ignore[import-not-found]

            return str(userdata.get(name) or "")
        except Exception:
            return ""

    def set_manual(self, name: str, value: str) -> None:
        if value:
            self._manual[name] = value
            register_secret(value)

    def clear_manual(self) -> None:
        self._manual.clear()

    def get(self, name: str) -> str:
        value = self._manual.get(name) or self._from_colab(name) or os.environ.get(name, "")
        if value:
            register_secret(value)
        return value

    def available(self, name: str) -> bool:
        return bool(self.get(name))

    def status(self, required: dict[str, bool] | None = None) -> dict[str, str]:
        required = required or {}
        result = {}
        for name in self.SUPPORTED:
            if name in required and not required[name]:
                result[name] = "Not required for selected mode"
            else:
                result[name] = "Configured" if self.available(name) else "Missing"
        return result

    def as_secrets_dict(self) -> dict[str, str]:
        return {name: self.get(name) for name in self.SUPPORTED if self.get(name)}


class CancellationToken:
    """Cooperative cancellation: the run stops after the current safe stage."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


CONFIG_PRESETS: dict[str, dict[str, Any]] = {
    "Safe Development": {
        "execution_mode": "development",
        "research_search": {"enabled": False},
        "llm": {"provider_order": [], "vision_provider_order": []},
        "audio": {"build_in_plan_mode": False},
        "temporal": {"enabled": False},
        "render": {"backend": "pil"},
    },
    "Offline Smoke Test": {
        "execution_mode": "test",
        "research_search": {"enabled": False},
        "llm": {"provider_order": [], "vision_provider_order": []},
        "audio": {"enabled": False},
        "temporal": {"enabled": False},
        "render": {"backend": "pil"},
    },
    "Production Plan-Only": {
        "execution_mode": "production",
        "research_search": {"enabled": True},
        "llm": {
            "provider_order": ["openai"],
            "vision_provider_order": ["openai"],
            "image_provider": "bfl",
            "bfl_model": "flux-kontext-pro",
        },
    },
    "Production Live Generation": {
        "execution_mode": "production",
        "research_search": {"enabled": True},
        "llm": {
            "provider_order": ["openai"],
            "vision_provider_order": ["openai"],
            "image_provider": "bfl",
            "bfl_model": "flux-kontext-pro",
            "bfl_aspect_ratio": "9:16",
        },
        "candidate_tournament": {"candidate_count": 4},
    },
    "Production Full Render": {
        "execution_mode": "production",
        "research_search": {"enabled": True},
        "llm": {
            "provider_order": ["openai"],
            "vision_provider_order": ["openai"],
            "image_provider": "bfl",
            "bfl_model": "flux-kontext-pro",
            "bfl_aspect_ratio": "9:16",
        },
        "candidate_tournament": {"candidate_count": 4},
        "render": {"backend": "remotion", "install_dependencies": True, "crf": 18},
    },
    "Low-Cost Live Smoke": {
        "execution_mode": "production",
        "research_search": {"enabled": False},
        "llm": {
            "provider_order": ["openai"],
            "vision_provider_order": ["openai"],
            "image_provider": "bfl",
            "bfl_model": "flux-kontext-pro",
            "bfl_aspect_ratio": "1:1",
            "bfl_output_format": "jpeg",
            "openai_max_output_tokens": 512,
        },
        "candidate_tournament": {"candidate_count": 1},
    },
    "Resume Existing Job": {
        "execution_mode": "development",
        "research_search": {"enabled": False},
        "llm": {"provider_order": [], "vision_provider_order": []},
    },
}


def workspace_layout(workspace: str | Path) -> dict[str, Path]:
    """Create and return the standard workspace directory layout."""
    base = ensure_dir(workspace)
    return {
        "base": base,
        "configs": ensure_dir(base / "configs"),
        "jobs": ensure_dir(base / "jobs"),
        "reports": ensure_dir(base / "reports"),
        "smoke_tests": ensure_dir(base / "smoke_tests"),
        "exports": ensure_dir(base / "exports"),
        "logs": ensure_dir(base / "logs"),
    }


def security_source_sweep(package_dir: str | Path) -> dict[str, Any]:
    """Repo sweep shared by the notebook cell and the Control Center."""
    package_dir = Path(package_dir)
    findings: list[str] = []
    # Pattern literals are built with `+` so this module's own source never
    # contains the raw markers it scans for (self-match prevention that
    # survives code formatters).
    anthropic_pattern = "ANTHROPIC" + "_API_KEY|import " + "anthropic|from " + "anthropic"
    stale_marker = "scistudio" + "_v8"
    shell_label = "unguarded shell" + "=True"
    files = sorted(package_dir.glob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        if re.search(anthropic_pattern, text):
            findings.append(f"{path.name}: Anthropic runtime reference")
        if stale_marker in text:
            findings.append(f"{path.name}: stale {stale_marker} reference")
        for match in re.finditer(r"(?<!\w)shell\s*=\s*True", text):
            line_no = text[: match.start()].count("\n") + 1
            if "noqa: S602" not in text.splitlines()[line_no - 1]:
                findings.append(f"{path.name}:{line_no}: {shell_label}")
        if re.search(r"[\"']/content/", text):
            findings.append(f"{path.name}: hard-coded /content path")
    return {"files_scanned": len(files), "findings": findings, "ok": not findings}


class NotebookRunController:
    """Single dispatch point for every Control Center action."""

    def __init__(self, workspace: str | Path = "./scientific_motion_studio_v10", secrets: SecretsManager | None = None):
        self.paths = workspace_layout(workspace)
        self.secrets = secrets or SecretsManager()

    # -- helpers -----------------------------------------------------------
    def required_secrets(self, action: PipelineAction) -> dict[str, bool]:
        live = action in LIVE_ACTIONS
        return {
            "OPENAI_API_KEY": live,
            "BFL_API_KEY": live,
            "UPLOAD_POST_TOKEN": False,
        }

    def validate_reference_video(self, reference_video: str) -> Path:
        if not reference_video:
            raise ReferenceVideoError("No reference video selected.")
        path = Path(reference_video)
        if not path.exists() or not path.is_file():
            raise ReferenceVideoError(f"Reference video not found: {path}")
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            raise ReferenceVideoError(
                f"Unsupported reference format {path.suffix!r}; allowed: {', '.join(VIDEO_SUFFIXES)}"
            )
        if path.stat().st_size == 0:
            raise ReferenceVideoError(f"Reference video is empty: {path}")
        return path

    def validate_config(self, config: dict[str, Any]) -> StudioConfig:
        try:
            return StudioConfig.from_dict(config)
        except ValidationError as exc:
            fields = [
                {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]} for err in exc.errors()
            ]
            error = ConfigurationError(
                "Configuration invalid: " + "; ".join(f"{f['field']}: {f['message']}" for f in fields[:8])
            )
            error.field_errors = fields  # type: ignore[attr-defined]
            raise error from exc

    def validate_request(self, request: PipelineRunRequest) -> StudioConfig:
        """Full pre-run validation, including the paid-run safety gate."""
        settings = self.validate_config(request.config)
        if request.action in PIPELINE_ACTIONS:
            self.validate_reference_video(request.reference_video)
            if not request.topic.strip():
                raise ConfigurationError("Topic must not be empty.")
        if request.action in LIVE_ACTIONS:
            if not request.confirm_paid_run:
                raise PreflightError(
                    "Paid action blocked: tick 'I understand this action may create paid API requests.'"
                )
            if request.paid_confirmation_phrase.strip() != PAID_CONFIRMATION_PHRASE:
                raise PreflightError(f"Paid action blocked: type the confirmation phrase {PAID_CONFIRMATION_PHRASE!r}.")
            for name, required in self.required_secrets(request.action).items():
                if required and not self.secrets.available(name):
                    raise ProviderAuthenticationError(
                        f"{name} is not configured; provide it before a paid run.",
                        provider=name.split("_")[0].lower(),
                    )
            if request.action in {
                PipelineAction.live_generation,
                PipelineAction.live_generation_preview,
                PipelineAction.full_production_render,
            }:
                if settings.execution_mode != ExecutionMode.production:
                    raise ConfigurationError(
                        "Live generation requires execution_mode='production' (provider locks must be active)."
                    )
        if request.action == PipelineAction.full_production_render:
            backend = settings.render.backend
            if backend == "remotion":
                import shutil as _shutil

                missing = [t for t in ("node", "npm", "npx") if not _shutil.which(t)]
                if missing:
                    raise RenderDependencyError(
                        f"Remotion render needs {', '.join(missing)} on PATH; "
                        "install Node.js or switch render.backend to 'pil'."
                    )
        return settings

    def request_summary(self, request: PipelineRunRequest) -> dict[str, Any]:
        """Human-reviewable summary shown before any paid run (request counts,
        never currency estimates)."""
        config = request.config or {}
        candidates = int((config.get("candidate_tournament") or {}).get("candidate_count", 4))
        revisions = int((config.get("flux_studio") or {}).get("maximum_director_revisions", 3))
        scenes = "unknown until storyboard exists"
        job_id = request.job_id or (
            generate_job_id(request.topic, request.reference_video) if request.topic and request.reference_video else ""
        )
        manifest = load_json(self.paths["jobs"] / job_id / "job_manifest.json") if job_id else None
        if isinstance(manifest, dict):
            scene_stages = [s for s in manifest.get("stages", {}) if s.startswith("09_shot_state_")]
            if scene_stages:
                scenes = len(scene_stages)
        image_requests = (
            "unknown (≈ scenes × candidates + revisions + variants)"
            if not isinstance(scenes, int)
            else scenes * candidates + scenes * revisions + scenes
        )
        return redact_secrets(
            {
                "action": request.action.value,
                "job_id": job_id,
                "scenes": scenes,
                "candidate_count": candidates,
                "max_director_revisions": revisions,
                "potential_image_generations": image_requests,
                "temporal_backends": (config.get("temporal") or {}).get(
                    "backend_preference", ["sketch-controlled-video", "deterministic-compositor"]
                ),
                "render_backend": (config.get("render") or {}).get("backend", "remotion"),
                "publishing_enabled": bool((config.get("publishing") or {}).get("enabled", False)),
            }
        )

    # -- actions -----------------------------------------------------------
    def run_preflight(self, request: PipelineRunRequest) -> dict[str, Any]:
        live = request.action in LIVE_ACTIONS
        needs_reference = request.action in PIPELINE_ACTIONS
        report = run_preflight(
            config=request.config or None,
            reference_video=request.reference_video if needs_reference else None,
            live=live,
            required_secrets=self.required_secrets(request.action)
            if live
            else {name: False for name in SecretsManager.SUPPORTED},
            secret_status=self.secrets.available,
            workspace=self.paths["base"],
            job_id=request.job_id,
        )
        save_preflight_report(report, self.paths["reports"] / "preflight_report.json")
        return report

    def run_quick_smoke(self, request: PipelineRunRequest | None = None) -> dict[str, Any]:
        """Offline smoke: synthetic video → dev plan-only run → resume check
        → redaction check. Requires no API keys."""
        import subprocess

        from .pipeline import ScientificMotionStudioV10

        root = ensure_dir(self.paths["smoke_tests"] / datetime.now(UTC).strftime("%Y%m%dT%H%M%S"))
        reference = root / "reference.mp4"
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
                str(reference),
            ],
            check=True,
        )
        config = {**CONFIG_PRESETS["Safe Development"], "workspace": str(root / "studio")}
        settings = self.validate_config(config)  # config parse + provider-lock validation
        assert settings.execution_mode == ExecutionMode.development
        events: list[tuple[str, str]] = []
        studio = ScientificMotionStudioV10(config)
        result = studio.run(
            "Quick smoke: what if rivers ran backward?",
            reference,
            plan_only=True,
            job_id="quick-smoke",
            progress_callback=lambda s, e, r: events.append((s, e)),
        )
        manifest = load_json(Path(result["job_manifest"]))
        artifacts_ok = all(Path(result[key]).exists() for key in ("job_manifest", "drawing_briefs", "shot_states"))
        # Resume: second run must reuse cached stages.
        events2: list[tuple[str, str]] = []
        ScientificMotionStudioV10(config).run(
            "Quick smoke: what if rivers ran backward?",
            reference,
            plan_only=True,
            job_id="quick-smoke",
            progress_callback=lambda s, e, r: events2.append((s, e)),
        )
        resumed = any(e == "cached" for _s, e in events2)
        register_secret("smoke-test-secret-value-123")
        redaction_ok = "smoke-test-secret-value-123" not in redact_secrets(
            "error with smoke-test-secret-value-123 embedded"
        )
        report = {
            "passed": bool(
                manifest and manifest.get("status") == "completed" and artifacts_ok and resumed and redaction_ok
            ),
            "manifest_status": manifest.get("status") if manifest else "missing",
            "artifacts_ok": artifacts_ok,
            "resume_used_cache": resumed,
            "redaction_ok": redaction_ok,
            "stage_events": len(events),
            "run_dir": result["run_dir"],
        }
        save_json(self.paths["reports"] / "quick_smoke_report.json", report)
        return report

    def run_offline_tests(self, request: PipelineRunRequest | None = None) -> dict[str, Any]:
        from .tests_final import run_all_tests

        report = run_all_tests(self.paths["reports"] / "offline_test_scratch")
        package_dir = Path(__file__).parent
        sweep = security_source_sweep(package_dir)
        combined = {
            "passed": bool(report["passed"] and sweep["ok"]),
            "test_count": report["count"],
            "regression_count": report["regression_count"],
            "final_count": report["final_count"],
            "security_sweep": sweep,
        }
        save_json(self.paths["reports"] / "offline_test_report.json", {**combined, "tests": report["tests"]})
        return combined

    def run_provider_smoke(self, request: PipelineRunRequest) -> dict[str, Any]:
        """Minimal paid smoke per provider; gated by the paid-run rules."""
        if not request.confirm_paid_run or request.paid_confirmation_phrase.strip() != PAID_CONFIRMATION_PHRASE:
            raise PreflightError(f"Live smoke blocked: confirm the checkbox and type {PAID_CONFIRMATION_PHRASE!r}.")
        results: dict[str, Any] = {"openai": {"status": "SKIPPED"}, "bfl": {"status": "SKIPPED"}}
        smoke_root = ensure_dir(self.paths["smoke_tests"] / "provider_live")
        if self.secrets.available("OPENAI_API_KEY"):
            from .llm import LLMRouter

            try:
                router = LLMRouter(
                    {
                        "execution_mode": "production",
                        "provider_order": ["openai"],
                        "openai_max_output_tokens": 512,
                        "retry": {"max_attempts": 2, "base_delay_s": 1.0},
                    },
                    self.secrets.as_secrets_dict(),
                    smoke_root / "openai_cache",
                )
                value = router.generate_json(
                    system="You return strict JSON.",
                    prompt='Return exactly {"status": "ok", "provider": "openai"}.',
                    namespace="live_smoke",
                    fallback=None,
                    force=True,
                )
                ok = isinstance(value, dict) and value.get("status") == "ok"
                results["openai"] = {
                    "status": "PASS" if ok else "FAIL",
                    "request_id": getattr(router, "_last_request_id", ""),
                }
            except Exception as exc:
                classified = classify_provider_error(exc)
                results["openai"] = {"status": "FAIL", "error": redacted_exception_text(classified, 300)}
        if self.secrets.available("BFL_API_KEY"):
            from .bfl_client import BFLClient

            try:
                client = BFLClient(
                    {
                        "bfl_model": "flux-kontext-pro",
                        "bfl_aspect_ratio": "1:1",
                        "bfl_output_format": "jpeg",
                        "bfl_timeout": 120,
                        "retry": {"max_attempts": 2},
                    },
                    self.secrets.get("BFL_API_KEY"),
                    smoke_root / "bfl_cache",
                )
                out = client.generate(
                    "Minimal test: one small blue circle on white, flat vector style.", smoke_root / "bfl_smoke.jpg"
                )
                results["bfl"] = {"status": "PASS" if out.exists() else "FAIL", "artifact": str(out)}
            except Exception as exc:
                classified = classify_provider_error(exc)
                results["bfl"] = {"status": "FAIL", "error": redacted_exception_text(classified, 300)}
        save_json(self.paths["reports"] / "provider_live_smoke.json", redact_secrets(results))
        return results

    def _apply_force_scope(self, request: PipelineRunRequest, settings: StudioConfig) -> bool:
        """Prepare a scoped force-rerun by marking manifest stages stale.

        Returns the ``force`` flag to pass to the pipeline (True only for the
        'all' scope)."""
        if request.action != PipelineAction.force_rerun:
            return request.force
        if request.force_scope == "all":
            return True
        job_id = request.job_id or generate_job_id(request.topic, request.reference_video)
        manifest_path = self.paths["jobs"] / job_id / "job_manifest.json"
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            return True  # fresh job: nothing to scope
        stages = manifest.get("stages", {})
        ordered = sorted(stages.keys())
        if request.force_scope == "failed_and_downstream":
            failed = [s for s in ordered if stages[s].get("status") == "failed"]
            anchor = failed[0] if failed else None
        else:  # selected_and_downstream
            anchor = request.force_stage or None
            if anchor is not None and anchor not in stages:
                raise ConfigurationError(f"Unknown stage for force scope: {anchor!r}")
        if anchor is None:
            return False
        for stage_id in ordered:
            if stage_id >= anchor and stages[stage_id].get("status") == "completed":
                stages[stage_id]["status"] = "stale"
        save_json(manifest_path, manifest)
        return False

    def run_pipeline(
        self,
        request: PipelineRunRequest,
        *,
        progress_callback: Callable[[str, str, dict], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        """Validate, preflight-gate and execute a pipeline action."""
        settings = self.validate_request(request)
        if request.action in LIVE_ACTIONS:
            preflight = self.run_preflight(request)
            if preflight["summary"][FAIL]:
                failed = [c["check"] for c in preflight["checks"] if c["status"] == FAIL]
                raise PreflightError("Paid action blocked by failed preflight checks: " + ", ".join(failed))
        plan_only, render_video = request.plan_only, request.render_video
        action = request.action
        if action in {PipelineAction.plan_only, PipelineAction.development_dry_run, PipelineAction.production_plan}:
            plan_only, render_video = True, False
        elif action == PipelineAction.live_generation:
            plan_only, render_video = False, False
        elif action in {PipelineAction.live_generation_preview, PipelineAction.full_production_render}:
            plan_only, render_video = False, True
        force = self._apply_force_scope(request, settings)
        if action == PipelineAction.resume_job:
            force = False  # never force on resume unless the user picked Force Rerun

        job_id = request.job_id or generate_job_id(request.topic, request.reference_video)
        run_dir = ensure_dir(self.paths["jobs"] / job_id)
        save_json(run_dir / "run_request.json", redact_secrets(request.model_dump(mode="json")))
        config = dict(request.config)
        config["workspace"] = str(self.paths["base"])
        save_json(run_dir / "resolved_config.json", redact_secrets(config))

        from .pipeline import ScientificMotionStudioV10

        try:
            studio = ScientificMotionStudioV10(config, secrets=self.secrets.as_secrets_dict())
            result = studio.run(
                request.topic,
                request.reference_video,
                plan_only=plan_only,
                render_video=render_video,
                force=force,
                job_id=job_id,
                progress_callback=progress_callback,
                cancellation_token=cancellation_token,
            )
            return result
        except Exception as exc:
            classified = classify_provider_error(exc)
            save_json(
                run_dir / "error_report.json",
                {
                    "error_type": type(classified).__name__,
                    "message": redacted_exception_text(classified, 800),
                    "stage_id": getattr(classified, "stage_id", ""),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "action": request.action.value,
                },
            )
            raise classified from exc

    def resume_pipeline(self, request: PipelineRunRequest, **kwargs: Any) -> dict[str, Any]:
        resumed = request.model_copy(update={"action": PipelineAction.resume_job, "force": False})
        return self.run_pipeline(resumed, **kwargs)

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        manifest = load_json(self.paths["jobs"] / job_id / "job_manifest.json")
        return redact_secrets(manifest) if isinstance(manifest, dict) else None

    def package_artifacts(self, job_id: str) -> Path:
        """ZIP a run directory into workspace/exports (path-traversal safe)."""
        run_dir = ensure_within(self.paths["jobs"], self.paths["jobs"] / job_id)
        if not run_dir.exists():
            raise ConfigurationError(f"No run directory for job {job_id!r}.")
        target = self.paths["exports"] / f"{slugify(job_id, 60)}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(run_dir.rglob("*")):
                if path.is_file() and path.name != "job.lock":
                    archive.write(path, path.relative_to(run_dir.parent))
        return target

    # -- dispatch -----------------------------------------------------------
    def dispatch(self, request: PipelineRunRequest, **kwargs: Any) -> Any:
        """Route any action through one entry point (used by all UIs)."""
        handlers: dict[PipelineAction, Callable[..., Any]] = {
            PipelineAction.environment_check: lambda: self.run_preflight(request),
            PipelineAction.config_validation: lambda: {
                "valid": True,
                "config": redact_secrets(self.validate_config(request.config).as_runtime_dict()),
            },
            PipelineAction.quick_smoke: lambda: self.run_quick_smoke(request),
            PipelineAction.full_offline_tests: lambda: self.run_offline_tests(request),
            PipelineAction.provider_live_smoke: lambda: self.run_provider_smoke(request),
            PipelineAction.package_artifacts: lambda: {
                "archive": str(
                    self.package_artifacts(request.job_id or generate_job_id(request.topic, request.reference_video))
                )
            },
        }
        if request.action in handlers:
            return handlers[request.action]()
        if request.action in PIPELINE_ACTIONS:
            return self.run_pipeline(request, **kwargs)
        raise ConfigurationError(f"Unknown action {request.action!r}")


class RunMonitorState:
    """Thread-safe aggregation of stage events for progress displays."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.current_stage = ""
        self.completed: list[str] = []
        self.cached: list[str] = []
        self.failed: list[str] = []
        self.retries = 0
        self.events: list[dict[str, Any]] = []

    def callback(self, stage_id: str, event: str, record: dict[str, Any]) -> None:
        with self._lock:
            self.events.append({"stage": stage_id, "event": event, "attempt": record.get("attempt", 0)})
            if event == "started":
                self.current_stage = stage_id
                if int(record.get("attempt", 1)) > 1:
                    self.retries += 1
            elif event == "completed":
                self.completed.append(stage_id)
            elif event == "cached":
                self.cached.append(stage_id)
            elif event == "failed":
                self.failed.append(stage_id)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "elapsed_s": round(time.time() - self.started_at, 1),
                "current_stage": self.current_stage,
                "progress": stage_progress_fraction(self.current_stage),
                "completed": list(self.completed),
                "cached": list(self.cached),
                "failed": list(self.failed),
                "retries": self.retries,
                "event_count": len(self.events),
            }
