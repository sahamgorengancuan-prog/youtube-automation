"""BFL adapter. ALL BFL request-schema details live here — nowhere else.

The adapter never makes a network call by itself: a `transport` callable
(method, url, headers, json_body) -> (status, dict|bytes) is injected. Offline
(transport=None) every call raises ProviderRequestError — explicit failure,
never a silent placeholder."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ..exceptions import AssetIntegrityError, ProviderRequestError, ProviderSchemaError
from ..filesystem import atomic_write_bytes

Transport = Callable[..., tuple[int, Any]]

BASE_URL = "https://api.bfl.ai/v1"
MIN_IMAGE_BYTES = 4096
SEED_MODULUS = 2_147_483_647  # BFL accepts 32-bit-range seeds


def build_payload(
    prompt: str,
    model: str,
    seed: int,
    width: int,
    height: int,
    reference_paths: list[str] | None = None,
    output_format: str = "png",
) -> dict[str, Any]:
    """Single place where the BFL request body shape is defined."""
    payload: dict[str, Any] = {
        "prompt": prompt,
        # BFL expects a 32-bit-range seed; our stable hash seeds are wider.
        "seed": int(seed) % SEED_MODULUS,
        "width": width,
        "height": height,
        "output_format": output_format,
        "safety_tolerance": 2,
        "prompt_upsampling": False,
    }
    if reference_paths:
        payload["reference_images"] = list(reference_paths)
    return payload


def _polling_target(polling_url: str, request_id: str, base_url: str) -> str:
    """BFL returns a REGION-SPECIFIC polling_url (e.g. https://api.us1.bfl.ai/...).
    Constructing `{base}/get_result?id=` instead yields 404, so always prefer the
    URL the API handed back, and carry the id as a query param when absent."""
    url = polling_url or f"{base_url}/get_result"
    if "id=" in url:
        return url
    return f"{url}{'&' if '?' in url else '?'}id={request_id}"


class BFLAdapter:
    def __init__(self, api_key: str = "", transport: Transport | None = None, base_url: str = BASE_URL):
        self.api_key = api_key
        self.transport = transport
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json", "x-key": self.api_key, "Content-Type": "application/json"}

    def _require_transport(self, stage: str) -> Transport:
        if self.transport is None:
            raise ProviderRequestError(
                "BFL transport not configured (offline mode performs no paid calls)",
                stage=stage,
            )
        if not self.api_key:
            raise ProviderRequestError("BFL_API_KEY missing", stage=stage)
        return self.transport

    def submit_job(self, model: str, payload: dict[str, Any]) -> dict[str, str]:
        """Returns {id, polling_url}. The polling_url is REGION-SPECIFIC and must
        be used verbatim for polling."""
        transport = self._require_transport("bfl.submit")
        url = f"{self.base_url}/{model}"
        status, body = transport("POST", url, self._headers(), payload)
        if status == 404:
            raise ProviderRequestError(
                f"BFL endpoint not found: {url} — check the model name "
                f"(e.g. 'flux-2-pro', 'flux-2-pro-preview', 'flux-2-flex')",
                stage="bfl.submit",
            )
        if status != 200 or not isinstance(body, dict) or "id" not in body:
            detail = str(body)[:200] if body is not None else ""
            raise ProviderSchemaError(
                f"unexpected BFL submit response (status {status}) from {url}: {detail}",
                stage="bfl.submit",
            )
        return {"id": str(body["id"]), "polling_url": str(body.get("polling_url", ""))}

    def submit(self, model: str, payload: dict[str, Any]) -> str:
        return self.submit_job(model, payload)["id"]

    def poll(self, request_id: str, timeout_s: float = 300.0, interval_s: float = 2.0,
             sleep=time.sleep, polling_url: str = "") -> str:
        transport = self._require_transport("bfl.poll")
        target = _polling_target(polling_url, request_id, self.base_url)
        waited = 0.0
        while waited <= timeout_s:
            status, body = transport("GET", target, self._headers(), None)
            if status == 404:
                raise ProviderRequestError(
                    f"BFL poll returned 404 for {target} — the API's region-specific "
                    "polling_url was not used or the request expired",
                    stage="bfl.poll",
                )
            if status != 200 or not isinstance(body, dict):
                raise ProviderSchemaError(
                    f"unexpected BFL poll response (status {status}) from {target}", stage="bfl.poll"
                )
            state = body.get("status", "")
            if state == "Ready":
                sample = (body.get("result") or {}).get("sample", "")
                if not sample:
                    raise ProviderSchemaError("BFL Ready result lacks sample url", stage="bfl.poll")
                return str(sample)
            if state in ("Error", "Content Moderated", "Request Moderated", "Task not found"):
                raise ProviderRequestError(f"BFL request failed: {state}", stage="bfl.poll")
            sleep(interval_s)
            waited += interval_s
        raise ProviderRequestError(f"BFL polling timed out after {timeout_s}s", stage="bfl.poll")

    def download(self, url: str, out_path: str | Path) -> Path:
        transport = self._require_transport("bfl.download")
        status, body = transport("GET", url, {}, None)
        if status != 200 or not isinstance(body, (bytes, bytearray)):
            raise ProviderRequestError(f"image download failed (status {status})", stage="bfl.download")
        if len(body) < MIN_IMAGE_BYTES:
            raise AssetIntegrityError(
                f"downloaded image suspiciously small ({len(body)} bytes)", stage="bfl.download"
            )
        return atomic_write_bytes(out_path, bytes(body))

    def generate(
        self,
        prompt: str,
        model: str,
        seed: int,
        width: int,
        height: int,
        out_path: str | Path,
        reference_paths: list[str] | None = None,
    ) -> Path:
        payload = build_payload(prompt, model, seed, width, height, reference_paths)
        job = self.submit_job(model, payload)
        sample_url = self.poll(job["id"], polling_url=job["polling_url"])
        return self.download(sample_url, out_path)
