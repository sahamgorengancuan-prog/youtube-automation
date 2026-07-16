"""Hardened Black Forest Labs FLUX Kontext client.

Submission → polling → download with:

* model / base-URL / aspect-ratio validation before any paid call;
* https + host-allowlist policy on the endpoint, the server-supplied polling
  URL and the result ``sample`` URL (SSRF prevention);
* bounded retries with exponential backoff and jitter for 408/409/429/5xx and
  connection failures, honouring ``Retry-After`` hints;
* explicit handling of every polling status — unknown statuses fail loudly;
* ``raise_for_status`` + Content-Type check + size cap + Pillow verification
  on the final download, written atomically (no partial cache artifacts);
* a content-addressed cache keyed on every generation input (provider, model,
  endpoint, prompt, input-image SHA-256, aspect, format, seed, upsampling,
  safety tolerance and cache schema version) — never filename + size;
* failures are never written into the success cache;
* observability records carry request id, model, prompt hash, input-image
  hash, latency and status — never raw base64 payloads or API keys.

Note on HTTP style: calls go through module-level ``requests.post`` /
``requests.get`` (not a private ``Session``) so the offline regression suite
can intercept the exact wire contract with ``unittest.mock.patch``.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import requests

from .config_models import BFL_ALLOWED_MODELS, BFL_MAX_ASPECT, BFL_MIN_ASPECT
from .errors import DownloadPolicyError, ProviderError
from .http_safety import call_with_retries, download_image, is_retryable_status, validate_url
from .security import redacted_exception_text, register_secret
from .utils import atomic_copy, ensure_dir, hash_value, save_json, sha256_file

CACHE_SCHEMA_VERSION = 3

_PENDING_STATUSES = {"pending", "queued", "processing", "running", "task queued"}
_MODERATED_STATUSES = {"content moderated", "request moderated"}
_FAILED_STATUSES = {"error", "failed", "task not found"}


class BFLError(ProviderError):
    """A BFL FLUX Kontext request failed."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str = "",
        status: str = "",
    ):
        super().__init__(message, provider="bfl", retryable=retryable, status_code=status_code, request_id=request_id)
        self.status = status


