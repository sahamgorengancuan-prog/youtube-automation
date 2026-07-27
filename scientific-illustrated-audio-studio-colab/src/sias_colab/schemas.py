"""Colab-layer schemas + full re-export of the engine's."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from sias.schemas import *  # noqa: F401,F403 (engine schemas are the contract)


class CanaryReport(BaseModel):
    status: str = "PENDING"  # PASS | FAIL | PARTIAL
    checks: dict[str, Any] = Field(default_factory=dict)
    request_ids: dict[str, str] = Field(default_factory=dict)
    cost_snapshot: dict[str, Any] = Field(default_factory=dict)
    higgsfield_included: bool = False
    notes: list[str] = Field(default_factory=list)
