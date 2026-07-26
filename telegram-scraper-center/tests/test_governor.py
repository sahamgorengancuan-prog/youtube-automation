"""Test rate governor: kuota, jeda, flood wait, dan limitasi agent.

Semua memakai jam palsu agar tidak ada ``sleep`` sungguhan.
"""

from __future__ import annotations

import random

import pytest

from tsc.governor import DAY, HOUR, AgentGovernor, GovernorPool
from tsc.models import Agent, AgentState


def make_governor(limits, clock, **overrides) -> AgentGovernor:
    agent = Agent(id=1, campaign_id=1, label="admin-1", session="s", **overrides)
    governor = AgentGovernor(agent, limits, clock=clock, rng=random.Random(0))
    governor.mark_verified(user_id=999, username="admin1")
    return governor


def test_agent_belum_divalidasi_ditolak(limits, clock):
    agent = Agent(id=1, campaign_id=1, label="a", session="s")
    governor = AgentGovernor(agent, limits, clock=clock)
    permit = governor.check()
    assert not permit.allowed
    assert permit.blocked_forever


def test_agent_siap_boleh_jalan(limits, clock):
    assert make_governor(limits, clock).check().allowed


def test_sukses_menambah_kuota_dan_memasang_jeda(limits, clock):
    limits.min_gap_seconds = 30
    governor = make_governor(limits, clock)
    governor.on_success()

    assert governor.agent.daily_used == 1
    assert governor.agent.total_invited == 1
    assert governor.agent.state is AgentState.COOLDOWN

    permit = governor.check()
    assert not permit.allowed
    assert permit.wait_seconds == pytest.approx(30, abs=1)

    clock.advance(31)
    assert governor.check().allowed


def test_kuota_harian_menghentikan_agent(limits, clock):
    governor = make_governor(limits, clock)
    for _ in range(limits.invites_per_day_per_agent):
        governor.on_success()
        clock.advance(1)

    permit = governor.check()
    assert not permit.allowed
    assert governor.agent.state is AgentState.QUOTA
    assert governor.remaining_today() == 0


def test_kuota_harian_pulih_setelah_24_jam(limits, clock):
    governor = make_governor(limits, clock)
    for _ in range(limits.invites_per_day_per_agent):
        governor.on_success()
    assert governor.agent.state is AgentState.QUOTA

    clock.advance(DAY + 1)
    assert governor.check().allowed
    assert governor.agent.daily_used == 0


def test_kuota_per_jam_memasang_jeda_bukan_mematikan(limits, clock):
    governor = make_governor(limits, clock)
    for _ in range(limits.invites_per_hour_per_agent):
        governor.on_success()

    permit = governor.check()
    assert not permit.allowed
    assert not permit.blocked_forever
    assert governor.agent.state is not AgentState.QUOTA

    clock.advance(HOUR + 1)
    assert governor.check().allowed


def test_flood_wait_dihormati_penuh_plus_margin(limits, clock):
    limits.flood_wait_margin_seconds = 15
    governor = make_governor(limits, clock)
    governor.on_flood_wait(120)

    assert governor.agent.state is AgentState.FLOOD_WAIT
    permit = governor.check()
    # Tidak boleh lebih pendek dari yang diminta server.
    assert permit.wait_seconds >= 120
    assert permit.wait_seconds == pytest.approx(135, abs=1)

    clock.advance(134)
    assert not governor.check().allowed
    clock.advance(2)
    assert governor.check().allowed


def test_peer_flood_memarkir_agent_lama(limits, clock):
    governor = make_governor(limits, clock)
    governor.on_peer_flood()

    assert governor.agent.state is AgentState.LIMITED
    assert governor.agent.state.is_skippable
    assert governor.check().wait_seconds == pytest.approx(
        limits.peer_flood_park_hours * HOUR, abs=2
    )


def test_gagal_beruntun_memicu_backoff(limits, clock):
    governor = make_governor(limits, clock)
    for _ in range(limits.max_consecutive_failures):
        governor.on_member_error("privacy_restricted")
        clock.advance(1)
    assert governor.agent.state is AgentState.LIMITED
    assert governor.agent.consecutive_failures >= limits.max_consecutive_failures


def test_sukses_mereset_hitungan_gagal(limits, clock):
    governor = make_governor(limits, clock)
    governor.on_member_error("privacy_restricted")
    governor.on_member_error("privacy_restricted")
    governor.on_success()
    assert governor.agent.consecutive_failures == 0


def test_jeda_manual_dan_lanjut(limits, clock):
    governor = make_governor(limits, clock)
    governor.pause()
    assert governor.check().blocked_forever
    governor.resume()
    assert governor.check().allowed


def test_resume_tidak_menghapus_flood_wait(limits, clock):
    """Operator tidak boleh bisa mengakali limit server lewat tombol resume."""
    governor = make_governor(limits, clock)
    governor.on_flood_wait(300)
    governor.resume()
    permit = governor.check()
    assert not permit.allowed
    assert permit.wait_seconds > 250


def test_agent_dinonaktifkan_tetap_mati(limits, clock):
    governor = make_governor(limits, clock)
    governor.disable("sesi kedaluwarsa")
    governor.resume()
    assert governor.agent.state is AgentState.DISABLED
    assert governor.check().blocked_forever


def test_pool_menyaring_agent_yang_di_skip(limits, clock):
    agents = [Agent(id=i, campaign_id=1, label=f"a{i}", session="s") for i in range(1, 4)]
    pool = GovernorPool(agents, limits, clock=clock)
    for governor in pool:
        governor.mark_verified(1, "x")

    pool[2].on_peer_flood()
    pool[3].pause()

    usable = pool.usable()
    assert [g.agent.id for g in usable] == [1]
    assert len(pool) == 3
