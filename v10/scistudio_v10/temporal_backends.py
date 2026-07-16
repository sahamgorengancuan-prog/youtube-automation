"""Temporal video backends.

``SketchControlledVideoBackend`` runs an external command described as an
**argv token list** with ``{placeholder}`` substitution applied per token.
Tokens are passed to ``subprocess.run`` as a list — never joined through a
shell — so LLM-derived values such as the motion prompt cannot inject
commands. Legacy single-string ``command`` configuration is parsed with
``shlex.split`` into the same argv form.

Shell execution is supported only for advanced users via
``unsafe_shell_command`` + ``allow_shell=true``; it is disabled by default,
explicitly marked unsafe, and **rejected in production mode** unless
``security_override_unsafe_shell=true`` is also supplied.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol

from .errors import UnsafeCommandError
from .schemas import TemporalRequest, TemporalResult
from .utils import ensure_dir, save_json

_ALLOWED_PLACEHOLDERS = frozenset(
    {
        "start",
        "end",
        "sketch_start",
        "sketch_end",
        "output",
        "prompt",
        "fps",
        "frames",
    }
)


class TemporalBackend(Protocol):
    backend_id: str

    def available(self) -> bool: ...
    def supports(self, request: TemporalRequest) -> bool: ...
    def generate(self, request: TemporalRequest) -> TemporalResult: ...


class DeterministicCompositorBackend:
    """Still-frame hold compositor built on FFmpeg (always available)."""

    backend_id = "deterministic-compositor"

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)

    def available(self) -> bool:
        return True

    def supports(self, request: TemporalRequest) -> bool:
        return request.complexity in {"hold", "simple", "articulated"}

    def generate(self, request: TemporalRequest) -> TemporalResult:
        output = Path(request.output_path or self.root / f"{request.scene_id}.mp4")
        ensure_dir(output.parent)
        duration = max(1 / request.fps, request.duration_frames / request.fps)
        command = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            request.beauty_start,
            "-t",
            f"{duration:.4f}",
            "-r",
            str(request.fps),
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
        subprocess.run(command, check=True, capture_output=True)
        return TemporalResult(
            scene_id=request.scene_id,
            backend_id=self.backend_id,
            output_path=str(output),
            deterministic=True,
            metadata={"command": "ffmpeg still-frame compositor"},
        )


class _SafeFormatMap(dict):
    """Formatting map that rejects unknown placeholders instead of KeyError-ing
    into silent empty substitutions."""

    def __missing__(self, key: str) -> str:
        raise UnsafeCommandError(
            f"Unknown placeholder {{{key}}} in temporal command template; allowed: {sorted(_ALLOWED_PLACEHOLDERS)}"
        )


class SketchControlledVideoBackend:
    """Adapter for SketchVideo-like one/two-keyframe sketch conditioning.

    It intentionally fails rather than silently replacing organic motion with
    PowerPoint-like motion.
    """

    backend_id = "sketch-controlled-video"

    def __init__(self, config: dict[str, Any], root: str | Path, execution_mode: str = "development"):
        self.config = config
        self.root = ensure_dir(root)
        self.execution_mode = str(execution_mode).lower()

    def _argv_template(self) -> list[str]:
        argv = self.config.get("argv") or []
        if argv:
            return [str(token) for token in argv]
        legacy = str(self.config.get("command", "") or "")
        if legacy:
            return shlex.split(legacy)
        return []

    def available(self) -> bool:
        if self.config.get("allow_shell") and self.config.get("unsafe_shell_command"):
            return bool(shutil.which(shlex.split(str(self.config["unsafe_shell_command"]))[0]))
        template = self._argv_template()
        return bool(template and shutil.which(template[0]))

    def supports(self, request: TemporalRequest) -> bool:
        return request.complexity in {"articulated", "deformation", "organic"} and bool(request.control_sketch_start)

    def _values(self, request: TemporalRequest, output: Path) -> dict[str, Any]:
        return {
            "start": request.beauty_start,
            "end": request.beauty_end or request.beauty_start,
            "sketch_start": request.control_sketch_start,
            "sketch_end": request.control_sketch_end,
            "output": str(output),
            "prompt": request.motion_prompt,
            "fps": request.fps,
            "frames": request.duration_frames,
        }

    def generate(self, request: TemporalRequest) -> TemporalResult:
        if not self.available():
            raise RuntimeError("Sketch-controlled temporal backend is unavailable")
        output = Path(request.output_path or self.root / f"{request.scene_id}.mp4")
        ensure_dir(output.parent)
        values = self._values(request, output)
        timeout = float(self.config.get("timeout_s", 1800.0))

        if self.config.get("allow_shell") and self.config.get("unsafe_shell_command"):
            self._run_unsafe_shell(values, timeout)
        else:
            argv = [token.format_map(_SafeFormatMap(values)) for token in self._argv_template()]
            subprocess.run(argv, check=True, timeout=timeout)

        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(f"Temporal backend produced no output for scene {request.scene_id}")
        return TemporalResult(
            scene_id=request.scene_id,
            backend_id=self.backend_id,
            output_path=str(output),
            metadata={"external_command": True},
        )

    def _run_unsafe_shell(self, values: dict[str, Any], timeout: float) -> None:
        """UNSAFE opt-in shell path. Disabled by default; rejected in production
        without an explicit security override."""
        if self.execution_mode == "production" and not self.config.get("security_override_unsafe_shell"):
            raise UnsafeCommandError(
                "Shell-based temporal command execution is not allowed in "
                "production mode without security_override_unsafe_shell=true"
            )
        command = str(self.config["unsafe_shell_command"]).format_map(
            _SafeFormatMap({key: shlex.quote(str(value)) for key, value in values.items()})
        )
        subprocess.run(command, shell=True, check=True, timeout=timeout)  # noqa: S602 - explicit opt-in, quoted values


class TemporalBackendRouter:
    """Chooses the first available backend that supports the request; fails
    loudly when high-complexity motion has no capable backend."""

    def __init__(self, backends: list[TemporalBackend], root: str | Path):
        self.backends = backends
        self.root = ensure_dir(root)

    def select(self, request: TemporalRequest) -> TemporalBackend:
        preferred = request.backend_preference or [
            "sketch-controlled-video",
            "deterministic-compositor",
        ]
        by_id = {backend.backend_id: backend for backend in self.backends}
        for backend_id in preferred:
            backend = by_id.get(backend_id)
            if backend and backend.available() and backend.supports(request):
                return backend
        for backend in self.backends:
            if backend.available() and backend.supports(request):
                return backend
        raise RuntimeError(f"No temporal backend can satisfy scene {request.scene_id} complexity={request.complexity}")

    def generate(self, request: TemporalRequest) -> TemporalResult:
        backend = self.select(request)
        result = backend.generate(request)
        save_json(self.root / f"{request.scene_id}.json", result)
        return result
