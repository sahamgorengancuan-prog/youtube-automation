"""HTTP safety primitives shared by every network client in the studio.

Provides:

* :func:`validate_url` — scheme + host-allowlist policy for provider URLs
  (submission endpoints, polling URLs, result/download URLs). Prevents SSRF
  through provider-supplied links.
* :func:`is_retryable_status` / :func:`classify_exception` — retry
  classification (408, 409, 429 and 5xx are retryable; 4xx auth/validation
  failures are not).
* :func:`backoff_delays` — exponential backoff with jitter, honouring a
  server ``Retry-After`` hint when supplied.
* :func:`download_image` — bounded, validated, atomic image download
  (``raise_for_status``, Content-Type check, byte cap, Pillow verification,
  temp-file + atomic rename).
"""

from __future__ import annotations

import io
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from .errors import DownloadPolicyError

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 403, 404, 422})

DEFAULT_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 256 * 1024


def _host_matches(host: str, pattern: str) -> bool:
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".bfl.ai"
        return host.endswith(suffix) and host != suffix.lstrip(".")
    return host == pattern


def validate_url(url: str, *, allowed_hosts: Iterable[str], purpose: str = "request") -> str:
    """Validate that *url* is https and its host is in the allowlist.

    Returns the URL unchanged when valid; raises DownloadPolicyError otherwise.
    """
    if not url or not isinstance(url, str):
        raise DownloadPolicyError(f"Empty or non-string URL for {purpose}", provider="http")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DownloadPolicyError(
            f"URL scheme {parts.scheme!r} rejected for {purpose}; only https is allowed",
            provider="http",
        )
    host = parts.hostname or ""
    patterns = [p for p in allowed_hosts if p]
    if not patterns:
        raise DownloadPolicyError(
            f"No allowed hosts configured for {purpose}; refusing outbound request",
            provider="http",
        )
    if not any(_host_matches(host, pattern) for pattern in patterns):
        raise DownloadPolicyError(
            f"Host {host!r} is not in the allowed host list for {purpose}",
            provider="http",
        )
    return url


def is_retryable_status(status_code: int | None) -> bool:
    """408/409/429 and 5xx responses are retryable; other 4xx are not."""
    if status_code is None:
        return False
    if status_code in RETRYABLE_STATUS_CODES:
        return True
    return 500 <= status_code <= 599


def classify_exception(exc: BaseException) -> bool:
    """Best-effort retryability classification for arbitrary client errors.

    Connection/timeout errors are retryable. Errors exposing a
    ``status_code`` (or a ``response.status_code``) follow HTTP rules.
    Authentication/validation failures are never retried.
    """
    explicit = getattr(exc, "retryable", None)
    if isinstance(explicit, bool):
        return explicit
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status is not None:
        try:
            return is_retryable_status(int(status))
        except (TypeError, ValueError):
            return False
    name = type(exc).__name__.lower()
    if any(marker in name for marker in ("timeout", "connection", "protocol", "chunked")):
        return True
    if any(marker in name for marker in ("authentication", "permission", "badrequest", "notfound")):
        return False
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def backoff_delays(
    attempts: int,
    *,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.25,
) -> list[float]:
    """Exponential backoff schedule with multiplicative jitter."""
    delays = []
    for index in range(max(0, attempts - 1)):
        delay = min(max_delay, base_delay * (2**index))
        delay *= 1.0 + random.uniform(-jitter, jitter)
        delays.append(max(0.05, delay))
    return delays


def retry_after_hint(headers: Any) -> float | None:
    """Parse a server Retry-After header (seconds form) when present."""
    try:
        raw = (headers or {}).get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def call_with_retries(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.25,
    is_retryable: Callable[[BaseException], bool] = classify_exception,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Run *fn* with bounded retries for retryable failures.

    Non-retryable failures propagate immediately with their original type.
    """
    delays = backoff_delays(max_attempts, base_delay=base_delay, max_delay=max_delay, jitter=jitter)
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - classified below
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            hint = retry_after_hint(getattr(getattr(exc, "response", None), "headers", None))
            delay = hint if hint is not None else delays[min(attempt - 1, len(delays) - 1)]
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)


def download_image(
    response: Any,
    destination: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    require_image_content_type: bool = True,
) -> Path:
    """Persist an already-issued image response safely and atomically.

    The response must expose ``raise_for_status()``, ``headers`` and either
    ``iter_content(chunk_size)`` or ``content``. The payload is size-capped,
    verified with Pillow (rejecting HTML/JSON bodies, empty files and corrupt
    images) and written to a temporary file that is atomically renamed only
    after validation — no partial artifacts are left on failure.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    response.raise_for_status()
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type", "")).lower().split(";")[0].strip()
    if require_image_content_type and content_type and not content_type.startswith("image/"):
        raise DownloadPolicyError(
            f"Download rejected: Content-Type {content_type!r} is not an image",
            provider="http",
        )

    declared = headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise DownloadPolicyError(
                    f"Download rejected: declared size {declared} exceeds cap {max_bytes}",
                    provider="http",
                )
        except (TypeError, ValueError):
            pass

    chunks: list[bytes] = []
    total = 0
    iterator = None
    if hasattr(response, "iter_content"):
        iterator = response.iter_content(_DOWNLOAD_CHUNK_BYTES)
    if iterator is not None:
        for chunk in iterator:
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise DownloadPolicyError(
                    f"Download rejected: payload exceeded cap of {max_bytes} bytes",
                    provider="http",
                )
            chunks.append(chunk)
        payload = b"".join(chunks)
    else:
        payload = getattr(response, "content", b"") or b""
        if len(payload) > max_bytes:
            raise DownloadPolicyError(
                f"Download rejected: payload exceeded cap of {max_bytes} bytes",
                provider="http",
            )

    if not payload:
        raise DownloadPolicyError("Download rejected: empty response body", provider="http")

    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
    except DownloadPolicyError:
        raise
    except Exception as exc:
        raise DownloadPolicyError(
            f"Download rejected: payload is not a valid image ({type(exc).__name__})",
            provider="http",
        ) from exc

    handle = tempfile.NamedTemporaryFile(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".part", delete=False
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, destination)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return destination
