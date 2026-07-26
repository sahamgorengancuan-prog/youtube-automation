"""Dynamic model resolver: fetch the catalog, filter image-input support and
family, rank by preferred terms. Selected IDs are recorded in the run manifest
instead of hard-coding a slug forever."""

from __future__ import annotations

from typing import Any

from ..exceptions import ProviderRequestError


def _supports_images(model: dict[str, Any]) -> bool:
    modality = str(model.get("architecture", {}).get("modality", "")) + " " + str(
        model.get("architecture", {}).get("input_modalities", "")
    )
    return "image" in modality.lower()


def resolve_model(
    catalog: list[dict[str, Any]],
    family_prefix: str,
    prefer_terms: list[str] | None = None,
) -> str:
    prefer_terms = prefer_terms or []
    candidates = [
        m for m in catalog if str(m.get("id", "")).startswith(family_prefix) and _supports_images(m)
    ]
    if not candidates:
        raise ProviderRequestError(
            f"no image-capable model found for family {family_prefix!r}", stage="model_resolver"
        )

    def rank(model: dict[str, Any]) -> tuple:
        mid = str(model.get("id", "")).lower()
        term_hits = sum(1 for t in prefer_terms if t.lower() in mid)
        return (-term_hits, mid)

    return str(sorted(candidates, key=rank)[0]["id"])
