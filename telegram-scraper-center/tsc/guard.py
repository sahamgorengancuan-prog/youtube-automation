"""Gerbang validasi sebelum campaign boleh jalan.

Tiga pemeriksaan wajib:

1. **Agent adalah admin di grup target** dan punya hak menambah member.
   Agent yang tidak lolos dinonaktifkan, bukan menggagalkan seluruh campaign —
   selama jumlah agent yang lolos masih di atas minimum.
2. **Grup source dikelola sendiri.** Minimal satu agent harus admin di setiap
   source. Ini yang membuat framework ini alat pemindahan/konsolidasi komunitas
   sendiri, bukan alat scraping grup milik orang lain. Lihat docs/SAFETY.md.
3. **Jumlah agent yang lolos** memenuhi ``config.min_agents``.

Agent yang sedang dilimitasi tetap lolos validasi (statusnya diingat), hanya
di-skip saat pembagian task sampai kembali free — sesuai requirement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Config
from .governor import GovernorPool
from .models import AgentState, Severity
from .store import Store
from .telegram.base import ChatInfo, TelegramError


@dataclass(slots=True)
class SourceCheck:
    chat_ref: str
    ok: bool
    title: str = ""
    verified_by: str = ""
    member_count: int = 0
    reason: str = ""


@dataclass(slots=True)
class AgentCheck:
    label: str
    ok: bool
    user_id: int | None = None
    username: str | None = None
    reason: str = ""
    limited: bool = False


@dataclass(slots=True)
class GuardReport:
    """Hasil lengkap validasi, siap ditampilkan di dashboard."""

    target: SourceCheck | None = None
    sources: list[SourceCheck] = field(default_factory=list)
    agents: list[AgentCheck] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def usable_agents(self) -> list[AgentCheck]:
        return [a for a in self.agents if a.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": asdict(self.target) if self.target else None,
            "sources": [asdict(s) for s in self.sources],
            "agents": [asdict(a) for a in self.agents],
            "blocking": self.blocking,
            "warnings": self.warnings,
        }


class Guard:
    """Menjalankan validasi memakai klien yang sudah terhubung."""

    def __init__(
        self,
        cfg: Config,
        store: Store,
        campaign_id: int,
        clients: dict[str, Any],
        pool: GovernorPool,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.campaign_id = campaign_id
        self.clients = clients          # label -> TelegramClient (sudah connect)
        self.pool = pool
        self._by_label = {g.agent.label: g for g in pool}

    # --------------------------------------------------------------- helpers

    def _log(self, severity: Severity, kind: str, message: str, **data: Any) -> None:
        self.store.log_event(
            campaign_id=self.campaign_id,
            severity=severity,
            kind=kind,
            message=message,
            data=data,
        )

    async def _resolve_with_any_agent(self, ref: str) -> tuple[str, ChatInfo] | None:
        """Coba resolve chat memakai agent mana pun yang berhasil."""
        for label, client in self.clients.items():
            try:
                return label, await client.resolve_chat(ref)
            except TelegramError:
                continue
        return None

    # ----------------------------------------------------------------- checks

    async def check_target(self) -> tuple[SourceCheck, dict[str, ChatInfo]]:
        """Validasi grup target dan hak admin tiap agent di sana."""
        ref = self.cfg.campaign.target
        resolved: dict[str, ChatInfo] = {}
        found = await self._resolve_with_any_agent(ref)
        if found is None:
            return (
                SourceCheck(ref, False, reason="Grup target tidak bisa diakses agent mana pun."),
                resolved,
            )
        _, chat = found

        for label, client in self.clients.items():
            try:
                resolved[label] = await client.resolve_chat(ref)
            except TelegramError as exc:
                resolved[label] = chat
                self._log(
                    Severity.WARN,
                    "guard.target_resolve",
                    f"Agent {label} gagal resolve target: {exc.code}",
                    agent=label,
                )

        return (
            SourceCheck(
                ref,
                True,
                title=chat.title,
                member_count=chat.participants_count,
                verified_by="resolved",
            ),
            resolved,
        )

    async def check_agents(self, target_by_label: dict[str, ChatInfo]) -> list[AgentCheck]:
        """Pastikan tiap agent benar-benar admin di target dan boleh invite."""
        checks: list[AgentCheck] = []
        for label, client in self.clients.items():
            governor = self._by_label.get(label)
            chat = target_by_label.get(label)
            if governor is None or chat is None:
                checks.append(AgentCheck(label, False, reason="agent tidak dikenal / target gagal"))
                continue

            try:
                rights = await client.get_admin_rights(chat)
            except TelegramError as exc:
                governor.disable(f"gagal cek hak admin: {exc.code}")
                self.store.save_agent(governor.agent)
                checks.append(AgentCheck(label, False, reason=f"cek hak admin gagal: {exc.code}"))
                continue

            if not rights.can_run_campaign:
                governor.disable("bukan admin target atau tidak punya hak menambah member")
                self.store.save_agent(governor.agent)
                checks.append(
                    AgentCheck(
                        label,
                        False,
                        reason="bukan admin grup target / tidak punya hak 'Add Users'",
                    )
                )
                self._log(
                    Severity.ERROR,
                    "guard.agent_rejected",
                    f"Agent {label} ditolak: bukan admin target.",
                    agent=label,
                    rights=rights.raw,
                )
                continue

            # Agent yang sedang dilimitasi TETAP lolos — hanya diskip saat
            # pembagian task sampai statusnya kembali free.
            limited = governor.agent.state in {AgentState.LIMITED, AgentState.FLOOD_WAIT}
            checks.append(
                AgentCheck(
                    label,
                    True,
                    user_id=governor.agent.user_id,
                    username=governor.agent.username,
                    limited=limited,
                    reason="sedang dilimitasi, akan di-skip sampai free" if limited else "",
                )
            )
        return checks

    async def check_sources(self) -> list[SourceCheck]:
        """Setiap source harus dikelola minimal oleh satu agent (admin)."""
        results: list[SourceCheck] = []
        for position, ref in enumerate(self.cfg.campaign.sources):
            source_id = self.store.upsert_source(self.campaign_id, ref, position)
            verified_by = ""
            chat: ChatInfo | None = None
            last_error = ""

            for label, client in self.clients.items():
                governor = self._by_label.get(label)
                if governor is None or governor.agent.state.is_terminal:
                    continue
                try:
                    candidate = await client.resolve_chat(ref)
                    rights = await client.get_admin_rights(candidate)
                except TelegramError as exc:
                    last_error = exc.code
                    continue
                chat = chat or candidate
                if rights.is_admin:
                    verified_by = label
                    chat = candidate
                    break

            if chat is None:
                results.append(
                    SourceCheck(
                        ref,
                        False,
                        reason=f"tidak bisa diakses agent mana pun ({last_error or 'unknown'})",
                    )
                )
                continue

            if not verified_by:
                results.append(
                    SourceCheck(
                        ref,
                        False,
                        title=chat.title,
                        member_count=chat.participants_count,
                        reason=(
                            "tidak ada agent yang berstatus admin di grup ini. "
                            "Framework hanya memproses grup yang Anda kelola sendiri."
                        ),
                    )
                )
                self._log(
                    Severity.ERROR,
                    "guard.source_rejected",
                    f"Source {ref} ditolak: tidak ada agent admin di grup tersebut.",
                    source=ref,
                )
                continue

            self.store.mark_source_verified(
                source_id,
                title=chat.title,
                verified_by=verified_by,
                member_count=chat.participants_count,
            )
            results.append(
                SourceCheck(
                    ref,
                    True,
                    title=chat.title,
                    verified_by=verified_by,
                    member_count=chat.participants_count,
                )
            )
        return results

    async def run(self) -> GuardReport:
        """Jalankan seluruh pemeriksaan dan rangkum hasilnya."""
        report = GuardReport()

        target, target_by_label = await self.check_target()
        report.target = target
        if not target.ok:
            report.blocking.append(f"Grup target '{target.chat_ref}': {target.reason}")
            return report

        report.agents = await self.check_agents(target_by_label)
        usable = report.usable_agents
        if len(usable) <= self.cfg.min_agents:
            report.blocking.append(
                f"Hanya {len(usable)} agent lolos validasi admin, "
                f"minimal harus lebih dari {self.cfg.min_agents}."
            )

        report.sources = await self.check_sources()
        rejected = [s for s in report.sources if not s.ok]
        for source in rejected:
            report.blocking.append(f"Source '{source.chat_ref}': {source.reason}")
        if not [s for s in report.sources if s.ok]:
            report.blocking.append("Tidak ada source yang lolos validasi.")

        limited = [a for a in usable if a.limited]
        if limited:
            report.warnings.append(
                f"{len(limited)} agent sedang dilimitasi "
                f"({', '.join(a.label for a in limited)}) — akan di-skip sampai free."
            )

        for governor in self.pool:
            self.store.save_agent(governor.agent)

        self._log(
            Severity.INFO if report.ok else Severity.ERROR,
            "guard.report",
            "Validasi lolos." if report.ok else "Validasi gagal.",
            blocking=report.blocking,
            warnings=report.warnings,
        )
        return report
