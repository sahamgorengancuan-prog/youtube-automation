"""Test lapisan penyimpanan: member, batch, opt-out, dan taksonomi error."""

from __future__ import annotations

from tsc.models import BatchState, MemberStatus, Severity


def _campaign(store):
    return store.create_campaign("uji", "@target", {})


def _members(count, start=1000):
    return [
        {"user_id": start + i, "username": f"u{i}", "access_hash": i}
        for i in range(count)
    ]


def test_member_duplikat_lintas_source_diabaikan(store):
    campaign_id = _campaign(store)
    a = store.upsert_source(campaign_id, "@a", 0)
    b = store.upsert_source(campaign_id, "@b", 1)

    assert store.add_members(campaign_id, a, _members(10)) == 10
    # Source kedua berisi 5 orang yang sama + 5 orang baru.
    assert store.add_members(campaign_id, b, _members(10, start=1005)) == 5
    assert store.count_members(campaign_id) == 15


def test_bot_dan_akun_terhapus_tidak_masuk_antrean(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(
        campaign_id,
        source_id,
        [
            {"user_id": 1, "is_bot": True},
            {"user_id": 2, "is_deleted": True},
            {"user_id": 3},
        ],
    )
    eligible = store.eligible_members(campaign_id)
    assert [m.user_id for m in eligible] == [3]


def test_optout_mengeluarkan_member_dari_antrean(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(campaign_id, source_id, _members(5))
    assert len(store.eligible_members(campaign_id)) == 5

    store.add_optout(campaign_id, 1002, "diminta yang bersangkutan")

    remaining = store.eligible_members(campaign_id)
    assert 1002 not in [m.user_id for m in remaining]
    assert len(remaining) == 4
    assert store.count_optout(campaign_id) == 1


def test_batch_menandai_member_sebagai_queued(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(campaign_id, source_id, _members(6))
    agent_id = store.upsert_agent(campaign_id, "admin-1", "sessions/a1")
    member_ids = [m.id for m in store.eligible_members(campaign_id)]

    batch_id = store.create_batch(campaign_id, agent_id, 1, member_ids[:3])

    batch = store.load_batch(batch_id)
    assert batch is not None and batch.size == 3
    assert store.status_breakdown(campaign_id)[MemberStatus.QUEUED.value] == 3
    # Member yang sudah masuk batch tidak ikut terjaring lagi.
    assert len(store.eligible_members(campaign_id)) == 3


def test_pending_member_ids_menyusut_saat_selesai(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(campaign_id, source_id, _members(3))
    agent_id = store.upsert_agent(campaign_id, "admin-1", "sessions/a1")
    member_ids = [m.id for m in store.eligible_members(campaign_id)]
    batch_id = store.create_batch(campaign_id, agent_id, 1, member_ids)

    assert len(store.pending_member_ids(batch_id)) == 3
    store.set_member_status(member_ids[0], MemberStatus.INVITED)
    assert len(store.pending_member_ids(batch_id)) == 2


def test_clear_pending_batches_mengembalikan_member(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(campaign_id, source_id, _members(4))
    agent_id = store.upsert_agent(campaign_id, "admin-1", "sessions/a1")
    member_ids = [m.id for m in store.eligible_members(campaign_id)]
    store.create_batch(campaign_id, agent_id, 1, member_ids)

    assert store.clear_pending_batches(campaign_id) == 1
    assert len(store.eligible_members(campaign_id)) == 4


def test_batch_yang_sudah_jalan_tidak_ikut_dibersihkan(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(campaign_id, source_id, _members(4))
    agent_id = store.upsert_agent(campaign_id, "admin-1", "sessions/a1")
    member_ids = [m.id for m in store.eligible_members(campaign_id)]
    batch_id = store.create_batch(campaign_id, agent_id, 1, member_ids)
    store.set_batch_state(batch_id, BatchState.RUNNING)

    assert store.clear_pending_batches(campaign_id) == 0
    assert store.load_batch(batch_id).state is BatchState.RUNNING


def test_next_batch_mendahulukan_yang_sedang_berjalan(store):
    campaign_id = _campaign(store)
    source_id = store.upsert_source(campaign_id, "@a", 0)
    store.add_members(campaign_id, source_id, _members(4))
    agent_id = store.upsert_agent(campaign_id, "admin-1", "sessions/a1")
    ids = [m.id for m in store.eligible_members(campaign_id)]
    first = store.create_batch(campaign_id, agent_id, 1, ids[:2])
    second = store.create_batch(campaign_id, agent_id, 2, ids[2:])
    store.set_batch_state(second, BatchState.RUNNING)

    assert store.next_batch_for_agent(campaign_id, agent_id).id == second
    store.set_batch_state(second, BatchState.DONE)
    assert store.next_batch_for_agent(campaign_id, agent_id).id == first


def test_taksonomi_error_mengelompokkan_per_kode(store):
    campaign_id = _campaign(store)
    for code, times in [("privacy_restricted", 5), ("flood_wait", 2)]:
        for _ in range(times):
            store.record_attempt(
                campaign_id=campaign_id,
                member_id=1,
                agent_id=1,
                batch_id=None,
                result="error",
                error_code=code,
                detail=f"contoh {code}",
            )
    taxonomy = store.error_taxonomy(campaign_id)
    assert taxonomy[0]["error_code"] == "privacy_restricted"
    assert taxonomy[0]["n"] == 5
    assert {row["error_code"] for row in taxonomy} == {"privacy_restricted", "flood_wait"}


def test_throughput_menghitung_sukses_dan_gagal(store):
    campaign_id = _campaign(store)
    for result in ["ok", "ok", "error"]:
        store.record_attempt(
            campaign_id=campaign_id,
            member_id=1,
            agent_id=1,
            batch_id=None,
            result=result,
            error_code="" if result == "ok" else "rpc_error",
        )
    assert store.throughput(campaign_id) == {"ok": 2, "failed": 1}


def test_event_dapat_dibaca_bertahap(store):
    campaign_id = _campaign(store)
    ids = [
        store.log_event(
            campaign_id=campaign_id,
            severity=Severity.INFO,
            kind="uji",
            message=f"pesan {i}",
        )
        for i in range(5)
    ]
    lanjutan = store.events_since(ids[2])
    assert [e["message"] for e in lanjutan] == ["pesan 3", "pesan 4"]
    assert lanjutan[0]["data"] == {}


def test_prune_event_menyisakan_yang_terbaru(store):
    campaign_id = _campaign(store)
    for i in range(30):
        store.log_event(
            campaign_id=campaign_id, severity=Severity.DEBUG, kind="k", message=str(i)
        )
    assert store.prune_events(keep=10) == 20
    tersisa = store.recent_events(50)
    assert len(tersisa) == 10
    assert tersisa[-1]["message"] == "29"
