"""Test perencanaan batch: pembagian rata, ukuran sesi, dan kapasitas agent."""

from __future__ import annotations

import pytest

from tsc.batching import (
    choose_batch_size,
    deal_round_robin,
    interleave_by_source,
    plan_batches,
)


def test_batch_size_mengikuti_preferensi_operator(limits):
    # 6 agent × 100 member = jatah per agent tepat 100 = session_size.
    assert choose_batch_size(600, 6, limits) == 100


def test_batch_size_tidak_melebihi_jatah_per_agent(limits):
    # Hanya 60 member untuk 6 agent → 10 per agent, jangan bikin batch 100.
    assert choose_batch_size(60, 6, limits) == 10


def test_batch_size_diratakan_agar_tidak_ada_ekor_kecil(limits):
    # 6 agent × 105 member: batch 100 akan menyisakan ekor 5 (terlalu kecil),
    # jadi ukurannya diratakan menjadi dua sesi seimbang.
    size = choose_batch_size(630, 6, limits)
    assert size < 100
    assert size >= limits.min_batch_size


def test_batch_size_hormati_batas_minimum(limits):
    # 6 member untuk 6 agent → 1 per agent, tapi min_batch_size = 10.
    assert choose_batch_size(6, 6, limits) == 1  # dibatasi jatah per agent


def test_round_robin_membagi_rata():
    result = deal_round_robin(list(range(100)), [1, 2, 3, 4])
    sizes = sorted(len(v) for v in result.values())
    assert sizes == [25, 25, 25, 25]
    # Tidak ada member yang hilang atau ganda.
    all_ids = sorted(i for v in result.values() for i in v)
    assert all_ids == list(range(100))


def test_round_robin_menghormati_kapasitas():
    result = deal_round_robin(list(range(30)), [1, 2], caps={1: 5, 2: 5})
    assert len(result[1]) == 5
    assert len(result[2]) == 5
    assert len(result[-1]) == 20  # sisanya masuk overflow


def test_plan_membagi_ke_semua_agent(limits):
    plan = plan_batches(list(range(1, 601)), [10, 20, 30, 40, 50, 60], limits)
    assert plan.agent_count == 6
    assert sum(plan.per_agent.values()) == 600
    assert len(plan.batches) == 6  # 100 member per agent = 1 sesi masing-masing
    assert {b.session_no for b in plan.batches} == {1}


def test_plan_membuat_beberapa_sesi_per_agent(limits):
    # 6 agent, 1200 member → 200 per agent → 2 sesi @100.
    plan = plan_batches(list(range(1200)), [1, 2, 3, 4, 5, 6], limits)
    per_agent_sessions = {}
    for batch in plan.batches:
        per_agent_sessions.setdefault(batch.agent_id, []).append(batch.session_no)
    assert all(sorted(v) == [1, 2] for v in per_agent_sessions.values())


def test_plan_tanpa_agent_menghasilkan_catatan(limits):
    plan = plan_batches([1, 2, 3], [], limits)
    assert plan.batches == []
    assert any("agent" in note.lower() for note in plan.notes)


def test_plan_tanpa_member(limits):
    plan = plan_batches([], [1, 2], limits)
    assert plan.batches == []
    assert plan.total_members == 0


def test_plan_menandai_overflow_kuota(limits):
    plan = plan_batches(list(range(100)), [1, 2], limits, caps={1: 10, 2: 10})
    assert sum(len(b.member_ids) for b in plan.batches) == 20
    assert any("ditunda" in note for note in plan.notes)


def test_estimasi_hari_memakai_kuota_harian(limits):
    plan = plan_batches(list(range(600)), [1, 2, 3, 4, 5, 6], limits)
    # 100 member per agent, kuota 50/hari → 2 hari.
    assert plan.estimated_days(limits) == pytest.approx(2.0)


def test_interleave_menyebar_antar_source():
    mixed = interleave_by_source({1: [1, 2, 3], 2: [10, 20], 3: [100]})
    # Tiga member pertama harus berasal dari tiga source berbeda.
    assert mixed[:3] == [1, 10, 100]
    assert sorted(mixed) == [1, 2, 3, 10, 20, 100]


def test_interleave_menangani_source_kosong():
    assert interleave_by_source({1: [], 2: [5, 6]}) == [5, 6]
