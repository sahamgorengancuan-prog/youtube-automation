"""Mesin eksekusi campaign.

Alur lengkap:

    connect → validasi (guard) → scrape source → rencana batch → jalankan

Eksekusi memakai satu task asyncio per agent sehingga semua agent bekerja
**paralel**. Tiap task mengambil batch miliknya, meminta izin ke governor, lalu
mengundang member satu per satu. Kegagalan diserahkan ke triage.

Seluruh status disimpan ke SQLite setiap langkah, jadi proses bisa dimatikan
kapan saja (Ctrl+C atau stop.bat) dan dilanjutkan lagi tanpa kehilangan antrean.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .batching import Plan, interleave_by_source, plan_batches
from .config import Config
from .governor import GovernorPool
from .guard import Guard, GuardReport
from .llm import TriageAdvisor, TriageContext, needs_llm, rule_triage
from .models import (
    AgentState,
    BatchState,
    CampaignState,
    MemberStatus,
    Severity,
    TriageAction,
)
from .notifier import TelegramNotifier
from .store import Store
from .telegram import build_client
from .telegram.base import ChatInfo, TelegramError
from .telegram.simulator import SimulatorWorld

#: Error yang memetakan langsung ke status akhir member.
_ERROR_TO_STATUS = {
    "privacy_restricted": MemberStatus.BLOCKED_PRIVACY,
    "already_participant": MemberStatus.ALREADY_IN,
    "not_mutual_contact": MemberStatus.BLOCKED_PRIVACY,
    "user_channels_too_much": MemberStatus.SKIPPED,
    "user_deactivated": MemberStatus.SKIPPED,
}


@dataclass
class EngineStatus:
    """Status ringkas mesin, dibaca dashboard."""

    phase: str = "idle"           # idle|connecting|validating|scraping|planning|running|paused|done|error
    detail: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    guard: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    scrape: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "detail": self.detail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": (self.finished_at or time.time()) - self.started_at
            if self.started_at
            else 0,
            "error": self.error,
            "guard": self.guard,
            "plan": self.plan,
            "scrape": self.scrape,
        }


class Engine:
    """Orkestrator satu campaign."""

    def __init__(
        self,
        cfg: Config,
        store: Store,
        *,
        campaign_id: int | None = None,
        notifier: TelegramNotifier | None = None,
        advisor: TriageAdvisor | None = None,
        world: SimulatorWorld | None = None,
        clock: Any = None,
        sleep: Any = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.notifier = notifier or TelegramNotifier(cfg.notifier)
        self.advisor = advisor or TriageAdvisor(cfg.llm)
        self.status = EngineStatus()
        # Jam dan fungsi tidur dibuat bisa disuntik supaya test bisa memakai
        # waktu virtual — tanpa itu, satu flood wait 120 detik berarti test
        # benar-benar menunggu 120 detik.
        self._clock = clock or time.time
        self._sleep = sleep or asyncio.sleep

        if cfg.telegram.mode == "simulate" and world is None:
            world = SimulatorWorld(
                seed=cfg.telegram.simulate_seed,
                members_per_source=cfg.telegram.simulate_members_per_source,
            )
        self.world = world

        self.campaign_id = campaign_id or self._ensure_campaign()
        agents = self._ensure_agents()
        self.pool = GovernorPool(agents, cfg.limits, clock=self._clock)

        self.clients: dict[str, Any] = {}
        self._target_chat: dict[str, ChatInfo] = {}
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()          # set = tidak dijeda
        self._lock = threading.RLock()
        self._triage_log: list[dict[str, Any]] = []

    # ----------------------------------------------------------------- setup

    def _ensure_campaign(self) -> int:
        existing = self.store.latest_campaign_id()
        if existing is not None:
            row = self.store.get_campaign(existing)
            if row and row["target"] == self.cfg.campaign.target:
                return existing
        return self.store.create_campaign(
            self.cfg.campaign.name, self.cfg.campaign.target, self.cfg.to_dict()
        )

    def _ensure_agents(self):
        for agent_cfg in self.cfg.agents:
            self.store.upsert_agent(self.campaign_id, agent_cfg.label, agent_cfg.session)
        return self.store.list_agents(self.campaign_id)

    def _log(
        self,
        severity: Severity,
        kind: str,
        message: str,
        *,
        agent: str = "",
        notify: bool = False,
        **data: Any,
    ) -> None:
        self.store.log_event(
            campaign_id=self.campaign_id,
            severity=severity,
            kind=kind,
            message=message,
            agent_label=agent,
            data=data,
        )
        if notify:
            self.notifier.notify(severity, message, agent and f"Agent: {agent}" or "", dedupe_key=kind)

    # ------------------------------------------------------------- lifecycle

    async def connect_all(self) -> None:
        """Login semua agent. Agent yang gagal dinonaktifkan, bukan menggagalkan run."""
        self.status.phase = "connecting"
        for agent_cfg in self.cfg.agents:
            governor = next(
                (g for g in self.pool if g.agent.label == agent_cfg.label), None
            )
            if governor is None:
                continue
            client = build_client(self.cfg, agent_cfg, self.world)
            try:
                me = await client.connect()
            except TelegramError as exc:
                governor.disable(f"login gagal: {exc.code}")
                self.store.save_agent(governor.agent)
                self._log(
                    Severity.ERROR,
                    "agent.login_failed",
                    f"Agent {agent_cfg.label} gagal login: {exc}",
                    agent=agent_cfg.label,
                    notify=True,
                )
                continue
            governor.mark_verified(me.user_id, me.username)
            self.store.save_agent(governor.agent)
            self.clients[agent_cfg.label] = client
            self._log(
                Severity.INFO,
                "agent.connected",
                f"Agent {agent_cfg.label} terhubung (@{me.username or me.user_id}).",
                agent=agent_cfg.label,
            )

    async def disconnect_all(self) -> None:
        for client in self.clients.values():
            with contextlib.suppress(Exception):
                await client.disconnect()
        self.clients.clear()

    async def validate(self) -> GuardReport:
        """Jalankan seluruh gerbang validasi."""
        self.status.phase = "validating"
        guard = Guard(self.cfg, self.store, self.campaign_id, self.clients, self.pool)
        report = await guard.run()
        self.status.guard = report.to_dict()
        if report.ok:
            self.store.set_campaign_state(self.campaign_id, CampaignState.VALIDATED)
            self._target_chat = await self._resolve_targets()
        else:
            self._log(
                Severity.CRITICAL,
                "guard.blocked",
                "Campaign diblokir oleh validasi: " + "; ".join(report.blocking),
                notify=True,
            )
        return report

    async def _resolve_targets(self) -> dict[str, ChatInfo]:
        out: dict[str, ChatInfo] = {}
        for label, client in self.clients.items():
            with contextlib.suppress(TelegramError):
                out[label] = await client.resolve_chat(self.cfg.campaign.target)
        return out

    # ---------------------------------------------------------------- scrape

    async def scrape_sources(self) -> dict[str, int]:
        """Ambil member dari semua source yang lolos validasi, berurutan (antre)."""
        self.status.phase = "scraping"
        results: dict[str, int] = {}
        sources = [s for s in self.store.list_sources(self.campaign_id) if s["admin_verified"]]

        for source in sources:
            if self._stop_event.is_set():
                break
            label = source["verified_by"] or next(iter(self.clients), "")
            client = self.clients.get(label)
            if client is None:
                results[source["chat_ref"]] = 0
                continue

            self.status.detail = f"Mengambil member dari {source['chat_ref']}"
            try:
                chat = await client.resolve_chat(source["chat_ref"])
                buffer: list[dict[str, Any]] = []
                total = 0
                async for user in client.iter_participants(chat):
                    buffer.append(user.to_row())
                    if len(buffer) >= self.cfg.limits.scrape_page_size:
                        total += self.store.add_members(
                            self.campaign_id, source["id"], buffer
                        )
                        buffer.clear()
                    if self._stop_event.is_set():
                        break
                if buffer:
                    total += self.store.add_members(self.campaign_id, source["id"], buffer)
            except TelegramError as exc:
                self._log(
                    Severity.ERROR,
                    "scrape.failed",
                    f"Gagal mengambil member dari {source['chat_ref']}: {exc.code}",
                    source=source["chat_ref"],
                    notify=True,
                )
                results[source["chat_ref"]] = 0
                continue

            self.store.mark_source_scraped(source["id"], total)
            results[source["chat_ref"]] = total
            self._log(
                Severity.INFO,
                "scrape.done",
                f"{total} member baru dari {source['chat_ref']}.",
                source=source["chat_ref"],
            )

        self.status.scrape = results
        self.status.detail = ""
        return results

    # --------------------------------------------------------------- planning

    def build_plan(self, *, respect_daily_quota: bool = True) -> Plan:
        """Susun ulang batch dari member yang masih PENDING.

        Tidak mengubah ``status.phase`` — pemanggil yang menentukan fase, supaya
        penyusunan ulang dari dashboard tidak membuat campaign yang sudah
        selesai kembali terlihat seperti sedang bekerja.
        """
        self.store.clear_pending_batches(self.campaign_id)

        dilewati = self.store.skip_ineligible(self.campaign_id)
        if dilewati:
            self._log(
                Severity.INFO,
                "plan.skipped",
                f"{dilewati} member dilewati (bot, akun terhapus, atau opt-out).",
                count=dilewati,
            )

        members = self.store.eligible_members(self.campaign_id)
        by_source: dict[int, list[int]] = {}
        for member in members:
            by_source.setdefault(member.source_id, []).append(member.id)
        member_ids = interleave_by_source(by_source)

        # Agent yang sedang dilimitasi tidak diberi batch baru; tugasnya
        # otomatis jatuh ke agent lain. Batch lamanya tetap tersimpan.
        usable = [g for g in self.pool if not g.agent.state.is_skippable]
        agent_ids = [g.agent.id for g in usable]

        caps = None
        if respect_daily_quota:
            # Rencanakan beberapa hari ke depan agar batch tidak terlalu remeh,
            # tapi tetap proporsional terhadap kapasitas tiap agent.
            horizon_days = 7
            caps = {
                g.agent.id: g.remaining_today()
                + self.cfg.limits.invites_per_day_per_agent * (horizon_days - 1)
                for g in usable
            }

        plan = plan_batches(member_ids, agent_ids, self.cfg.limits, caps=caps)
        for batch in plan.batches:
            self.store.create_batch(
                self.campaign_id, batch.agent_id, batch.session_no, batch.member_ids
            )

        summary = plan.summary(self.cfg.limits)
        self.status.plan = summary
        self._log(
            Severity.INFO,
            "plan.created",
            (
                f"Rencana dibuat: {summary['batch_count']} batch × ~{summary['batch_size']} "
                f"member untuk {summary['agent_count']} agent "
                f"(perkiraan {summary['estimated_days']} hari)."
            ),
            **summary,
        )
        return plan

    # -------------------------------------------------------------- execution

    async def run(self) -> None:
        """Jalankan seluruh pipeline sampai selesai atau dihentikan."""
        self.status.started_at = time.time()
        self.status.error = ""
        self.notifier.start()
        try:
            await self.connect_all()
            report = await self.validate()
            if not report.ok:
                self.status.phase = "error"
                self.status.error = "; ".join(report.blocking)
                self.store.set_campaign_state(self.campaign_id, CampaignState.ABORTED)
                return

            if self.store.count_members(self.campaign_id) == 0:
                await self.scrape_sources()
            self.status.phase = "planning"
            self.build_plan()

            self.store.set_campaign_state(self.campaign_id, CampaignState.RUNNING)
            self.status.phase = "running"
            self.notifier.notify(
                Severity.INFO,
                f"Campaign '{self.cfg.campaign.name}' dimulai",
                f"Target: {self.cfg.campaign.target}\n"
                f"Member antre: {self.store.status_breakdown(self.campaign_id).get('queued', 0)}",
                dedupe_key="campaign.start",
            )

            await self._run_workers()

            done = not self._stop_event.is_set()
            self.status.phase = "done" if done else "idle"
            self.store.set_campaign_state(
                self.campaign_id, CampaignState.DONE if done else CampaignState.PAUSED
            )
            counts = self.store.status_breakdown(self.campaign_id)
            self.notifier.notify(
                Severity.INFO,
                f"Campaign '{self.cfg.campaign.name}' {'selesai' if done else 'dihentikan'}",
                f"Diundang: {counts.get('invited', 0)} · Gagal: {counts.get('failed', 0)} · "
                f"Privasi: {counts.get('blocked_privacy', 0)}",
                dedupe_key="campaign.end",
            )
        except Exception as exc:  # pragma: no cover - jaring pengaman
            self.status.phase = "error"
            self.status.error = f"{type(exc).__name__}: {exc}"
            self._log(
                Severity.CRITICAL,
                "engine.crash",
                f"Mesin berhenti karena error tak tertangani: {exc}",
                notify=True,
            )
            raise
        finally:
            self.status.finished_at = time.time()
            await self.disconnect_all()

    async def _run_workers(self) -> None:
        workers = [
            asyncio.create_task(self._agent_worker(g.agent.id), name=f"agent-{g.agent.label}")
            for g in self.pool
            if g.agent.label in self.clients and not g.agent.state.is_terminal
        ]
        if not workers:
            self._log(Severity.ERROR, "engine.no_worker", "Tidak ada agent yang bisa bekerja.")
            return
        heartbeat = asyncio.create_task(self._heartbeat(), name="heartbeat")
        try:
            await asyncio.gather(*workers)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self) -> None:
        interval = max(60, self.cfg.notifier.heartbeat_minutes * 60)
        while not self._stop_event.is_set():
            await asyncio.sleep(interval)
            if self._stop_event.is_set():
                break
            self.notifier.notify_snapshot(self.snapshot())
            self.store.prune_events()

    async def _agent_worker(self, agent_id: int) -> None:
        """Loop kerja satu agent: ambil batch → undang member → ulangi."""
        governor = self.pool[agent_id]
        agent = governor.agent
        client = self.clients[agent.label]
        chat = self._target_chat.get(agent.label)
        if chat is None:
            governor.disable("grup target tidak bisa di-resolve oleh agent ini")
            self.store.save_agent(agent)
            return

        while not self._stop_event.is_set():
            await self._pause_event.wait()
            if self._stop_event.is_set():
                break

            batch = self.store.next_batch_for_agent(self.campaign_id, agent_id)
            if batch is None:
                self._log(
                    Severity.INFO,
                    "agent.idle",
                    f"Agent {agent.label} tidak punya batch tersisa.",
                    agent=agent.label,
                )
                return

            if batch.state is BatchState.PENDING:
                self.store.set_batch_state(batch.id, BatchState.RUNNING)
                self._log(
                    Severity.INFO,
                    "batch.start",
                    f"Agent {agent.label} memulai batch #{batch.id} "
                    f"(sesi {batch.session_no}, {batch.size} member).",
                    agent=agent.label,
                    batch_id=batch.id,
                )

            finished = await self._process_batch(governor, client, chat, batch.id)
            if not finished:
                return  # agent diparkir permanen / campaign dihentikan

    async def _process_batch(self, governor, client, chat: ChatInfo, batch_id: int) -> bool:
        """Kerjakan satu batch. Mengembalikan False kalau worker harus berhenti."""
        agent = governor.agent
        while not self._stop_event.is_set():
            await self._pause_event.wait()
            pending = self.store.pending_member_ids(batch_id)
            if not pending:
                self.store.set_batch_state(batch_id, BatchState.DONE)
                batch = self.store.load_batch(batch_id)
                self._log(
                    Severity.INFO,
                    "batch.done",
                    f"Agent {agent.label} menyelesaikan batch #{batch_id} "
                    f"({batch.done_count if batch else 0} berhasil).",
                    agent=agent.label,
                    batch_id=batch_id,
                    notify=True,
                )
                return True

            permit = governor.check()
            self.store.save_agent(agent)
            if not permit.allowed:
                if permit.blocked_forever:
                    self._log(
                        Severity.WARN,
                        "agent.parked",
                        f"Agent {agent.label} diparkir: {permit.reason}. "
                        "Batch dikembalikan ke antrean.",
                        agent=agent.label,
                        notify=True,
                    )
                    self.store.set_batch_state(batch_id, BatchState.PENDING)
                    return False
                await self._sleep_interruptible(min(permit.wait_seconds, 60))
                continue

            member_id = pending[0]
            keep_going = await self._invite_one(governor, client, chat, batch_id, member_id)
            if not keep_going:
                self.store.set_batch_state(batch_id, BatchState.PENDING)
                return False
        return False

    async def _invite_one(
        self, governor, client, chat: ChatInfo, batch_id: int, member_id: int
    ) -> bool:
        """Undang satu member dan tangani hasilnya. False = worker harus berhenti."""
        agent = governor.agent
        member = self.store.get_member(member_id)
        if member is None:
            return True

        from .telegram.base import UserInfo

        user = UserInfo(
            user_id=member.user_id,
            username=member.username,
            access_hash=member.access_hash,
        )
        governor.on_started()
        started = time.perf_counter()

        try:
            await client.invite(chat, user)
        except TelegramError as exc:
            latency = int((time.perf_counter() - started) * 1000)
            return await self._handle_failure(
                governor, batch_id, member_id, exc, latency
            )

        latency = int((time.perf_counter() - started) * 1000)
        governor.on_success()
        self.store.save_agent(agent)
        self.store.set_member_status(member_id, MemberStatus.INVITED)
        self.store.bump_batch_counter(batch_id, done=1)
        self.store.record_attempt(
            campaign_id=self.campaign_id,
            member_id=member_id,
            agent_id=agent.id,
            batch_id=batch_id,
            result="ok",
            latency_ms=latency,
        )
        return True

    async def _handle_failure(
        self, governor, batch_id: int, member_id: int, exc: TelegramError, latency: int
    ) -> bool:
        """Terapkan keputusan triage atas satu kegagalan."""
        agent = governor.agent
        attempts = self.store.bump_member_attempt(member_id)
        self.store.record_attempt(
            campaign_id=self.campaign_id,
            member_id=member_id,
            agent_id=agent.id,
            batch_id=batch_id,
            result="error",
            error_code=exc.code,
            detail=str(exc),
            latency_ms=latency,
        )

        decision = rule_triage(
            exc, attempts=attempts, max_attempts=self.cfg.limits.max_attempts_per_member
        )
        if needs_llm(decision) and self.advisor.available:
            decision = self.advisor.advise(self._triage_context(exc, agent, attempts, batch_id), decision)

        self._remember_triage(exc, decision, agent.label)

        # --- terapkan aksi -------------------------------------------------
        if decision.action is TriageAction.RETRY:
            governor.park(
                max(1, decision.wait_seconds), AgentState.COOLDOWN, f"retry: {exc.code}"
            )
            self.store.save_agent(agent)
            return True

        if decision.action is TriageAction.BACKOFF:
            if exc.code == "flood_wait":
                governor.on_flood_wait(getattr(exc, "seconds", decision.wait_seconds))
            else:
                governor.park(
                    max(1, decision.wait_seconds), AgentState.COOLDOWN, decision.reason
                )
            self.store.save_agent(agent)
            self._log(
                Severity.WARN,
                "triage.backoff",
                f"Agent {agent.label} melambat: {decision.reason}",
                agent=agent.label,
                error_code=exc.code,
            )
            return True

        if decision.action is TriageAction.SKIP_MEMBER:
            status = _ERROR_TO_STATUS.get(exc.code, MemberStatus.FAILED)
            self.store.set_member_status(member_id, status, error=exc.code)
            self.store.bump_batch_counter(
                batch_id,
                done=1 if status is MemberStatus.ALREADY_IN else 0,
                failed=0 if status is MemberStatus.ALREADY_IN else 1,
            )
            governor.on_member_error(exc.code)
            self.store.save_agent(agent)
            return True

        if decision.action is TriageAction.PARK_AGENT:
            if exc.code == "peer_flood":
                governor.on_peer_flood()
            elif decision.wait_seconds < 0:
                governor.disable(decision.reason or exc.code)
            else:
                governor.park(decision.wait_seconds, AgentState.LIMITED, decision.reason)
            self.store.save_agent(agent)
            self._log(
                Severity.ERROR,
                "triage.park_agent",
                f"Agent {agent.label} diparkir: {decision.reason}",
                agent=agent.label,
                error_code=exc.code,
                notify=True,
            )
            self._replan_after_agent_loss()
            return False

        if decision.action is TriageAction.STOP_CAMPAIGN:
            self._log(
                Severity.CRITICAL,
                "triage.stop_campaign",
                f"Campaign dihentikan oleh triage: {decision.reason}",
                agent=agent.label,
                error_code=exc.code,
                notify=True,
            )
            self.request_stop()
            return False

        # ESCALATE: tandai member, biarkan operator memutuskan.
        self.store.set_member_status(member_id, MemberStatus.FAILED, error=exc.code)
        self.store.bump_batch_counter(batch_id, failed=1)
        governor.on_member_error(exc.code)
        self.store.save_agent(agent)
        self._log(
            Severity.WARN,
            "triage.escalate",
            f"Butuh keputusan operator untuk error '{exc.code}': {decision.reason}",
            agent=agent.label,
            error_code=exc.code,
            notify=True,
        )
        return True

    def _triage_context(self, exc: TelegramError, agent, attempts: int, batch_id: int):
        batch = self.store.load_batch(batch_id)
        errors = {
            row["error_code"]: row["n"]
            for row in self.store.error_taxonomy(self.campaign_id, window_seconds=3600)
        }
        agents = self.pool.agents
        return TriageContext(
            error_code=exc.code,
            error_detail=str(exc),
            agent_label=agent.label,
            agent_state=agent.state.value,
            agent_consecutive_failures=agent.consecutive_failures,
            agent_daily_used=agent.daily_used,
            agent_daily_quota=self.cfg.limits.invites_per_day_per_agent,
            member_attempts=attempts,
            batch_progress=batch.progress if batch else 0.0,
            recent_error_counts=errors,
            active_agents=sum(1 for a in agents if not a.state.is_skippable),
            limited_agents=sum(
                1 for a in agents if a.state in {AgentState.LIMITED, AgentState.FLOOD_WAIT}
            ),
        )

    def _remember_triage(self, exc: TelegramError, decision, agent_label: str) -> None:
        with self._lock:
            self._triage_log.insert(
                0,
                {
                    "ts": time.time(),
                    "agent": agent_label,
                    "error_code": exc.code,
                    **decision.to_dict(),
                },
            )
            del self._triage_log[50:]

    def _replan_after_agent_loss(self) -> None:
        """Alihkan batch PENDING milik agent yang hilang ke agent yang tersisa."""
        usable = [g for g in self.pool if not g.agent.state.is_skippable]
        if not usable:
            self._log(
                Severity.CRITICAL,
                "engine.no_agent_left",
                "Semua agent sedang dilimitasi atau dinonaktifkan. Campaign menunggu.",
                notify=True,
            )
            return
        self.build_plan()
        self._log(
            Severity.INFO,
            "plan.rebalanced",
            f"Batch dirapikan ulang ke {len(usable)} agent yang tersisa.",
        )

    async def _sleep_interruptible(self, seconds: float) -> None:
        """Tidur bertahap supaya perintah stop terasa dalam <1 detik."""
        remaining = max(0.0, seconds)
        while remaining > 0 and not self._stop_event.is_set():
            chunk = min(0.5, remaining)
            await self._sleep(chunk)
            remaining -= chunk

    # ----------------------------------------------------------------- kontrol

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()

    def request_pause(self) -> None:
        self._pause_event.clear()
        self.status.phase = "paused"
        self.store.set_campaign_state(self.campaign_id, CampaignState.PAUSED)
        self._log(Severity.WARN, "engine.paused", "Campaign dijeda oleh operator.", notify=True)

    def request_resume(self) -> None:
        self._pause_event.set()
        self.status.phase = "running"
        self.store.set_campaign_state(self.campaign_id, CampaignState.RUNNING)
        self._log(Severity.INFO, "engine.resumed", "Campaign dilanjutkan.", notify=True)

    def pause_agent(self, agent_id: int) -> bool:
        try:
            governor = self.pool[agent_id]
        except KeyError:
            return False
        governor.pause()
        self.store.save_agent(governor.agent)
        self._log(
            Severity.WARN,
            "agent.paused",
            f"Agent {governor.agent.label} dijeda operator.",
            agent=governor.agent.label,
        )
        return True

    def resume_agent(self, agent_id: int) -> bool:
        try:
            governor = self.pool[agent_id]
        except KeyError:
            return False
        governor.resume()
        self.store.save_agent(governor.agent)
        self._log(
            Severity.INFO,
            "agent.resumed",
            f"Agent {governor.agent.label} diaktifkan lagi.",
            agent=governor.agent.label,
        )
        return True

    # ---------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        """Potret lengkap untuk dashboard."""
        campaign = self.store.get_campaign(self.campaign_id) or {}
        counts = self.store.status_breakdown(self.campaign_id)
        total = sum(counts.values()) or 1
        return {
            "campaign": {
                "id": self.campaign_id,
                "name": campaign.get("name", self.cfg.campaign.name),
                "target": campaign.get("target", self.cfg.campaign.target),
                "state": campaign.get("state", CampaignState.DRAFT.value),
            },
            "status": self.status.to_dict(),
            "members": counts,
            "progress": round(
                (counts.get("invited", 0) + counts.get("already_in", 0)) / total, 4
            ),
            "agents": [g.agent.snapshot() for g in self.pool],
            "sources": self.store.list_sources(self.campaign_id),
            "batches": self.store.list_batches(self.campaign_id),
            "errors": self.store.error_taxonomy(self.campaign_id),
            "throughput_1h": self.store.throughput(self.campaign_id),
            "triage": list(self._triage_log[:20]),
            "llm": self.advisor.stats(),
            "notifier": self.notifier.stats(),
            "limits": {
                "per_day": self.cfg.limits.invites_per_day_per_agent,
                "per_hour": self.cfg.limits.invites_per_hour_per_agent,
                "session_size": self.cfg.limits.session_size,
                "min_gap": self.cfg.limits.min_gap_seconds,
            },
            "mode": self.cfg.telegram.mode,
            "ts": time.time(),
        }


class EngineRunner:
    """Menjalankan ``Engine`` di thread terpisah dengan event loop sendiri.

    Dipakai dashboard supaya HTTP server tetap responsif selama campaign jalan.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return False
        self._thread = threading.Thread(target=self._run, name="tsc-engine", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self.engine.run())
        except Exception:
            pass  # sudah dicatat di Engine.run
        finally:
            with contextlib.suppress(Exception):
                self._loop.close()
            self._loop = None

    def _call(self, fn, *args) -> None:
        """Jalankan perintah kontrol di dalam loop mesin (thread-safe)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            fn(*args)
            return
        loop.call_soon_threadsafe(fn, *args)

    def stop(self, timeout: float = 30.0) -> None:
        if not self.running:
            return
        self._call(self.engine.request_stop)
        assert self._thread is not None
        self._thread.join(timeout=timeout)

    def pause(self) -> None:
        self._call(self.engine.request_pause)

    def resume(self) -> None:
        self._call(self.engine.request_resume)
