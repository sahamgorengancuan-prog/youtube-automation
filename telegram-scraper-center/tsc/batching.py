"""Perencanaan batch dan pembagian task ke agent.

Alur: kumpulan member yang layak → dibagi rata ke agent yang bisa dipakai →
tiap jatah agent dipotong menjadi sesi berukuran ``session_size``.

Contoh sesuai requirement: admin-1 mendapat Batch A berisi 500 member (sesi 1),
lalu sesi 2 berisi 500 berikutnya, dan seterusnya. Agent bekerja paralel;
kuota harian tiap agent yang menentukan seberapa cepat sesi itu benar-benar
selesai.

Seluruh fungsi di modul ini murni (tanpa I/O, tanpa random) sehingga hasilnya
deterministik dan gampang diuji.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .config import Limits


@dataclass(slots=True)
class PlannedBatch:
    """Rencana satu batch sebelum ditulis ke database."""

    agent_id: int
    session_no: int
    member_ids: list[int]

    @property
    def size(self) -> int:
        return len(self.member_ids)


@dataclass(slots=True)
class Plan:
    """Hasil perencanaan lengkap plus penjelasan untuk ditampilkan di dashboard."""

    batches: list[PlannedBatch]
    batch_size: int
    per_agent: dict[int, int]
    total_members: int
    agent_count: int
    notes: list[str]

    @property
    def session_count(self) -> int:
        """Jumlah sesi terbanyak yang dimiliki satu agent."""
        if not self.batches:
            return 0
        return max(b.session_no for b in self.batches)

    def estimated_days(self, limits: Limits) -> float:
        """Perkiraan hari kerja berdasarkan kuota harian per agent."""
        if not self.per_agent or limits.invites_per_day_per_agent < 1:
            return 0.0
        busiest = max(self.per_agent.values())
        return busiest / limits.invites_per_day_per_agent

    def summary(self, limits: Limits) -> dict:
        return {
            "total_members": self.total_members,
            "agent_count": self.agent_count,
            "batch_size": self.batch_size,
            "batch_count": len(self.batches),
            "max_sessions_per_agent": self.session_count,
            "per_agent": self.per_agent,
            "estimated_days": round(self.estimated_days(limits), 1),
            "notes": self.notes,
        }


def choose_batch_size(total_members: int, agent_count: int, limits: Limits) -> int:
    """Pilih ukuran batch yang seimbang.

    Aturannya, berurutan:

    1. Mulai dari ``limits.session_size`` (preferensi operator).
    2. Jangan sampai satu agent hanya kebagian potongan sisa yang sangat kecil —
       kalau jatah per agent lebih kecil dari batch, kecilkan batch.
    3. Jangan lebih kecil dari ``min_batch_size`` (overhead per batch) dan
       jangan lebih besar dari ``max_batch_size``.
    4. Ratakan: kalau jatah agent 520 dan batch 500, lebih baik 2 sesi @260
       daripada sesi kedua yang cuma berisi 20.
    """
    if total_members <= 0 or agent_count <= 0:
        return max(1, limits.min_batch_size)

    per_agent = math.ceil(total_members / agent_count)
    size = min(limits.session_size, per_agent)

    if size > 0:
        sessions = math.ceil(per_agent / size)
        # Ratakan sesi agar tidak ada ekor kecil.
        balanced = math.ceil(per_agent / sessions)
        # Ekor dianggap "kecil" kalau di bawah 40% ukuran batch.
        tail = per_agent - (sessions - 1) * size
        if sessions > 1 and tail < size * 0.4:
            size = balanced

    size = max(limits.min_batch_size, min(size, limits.max_batch_size))
    return max(1, min(size, per_agent))


def deal_round_robin(
    member_ids: Sequence[int], agent_ids: Sequence[int], caps: dict[int, int] | None = None
) -> dict[int, list[int]]:
    """Bagikan member ke agent secara bergantian, menghormati kapasitas.

    ``caps`` membatasi jumlah member maksimum per agent (mis. sisa kuota).
    Member yang tidak kebagian karena semua agent penuh akan dikembalikan pada
    kunci ``-1`` supaya pemanggil bisa menjadwalkannya di gelombang berikutnya.
    """
    if not agent_ids:
        return {-1: list(member_ids)}

    result: dict[int, list[int]] = {aid: [] for aid in agent_ids}
    overflow: list[int] = []
    caps = caps or {}
    remaining = {aid: caps.get(aid, math.inf) for aid in agent_ids}

    index = 0
    for member_id in member_ids:
        placed = False
        for _ in range(len(agent_ids)):
            agent_id = agent_ids[index % len(agent_ids)]
            index += 1
            if remaining[agent_id] > 0:
                result[agent_id].append(member_id)
                remaining[agent_id] -= 1
                placed = True
                break
        if not placed:
            overflow.append(member_id)

    if overflow:
        result[-1] = overflow
    return result


def plan_batches(
    member_ids: Sequence[int],
    agent_ids: Sequence[int],
    limits: Limits,
    *,
    caps: dict[int, int] | None = None,
) -> Plan:
    """Susun rencana batch lengkap.

    ``caps`` opsional: batas member per agent untuk gelombang ini. Kalau
    diberikan dan total kapasitas kurang, sisanya dilaporkan lewat ``notes``
    dan tidak dibuatkan batch (akan direncanakan ulang di gelombang berikutnya).
    """
    notes: list[str] = []
    member_ids = list(member_ids)
    agent_ids = list(agent_ids)

    if not member_ids:
        notes.append("Tidak ada member yang layak dijadwalkan.")
        return Plan([], 0, {}, 0, len(agent_ids), notes)
    if not agent_ids:
        notes.append("Tidak ada agent yang bisa dipakai — semua sedang dilimitasi/dijeda.")
        return Plan([], 0, {}, len(member_ids), 0, notes)

    allocation = deal_round_robin(member_ids, agent_ids, caps)
    overflow = allocation.pop(-1, [])
    if overflow:
        notes.append(
            f"{len(overflow)} member ditunda ke gelombang berikutnya "
            "karena kuota agent untuk periode ini sudah penuh."
        )

    scheduled_total = sum(len(v) for v in allocation.values())
    batch_size = choose_batch_size(scheduled_total, len(agent_ids), limits)
    if batch_size != limits.session_size:
        notes.append(
            f"Ukuran batch disesuaikan dari {limits.session_size} menjadi {batch_size} "
            "agar sesi antar-agent seimbang."
        )

    batches: list[PlannedBatch] = []
    per_agent: dict[int, int] = {}
    for agent_id in agent_ids:
        chunk_source = allocation.get(agent_id, [])
        per_agent[agent_id] = len(chunk_source)
        for session_no, start in enumerate(range(0, len(chunk_source), batch_size), start=1):
            batches.append(
                PlannedBatch(
                    agent_id=agent_id,
                    session_no=session_no,
                    member_ids=chunk_source[start : start + batch_size],
                )
            )

    if batches:
        notes.append(
            f"{scheduled_total} member dibagi menjadi {len(batches)} batch "
            f"untuk {len(agent_ids)} agent (paralel)."
        )

    return Plan(
        batches=batches,
        batch_size=batch_size,
        per_agent=per_agent,
        total_members=len(member_ids),
        agent_count=len(agent_ids),
        notes=notes,
    )


def interleave_by_source(members_by_source: dict[int, Sequence[int]]) -> list[int]:
    """Selang-seling member antar source agar antrian tidak didominasi satu grup.

    Berguna saat ada beberapa source dengan ukuran timpang: hasil invite
    tersebar merata sehingga kalau campaign dihentikan di tengah jalan, semua
    source sudah terwakili.
    """
    queues = [list(v) for v in members_by_source.values() if v]
    out: list[int] = []
    while queues:
        for queue in list(queues):
            if queue:
                out.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
    return out
