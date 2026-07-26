"""Test gerbang validasi — termasuk penolakan source yang bukan milik operator."""

from __future__ import annotations

import asyncio

import pytest

from tsc.governor import GovernorPool
from tsc.guard import Guard
from tsc.store import Store
from tsc.telegram.simulator import SimulatedClient, SimulatorWorld


def build(config, store: Store, admin_labels=None):
    """Siapkan Guard dengan dunia simulasi yang bisa diatur siapa admin-nya."""
    campaign_id = store.create_campaign(
        config.campaign.name, config.campaign.target, config.to_dict()
    )
    world = SimulatorWorld(seed=1, members_per_source=50, admin_labels=admin_labels)

    clients = {}
    for agent_cfg in config.agents:
        store.upsert_agent(campaign_id, agent_cfg.label, agent_cfg.session)
        clients[agent_cfg.label] = SimulatedClient(
            agent_cfg.label, world, latency_range=(0, 0)
        )

    pool = GovernorPool(store.list_agents(campaign_id), config.limits)
    for governor in pool:
        governor.mark_verified(user_id=governor.agent.id, username=governor.agent.label)

    guard = Guard(config, store, campaign_id, clients, pool)
    return guard, pool, campaign_id


async def connect_all(guard):
    for client in guard.clients.values():
        await client.connect()


def run(coro):
    return asyncio.run(coro)


def test_semua_admin_maka_lolos(config, store):
    guard, _, _ = build(config, store)

    async def go():
        await connect_all(guard)
        return await guard.run()

    report = run(go())
    assert report.ok, report.blocking
    assert len(report.usable_agents) == len(config.agents)
    assert all(s.ok for s in report.sources)


def test_agent_bukan_admin_ditolak_dan_dinonaktifkan(config, store):
    admins = {a.label for a in config.agents} - {"admin-6"}
    guard, pool, _ = build(config, store, admin_labels=admins)

    async def go():
        await connect_all(guard)
        return await guard.run()

    report = run(go())
    ditolak = [a for a in report.agents if not a.ok]
    assert [a.label for a in ditolak] == ["admin-6"]
    assert pool[6].agent.state.is_terminal


def test_agent_lolos_kurang_dari_minimum_memblokir_campaign(config, store):
    # Hanya 2 agent yang admin, minimum harus > 5.
    guard, _, _ = build(config, store, admin_labels={"admin-1", "admin-2"})

    async def go():
        await connect_all(guard)
        return await guard.run()

    report = run(go())
    assert not report.ok
    assert any("agent lolos validasi" in reason for reason in report.blocking)


def test_source_tanpa_agent_admin_ditolak(config, store):
    """Inti guardrail: grup sumber yang tidak dikelola operator tidak diproses."""
    guard, _, campaign_id = build(config, store)

    # Buat seluruh agent bukan admin di source manapun, tapi tetap admin di target.
    target_ref = config.campaign.target

    async def only_target_admin(self, chat):
        from tsc.telegram.base import AdminRights

        is_target = chat.id == self.world.chat(target_ref).id
        return AdminRights(
            is_admin=is_target, is_creator=False, can_invite_users=is_target
        )

    for client in guard.clients.values():
        client.get_admin_rights = only_target_admin.__get__(client, type(client))

    async def go():
        await connect_all(guard)
        return await guard.run()

    report = run(go())
    assert not report.ok
    assert all(not s.ok for s in report.sources)
    assert any("admin di grup ini" in s.reason for s in report.sources)
    # Tidak ada source yang ditandai terverifikasi di database.
    assert all(row["admin_verified"] == 0 for row in store.list_sources(campaign_id))


def test_source_yang_lolos_tercatat_di_database(config, store):
    guard, _, campaign_id = build(config, store)

    async def go():
        await connect_all(guard)
        return await guard.run()

    run(go())
    sources = store.list_sources(campaign_id)
    assert len(sources) == 2
    assert all(row["admin_verified"] == 1 for row in sources)
    assert all(row["verified_by"] for row in sources)
    assert all(row["member_count"] > 0 for row in sources)


def test_agent_dilimitasi_tetap_lolos_hanya_diberi_peringatan(config, store):
    guard, pool, _ = build(config, store)
    pool[1].on_peer_flood()  # admin-1 sedang dilimitasi

    async def go():
        await connect_all(guard)
        return await guard.run()

    report = run(go())
    assert report.ok, report.blocking
    admin1 = next(a for a in report.agents if a.label == "admin-1")
    assert admin1.ok and admin1.limited
    assert any("dilimitasi" in w for w in report.warnings)
