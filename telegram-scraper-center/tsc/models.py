"""Tipe data inti untuk Telegram Scraper Center.

Semua enum disimpan sebagai string di SQLite supaya database tetap mudah
dibaca manusia saat debugging (``sqlite3 data/tsc.db "select * from agents"``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentState(str, Enum):
    """Status siklus hidup satu akun agent (user Telegram admin)."""

    UNVERIFIED = "unverified"      # belum divalidasi sebagai admin target
    READY = "ready"                # siap menerima task
    WORKING = "working"            # sedang mengerjakan batch
    COOLDOWN = "cooldown"          # jeda normal antar invite
    FLOOD_WAIT = "flood_wait"      # server minta tunggu (FloodWaitError)
    LIMITED = "limited"            # kena PeerFlood / limitasi berat, diparkir
    QUOTA = "quota"                # kuota harian habis
    PAUSED = "paused"              # dijeda manual oleh operator
    DISABLED = "disabled"          # dimatikan (gagal login / bukan admin)

    @property
    def is_skippable(self) -> bool:
        """True kalau scheduler harus melewati agent ini untuk sementara.

        Sesuai requirement: agent yang sedang dilimitasi tetap boleh diinput,
        hanya di-skip saat pembagian task sampai kembali free.
        """
        return self in {
            AgentState.FLOOD_WAIT,
            AgentState.LIMITED,
            AgentState.QUOTA,
            AgentState.PAUSED,
            AgentState.DISABLED,
            AgentState.UNVERIFIED,
        }

    @property
    def is_terminal(self) -> bool:
        """True kalau agent tidak akan pulih sendiri tanpa intervensi."""
        return self is AgentState.DISABLED


class MemberStatus(str, Enum):
    """Status satu member dalam antrian sebuah campaign."""

    PENDING = "pending"            # baru discrape, belum masuk batch
    QUEUED = "queued"              # sudah dialokasikan ke batch
    INVITED = "invited"            # berhasil diundang
    ALREADY_IN = "already_in"      # sudah jadi member target
    SKIPPED = "skipped"            # bot / deleted / opt-out / tidak valid
    BLOCKED_PRIVACY = "blocked_privacy"  # privasi user menolak invite
    FAILED = "failed"              # gagal setelah retry habis


class BatchState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CampaignState(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"        # semua guard lolos
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    ABORTED = "aborted"


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class TriageAction(str, Enum):
    """Aksi yang boleh diambil oleh triage engine (rule-based maupun LLM).

    Sengaja tertutup dan tidak memuat aksi 'kirim pesan' — framework ini tidak
    mengirim DM ke siapa pun. Lihat docs/SAFETY.md.
    """

    RETRY = "retry"                # ulangi member yang sama setelah jeda
    BACKOFF = "backoff"            # perlambat agent, lanjut member berikutnya
    SKIP_MEMBER = "skip_member"    # tandai member gagal, lanjut
    PARK_AGENT = "park_agent"      # parkir agent ini, task dialihkan
    STOP_CAMPAIGN = "stop_campaign"  # hentikan semuanya, butuh operator
    ESCALATE = "escalate"          # tidak yakin, minta keputusan manusia


@dataclass(slots=True)
class Agent:
    """Satu akun user Telegram yang dipakai sebagai pekerja."""

    id: int
    campaign_id: int
    label: str
    session: str
    state: AgentState = AgentState.UNVERIFIED
    user_id: int | None = None
    username: str | None = None
    # Epoch detik: agent tidak boleh dipakai sebelum waktu ini.
    available_at: float = 0.0
    daily_used: int = 0
    hourly_used: int = 0
    daily_window_start: float = 0.0
    hourly_window_start: float = 0.0
    consecutive_failures: int = 0
    total_invited: int = 0
    total_failed: int = 0
    note: str = ""

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state.value,
            "username": self.username,
            "user_id": self.user_id,
            "available_in": max(0, int(self.available_at - now)),
            "daily_used": self.daily_used,
            "hourly_used": self.hourly_used,
            "total_invited": self.total_invited,
            "total_failed": self.total_failed,
            "consecutive_failures": self.consecutive_failures,
            "note": self.note,
        }


@dataclass(slots=True)
class Member:
    """Satu calon member hasil scrape dari sebuah source group."""

    id: int
    campaign_id: int
    source_id: int
    user_id: int
    username: str | None = None
    access_hash: int | None = None
    is_bot: bool = False
    is_deleted: bool = False
    last_seen_days: int | None = None
    status: MemberStatus = MemberStatus.PENDING
    attempts: int = 0
    last_error: str = ""


@dataclass(slots=True)
class Batch:
    """Satu unit kerja: sekumpulan member untuk satu agent pada satu sesi."""

    id: int
    campaign_id: int
    agent_id: int
    session_no: int
    member_ids: list[int] = field(default_factory=list)
    state: BatchState = BatchState.PENDING
    done_count: int = 0
    failed_count: int = 0

    @property
    def size(self) -> int:
        return len(self.member_ids)

    @property
    def progress(self) -> float:
        if not self.member_ids:
            return 1.0
        return (self.done_count + self.failed_count) / len(self.member_ids)


@dataclass(slots=True)
class TriageDecision:
    """Hasil keputusan triage untuk satu kegagalan."""

    action: TriageAction
    wait_seconds: int = 0
    reason: str = ""
    source: str = "rule"           # "rule" | "llm" | "fallback"
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "wait_seconds": self.wait_seconds,
            "reason": self.reason,
            "source": self.source,
            "confidence": self.confidence,
        }
