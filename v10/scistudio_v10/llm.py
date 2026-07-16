"""Provider router for reasoning (OpenAI GPT) and image generation (BFL FLUX
Kontext), with execution-mode-aware provider locking.

Production boundary
-------------------
* ``production`` mode: OpenAI GPT is the **only** text/vision provider and
  BFL FLUX Kontext the **only** image provider. When they are unavailable or
  exhausted, calls raise :class:`ProviderUnavailableError` — the pipeline
  fails clearly instead of silently degrading to Gemini, OpenRouter, local
  models, OpenAI Images or deterministic fallbacks.
* ``development`` / ``test`` modes: alternative providers stay available for
  experimentation, and deterministic fallbacks are permitted — but fallback
  results are cached in a separate short-lived namespace and marked as
  fallbacks; they never masquerade as live provider output.

Reliability
-----------
Every remote call runs under a bounded retry policy (exponential backoff with
jitter; 408/409/429/5xx and connection errors retry, auth/validation errors
do not). Malformed JSON from OpenAI triggers a bounded repair round-trip.
Request IDs, token usage and latency are captured into the event log when an
event logger is attached. Secrets are registered for global redaction and
never logged.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

from .errors import ProviderError, ProviderUnavailableError
from .http_safety import call_with_retries
from .security import redacted_exception_text, register_secret
from .utils import ensure_dir, extract_json, hash_value, load_json, save_json, sha256_file

CACHE_SCHEMA_VERSION = 5
FALLBACK_NAMESPACE = "_fallback"
FALLBACK_MARKER = "__scistudio_fallback__"

_PRODUCTION_TEXT_PROVIDERS = ("openai",)
_KNOWN_TEXT_PROVIDERS = ("openai", "gemini", "openrouter", "local")
_KNOWN_VISION_PROVIDERS = ("openai", "gemini", "openrouter")


class LLMRouter:
    """Lazy, execution-mode-aware provider router.

    Local checkpoints are never loaded when a remote provider succeeds,
    preventing multi-GB downloads in remote-provider runs.
    """

    def __init__(
        self,
        config: dict[str, Any],
        secrets: dict[str, Any],
        cache_root: str | Path,
        *,
        event_logger: Any | None = None,
    ):
        self.config = config
        self.secrets = secrets
        self.cache_root = ensure_dir(cache_root)
        self.events = event_logger
        self._local = None
        self._local_tokenizer = None
        self._gemini_client = None
        self._openai_client = None
        self._openrouter_client = None
        self._bfl_client = None
        for name in ("OPENAI_API_KEY", "BFL_API_KEY", "BFL_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN"):
            register_secret(self._secret(name))

    # -- mode & provider selection ----------------------------------------
    @property
    def execution_mode(self) -> str:
        return str(self.config.get("execution_mode", "development")).lower()

    @property
    def is_production(self) -> bool:
        return self.execution_mode == "production"

    @property
    def provider_order(self) -> list[str]:
        order = self.config.get("provider_order", ["openai"])
        if isinstance(order, str):
            order = [item.strip() for item in order.split(",") if item.strip()]
        order = [str(item).lower() for item in order]
        if self.is_production:
            # Hard runtime lock, independent of config validation.
            order = [item for item in order if item in _PRODUCTION_TEXT_PROVIDERS] or ["openai"]
        return list(order)

    @property
    def vision_provider_order(self) -> list[str]:
        order = self.config.get("vision_provider_order")
        if order is None:
            order = self.provider_order
        if isinstance(order, str):
            order = [item.strip() for item in order.split(",") if item.strip()]
        order = [str(item).lower() for item in order]
        if self.is_production:
            order = [item for item in order if item in _PRODUCTION_TEXT_PROVIDERS] or ["openai"]
        return [item for item in order if item in _KNOWN_VISION_PROVIDERS]

    def _secret(self, *names: str) -> str:
        for name in names:
            value = self.secrets.get(name) or os.environ.get(name)
            if value:
                return value
        return ""

    def available(self, provider: str) -> bool:
        provider = provider.lower()
        if self.is_production and provider not in _PRODUCTION_TEXT_PROVIDERS:
            return False
        if provider == "gemini":
            return bool(self._secret("GEMINI_API_KEY"))
        if provider == "openai":
            return bool(self._secret("OPENAI_API_KEY"))
        if provider == "openrouter":
            return bool(self._secret("OPENROUTER_API_KEY"))
        if provider == "local":
            return bool(self.config.get("enable_local_fallback", False))
        return False

    def available_bfl(self) -> bool:
        return bool(self._secret("BFL_API_KEY", "BFL_KEY"))

    # -- observability ------------------------------------------------------
    def _emit(self, event: str, **payload: Any) -> None:
        if self.events is not None:
            try:
                self.events.emit(event, **payload)
            except Exception:
                pass

    def _retry_kwargs(self) -> dict[str, Any]:
        retry_cfg = self.config.get("retry") or {}
        return {
            "max_attempts": int(retry_cfg.get("max_attempts", 4)),
            "base_delay": float(retry_cfg.get("base_delay_s", 1.0)),
            "max_delay": float(retry_cfg.get("max_delay_s", 30.0)),
            "jitter": float(retry_cfg.get("jitter", 0.25)),
        }

    # -- fallback cache (short-lived, clearly marked) ------------------------
    def _fallback_cache_path(self, namespace: str, key: str) -> Path:
        return self.cache_root / FALLBACK_NAMESPACE / namespace / f"{key}.json"

    def _load_fresh_fallback(self, namespace: str, key: str) -> tuple[bool, Any]:
        path = self._fallback_cache_path(namespace, key)
        envelope = load_json(path)
        if not isinstance(envelope, dict) or not envelope.get(FALLBACK_MARKER):
            return False, None
        ttl = float(self.config.get("fallback_cache_ttl_s", 3600.0))
        if ttl > 0 and (time.time() - float(envelope.get("cached_at", 0))) > ttl:
            return False, None
        return True, envelope.get("value")

    def _store_fallback(self, namespace: str, key: str, value: Any, reasons: list[str]) -> None:
        save_json(
            self._fallback_cache_path(namespace, key),
            {
                FALLBACK_MARKER: True,
                "reason": [
                    redacted_exception_text(RuntimeError(r), 300) if not isinstance(r, str) else r for r in reasons[-3:]
                ],
                "cached_at": time.time(),
                "value": value,
            },
        )

    # -- text JSON generation -------------------------------------------------
    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        namespace: str,
        fallback: Any,
        json_schema: dict[str, Any] | None = None,
        force: bool = False,
        temperature: float | None = None,
    ) -> Any:
        key = hash_value(
            {
                "order": self.provider_order,
                "models": {
                    "openai": self.config.get("openai_model", "gpt-5-mini"),
                    "gemini": self.config.get("gemini_model", ""),
                    "openrouter": self.config.get("openrouter_model", ""),
                    "local": self.config.get("local_model", ""),
                },
                "system": system,
                "prompt": prompt,
                "schema": json_schema,
                "temperature": temperature,
                "effort": self.config.get("openai_reasoning_effort", "low"),
                "max_output_tokens": self.config.get("openai_max_output_tokens", 8000),
                "cache_schema": CACHE_SCHEMA_VERSION,
            },
            32,
        )
        cache_path = self.cache_root / namespace / f"{key}.json"
        if cache_path.exists() and not force:
            cached = load_json(cache_path, None)
            if cached is not None:
                return cached
        if not force:
            fresh, value = self._load_fresh_fallback(namespace, key)
            if fresh:
                return value

        errors: list[str] = []
        prompt_hash = hash_value(prompt, 24)
        for provider in self.provider_order:
            if not self.available(provider):
                continue
            started = time.perf_counter()
            try:
                output = self._call_text_provider(provider, system, prompt, json_schema, temperature)
                parsed = extract_json(output, fallback=None)
                if parsed is None and provider == "openai":
                    parsed = self._openai_json_repair(system, prompt, output, temperature)
                if parsed is not None:
                    save_json(cache_path, parsed)
                    self._emit(
                        "llm.completed",
                        provider=provider,
                        namespace=namespace,
                        prompt_hash=prompt_hash,
                        latency_s=round(time.perf_counter() - started, 3),
                        request_id=getattr(self, "_last_request_id", ""),
                        usage=getattr(self, "_last_usage", {}),
                        status="ok",
                    )
                    return parsed
                errors.append(f"{provider}: returned non-JSON output")
            except Exception as exc:
                errors.append(f"{provider}: {redacted_exception_text(exc, 400)}")
                self._emit(
                    "llm.failed",
                    provider=provider,
                    namespace=namespace,
                    prompt_hash=prompt_hash,
                    latency_s=round(time.perf_counter() - started, 3),
                    error_class=type(exc).__name__,
                )

        if self.is_production:
            raise ProviderUnavailableError(
                "Production reasoning provider (OpenAI) unavailable or exhausted; "
                "refusing silent fallback. Errors: " + (" | ".join(errors) or "no provider configured"),
                provider="openai",
            )
        value = fallback() if callable(fallback) else fallback
        self._store_fallback(namespace, key, value, errors or ["no provider configured"])
        if errors:
            self._emit("llm.fallback", namespace=namespace, prompt_hash=prompt_hash, reasons=errors[-3:])
        return value

    def _call_text_provider(
        self, provider: str, system: str, prompt: str, schema: dict[str, Any] | None, temperature: float | None
    ) -> str:
        retry_kwargs = self._retry_kwargs()
        if provider == "openai":
            return call_with_retries(lambda: self._openai_json(system, prompt, temperature), **retry_kwargs)
        if provider == "gemini":
            return call_with_retries(lambda: self._gemini_json(system, prompt, schema, temperature), **retry_kwargs)
        if provider == "openrouter":
            return call_with_retries(lambda: self._openrouter_json(system, prompt, temperature), **retry_kwargs)
        if provider == "local":
            return self._local_json(system, prompt, temperature)
        raise ProviderError(f"Unknown text provider {provider!r}", provider=provider)

    def _openai_json_repair(self, system: str, prompt: str, bad_output: str, temperature: float | None) -> Any:
        """Bounded repair loop for malformed OpenAI JSON output."""
        attempts = int(self.config.get("openai_json_repair_attempts", 1))
        for _ in range(max(0, attempts)):
            repair_prompt = (
                prompt
                + "\n\nYour previous output was not valid JSON. Previous output (truncated):\n"
                + str(bad_output)[:2000]
                + "\nReturn ONLY the corrected, complete JSON object."
            )
            try:
                output = call_with_retries(
                    lambda rp=repair_prompt: self._openai_json(system, rp, temperature),
                    **self._retry_kwargs(),
                )
            except Exception:
                return None
            parsed = extract_json(output, fallback=None)
            if parsed is not None:
                return parsed
            bad_output = output
        return None

    # -- vision critique -------------------------------------------------------
    def critique_image(
        self,
        *,
        image_path: str | Path,
        prompt: str,
        namespace: str = "vision_critic",
        fallback: Any = None,
        force: bool = False,
    ) -> Any:
        image_path = Path(image_path)
        # Cache key uses the image *content* hash — never filename + size.
        content_hash = sha256_file(image_path) if image_path.exists() else ""
        key = hash_value(
            {
                "image_sha256": content_hash,
                "prompt": prompt,
                "order": self.vision_provider_order,
                "models": {
                    "openai": self.config.get("openai_vision_model", self.config.get("openai_model", "gpt-5-mini")),
                    "gemini": self.config.get("gemini_vision_model", ""),
                    "openrouter": self.config.get("openrouter_vision_model", ""),
                },
                "cache_schema": CACHE_SCHEMA_VERSION,
            },
            32,
        )
        cache_path = self.cache_root / namespace / f"{key}.json"
        if cache_path.exists() and not force:
            cached = load_json(cache_path, None)
            if cached is not None:
                return cached

        errors: list[str] = []
        retry_kwargs = self._retry_kwargs()
        for provider in self.vision_provider_order:
            if not self.available(provider):
                continue
            try:
                if provider == "gemini":
                    output = call_with_retries(lambda: self._gemini_vision(image_path, prompt), **retry_kwargs)
                elif provider == "openrouter":
                    output = call_with_retries(lambda: self._openrouter_vision(image_path, prompt), **retry_kwargs)
                else:
                    output = call_with_retries(lambda: self._openai_vision(image_path, prompt), **retry_kwargs)
                parsed = extract_json(output, fallback=None)
                if parsed is not None:
                    save_json(cache_path, parsed)
                    return parsed
                errors.append(f"{provider}: non-JSON vision response")
            except Exception as exc:
                errors.append(f"{provider}: {redacted_exception_text(exc, 400)}")

        if self.is_production:
            raise ProviderUnavailableError(
                "Production vision provider (OpenAI) unavailable or exhausted; "
                "refusing unreviewed approval. Errors: " + (" | ".join(errors) or "no provider configured"),
                provider="openai",
            )
        if errors:
            self._emit("llm.vision_fallback", namespace=namespace, reasons=errors[-2:])
        return fallback() if callable(fallback) else fallback

    # -- image generation -------------------------------------------------------
    def generate_reference_image(
        self,
        *,
        prompt: str,
        output_path: str | Path,
        init_image: str | Path | None = None,
        force: bool = False,
    ) -> Path | None:
        """Generate a raster image.

        Production: BFL FLUX Kontext exclusively; failure raises.
        Development/test: BFL first, then explicitly configured alternates.
        """
        output_path = Path(output_path)
        ensure_dir(output_path.parent)
        min_bytes = int(self.config.get("min_valid_image_bytes", 1024))
        if output_path.exists() and output_path.stat().st_size > min_bytes and not force:
            return output_path

        if self.is_production:
            if str(self.config.get("image_provider", "bfl")) != "bfl":
                raise ProviderUnavailableError("Production image provider must be BFL FLUX Kontext", provider="bfl")
            if not self.available_bfl():
                raise ProviderUnavailableError(
                    "BFL_API_KEY is not configured; production image generation "
                    "cannot proceed (no fallback is authorized)",
                    provider="bfl",
                )
            return self._bfl_flux_image(prompt, output_path, init_image=init_image, force=force)

        errors: list[str] = []
        if self.config.get("image_provider", "bfl") == "bfl" and self.available_bfl():
            try:
                return self._bfl_flux_image(prompt, output_path, init_image=init_image, force=force)
            except Exception as exc:
                errors.append(f"bfl: {redacted_exception_text(exc, 300)}")
        alternate = self._development_alternate_image(prompt, output_path, errors)
        if alternate is not None:
            return alternate
        if errors:
            self._emit("image.unavailable", reasons=errors[-3:])
        return None

    def _development_alternate_image(self, prompt: str, output_path: Path, errors: list[str]) -> Path | None:
        """Non-production alternates; each requires explicit configuration."""
        gemini_model = self.config.get("gemini_image_model")
        if gemini_model and self.available("gemini"):
            try:
                from google.genai import types

                response = self._gemini().models.generate_images(
                    model=gemini_model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(number_of_images=1),
                )
                generated = getattr(response, "generated_images", None) or []
                if generated:
                    image = getattr(generated[0], "image", generated[0])
                    data = getattr(image, "image_bytes", None) or getattr(image, "bytes", None)
                    if data:
                        from .utils import atomic_write_bytes

                        atomic_write_bytes(output_path, data)
                        return output_path
            except Exception as exc:
                errors.append(f"gemini image: {redacted_exception_text(exc, 300)}")
        openai_model = self.config.get("openai_image_model")
        if openai_model and self.available("openai"):
            try:
                image_kwargs = {
                    "model": openai_model,
                    "prompt": prompt,
                    "size": self.config.get("openai_image_size", "1024x1024"),
                }
                if "dall-e" in str(openai_model).lower():
                    image_kwargs["response_format"] = "b64_json"
                response = self._openai().images.generate(**image_kwargs)
                item = response.data[0]
                encoded = getattr(item, "b64_json", None)
                if encoded:
                    from .utils import atomic_write_bytes

                    atomic_write_bytes(output_path, base64.b64decode(encoded))
                    return output_path
            except Exception as exc:
                errors.append(f"openai image: {redacted_exception_text(exc, 300)}")
        local_model = self.config.get("local_image_model")
        if local_model and self.config.get("enable_local_image_generation", False):
            local = self._flux_image(prompt, output_path, local_model)
            if local is not None:
                return local
            errors.append("local image: pipeline unavailable or failed")
        return None

    def _bfl(self):
        if self._bfl_client is None:
            from .bfl_client import BFLClient

            self._bfl_client = BFLClient(
                self.config,
                self._secret("BFL_API_KEY", "BFL_KEY"),
                self.cache_root,
                event_logger=self.events,
            )
        return self._bfl_client

    def _bfl_flux_image(
        self,
        prompt: str,
        output_path: Path,
        *,
        init_image: str | Path | None = None,
        force: bool = False,
    ) -> Path | None:
        """BFL FLUX Kontext generation via the hardened client.

        In production a failure raises; in development it returns ``None`` so
        explicitly configured alternates can be tried.
        """
        # Per-call overrides (aspect/seed/format) may have been set on config
        # by the studio; the client reads them at construction, so rebuild when
        # the relevant knobs changed.
        self._bfl_client = None
        try:
            return self._bfl().generate(prompt, output_path, init_image=init_image, force=force)
        except Exception:
            if self.is_production:
                raise
            self._emit("bfl.failed_development", prompt_hash=hash_value(prompt, 24))
            return None

    def _flux_image(self, prompt: str, output_path: Path, model: str) -> Path | None:
        """Optional local FLUX pipeline (development only; needs a large GPU)."""
        try:
            import torch

            dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            steps = int(self.config.get("flux_steps", self.config.get("local_image_steps", 28)))
            guidance = float(self.config.get("flux_guidance", 2.5))
            token = self.secrets.get("HF_TOKEN") or os.environ.get("HF_TOKEN") or None
            init_path = self.config.get("flux_init_image")
            is_kontext = "kontext" in str(model).lower()
            if is_kontext and init_path and Path(init_path).exists():
                from diffusers import FluxKontextPipeline
                from diffusers.utils import load_image

                pipe = FluxKontextPipeline.from_pretrained(model, torch_dtype=dtype, token=token)
                if torch.cuda.is_available():
                    pipe.enable_model_cpu_offload()
                image = pipe(
                    image=load_image(str(init_path)),
                    prompt=prompt,
                    guidance_scale=guidance,
                    num_inference_steps=steps,
                ).images[0]
            else:
                from diffusers import FluxPipeline

                base = "black-forest-labs/FLUX.1-dev" if is_kontext else model
                pipe = FluxPipeline.from_pretrained(base, torch_dtype=dtype, token=token)
                if torch.cuda.is_available():
                    pipe.enable_model_cpu_offload()
                image = pipe(
                    prompt=prompt,
                    guidance_scale=guidance,
                    num_inference_steps=steps,
                    height=int(self.config.get("local_image_height", 1024)),
                    width=int(self.config.get("local_image_width", 1024)),
                ).images[0]
            image.save(output_path)
            del pipe
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            return output_path
        except Exception as exc:
            self._emit("flux_local.failed", error_class=type(exc).__name__)
            return None

    # -- provider clients -------------------------------------------------------
    def _gemini(self):
        if self._gemini_client is None:
            from google import genai

            api_key = self._secret("GEMINI_API_KEY")
            self._gemini_client = genai.Client(api_key=api_key)
        return self._gemini_client

    def _gemini_json(self, system: str, prompt: str, schema: dict[str, Any] | None, temperature: float | None):
        from google.genai import types

        model = self.config.get("gemini_model", "gemini-2.5-flash")
        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": self.config.get("temperature", 0.75) if temperature is None else temperature,
            "response_mime_type": "application/json",
        }
        if schema:
            kwargs["response_json_schema"] = schema
        response = self._gemini().models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(**kwargs),
        )
        return response.text

    def _gemini_vision(self, image_path: Path, prompt: str):
        from google.genai import types

        model = self.config.get("gemini_vision_model", self.config.get("gemini_model", "gemini-2.5-flash"))
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        response = self._gemini().models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime), prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.25),
        )
        return response.text

    def _openai(self):
        if self._openai_client is None:
            from openai import OpenAI

            api_key = self._secret("OPENAI_API_KEY")
            # Timeouts are enforced client-side; retries are handled by the
            # router's classified retry policy, so the SDK's own retry is off.
            self._openai_client = OpenAI(
                api_key=api_key,
                timeout=float(self.config.get("openai_read_timeout_s", 180.0)),
                max_retries=0,
            )
        return self._openai_client

    @staticmethod
    def _reasoning_kwargs(model: str, config: dict) -> dict:
        # gpt-5 / o-series are reasoning models: cap effort (default low) so
        # reasoning tokens don't consume the whole budget and leave empty
        # output. Non-reasoning models ignore this.
        low = model.lower()
        if low.startswith(("gpt-5", "o1", "o3", "o4")) or "gpt-5" in low:
            return {"reasoning": {"effort": config.get("openai_reasoning_effort", "low")}}
        return {}

    def _capture_response_meta(self, response) -> None:
        self._last_request_id = str(getattr(response, "id", "") or "")
        usage = getattr(response, "usage", None)
        self._last_usage = {}
        if usage is not None:
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                value = getattr(usage, field, None)
                if value is not None:
                    self._last_usage[field] = value

    @staticmethod
    def _responses_text(response) -> str:
        # output_text can be empty on reasoning models; fall back to walking items.
        text = getattr(response, "output_text", None)
        if text:
            return text
        parts: list[str] = []
        for item in getattr(response, "output", None) or []:
            for content in getattr(item, "content", None) or []:
                value = getattr(content, "text", None)
                if isinstance(value, str) and value:
                    parts.append(value)
        return "".join(parts)

    def _openai_json(self, system: str, prompt: str, temperature: float | None):
        model = self.config.get("openai_model", "gpt-5-mini")
        response = self._openai().responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt + "\nReturn a single valid JSON object only."}],
                },
            ],
            text={"format": {"type": "json_object"}},
            max_output_tokens=int(self.config.get("openai_max_output_tokens", 8000)),
            **self._reasoning_kwargs(model, self.config),
        )
        self._capture_response_meta(response)
        text = self._responses_text(response)
        if not text:
            raise ProviderError(
                f"OpenAI returned empty output (request_id={self._last_request_id})",
                provider="openai",
                retryable=True,
                request_id=self._last_request_id,
            )
        return text

    def _openai_vision(self, image_path: Path, prompt: str):
        model = self.config.get("openai_vision_model", self.config.get("openai_model", "gpt-5-mini"))
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        data_url = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"
        response = self._openai().responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt + "\nReturn a single valid JSON object only."},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            text={"format": {"type": "json_object"}},
            max_output_tokens=int(self.config.get("openai_max_output_tokens", 8000)),
            **self._reasoning_kwargs(model, self.config),
        )
        self._capture_response_meta(response)
        return self._responses_text(response)

    def _openrouter(self):
        # OpenRouter is OpenAI-API compatible; reuse the OpenAI SDK with its base_url.
        if getattr(self, "_openrouter_client", None) is None:
            from openai import OpenAI

            self._openrouter_client = OpenAI(
                api_key=self._secret("OPENROUTER_API_KEY"),
                base_url=self.config.get("openrouter_base_url", "https://openrouter.ai/api/v1"),
                default_headers={
                    "HTTP-Referer": self.config.get("openrouter_referer", "https://scientific-motion-studio.local"),
                    "X-Title": "Scientific Motion Studio V10",
                },
                max_retries=0,
            )
        return self._openrouter_client

    def _openrouter_json(self, system: str, prompt: str, temperature: float | None):
        model = self.config.get("openrouter_model", "meta-llama/llama-3.3-70b-instruct:free")
        response = self._openrouter().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt + "\nReturn valid JSON only, no Markdown."},
            ],
            temperature=self.config.get("temperature", 0.7) if temperature is None else temperature,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def _openrouter_vision(self, image_path: Path, prompt: str):
        model = self.config.get("openrouter_vision_model", "meta-llama/llama-3.2-11b-vision-instruct:free")
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        data_url = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"
        response = self._openrouter().chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt + "\nReturn valid JSON only."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        return response.choices[0].message.content

    def _load_local(self):
        if self._local is not None:
            return self._local_tokenizer, self._local
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        model_name = self.config.get("local_model", "Qwen/Qwen2.5-3B-Instruct")
        token = self.secrets.get("HF_TOKEN") or os.environ.get("HF_TOKEN") or None
        quantization_config = None
        if torch.cuda.is_available() and self.config.get("local_4bit", True):
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        tokenizer = AutoTokenizer.from_pretrained(model_name, token=token)
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            token=token,
            device_map="auto" if torch.cuda.is_available() else None,
            torch_dtype="auto",
            quantization_config=quantization_config,
            low_cpu_mem_usage=True,
        )
        self._local_tokenizer, self._local = tokenizer, model
        return tokenizer, model

    def _local_json(self, system: str, prompt: str, temperature: float | None):
        import torch

        tokenizer, model = self._load_local()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt + "\nReturn valid JSON only, without Markdown."},
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt")
        device = next(model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=int(self.config.get("local_max_new_tokens", 2600)),
                do_sample=True,
                temperature=float(self.config.get("temperature", 0.7) if temperature is None else temperature),
                top_p=0.92,
                repetition_penalty=1.04,
            )
        output = generated[0, inputs["input_ids"].shape[1] :]
        return tokenizer.decode(output, skip_special_tokens=True)
