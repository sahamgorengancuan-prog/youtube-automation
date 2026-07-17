"""Structured preflight checker.

Every check yields PASS / WARNING / FAIL / SKIPPED with a detail line and an
actionable recommendation. The report is JSON-serializable, redacted, and can
be persisted as ``preflight_report.json``. Live-only checks (network, API
keys) are SKIPPED for offline actions instead of failing them.
"""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from .config_models import StudioConfig
from .security import redact_secrets
from .utils import load_json, save_json

PASS = "PASS"
WARNING = "WARNING"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")
_MIN_FREE_DISK_BYTES = 2 * 1024**3
_REQUIRED_PACKAGES = ("pydantic", "PIL", "requests")
_OPTIONAL_PACKAGES = ("openai", "fastapi", "ipywidgets", "cairosvg")


def _check(name: str, status: str, detail: str = "", recommendation: str = "") -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail, "recommendation": recommendation}


def run_preflight(
    *,
    config: dict[str, Any] | StudioConfig | None = None,
    reference_video: str | Path | None = None,
    live: bool = False,
    render_backend: str | None = None,
    required_secrets: dict[str, bool] | None = None,
    secret_status: Callable[[str], bool] | None = None,
    workspace: str | Path | None = None,
    job_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Run all preflight checks and return a structured report.

    ``required_secrets`` maps secret names to whether the selected action
    needs them; ``secret_status(name)`` reports availability without exposing
    values. ``which`` is injectable for tests.
    """
    checks: list[dict[str, str]] = []

    # --- runtime ---------------------------------------------------------
    py = sys.version_info
    checks.append(
        _check(
            "python_version",
            PASS if py >= (3, 11) else FAIL,
            f"Python {py.major}.{py.minor}.{py.micro}",
            "" if py >= (3, 11) else "Use a Python 3.11+ runtime.",
        )
    )
    checks.append(_check("operating_system", PASS, platform.platform()))

    cpu = os.cpu_count() or 0
    checks.append(
        _check(
            "cpu",
            PASS if cpu >= 2 else WARNING,
            f"{cpu} logical CPUs",
            "" if cpu >= 2 else "Pipelines are slow below 2 CPUs.",
        )
    )
    try:
        total_ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        ram_gb = total_ram / 1024**3
        checks.append(
            _check(
                "ram",
                PASS if ram_gb >= 4 else WARNING,
                f"{ram_gb:.1f} GiB",
                "" if ram_gb >= 4 else "4+ GiB RAM recommended.",
            )
        )
    except (ValueError, OSError, AttributeError):
        checks.append(_check("ram", SKIPPED, "unavailable on this platform"))
    gpu = which("nvidia-smi")
    checks.append(
        _check(
            "gpu", PASS if gpu else SKIPPED, "NVIDIA GPU tooling present" if gpu else "no GPU detected (not required)"
        )
    )

    # --- workspace -------------------------------------------------------
    settings: StudioConfig | None = None
    config_error = ""
    if config is not None:
        try:
            settings = StudioConfig.from_dict(config)
        except Exception as exc:
            config_error = str(redact_secrets(str(exc)))[:600]
    ws = Path(workspace or (settings.workspace if settings else "./scientific_motion_studio_v10"))
    try:
        ws.mkdir(parents=True, exist_ok=True)
        probe = ws / ".preflight_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("workspace_writable", PASS, str(ws)))
    except OSError as exc:
        checks.append(_check("workspace_writable", FAIL, f"{ws}: {exc}", "Choose a writable workspace directory."))
    try:
        usage = shutil.disk_usage(str(ws if ws.exists() else Path.cwd()))
        free_gb = usage.free / 1024**3
        status = PASS if usage.free >= _MIN_FREE_DISK_BYTES else WARNING
        checks.append(
            _check(
                "free_disk",
                status,
                f"{free_gb:.1f} GiB free",
                "" if status == PASS else "Less than 2 GiB free; renders may fail.",
            )
        )
    except OSError:
        checks.append(_check("free_disk", SKIPPED, "disk usage unavailable"))

    # --- package & configuration -----------------------------------------
    try:
        importlib.import_module("scistudio_v10")
        checks.append(_check("package_import", PASS, "scistudio_v10 importable"))
    except Exception as exc:  # pragma: no cover - import of self normally succeeds
        checks.append(_check("package_import", FAIL, type(exc).__name__, "Re-run the package generation cells."))
    if config is None:
        checks.append(_check("config_validation", SKIPPED, "no configuration supplied"))
        checks.append(_check("provider_lock", SKIPPED, "no configuration supplied"))
    elif settings is None:
        checks.append(_check("config_validation", FAIL, config_error, "Fix the configuration fields listed above."))
        checks.append(
            _check(
                "provider_lock",
                FAIL,
                "configuration invalid",
                "Provider lock can only be evaluated on a valid configuration.",
            )
        )
    else:
        checks.append(_check("config_validation", PASS, f"mode={settings.execution_mode.value}"))
        if settings.execution_mode.value == "production":
            checks.append(_check("provider_lock", PASS, "OpenAI-only reasoning/vision, BFL-only images enforced"))
        else:
            checks.append(
                _check("provider_lock", PASS, f"{settings.execution_mode.value} mode (locks apply in production)")
            )

    for module_name in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(module_name)
            checks.append(_check(f"required_package:{module_name}", PASS, "installed"))
        except ImportError:
            checks.append(_check(f"required_package:{module_name}", FAIL, "missing", f"pip install {module_name}"))
    for module_name in _OPTIONAL_PACKAGES:
        try:
            importlib.import_module(module_name)
            checks.append(_check(f"optional_package:{module_name}", PASS, "installed"))
        except ImportError:
            checks.append(
                _check(
                    f"optional_package:{module_name}",
                    WARNING,
                    "missing",
                    f"pip install {module_name} (only needed for related features)",
                )
            )

    # --- secrets ----------------------------------------------------------
    for name, required in (required_secrets or {}).items():
        available = bool(secret_status(name)) if secret_status else bool(os.environ.get(name))
        if not required:
            checks.append(_check(f"secret:{name}", SKIPPED, "not required for selected mode"))
        elif available:
            checks.append(_check(f"secret:{name}", PASS, "configured"))
        else:
            checks.append(
                _check(
                    f"secret:{name}",
                    FAIL,
                    "missing",
                    f"Provide {name} via Colab Secrets, environment variable or the Secrets tab.",
                )
            )

    # --- reference video --------------------------------------------------
    if reference_video is None:
        checks.append(_check("reference_video", SKIPPED, "not required for selected action"))
    else:
        ref = Path(reference_video)
        if not ref.exists() or not ref.is_file():
            checks.append(
                _check(
                    "reference_video",
                    FAIL,
                    f"not found: {ref}",
                    "Upload a reference video or point to an existing file.",
                )
            )
        elif ref.suffix.lower() not in VIDEO_SUFFIXES:
            checks.append(
                _check(
                    "reference_video",
                    FAIL,
                    f"unsupported extension {ref.suffix!r}",
                    f"Use one of: {', '.join(VIDEO_SUFFIXES)}",
                )
            )
        elif ref.stat().st_size == 0:
            checks.append(_check("reference_video", FAIL, "file is empty", "Re-upload the reference video."))
        else:
            checks.append(_check("reference_video", PASS, f"{ref.name} ({ref.stat().st_size / 1024:.0f} KiB)"))

    # --- external tools ---------------------------------------------------
    for tool in ("ffmpeg", "ffprobe"):
        found = which(tool)
        checks.append(
            _check(
                tool,
                PASS if found else FAIL,
                found or "not on PATH",
                "" if found else "Install FFmpeg (apt-get install -y ffmpeg).",
            )
        )
    backend = (render_backend or (settings.render.backend if settings else "remotion")).lower()
    node_tools = {tool: which(tool) for tool in ("node", "npm", "npx")}
    if backend == "remotion":
        for tool, found in node_tools.items():
            checks.append(
                _check(
                    tool,
                    PASS if found else FAIL,
                    found or "not on PATH",
                    "" if found else "Install Node.js 18+ or switch render backend to 'pil'.",
                )
            )
        checks.append(
            _check(
                "remotion_prerequisites",
                PASS if all(node_tools.values()) else FAIL,
                "node/npm/npx " + ("available" if all(node_tools.values()) else "incomplete"),
                "" if all(node_tools.values()) else "Remotion rendering needs Node.js tooling.",
            )
        )
    else:
        for tool, found in node_tools.items():
            checks.append(_check(tool, PASS if found else SKIPPED, found or f"not needed for backend {backend!r}"))
        checks.append(_check("remotion_prerequisites", SKIPPED, f"render backend is {backend!r}"))

    # --- network (live only) ----------------------------------------------
    if not live:
        checks.append(_check("network", SKIPPED, "offline action; no network required"))
    else:
        try:
            import socket

            with socket.create_connection(("api.bfl.ai", 443), timeout=5):
                pass
            checks.append(_check("network", PASS, "outbound https reachable"))
        except OSError as exc:
            checks.append(
                _check(
                    "network",
                    FAIL,
                    f"outbound https failed: {type(exc).__name__}",
                    "Live runs need outbound network access.",
                )
            )

    # --- temporal / publishing safety --------------------------------------
    if settings is not None:
        sketch = settings.temporal.sketch_backend
        if sketch.allow_shell:
            checks.append(
                _check(
                    "temporal_shell",
                    WARNING,
                    "allow_shell is enabled (unsafe opt-in path)",
                    "Prefer the argv template; shell execution is rejected in production without an explicit override.",
                )
            )
        else:
            checks.append(_check("temporal_shell", PASS, "argv template mode (safe)"))
        if settings.publishing.enabled:
            hosts = settings.publishing.allowed_hosts
            checks.append(
                _check(
                    "publishing_safety",
                    PASS if (settings.publishing.provider == "local-archive" or hosts) else FAIL,
                    f"provider={settings.publishing.provider}",
                    ""
                    if (settings.publishing.provider == "local-archive" or hosts)
                    else "upload-post publishing requires allowed_hosts.",
                )
            )
        else:
            checks.append(_check("publishing_safety", PASS, "publishing disabled"))

    # --- existing job ------------------------------------------------------
    if job_id:
        job_dir = ws / "jobs" / job_id
        manifest = load_json(job_dir / "job_manifest.json")
        if isinstance(manifest, dict):
            stages = manifest.get("stages", {})
            done = sum(1 for s in stages.values() if s.get("status") == "completed")
            checks.append(
                _check(
                    "existing_job_manifest",
                    PASS,
                    f"{job_id}: {manifest.get('status')} — {done}/{len(stages)} stages completed",
                )
            )
        else:
            checks.append(_check("existing_job_manifest", SKIPPED, f"no manifest for {job_id} (fresh job)"))
        lock = job_dir / "job.lock"
        if lock.exists():
            checks.append(
                _check(
                    "existing_job_lock",
                    WARNING,
                    "lock file present",
                    "A crashed worker's stale lock is reclaimed automatically; a live worker blocks the run.",
                )
            )
        else:
            checks.append(_check("existing_job_lock", PASS, "no lock"))

    summary = {status: sum(1 for c in checks if c["status"] == status) for status in (PASS, WARNING, FAIL, SKIPPED)}
    return {
        "checks": [redact_secrets(c) for c in checks],
        "summary": summary,
        "ok": summary[FAIL] == 0,
    }


def save_preflight_report(report: dict[str, Any], destination: str | Path) -> Path:
    return save_json(Path(destination), report)
