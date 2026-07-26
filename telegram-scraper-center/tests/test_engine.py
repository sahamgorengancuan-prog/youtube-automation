"""Test end-to-end memakai klien simulasi.

Menjalankan pipeline penuh: connect → validasi → scrape → rencana → invite,
lalu memeriksa bahwa antrean habis, agent paralel benar-benar dipakai, dan
kegagalan tercatat dengan status yang tepat.

Semua test memakai *waktu virtual*: ``VirtualClock.sleep`` memajukan jam tanpa
menunggu sungguhan, jadi flood wait 120 detik selesai seketika tapi tetap
diperlakukan sebagai 120 detik oleh governor.
"""

from __future__ import annotations

import asyncio

import pytest

from tsc.models import AgentState, MemberStatus
from tsc.orchestrator import Engine
from tsc.telegram.simulator import SimulatorWorld


class VirtualClock:
    """Jam yang maju hanya ketika ada yang 'tidur'."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)
        await asyncio.sleep(0)  # beri giliran ke task lain


@pytest.fixture
def fast_config(config):
    """Konfigurasi yang cepat selesai: tanpa jeda, kuota longgar."""
    config.limits.min_gap_seconds = 0
    config.limits.gap_jitter_seconds = 0
    config.limits.invites_per_day_per_agent = 1000
    config.limits.invites_per_hour_per_agent = 1000
    config.limits.session_size = 40
    config.limits.min_batch_size = 5
    config.telegram.simulate_members_per_source = 120
    config.notifier.enabled = False
    config.llm.enabled = False
    return config


def make_engine(config, store, **world_kwargs) -> Engine:
    world = SimulatorWorld(
        seed=3,
        members_per_source=config.telegram.simulate_members_per_source,
        latency_range=(0.0, 0.0),
        **world_kwargs,
    )
    clock = VirtualClock()
    engine = Engine(config, store, world=world, clock=clock, sleep=clock.sleep)
    engine.virtual_clock = clock  # dipakai test untuk inspeksi
    return engine


async def prepare(engine: Engine) -> None:
    """Jalankan semua tahap sebelum eksekusi invite."""
    await engine.connect_all()
    report = await engine.validate()
    assert report.ok, report.blocking
    await engine.scrape_sources()
    engine.build_plan()


def test_pipeline_penuh_menghabiskan_antrean(fast_config, store):
    engine = make_engine(fast_config, store)
    asyncio.run(engine.run())

    assert engine.status.phase == "done", engine.status.error
    counts = store.status_breakdown(engine.campaign_id)

    # Tidak ada yang tertinggal di antrean.
    assert counts.get(MemberStatus.QUEUED.value, 0) == 0
    assert counts.get(MemberStatus.PENDING.value, 0) == 0
    # Ada yang benar-benar berhasil diundang.
    assert counts.get(MemberStatus.INVITED.value, 0) > 0
    # Kegagalan privasi tercatat terpisah, bukan dianggap error sistem.
    assert MemberStatus.BLOCKED_PRIVACY.value in counts


def test_semua_agent_ikut_bekerja(fast_config, store):
    engine = make_engine(fast_config, store)
    asyncio.run(engine.run())

    aktif = [a for a in engine.pool.agents if a.total_invited + a.total_failed > 0]
    # Beban dibagi ke banyak agent, bukan ditumpuk di satu akun.
    assert len(aktif) >= 4


def test_flood_wait_benar_benar_ditunggu(fast_config, store):
    """Jam virtual harus maju minimal sebesar flood wait yang diminta server."""
    engine = make_engine(fast_config, store)
    clock = engine.virtual_clock
    mulai = clock.now

    async def go():
        await prepare(engine)
        for client in engine.clients.values():
            client.flood_every = 5  # sering kena flood wait
        await engine._run_workers()

    asyncio.run(go())

    assert engine.world.flood_waits_raised > 0
    # Flood wait terkecil di simulator adalah 20 detik.
    assert clock.now - mulai >= 20


def test_peer_flood_memarkir_agent_dan_mengalihkan_batch(fast_config, store):
    """Agent yang kena limitasi harus di-skip; campaign tetap jalan."""
    engine = make_engine(fast_config, store)

    async def go():
        await prepare(engine)
        engine.clients["admin-1"].peer_flood_after = 3
        await engine._run_workers()

    asyncio.run(go())

    admin1 = next(a for a in engine.pool.agents if a.label == "admin-1")
    assert admin1.state in {AgentState.LIMITED, AgentState.DISABLED}
    assert engine.world.peer_floods_raised > 0
    # Agent lain tetap menyelesaikan pekerjaan.
    assert sum(a.total_invited for a in engine.pool.agents) > 0


def test_validasi_gagal_menghentikan_sebelum_invite(fast_config, store):
    """Kalau source bukan milik operator, tidak boleh ada satu pun invite."""
    engine = make_engine(fast_config, store, admin_labels=set())
    asyncio.run(engine.run())

    assert engine.status.phase == "error"
    assert engine.world.invite_calls == 0
    assert store.count_members(engine.campaign_id) == 0


def test_stop_menyimpan_progres_untuk_dilanjutkan(fast_config, store):
    engine = make_engine(fast_config, store)

    async def go():
        await prepare(engine)

        async def stop_after_beberapa_invite():
            while True:
                await asyncio.sleep(0)
                counts = store.status_breakdown(engine.campaign_id)
                if counts.get(MemberStatus.INVITED.value, 0) >= 10:
                    engine.request_stop()
                    return

        await asyncio.gather(engine._run_workers(), stop_after_beberapa_invite())

    asyncio.run(go())

    counts = store.status_breakdown(engine.campaign_id)
    # Sebagian sudah selesai, sisanya masih antre — tidak ada yang hilang.
    assert sum(counts.values()) == store.count_members(engine.campaign_id)
    assert counts.get(MemberStatus.INVITED.value, 0) >= 10
    assert counts.get(MemberStatus.QUEUED.value, 0) > 0


def test_lanjutan_setelah_stop_menghabiskan_sisa(fast_config, store):
    """Jalankan lagi dengan database yang sama: sisa antrean harus dikerjakan."""
    engine = make_engine(fast_config, store)

    async def go():
        await prepare(engine)

        async def stop_cepat():
            while True:
                await asyncio.sleep(0)
                if store.status_breakdown(engine.campaign_id).get("invited", 0) >= 5:
                    engine.request_stop()
                    return

        await asyncio.gather(engine._run_workers(), stop_cepat())

    asyncio.run(go())
    tersisa = store.status_breakdown(engine.campaign_id).get(MemberStatus.QUEUED.value, 0)
    assert tersisa > 0

    lanjutan = make_engine(fast_config, store)

    async def go2():
        await lanjutan.connect_all()
        await lanjutan.validate()
        await lanjutan._run_workers()

    asyncio.run(go2())
    akhir = store.status_breakdown(lanjutan.campaign_id)
    assert akhir.get(MemberStatus.QUEUED.value, 0) == 0


def test_snapshot_memuat_bagian_yang_dipakai_dashboard(fast_config, store):
    engine = make_engine(fast_config, store)
    asyncio.run(engine.run())
    snap = engine.snapshot()

    for key in (
        "campaign", "status", "members", "progress", "agents",
        "sources", "batches", "errors", "throughput_1h", "triage", "limits",
    ):
        assert key in snap, f"snapshot kehilangan '{key}'"
    assert 0.0 <= snap["progress"] <= 1.0
    assert len(snap["agents"]) == len(fast_config.agents)


def test_member_opt_out_tidak_pernah_diundang(fast_config, store):
    engine = make_engine(fast_config, store)

    async def go():
        await engine.connect_all()
        await engine.validate()
        await engine.scrape_sources()

    asyncio.run(go())

    korban = store.eligible_members(engine.campaign_id)[0]
    store.add_optout(engine.campaign_id, korban.user_id, "uji")
    engine.build_plan()

    asyncio.run(engine._run_workers())

    assert store.get_member(korban.id).status is not MemberStatus.INVITED
