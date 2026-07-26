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
        "seed": seed,
        "width": width,
        "height": height,
        "output_format": output_format,
        "safety_tolerance": 2,
        "prompt_upsampling": False,
    }
    if reference_paths:
        payload["reference_images"] = list(reference_paths)
    return payload


class BFLAdapter:
    def __init__(self, api_key: str = "", transport: Transport | None = None, base_url: str = BASE_URL):
        self.api_key = api_key
        self.transport = transport
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {"x-key": self.api_key, "Content-Type": "application/json"}

    def _require_transport(self, stage: str) -> Transport:
        if self.transport is None:
            raise ProviderRequestError(
                "BFL transport not configured (offline mode performs no paid calls)",
                stage=stage,
            )
        if not self.api_key:
            raise ProviderRequestError("BFL_API_KEY missing", stage=stage)
        return self.transport

    def submit(self, model: str, payload: dict[str, Any]) -> str:
        transport = self._require_transport("bfl.submit")
        status, body = transport("POST", f"{self.base_url}/{model}", self._headers(), payload)
        if status != 200 or not isinstance(body, dict) or "id" not in body:
            raise ProviderSchemaError(f"unexpected BFL submit response (status {status})", stage="bfl.submit")
        return str(body["id"])

    def poll(self, request_id: str, timeout_s: float = 300.0, interval_s: float = 2.0, sleep=time.sleep) -> str:
        transport = self._require_transport("bfl.poll")
        waited = 0.0
        while waited <= timeout_s:
            status, body = transport(
                "GET", f"{self.base_url}/get_result?id={request_id}", self._headers(), None
            )
            if status != 200 or not isinstance(body, dict):
                raise ProviderSchemaError(f"unexpected BFL poll response (status {status})", stage="bfl.poll")
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
        request_id = self.submit(model, payload)
        sample_url = self.poll(request_id)
        return self.download(sample_url, out_path)