class BFLClient:
    """Submit/poll/download client for the BFL FLUX Kontext API."""

    def __init__(
        self,
        config: dict[str, Any],
        api_key: str,
        cache_root: str | Path,
        *,
        event_logger: Any | None = None,
    ):
        if not api_key:
            raise BFLError("BFL_API_KEY is not configured")
        self.config = config
        self.api_key = api_key
        register_secret(api_key)
        self.cache_dir = ensure_dir(Path(cache_root) / "bfl_images")
        self.events = event_logger

        self.model = str(config.get("bfl_model", "flux-kontext-pro"))
        if self.model not in BFL_ALLOWED_MODELS:
            raise BFLError(f"Unsupported FLUX Kontext model {self.model!r}; allowed: {sorted(BFL_ALLOWED_MODELS)}")
        self.base_url = str(config.get("bfl_base_url", "https://api.bfl.ai/v1")).rstrip("/")
        hosts = list(config.get("bfl_allowed_url_hosts") or ["api.bfl.ai", "*.bfl.ai"])
        from urllib.parse import urlsplit

        base_host = urlsplit(self.base_url).hostname or ""
        if base_host and base_host not in hosts:
            hosts.append(base_host)
        self.allowed_hosts = hosts
        validate_url(self.base_url + "/", allowed_hosts=self.allowed_hosts, purpose="BFL base URL")

        self.timeout_s = float(config.get("bfl_timeout", 240))
        self.poll_interval_s = float(config.get("bfl_poll_interval", 1.5))
        self.max_download_bytes = int(config.get("bfl_max_download_bytes", 32 * 1024 * 1024))
        retry_cfg = config.get("retry") or {}
        self.max_attempts = int(retry_cfg.get("max_attempts", 4))
        self.base_delay_s = float(retry_cfg.get("base_delay_s", 1.0))
        self.max_delay_s = float(retry_cfg.get("max_delay_s", 30.0))
        self.jitter = float(retry_cfg.get("jitter", 0.25))

        self.output_format = str(config.get("bfl_output_format", "png")).lower()
        if self.output_format not in {"png", "jpeg"}:
            raise BFLError("bfl_output_format must be png or jpeg")
        self.aspect_ratio = self._validate_aspect(str(config.get("bfl_aspect_ratio", "9:16")))
        self.prompt_upsampling = bool(config.get("bfl_prompt_upsampling", False))
        self.safety_tolerance = int(config.get("bfl_safety_tolerance", 2))
        self.seed = config.get("bfl_seed")

    @staticmethod
    def _validate_aspect(aspect: str) -> str:
        try:
            left, right = [float(x) for x in str(aspect).split(":", 1)]
            ratio = left / right
        except (ValueError, ZeroDivisionError) as exc:
            raise BFLError(f"Unsupported BFL aspect ratio: {aspect!r}") from exc
        if not (BFL_MIN_ASPECT <= ratio <= BFL_MAX_ASPECT):
            raise BFLError(f"Unsupported BFL aspect ratio {aspect!r}: outside Kontext range 3:7..7:3")
        return aspect

    # -- cache -----------------------------------------------------------
    def cache_key(self, prompt: str, init_hash: str) -> str:
        return hash_value(
            {
                "provider": "bfl",
                "endpoint": self.base_url,
                "model": self.model,
                "prompt": prompt,
                "init": init_hash,
                "aspect": self.aspect_ratio,
                "prompt_upsampling": self.prompt_upsampling,
                "safety_tolerance": self.safety_tolerance,
                "output_format": self.output_format,
                "seed": self.seed,
                "cache_schema": CACHE_SCHEMA_VERSION,
            },
            32,
        )

    def _cached_image(self, key: str) -> Path | None:
        suffix = ".png" if self.output_format == "png" else ".jpg"
        cached = self.cache_dir / f"{key}{suffix}"
        if not cached.exists() or cached.stat().st_size == 0:
            return None
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(cached.read_bytes())) as image:
                image.verify()
            return cached
        except Exception:
            # Corrupt legacy cache entry: quarantine and regenerate.
            from .utils import quarantine_corrupt_file

            quarantine_corrupt_file(cached)
            return None

    # -- events ----------------------------------------------------------
    def _emit(self, event: str, **payload: Any) -> None:
        if self.events is not None:
            try:
                self.events.emit(event, provider="bfl", model=self.model, **payload)
            except Exception:
                pass

    # -- request ---------------------------------------------------------
    def generate(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        init_image: str | Path | None = None,
        force: bool = False,
    ) -> Path:
        """Generate (or reuse from cache) one image; returns the output path.

        Raises :class:`BFLError` or :class:`DownloadPolicyError` on failure —
        failures are never cached as successes.
        """
        output_path = Path(output_path)
        ensure_dir(output_path.parent)
        init_path = Path(init_image) if init_image else None
        init_hash = sha256_file(init_path) if init_path and init_path.exists() else ""
        prompt_hash = hash_value(prompt, 24)
        key = self.cache_key(prompt, init_hash)

        if not force:
            cached = self._cached_image(key)
            if cached is not None:
                atomic_copy(cached, output_path)
                self._emit("bfl.cache_hit", cache_key=key, prompt_hash=prompt_hash)
                return output_path

        payload: dict[str, Any] = {
            "prompt": prompt,
            "output_format": self.output_format,
            "aspect_ratio": self.aspect_ratio,
            "prompt_upsampling": self.prompt_upsampling,
            "safety_tolerance": self.safety_tolerance,
        }
        if self.seed is not None:
            payload["seed"] = int(self.seed)
        if init_path and init_path.exists():
            payload["input_image"] = base64.b64encode(init_path.read_bytes()).decode("ascii")

        safe_payload = {k: v for k, v in payload.items() if k != "input_image"}
        safe_payload["input_image_hash"] = init_hash
        save_json(
            self.cache_dir / f"{key}.request.json",
            {
                "endpoint": f"{self.base_url}/{self.model}",
                "payload": safe_payload,
                "cache_key": key,
            },
        )

        headers = {"x-key": self.api_key, "Content-Type": "application/json", "accept": "application/json"}
        started = time.perf_counter()
        attempts = {"submit": 0, "download": 0}

        def _submit() -> dict[str, Any]:
            attempts["submit"] += 1
            response = requests.post(
                f"{self.base_url}/{self.model}",
                headers=headers,
                json=payload,
                timeout=60,
            )
            status_code = getattr(response, "status_code", 200)
            if is_retryable_status(status_code):
                raise BFLError(f"BFL submit returned {status_code}", retryable=True, status_code=status_code)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not (body.get("id") or body.get("polling_url")):
                raise BFLError("BFL submit response missing id/polling_url")
            return body

        try:
            submitted = call_with_retries(
                _submit,
                max_attempts=self.max_attempts,
                base_delay=self.base_delay_s,
                max_delay=self.max_delay_s,
                jitter=self.jitter,
                on_retry=lambda n, exc, d: self._emit(
                    "bfl.retry", phase="submit", attempt=n, delay_s=round(d, 2), error=redacted_exception_text(exc, 300)
                ),
            )
        except BFLError:
            raise
        except Exception as exc:
            raise BFLError(f"BFL submit failed: {redacted_exception_text(exc)}", retryable=False) from exc

        request_id = str(submitted.get("id", ""))
        polling_url = submitted.get("polling_url") or f"{self.base_url}/get_result"
        validate_url(polling_url, allowed_hosts=self.allowed_hosts, purpose="BFL polling URL")

        sample_url = self._poll(polling_url, request_id)
        validate_url(sample_url, allowed_hosts=self.allowed_hosts, purpose="BFL result URL")

        suffix = ".png" if self.output_format == "png" else ".jpg"
        cached_target = self.cache_dir / f"{key}{suffix}"

        def _download() -> Path:
            attempts["download"] += 1
            response = requests.get(sample_url, timeout=120)
            return download_image(response, cached_target, max_bytes=self.max_download_bytes)

        try:
            call_with_retries(
                _download,
                max_attempts=self.max_attempts,
                base_delay=self.base_delay_s,
                max_delay=self.max_delay_s,
                jitter=self.jitter,
                is_retryable=lambda exc: (
                    not isinstance(exc, DownloadPolicyError)
                    and is_retryable_status(getattr(getattr(exc, "response", None), "status_code", None))
                ),
                on_retry=lambda n, exc, d: self._emit(
                    "bfl.retry",
                    phase="download",
                    attempt=n,
                    delay_s=round(d, 2),
                    error=redacted_exception_text(exc, 300),
                ),
            )
        except DownloadPolicyError:
            raise
        except Exception as exc:
            raise BFLError(f"BFL download failed: {redacted_exception_text(exc)}", request_id=request_id) from exc

        atomic_copy(cached_target, output_path)
        latency = round(time.perf_counter() - started, 3)
        save_json(
            self.cache_dir / f"{key}.meta.json",
            {
                "request_id": request_id,
                "model": self.model,
                "prompt_hash": prompt_hash,
                "input_image_hash": init_hash,
                "latency_s": latency,
                "status": "Ready",
                "attempts": attempts,
                "cache_key": key,
            },
        )
        self._emit(
            "bfl.generated",
            request_id=request_id,
            prompt_hash=prompt_hash,
            input_image_hash=init_hash,
            latency_s=latency,
            cache_key=key,
            attempts=attempts,
        )
        return output_path

    def _poll(self, polling_url: str, request_id: str) -> str:
        """Poll until Ready; returns the result sample URL."""
        deadline = time.time() + self.timeout_s
        params = None if "get_result" not in polling_url else {"id": request_id}
        last_status = ""
        while time.time() < deadline:
            try:
                response = requests.get(
                    polling_url,
                    headers={"x-key": self.api_key, "accept": "application/json"},
                    params=params,
                    timeout=30,
                )
            except Exception as exc:
                # Transient poll failure: wait and try again within the deadline.
                self._emit("bfl.poll_error", request_id=request_id, error=redacted_exception_text(exc, 300))
                time.sleep(self.poll_interval_s)
                continue
            status_code = getattr(response, "status_code", 200)
            if is_retryable_status(status_code):
                time.sleep(self.poll_interval_s)
                continue
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise BFLError("BFL polling response is not an object", request_id=request_id)
            status = str(body.get("status", ""))
            low = status.lower()
            last_status = status
            if low == "ready":
                result = body.get("result") or {}
                sample = result.get("sample") if isinstance(result, dict) else None
                if not sample:
                    raise BFLError("BFL Ready response missing result.sample URL", request_id=request_id, status=status)
                return str(sample)
            if low in _MODERATED_STATUSES:
                raise BFLError(f"BFL request was moderated (status={status})", request_id=request_id, status=status)
            if low in _FAILED_STATUSES:
                raise BFLError(f"BFL generation failed (status={status})", request_id=request_id, status=status)
            if low not in _PENDING_STATUSES:
                raise BFLError(f"BFL returned unknown status {status!r}", request_id=request_id, status=status)
            time.sleep(self.poll_interval_s)
        raise BFLError(
            f"BFL timed out after {self.timeout_s:.0f}s (last status={last_status or 'none'})",
            retryable=True,
            request_id=request_id,
            status=last_status,
        )
