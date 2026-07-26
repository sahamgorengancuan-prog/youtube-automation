"""Pengatur laju (rate governor) per agent.

Satu ``AgentGovernor`` memiliki satu agent dan menjawab dua pertanyaan:

1. Boleh kirim invite sekarang? Kalau belum, berapa detik lagi?
2. Setelah hasil invite diketahui, apa status agent berikutnya?

Prinsipnya **menghormati** limit Telegram, bukan mengakalinya: FloodWait
ditunggu penuh plus margin, PeerFlood memarkir agent berjam-jam, dan kuota
harian per akun ditegakkan sendiri agar tidak sampai menyentuh limit server.

Kelas ini murni komputasi waktu — tidak ada I/O — sehingga mudah diuji dengan
menyuntikkan ``clock``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from .config import Limits
from .models import Agent, AgentState

HOUR = 3600.0
DAY = 86400.0


@dataclass(slots=True)
class Permit:
    """Jawaban atas 'boleh jalan sekarang?'."""

    allowed: bool
    wait_seconds: float = 0.0
    reason: str = ""

    @property
    def blocked_forever(self) -> bool:
        """True kalau agent tidak akan pulih tanpa intervensi operator."""
        return not self.allowed and self.wait_seconds < 0


class AgentGovernor:
    """Penjaga kuota, jeda, dan status satu agent."""

    def __init__(
        self,
        agent: Agent,
        limits: Limits,
        *,
        clock: Callable[[], float] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.agent = agent
        self.limits = limits
        self._clock = clock or __import__("time").time
        self._rng = rng or random.Random(agent.id * 7919)

    # ------------------------------------------------------------- windowing

    def _roll_windows(self) -> None:
        """Reset penghitung kuota kalau jendela waktunya sudah lewat."""
        now = self._clock()
        agent = self.agent
        if now - agent.daily_window_start >= DAY:
            agent.daily_window_start = now
            agent.daily_used = 0
            if agent.state is AgentState.QUOTA:
                agent.state = AgentState.READY
                agent.note = "kuota harian direset"
        if now - agent.hourly_window_start >= HOUR:
            agent.hourly_window_start = now
            agent.hourly_used = 0

    # -------------------------------------------------------------- permission

    def check(self) -> Permit:
        """Apakah agent boleh mengirim satu invite sekarang?"""
        self._roll_windows()
        agent = self.agent
        now = self._clock()

        if agent.state is AgentState.DISABLED:
            return Permit(False, -1, f"agent dinonaktifkan: {agent.note or 'tanpa catatan'}")
        if agent.state is AgentState.UNVERIFIED:
            return Permit(False, -1, "agent belum lolos validasi admin")
        if agent.state is AgentState.PAUSED:
            return Permit(False, -1, "agent dijeda manual oleh operator")

        if agent.available_at > now:
            wait = agent.available_at - now
            reason = {
                AgentState.FLOOD_WAIT: "menunggu flood wait dari Telegram",
                AgentState.LIMITED: "akun sedang dilimitasi (PeerFlood)",
                AgentState.COOLDOWN: "jeda normal antar invite",
            }.get(agent.state, "menunggu jadwal berikutnya")
            return Permit(False, wait, reason)

        if agent.daily_used >= self.limits.invites_per_day_per_agent:
            agent.state = AgentState.QUOTA
            wait = max(0.0, agent.daily_window_start + DAY - now)
            return Permit(False, wait, "kuota harian habis")

        if agent.hourly_used >= self.limits.invites_per_hour_per_agent:
            wait = max(0.0, agent.hourly_window_start + HOUR - now)
            agent.state = AgentState.COOLDOWN
            agent.available_at = now + wait
            return Permit(False, wait, "kuota per jam habis")

        return Permit(True, 0.0, "")

    def next_available_in(self) -> float:
        """Perkiraan detik sampai agent bisa dipakai lagi (-1 = butuh operator)."""
        permit = self.check()
        if permit.allowed:
            return 0.0
        return permit.wait_seconds

    # ------------------------------------------------------------ transitions

    def _gap(self) -> float:
        jitter = self._rng.uniform(0, self.limits.gap_jitter_seconds)
        return self.limits.min_gap_seconds + jitter

    def on_started(self) -> None:
        if self.agent.state in {AgentState.READY, AgentState.COOLDOWN}:
            self.agent.state = AgentState.WORKING

    def on_success(self) -> None:
        """Catat satu invite berhasil dan jadwalkan jeda berikutnya."""
        self._roll_windows()
        agent = self.agent
        now = self._clock()
        agent.daily_used += 1
        agent.hourly_used += 1
        agent.total_invited += 1
        agent.consecutive_failures = 0
        if not agent.daily_window_start:
            agent.daily_window_start = now
        if not agent.hourly_window_start:
            agent.hourly_window_start = now
        agent.available_at = now + self._gap()
        agent.note = ""
        # Kuota habis ditandai langsung, bukan menunggu check() berikutnya,
        # supaya status di dashboard jujur sejak invite terakhir.
        if agent.daily_used >= self.limits.invites_per_day_per_agent:
            agent.state = AgentState.QUOTA
            agent.available_at = max(agent.available_at, agent.daily_window_start + DAY)
            agent.note = "kuota harian habis"
        else:
            agent.state = AgentState.COOLDOWN

    def on_member_error(self, code: str) -> None:
        """Kegagalan yang penyebabnya ada di sisi member, bukan agent.

        Tetap dihitung sebagai aktivitas (Telegram menghitungnya juga), tapi
        tidak memarkir agent. Beruntun terlalu banyak tetap memicu backoff
        karena itu gejala akun mulai dicurigai.
        """
        self._roll_windows()
        agent = self.agent
        now = self._clock()
        agent.hourly_used += 1
        agent.total_failed += 1
        agent.consecutive_failures += 1
        agent.available_at = now + self._gap()
        agent.state = AgentState.COOLDOWN
        agent.note = f"gagal: {code}"

        if agent.consecutive_failures >= self.limits.max_consecutive_failures:
            self.park(
                HOUR,
                AgentState.LIMITED,
                f"{agent.consecutive_failures} kegagalan beruntun ({code})",
            )

    def on_flood_wait(self, seconds: int) -> None:
        """Telegram meminta tunggu — hormati sepenuhnya plus margin."""
        total = seconds + self.limits.flood_wait_margin_seconds
        self.park(total, AgentState.FLOOD_WAIT, f"flood wait {seconds}s dari Telegram")

    def on_peer_flood(self) -> None:
        """Sinyal paling serius: parkir lama, jangan coba-coba lagi."""
        self.park(
            self.limits.peer_flood_park_hours * HOUR,
            AgentState.LIMITED,
            "PeerFlood: akun dilimitasi Telegram, diparkir",
        )

    def park(self, seconds: float, state: AgentState, note: str) -> None:
        agent = self.agent
        agent.available_at = self._clock() + max(0.0, seconds)
        agent.state = state
        agent.note = note

    def disable(self, note: str) -> None:
        self.agent.state = AgentState.DISABLED
        self.agent.note = note
        self.agent.available_at = float("inf")

    def pause(self) -> None:
        if self.agent.state is not AgentState.DISABLED:
            self.agent.state = AgentState.PAUSED
            self.agent.note = "dijeda operator"

    def resume(self) -> None:
        """Lepaskan jeda manual. Tidak menghapus flood wait / limitasi server."""
        agent = self.agent
        if agent.state is AgentState.DISABLED:
            return
        if agent.state is AgentState.PAUSED:
            agent.state = AgentState.READY
            agent.note = ""
            agent.available_at = min(agent.available_at, self._clock())

    def mark_verified(self, user_id: int, username: str | None) -> None:
        self.agent.user_id = user_id
        self.agent.username = username
        if self.agent.state is AgentState.UNVERIFIED:
            self.agent.state = AgentState.READY
            self.agent.note = ""
        now = self._clock()
        if not self.agent.daily_window_start:
            self.agent.daily_window_start = now
        if not self.agent.hourly_window_start:
            self.agent.hourly_window_start = now

    # ------------------------------------------------------------- kapasitas

    def remaining_today(self) -> int:
        """Sisa kuota invite hari ini (dipakai perencana batch)."""
        self._roll_windows()
        return max(0, self.limits.invites_per_day_per_agent - self.agent.daily_used)


class GovernorPool:
    """Kumpulan governor, dikunci berdasarkan id agent."""

    def __init__(self, agents: list[Agent], limits: Limits, **kwargs) -> None:
        self.limits = limits
        self._by_id = {a.id: AgentGovernor(a, limits, **kwargs) for a in agents}

    def __getitem__(self, agent_id: int) -> AgentGovernor:
        return self._by_id[agent_id]

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    @property
    def agents(self) -> list[Agent]:
        return [g.agent for g in self._by_id.values()]

    def usable(self) -> list[AgentGovernor]:
        """Agent yang tidak sedang di-skip (siap atau hanya menunggu jeda)."""
        return [g for g in self._by_id.values() if not g.agent.state.is_skippable]

    def soonest_wait(self) -> float:
        """Detik menuju agent pertama yang kembali tersedia (inf kalau tidak ada)."""
        waits = [
            g.next_available_in()
            for g in self._by_id.values()
            if g.next_available_in() >= 0
        ]
        return min(waits) if waits else float("inf")
