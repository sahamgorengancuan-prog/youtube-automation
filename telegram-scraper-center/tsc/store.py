"""Lapisan penyimpanan berbasis SQLite.

Kenapa SQLite dan bukan Postgres/Redis: satu file, nol instalasi, aman untuk
dibuka langsung dengan ``sqlite3`` saat investigasi insiden, dan cukup cepat
untuk ratusan ribu member. Mode WAL dipakai supaya dashboard bisa membaca
sementara worker menulis.

Semua akses lewat kelas ``Store``. Koneksi dibuat per-thread karena objek
sqlite3.Connection tidak aman dibagi antar-thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .models import (
    Agent,
    AgentState,
    Batch,
    BatchState,
    CampaignState,
    Member,
    MemberStatus,
    Severity,
)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    target      TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    chat_ref       TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    admin_verified INTEGER NOT NULL DEFAULT 0,
    verified_by    TEXT NOT NULL DEFAULT '',
    member_count   INTEGER NOT NULL DEFAULT 0,
    scraped_count  INTEGER NOT NULL DEFAULT 0,
    scraped_at     REAL,
    position       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign_id, chat_ref)
);

CREATE TABLE IF NOT EXISTS agents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id          INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    label                TEXT NOT NULL,
    session              TEXT NOT NULL,
    state                TEXT NOT NULL,
    user_id              INTEGER,
    username             TEXT,
    available_at         REAL NOT NULL DEFAULT 0,
    daily_used           INTEGER NOT NULL DEFAULT 0,
    hourly_used          INTEGER NOT NULL DEFAULT 0,
    daily_window_start   REAL NOT NULL DEFAULT 0,
    hourly_window_start  REAL NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_invited        INTEGER NOT NULL DEFAULT 0,
    total_failed         INTEGER NOT NULL DEFAULT 0,
    note                 TEXT NOT NULL DEFAULT '',
    UNIQUE (campaign_id, label)
);

CREATE TABLE IF NOT EXISTS members (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    source_id      INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL,
    username       TEXT,
    access_hash    INTEGER,
    is_bot         INTEGER NOT NULL DEFAULT 0,
    is_deleted     INTEGER NOT NULL DEFAULT 0,
    last_seen_days INTEGER,
    status         TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT NOT NULL DEFAULT '',
    UNIQUE (campaign_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_status ON members(campaign_id, status);

CREATE TABLE IF NOT EXISTS batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    agent_id     INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    session_no   INTEGER NOT NULL,
    state        TEXT NOT NULL,
    size         INTEGER NOT NULL DEFAULT 0,
    done_count   INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_batches_agent ON batches(campaign_id, agent_id, state);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id  INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    ordinal   INTEGER NOT NULL,
    PRIMARY KEY (batch_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_batch_items_member ON batch_items(member_id);

CREATE TABLE IF NOT EXISTS attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    member_id  INTEGER NOT NULL,
    agent_id   INTEGER NOT NULL,
    batch_id   INTEGER,
    result     TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_time ON attempts(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_error ON attempts(campaign_id, error_code);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER,
    ts          REAL NOT NULL,
    severity    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    agent_label TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL,
    data_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(id DESC);

CREATE TABLE IF NOT EXISTS optout (
    campaign_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (campaign_id, user_id)
);
"""


def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        id=row["id"],
        campaign_id=row["campaign_id"],
        label=row["label"],
        session=row["session"],
        state=AgentState(row["state"]),
        user_id=row["user_id"],
        username=row["username"],
        available_at=row["available_at"],
        daily_used=row["daily_used"],
        hourly_used=row["hourly_used"],
        daily_window_start=row["daily_window_start"],
        hourly_window_start=row["hourly_window_start"],
        consecutive_failures=row["consecutive_failures"],
        total_invited=row["total_invited"],
        total_failed=row["total_failed"],
        note=row["note"],
    )


