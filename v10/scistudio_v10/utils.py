"""Filesystem, hashing, JSON and subprocess utilities.

All JSON/file writes performed through this module are **atomic** (temp file
in the destination directory + ``os.replace``), and all JSON reads are
**corruption-safe**: a truncated or invalid file is quarantined with a
``.corrupt`` suffix and the caller receives the default value instead of a
crash that would permanently break job resume.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable, Iterable

from .security import redact_secrets

_COMMAND_ERROR_TEXT_LIMIT = 4000


def ensure_dir(path: str | Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(value: str, max_len: int = 80) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
    return (text or "untitled")[:max_len].rstrip("-")


def canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hash_value(value: Any, length: int = 20) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    """Recursively serialize nested Pydantic models and path-like values."""
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    """Write bytes atomically: temp file in the same directory, then rename."""
    path = Path(path)
    ensure_dir(path.parent)
    handle = tempfile.NamedTemporaryFile(dir=str(path.parent), prefix=f".{path.name}.", suffix=".part", delete=False)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return path


def atomic_copy(src: str | Path, dst: str | Path) -> Path:
    """Copy a file atomically (temp + rename); never leaves partial targets."""
    src, dst = Path(src), Path(dst)
    ensure_dir(dst.parent)
    handle = tempfile.NamedTemporaryFile(dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".part", delete=False)
    handle.close()
    try:
        shutil.copy2(src, handle.name)
        os.replace(handle.name, dst)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return dst


def save_json(path: str | Path, value: Any) -> Path:
    path = Path(path)
    payload = json.dumps(_jsonable(value), ensure_ascii=False, indent=2, default=str)
    return atomic_write_bytes(path, payload.encode("utf-8"))


def quarantine_corrupt_file(path: Path) -> Path | None:
    """Move a corrupt file aside so subsequent runs regenerate it."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, target)
        return target
    except OSError:
        return None


def load_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON; quarantine corrupt files and return *default* instead of raising."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        quarantine_corrupt_file(path)
        return default


def ensure_within(base: str | Path, candidate: str | Path) -> Path:
    """Resolve *candidate* and require it to stay inside *base* (anti-traversal)."""
    base = Path(base).resolve()
    resolved = Path(candidate).resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError(f"Path {resolved} escapes the allowed base directory {base}")
    return resolved


def extract_json(text: str | bytes | dict | list | None, fallback: Any = None) -> Any:
    if isinstance(text, (dict, list)):
        return text
    if text is None:
        return fallback
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = str(text).strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.S | re.I)
    for candidate in fenced:
        try:
            return json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue

    starts = [idx for idx in (text.find("{"), text.find("[")) if idx >= 0]
    if not starts:
        return fallback
    start = min(starts)
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except (json.JSONDecodeError, ValueError):
                    break
    return fallback


def run_command(
    command: Iterable[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | float | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run an argv-list command (never a shell string).

    Failure messages are redacted and length-capped so provider errors that
    leak through stderr cannot carry secrets into logs or manifests.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    result = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        timeout=timeout,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        stdout = redact_secrets(str(result.stdout or ""))[:_COMMAND_ERROR_TEXT_LIMIT]
        stderr = redact_secrets(str(result.stderr or ""))[:_COMMAND_ERROR_TEXT_LIMIT]
        printable = " ".join(redact_secrets(str(part)) for part in command)
        raise RuntimeError(f"Command failed ({result.returncode}): {printable}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    return result


def ffprobe_duration(path: str | Path) -> float:
    path = Path(path)
    if not path.exists():
        return 0.0
    try:
        result = run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        )
        return float((result.stdout or "0").strip())
    except (RuntimeError, ValueError, subprocess.SubprocessError):
        return 0.0


def copy_into(src: str | Path, dst: str | Path) -> Path:
    return atomic_copy(src, dst)


def retry(
    fn: Callable[[], Any],
    attempts: int = 3,
    delay: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    *,
    max_delay: float = 30.0,
    jitter: float = 0.25,
) -> Any:
    """Exponential-backoff retry with jitter for the given exception types."""
    from .http_safety import backoff_delays

    delays = backoff_delays(attempts, base_delay=delay, max_delay=max_delay, jitter=jitter)
    last: Exception | None = None
    for index in range(attempts):
        try:
            return fn()
        except exceptions as exc:
            last = exc
            if index + 1 < attempts:
                time.sleep(delays[min(index, len(delays) - 1)])
    if last:
        raise last
    raise RuntimeError("retry() exhausted without an exception")
