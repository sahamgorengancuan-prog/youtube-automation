"""Fixture bersama. Seluruh test berjalan di mode simulasi — tanpa jaringan."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsc.config import (  # noqa: E402
    AgentConfig,
    CampaignConfig,
    Config,
    DashboardConfig,
    Limits,
    LLMConfig,
    NotifierConfig,
    TelegramConfig,
)
from tsc.store import Store  # noqa: E402


@pytest.fixture
def limits() -> Limits:
    return Limits(
        session_size=100,
        min_batch_size=10,
        max_batch_size=500,
        invites_per_day_per_agent=50,
        invites_per_hour_per_agent=20,
        min_gap_seconds=0,
        gap_jitter_seconds=0,
        peer_flood_park_hours=24,
        max_consecutive_failures=3,
    )


@pytest.fixture
def config(tmp_path, limits) -> Config:
    return Config(
        campaign=CampaignConfig(
            name="test", target="@target", sources=["@sumber_a", "@sumber_b"]
        ),
        agents=[
            AgentConfig(label=f"admin-{i}", session=f"sessions/admin{i}") for i in range(1, 7)
        ],
        telegram=TelegramConfig(mode="simulate", simulate_members_per_source=300),
        limits=limits,
        llm=LLMConfig(enabled=False),
        notifier=NotifierConfig(enabled=False),
        dashboard=DashboardConfig(open_browser=False),
        database=str(tmp_path / "test.db"),
    )


@pytest.fixture
def store(config) -> Store:
    store = Store(config.database)
    yield store
    store.close()


class FakeClock:
    """Jam yang bisa dimajukan manual — supaya test tidak perlu sleep."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