def _row_to_member(row: sqlite3.Row) -> Member:
    return Member(
        id=row["id"],
        campaign_id=row["campaign_id"],
        source_id=row["source_id"],
        user_id=row["user_id"],
        username=row["username"],
        access_hash=row["access_hash"],
        is_bot=bool(row["is_bot"]),
        is_deleted=bool(row["is_deleted"]),
        last_seen_days=row["last_seen_days"],
        status=MemberStatus(row["status"]),
        attempts=row["attempts"],
        last_error=row["last_error"],
    )


class Store:
    """Fasad database. Aman dipakai dari banyak thread."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._init_schema()

    # ---------------------------------------------------------------- koneksi

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with self._write_lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            return self.conn.execute(sql, params)

    def _execmany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._write_lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # --------------------------------------------------------------- campaign

    def create_campaign(self, name: str, target: str, config: dict[str, Any]) -> int:
        now = time.time()
        cur = self._exec(
            "INSERT INTO campaigns(name, target, state, created_at, updated_at, config_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, target, CampaignState.DRAFT.value, now, now, json.dumps(config)),
        )
        return int(cur.lastrowid)

    def get_campaign(self, campaign_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        return dict(row) if row else None

    def latest_campaign_id(self) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM campaigns ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None

    def set_campaign_state(self, campaign_id: int, state: CampaignState) -> None:
        self._exec(
            "UPDATE campaigns SET state = ?, updated_at = ? WHERE id = ?",
            (state.value, time.time(), campaign_id),
        )

    # ---------------------------------------------------------------- sources

    def upsert_source(self, campaign_id: int, chat_ref: str, position: int) -> int:
        self._exec(
            "INSERT OR IGNORE INTO sources(campaign_id, chat_ref, position) VALUES (?, ?, ?)",
            (campaign_id, chat_ref, position),
        )
        row = self.conn.execute(
            "SELECT id FROM sources WHERE campaign_id = ? AND chat_ref = ?",
            (campaign_id, chat_ref),
        ).fetchone()
        return int(row["id"])

    def mark_source_verified(
        self, source_id: int, *, title: str, verified_by: str, member_count: int
    ) -> None:
        self._exec(
            "UPDATE sources SET admin_verified = 1, title = ?, verified_by = ?,"
            " member_count = ? WHERE id = ?",
            (title, verified_by, member_count, source_id),
        )

    def mark_source_scraped(self, source_id: int, scraped_count: int) -> None:
        self._exec(
            "UPDATE sources SET scraped_count = ?, scraped_at = ? WHERE id = ?",
            (scraped_count, time.time(), source_id),
        )

    def list_sources(self, campaign_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM sources WHERE campaign_id = ? ORDER BY position, id",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- agents

    def upsert_agent(self, campaign_id: int, label: str, session: str) -> int:
        self._exec(
            "INSERT OR IGNORE INTO agents(campaign_id, label, session, state)"
            " VALUES (?, ?, ?, ?)",
            (campaign_id, label, session, AgentState.UNVERIFIED.value),
        )
        row = self.conn.execute(
            "SELECT id FROM agents WHERE campaign_id = ? AND label = ?",
            (campaign_id, label),
        ).fetchone()
        return int(row["id"])

    def list_agents(self, campaign_id: int) -> list[Agent]:
        rows = self.conn.execute(
            "SELECT * FROM agents WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    def get_agent(self, agent_id: int) -> Agent | None:
        row = self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return _row_to_agent(row) if row else None

    def save_agent(self, agent: Agent) -> None:
        self._exec(
            "UPDATE agents SET state = ?, user_id = ?, username = ?, available_at = ?,"
            " daily_used = ?, hourly_used = ?, daily_window_start = ?,"
            " hourly_window_start = ?, consecutive_failures = ?, total_invited = ?,"
            " total_failed = ?, note = ? WHERE id = ?",
            (
                agent.state.value,
                agent.user_id,
                agent.username,
                agent.available_at,
                agent.daily_used,
                agent.hourly_used,
                agent.daily_window_start,
                agent.hourly_window_start,
                agent.consecutive_failures,
                agent.total_invited,
                agent.total_failed,
                agent.note,
                agent.id,
            ),
        )

    # ---------------------------------------------------------------- members

    def add_members(self, campaign_id: int, source_id: int, members: Iterable[dict]) -> int:
        """Sisipkan member hasil scrape. Duplikat lintas source diabaikan."""
        before = self.count_members(campaign_id)
        rows = [
            (
                campaign_id,
                source_id,
                m["user_id"],
                m.get("username"),
                m.get("access_hash"),
                int(bool(m.get("is_bot"))),
                int(bool(m.get("is_deleted"))),
                m.get("last_seen_days"),
                MemberStatus.PENDING.value,
            )
            for m in members
        ]
        if not rows:
            return 0
        self._execmany(
            "INSERT OR IGNORE INTO members(campaign_id, source_id, user_id, username,"
            " access_hash, is_bot, is_deleted, last_seen_days, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return self.count_members(campaign_id) - before

    def count_members(self, campaign_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM members WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return int(row["n"])

    def status_breakdown(self, campaign_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM members WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def eligible_members(self, campaign_id: int) -> list[Member]:
        """Member yang layak masuk batch: PENDING dan bukan bot/deleted/opt-out."""
        rows = self.conn.execute(
            "SELECT m.* FROM members m"
            " LEFT JOIN optout o ON o.campaign_id = m.campaign_id AND o.user_id = m.user_id"
            " WHERE m.campaign_id = ? AND m.status = ? AND m.is_bot = 0 AND m.is_deleted = 0"
            "   AND o.user_id IS NULL"
            " ORDER BY m.id",
            (campaign_id, MemberStatus.PENDING.value),
        ).fetchall()
        return [_row_to_member(r) for r in rows]

    def skip_ineligible(self, campaign_id: int) -> int:
        """Tandai member yang memang tidak akan pernah diundang sebagai SKIPPED.

        Tanpa ini, bot / akun terhapus / member opt-out akan menggantung di
        status PENDING selamanya dan membuat angka "belum dijadwalkan" di
        dashboard tidak pernah mencapai nol.
        """
        cur = self._exec(
            "UPDATE members SET status = ?, last_error = 'tidak layak diundang'"
            " WHERE campaign_id = ? AND status = ?"
            "   AND (is_bot = 1 OR is_deleted = 1"
            "        OR user_id IN (SELECT user_id FROM optout WHERE campaign_id = ?))",
            (MemberStatus.SKIPPED.value, campaign_id, MemberStatus.PENDING.value, campaign_id),
        )
        return cur.rowcount or 0

    def get_member(self, member_id: int) -> Member | None:
        row = self.conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        return _row_to_member(row) if row else None

    def set_member_status(
        self, member_id: int, status: MemberStatus, *, error: str = ""
    ) -> None:
        self._exec(
            "UPDATE members SET status = ?, last_error = ? WHERE id = ?",
            (status.value, error, member_id),
        )

    def bump_member_attempt(self, member_id: int) -> int:
        with self._write_lock:
            self.conn.execute(
                "UPDATE members SET attempts = attempts + 1 WHERE id = ?", (member_id,)
            )
            row = self.conn.execute(
                "SELECT attempts FROM members WHERE id = ?", (member_id,)
            ).fetchone()
        return int(row["attempts"]) if row else 0

    def mark_status_bulk(self, member_ids: Sequence[int], status: MemberStatus) -> None:
        if not member_ids:
            return
        self._execmany(
            "UPDATE members SET status = ? WHERE id = ?",
            [(status.value, mid) for mid in member_ids],
        )

    def add_optout(self, campaign_id: int, user_id: int, reason: str = "") -> None:
        self._exec(
            "INSERT OR REPLACE INTO optout(campaign_id, user_id, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (campaign_id, user_id, reason, time.time()),
        )
        self._exec(
            "UPDATE members SET status = ? WHERE campaign_id = ? AND user_id = ?"
            " AND status IN (?, ?)",
            (
                MemberStatus.SKIPPED.value,
                campaign_id,
                user_id,
                MemberStatus.PENDING.value,
                MemberStatus.QUEUED.value,
            ),
        )

    def count_optout(self, campaign_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM optout WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return int(row["n"])

    # ---------------------------------------------------------------- batches

    def create_batch(
        self, campaign_id: int, agent_id: int, session_no: int, member_ids: Sequence[int]
    ) -> int:
        with self._write_lock:
            cur = self.conn.execute(
                "INSERT INTO batches(campaign_id, agent_id, session_no, state, size, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    agent_id,
                    session_no,
                    BatchState.PENDING.value,
                    len(member_ids),
                    time.time(),
                ),
            )
            batch_id = int(cur.lastrowid)
            self.conn.executemany(
                "INSERT OR IGNORE INTO batch_items(batch_id, member_id, ordinal)"
                " VALUES (?, ?, ?)",
                [(batch_id, mid, i) for i, mid in enumerate(member_ids)],
            )
        self.mark_status_bulk(list(member_ids), MemberStatus.QUEUED)
        return batch_id

    def clear_pending_batches(self, campaign_id: int) -> int:
        """Hapus batch yang belum jalan (untuk replanning). Member dikembalikan."""
        rows = self.conn.execute(
            "SELECT id FROM batches WHERE campaign_id = ? AND state = ?",
            (campaign_id, BatchState.PENDING.value),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        member_rows = self.conn.execute(
            f"SELECT member_id FROM batch_items WHERE batch_id IN ({placeholders})", ids
        ).fetchall()
        self._exec(f"DELETE FROM batches WHERE id IN ({placeholders})", ids)
        self.mark_status_bulk(
            [int(r["member_id"]) for r in member_rows], MemberStatus.PENDING
        )
        return len(ids)

    def load_batch(self, batch_id: int) -> Batch | None:
        row = self.conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            return None
        items = self.conn.execute(
            "SELECT member_id FROM batch_items WHERE batch_id = ? ORDER BY ordinal",
            (batch_id,),
        ).fetchall()
        return Batch(
            id=row["id"],
            campaign_id=row["campaign_id"],
            agent_id=row["agent_id"],
            session_no=row["session_no"],
            member_ids=[int(i["member_id"]) for i in items],
            state=BatchState(row["state"]),
            done_count=row["done_count"],
            failed_count=row["failed_count"],
        )

    def next_batch_for_agent(self, campaign_id: int, agent_id: int) -> Batch | None:
        """Ambil batch berikutnya milik agent: yang RUNNING dulu, baru PENDING."""
        row = self.conn.execute(
            "SELECT id FROM batches WHERE campaign_id = ? AND agent_id = ?"
            " AND state IN (?, ?)"
            " ORDER BY CASE state WHEN ? THEN 0 ELSE 1 END, session_no, id LIMIT 1",
            (
                campaign_id,
                agent_id,
                BatchState.RUNNING.value,
                BatchState.PENDING.value,
                BatchState.RUNNING.value,
            ),
        ).fetchone()
        return self.load_batch(int(row["id"])) if row else None

    def set_batch_state(self, batch_id: int, state: BatchState) -> None:
        column = {
            BatchState.RUNNING: "started_at",
            BatchState.DONE: "finished_at",
            BatchState.FAILED: "finished_at",
            BatchState.CANCELLED: "finished_at",
        }.get(state)
        if column:
            self._exec(
                f"UPDATE batches SET state = ?, {column} = ? WHERE id = ?",
                (state.value, time.time(), batch_id),
            )
        else:
            self._exec("UPDATE batches SET state = ? WHERE id = ?", (state.value, batch_id))

    def bump_batch_counter(self, batch_id: int, *, done: int = 0, failed: int = 0) -> None:
        self._exec(
            "UPDATE batches SET done_count = done_count + ?, failed_count = failed_count + ?"
            " WHERE id = ?",
            (done, failed, batch_id),
        )

    def list_batches(self, campaign_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT b.*, a.label AS agent_label FROM batches b"
            " JOIN agents a ON a.id = b.agent_id"
            " WHERE b.campaign_id = ? ORDER BY b.agent_id, b.session_no",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def pending_member_ids(self, batch_id: int) -> list[int]:
        """Member dalam batch yang belum tuntas (masih QUEUED)."""
        rows = self.conn.execute(
            "SELECT bi.member_id FROM batch_items bi"
            " JOIN members m ON m.id = bi.member_id"
            " WHERE bi.batch_id = ? AND m.status = ?"
            " ORDER BY bi.ordinal",
            (batch_id, MemberStatus.QUEUED.value),
        ).fetchall()
        return [int(r["member_id"]) for r in rows]

    # --------------------------------------------------------------- attempts

    def record_attempt(
        self,
        *,
        campaign_id: int,
        member_id: int,
        agent_id: int,
        batch_id: int | None,
        result: str,
        error_code: str = "",
        detail: str = "",
        latency_ms: int = 0,
    ) -> None:
        self._exec(
            "INSERT INTO attempts(campaign_id, member_id, agent_id, batch_id, result,"
            " error_code, detail, latency_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                member_id,
                agent_id,
                batch_id,
                result,
                error_code,
                detail[:500],
                latency_ms,
                time.time(),
            ),
        )

    def error_taxonomy(self, campaign_id: int, window_seconds: float = 86400) -> list[dict]:
        """Ringkasan error terbanyak — dipakai panel 'Error Logging' di dashboard."""
        since = time.time() - window_seconds
        rows = self.conn.execute(
            "SELECT error_code, COUNT(*) AS n, MAX(created_at) AS last_seen,"
            "       MAX(detail) AS sample"
            " FROM attempts WHERE campaign_id = ? AND error_code != '' AND created_at >= ?"
            " GROUP BY error_code ORDER BY n DESC LIMIT 20",
            (campaign_id, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def throughput(self, campaign_id: int, window_seconds: float = 3600) -> dict[str, int]:
        since = time.time() - window_seconds
        row = self.conn.execute(
            "SELECT SUM(result = 'ok') AS ok, SUM(result != 'ok') AS bad"
            " FROM attempts WHERE campaign_id = ? AND created_at >= ?",
            (campaign_id, since),
        ).fetchone()
        return {"ok": int(row["ok"] or 0), "failed": int(row["bad"] or 0)}

    # ----------------------------------------------------------------- events

    def log_event(
        self,
        *,
        campaign_id: int | None,
        severity: Severity,
        kind: str,
        message: str,
        agent_label: str = "",
        data: dict[str, Any] | None = None,
    ) -> int:
        cur = self._exec(
            "INSERT INTO events(campaign_id, ts, severity, kind, agent_label, message, data_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                time.time(),
                severity.value,
                kind,
                agent_label,
                message,
                json.dumps(data or {}, default=str),
            ),
        )
        return int(cur.lastrowid)

    def events_since(self, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["data"] = json.loads(item.pop("data_json") or "{}")
            except json.JSONDecodeError:
                item["data"] = {}
            out.append(item)
        return out

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        if not rows:
            return []
        oldest = min(int(r["id"]) for r in rows)
        return self.events_since(oldest - 1, limit)

    def prune_events(self, keep: int = 20000) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        excess = int(row["n"]) - keep
        if excess <= 0:
            return 0
        self._exec(
            "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)",
            (excess,),
        )
        return excess


def iter_chunks(seq: Sequence[Any], size: int) -> Iterator[list[Any]]:
    """Bagi ``seq`` menjadi potongan sebesar ``size`` (potongan terakhir bisa lebih kecil)."""
    if size < 1:
        raise ValueError("size harus >= 1")
    for start in range(0, len(seq), size):
        yield list(seq[start : start + size])
