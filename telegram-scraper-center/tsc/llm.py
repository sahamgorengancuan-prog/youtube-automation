"""Mesin triage: memutuskan apa yang harus dilakukan saat invite gagal.

Dua lapis:

1. ``rule_triage`` — deterministik, selalu jalan, tidak butuh jaringan. Ini yang
   menangani mayoritas kasus (flood wait, peer flood, privasi, dsb).
2. ``TriageAdvisor`` — memanggil Claude untuk kasus yang tidak tercakup aturan
   atau saat polanya aneh (mis. error campuran yang meningkat tajam). LLM
   dipakai untuk *keputusan operasional*, dengan kumpulan aksi tertutup.

Batasan yang disengaja: himpunan aksi tidak memuat "kirim pesan". Framework ini
tidak mengirim DM ke siapa pun; invite yang gagal karena privasi ditandai untuk
ditindaklanjuti manusia. Lihat docs/SAFETY.md.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import LLMConfig
from .models import TriageAction, TriageDecision
from .telegram.base import (
    AGENT_LEVEL_ERRORS,
    PERMANENT_MEMBER_ERRORS,
    AuthError,
    ChannelPrivate,
    ChatAdminRequired,
    ChatWriteForbidden,
    FloodWait,
    NetworkError,
    PeerFlood,
    RpcError,
    TelegramError,
)

#: Skema keputusan yang dipaksakan ke model lewat structured outputs.
DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [a.value for a in TriageAction],
            "description": "Aksi operasional yang harus diambil orchestrator.",
        },
        "wait_seconds": {
            "type": "integer",
            "description": "Berapa detik agent harus diparkir. 0 kalau tidak perlu.",
        },
        "reason": {
            "type": "string",
            "description": "Penjelasan singkat berbahasa Indonesia untuk operator.",
        },
        "confidence": {
            "type": "number",
            "description": "Keyakinan 0..1 terhadap keputusan ini.",
        },
    },
    "required": ["action", "wait_seconds", "reason", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
Anda adalah asisten operasional untuk sistem pemindahan member antar grup \
Telegram yang dikelola sendiri oleh operator (semua grup sumber dan tujuan \
sudah diverifikasi dimiliki/di-admin-i operator).

Tugas Anda: membaca satu insiden kegagalan beserta konteks agent dan campaign, \
lalu memilih SATU aksi operasional dari daftar yang tersedia.

Prinsip yang wajib dipegang:
- Utamakan keselamatan akun. Kalau ragu antara melanjutkan atau memperlambat, \
  pilih memperlambat.
- Limit dari Telegram dihormati sepenuhnya, tidak diakali. Jangan pernah \
  menyarankan waktu tunggu yang lebih pendek dari yang diminta server.
- Kegagalan yang penyebabnya ada pada satu member (privasi, akun terhapus) \
  cukup di-skip; jangan menghukum agent.
- Kegagalan yang penyebabnya ada pada akun agent (peer flood, hak admin \
  dicabut) harus memarkir agent tersebut, bukan mengulang.
- Kalau polanya menunjukkan masalah sistemik (banyak agent gagal bersamaan, \
  error rate melonjak), hentikan campaign dan minta operator memeriksa.
- Kalau informasinya tidak cukup, pilih ESCALATE. Tebakan yang salah lebih \
  mahal daripada bertanya.

Jawab hanya dengan JSON sesuai skema. Alasan ditulis singkat dalam Bahasa \
Indonesia, maksimal dua kalimat.
"""


def rule_triage(error: TelegramError, *, attempts: int, max_attempts: int) -> TriageDecision:
    """Keputusan deterministik. Selalu dipakai lebih dulu sebelum LLM."""
    code = error.code

    if isinstance(error, FloodWait):
        return TriageDecision(
            action=TriageAction.BACKOFF,
            wait_seconds=int(error.seconds),
            reason=f"Telegram meminta tunggu {error.seconds} detik; dihormati penuh.",
        )

    if isinstance(error, PeerFlood):
        return TriageDecision(
            action=TriageAction.PARK_AGENT,
            wait_seconds=24 * 3600,
            reason="Akun kena PeerFlood. Diparkir lama; task dialihkan ke agent lain.",
        )

    if isinstance(error, (AuthError, ChatAdminRequired, ChatWriteForbidden, ChannelPrivate)):
        return TriageDecision(
            action=TriageAction.PARK_AGENT,
            wait_seconds=-1,
            reason=f"Masalah pada akses agent ({code}). Butuh pemeriksaan operator.",
        )

    if code in PERMANENT_MEMBER_ERRORS:
        return TriageDecision(
            action=TriageAction.SKIP_MEMBER,
            reason=f"Member tidak bisa diundang ({code}). Dilewati tanpa retry.",
        )

    if isinstance(error, (NetworkError, RpcError)):
        if attempts < max_attempts:
            return TriageDecision(
                action=TriageAction.RETRY,
                wait_seconds=min(60, 5 * (2**attempts)),
                reason=f"Gangguan sementara ({code}). Dicoba ulang dengan backoff.",
            )
        return TriageDecision(
            action=TriageAction.SKIP_MEMBER,
            reason=f"Gangguan sementara ({code}) tetap muncul setelah {attempts} percobaan.",
        )

    # Tidak dikenali — biarkan lapis berikutnya (LLM) yang menilai.
    return TriageDecision(
        action=TriageAction.ESCALATE,
        reason=f"Error '{code}' belum punya aturan penanganan.",
        confidence=0.0,
    )


def needs_llm(decision: TriageDecision) -> bool:
    """LLM hanya dipanggil kalau aturan tidak yakin."""
    return decision.action is TriageAction.ESCALATE


@dataclass(slots=True)
class TriageContext:
    """Konteks yang dikirim ke model. Sengaja tanpa data pribadi member.

    Tidak ada user_id, username, atau nomor telepon di sini — model cukup tahu
    bentuk masalahnya, bukan siapa orangnya.
    """

    error_code: str
    error_detail: str
    agent_label: str
    agent_state: str
    agent_consecutive_failures: int
    agent_daily_used: int
    agent_daily_quota: int
    member_attempts: int
    batch_progress: float
    recent_error_counts: dict[str, int]
    active_agents: int
    limited_agents: int

    def to_prompt(self) -> str:
        return json.dumps(
            {
                "insiden": {
                    "kode_error": self.error_code,
                    "detail": self.error_detail[:300],
                    "percobaan_ke": self.member_attempts,
                },
                "agent": {
                    "label": self.agent_label,
                    "status": self.agent_state,
                    "gagal_beruntun": self.agent_consecutive_failures,
                    "kuota_terpakai_hari_ini": self.agent_daily_used,
                    "kuota_harian": self.agent_daily_quota,
                },
                "campaign": {
                    "progres_batch": round(self.batch_progress, 3),
                    "agent_aktif": self.active_agents,
                    "agent_dilimitasi": self.limited_agents,
                    "error_1_jam_terakhir": self.recent_error_counts,
                },
            },
            ensure_ascii=False,
            indent=2,
        )


class TriageAdvisor:
    """Pembungkus Claude API untuk keputusan triage.

    Aman dipakai tanpa API key: kalau klien tidak tersedia, ``advise``
    mengembalikan keputusan fallback konservatif dan mencatat alasannya.
    """

    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self._client: Any = None
        self._client_error: str = ""
        self._lock = threading.Lock()
        self._call_times: list[float] = []
        self.calls_made = 0
        self.calls_failed = 0
        self.last_error = ""

    # ---------------------------------------------------------------- client

    @property
    def available(self) -> bool:
        return self.cfg.enabled and bool(os.environ.get(self.cfg.api_key_env, "").strip())

    def _get_client(self) -> Any:
        if self._client is not None or self._client_error:
            return self._client
        try:
            import anthropic
        except ImportError:
            self._client_error = (
                "Paket 'anthropic' belum terpasang — triage LLM dinonaktifkan, "
                "aturan deterministik tetap jalan."
            )
            return None
        try:
            self._client = anthropic.Anthropic(
                api_key=os.environ[self.cfg.api_key_env],
                timeout=self.cfg.timeout_seconds,
                max_retries=1,
            )
        except Exception as exc:  # pragma: no cover - bergantung lingkungan
            self._client_error = f"Gagal membuat klien Anthropic: {exc}"
        return self._client

    def _budget_ok(self) -> bool:
        """Batasi jumlah panggilan per jam supaya biaya terkendali."""
        now = time.time()
        with self._lock:
            self._call_times = [t for t in self._call_times if now - t < 3600]
            if len(self._call_times) >= self.cfg.max_calls_per_hour:
                return False
            self._call_times.append(now)
        return True

    # ---------------------------------------------------------------- advise

    def advise(self, ctx: TriageContext, fallback: TriageDecision) -> TriageDecision:
        """Minta keputusan ke Claude; kembalikan ``fallback`` kalau tidak bisa."""
        if not self.available:
            fallback.source = "fallback"
            fallback.reason = fallback.reason or "Triage LLM nonaktif (API key tidak diset)."
            return fallback
        if not self._budget_ok():
            fallback.source = "fallback"
            fallback.reason = "Kuota panggilan LLM per jam habis; memakai aturan bawaan."
            return fallback

        client = self._get_client()
        if client is None:
            fallback.source = "fallback"
            fallback.reason = self._client_error or fallback.reason
            return fallback

        try:
            response = client.messages.create(
                model=self.cfg.model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.cfg.effort,
                    "format": {"type": "json_schema", "schema": DECISION_SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Tentukan aksi operasional untuk insiden berikut.\n\n"
                            + ctx.to_prompt()
                        ),
                    }
                ],
            )
        except Exception as exc:
            self.calls_failed += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            fallback.source = "fallback"
            fallback.reason = f"Panggilan LLM gagal ({type(exc).__name__}); memakai aturan bawaan."
            return fallback

        self.calls_made += 1

        if getattr(response, "stop_reason", None) == "refusal":
            fallback.source = "fallback"
            fallback.reason = "Model menolak menjawab; memakai aturan bawaan."
            return fallback

        decision = self._parse(response)
        if decision is None:
            fallback.source = "fallback"
            fallback.reason = "Jawaban LLM tidak sesuai skema; memakai aturan bawaan."
            return fallback
        return decision

    @staticmethod
    def _parse(response: Any) -> TriageDecision | None:
        text = next(
            (b.text for b in getattr(response, "content", []) if getattr(b, "type", "") == "text"),
            "",
        )
        if not text.strip():
            return None
        try:
            data = json.loads(text)
            action = TriageAction(data["action"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        return TriageDecision(
            action=action,
            wait_seconds=int(data.get("wait_seconds", 0)),
            reason=str(data.get("reason", ""))[:400],
            source="llm",
            confidence=float(data.get("confidence", 0.5)),
        )

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "available": self.available,
            "model": self.cfg.model,
            "calls_made": self.calls_made,
            "calls_failed": self.calls_failed,
            "last_error": self.last_error or self._client_error,
        }
