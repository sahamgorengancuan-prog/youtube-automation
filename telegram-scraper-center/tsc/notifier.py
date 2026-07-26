"""Bot Telegram untuk mengabari operator.

Memakai Bot API lewat HTTP biasa (stdlib ``urllib``) supaya tidak menambah
dependency dan bisa jalan meski Telethon belum terpasang. Pengiriman dilakukan
di thread terpisah agar orchestrator tidak pernah menunggu jaringan.

Yang dikirim: campaign mulai/selesai, agent kena limitasi, batch selesai,
lonjakan error, dan ringkasan berkala.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import NotifierConfig
from .models import Severity

_SEVERITY_ORDER = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.WARN: 2,
    Severity.ERROR: 3,
    Severity.CRITICAL: 4,
}

_ICON = {
    Severity.DEBUG: "🔍",
    Severity.INFO: "ℹ️",
    Severity.WARN: "⚠️",
    Severity.ERROR: "❌",
    Severity.CRITICAL: "🚨",
}


def _escape(text: str) -> str:
    """Escape minimal untuk parse_mode HTML Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramNotifier:
    """Antrian pesan keluar dengan throttle dan penggabungan pesan sejenis."""

    API_ROOT = "https://api.telegram.org"

    def __init__(self, cfg: NotifierConfig) -> None:
        self.cfg = cfg
        self.min_severity = Severity(cfg.min_severity)
        self._queue: queue.Queue[tuple[Severity, str] | None] = queue.Queue(maxsize=500)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_sent: dict[str, float] = {}
        self.sent = 0
        self.failed = 0
        self.last_error = ""

    # ------------------------------------------------------------- lifecycle

    @property
    def token(self) -> str:
        return os.environ.get(self.cfg.bot_token_env, "").strip()

    @property
    def available(self) -> bool:
        return bool(self.cfg.enabled and self.token and self.cfg.chat_id)

    def start(self) -> None:
        if not self.available or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="tsc-notifier", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        self._thread = None

    # ---------------------------------------------------------------- publish

    def notify(
        self,
        severity: Severity,
        title: str,
        body: str = "",
        *,
        dedupe_key: str | None = None,
    ) -> bool:
        """Antrikan satu notifikasi. Mengembalikan False kalau di-drop."""
        if not self.available:
            return False
        if _SEVERITY_ORDER[severity] < _SEVERITY_ORDER[self.min_severity]:
            return False

        key = dedupe_key or title
        now = time.time()
        last = self._last_sent.get(key, 0.0)
        # Notifikasi kritis tidak pernah di-throttle.
        if severity is not Severity.CRITICAL and now - last < self.cfg.throttle_seconds:
            return False
        self._last_sent[key] = now

        text = f"{_ICON[severity]} <b>{_escape(title)}</b>"
        if body:
            text += f"\n{_escape(body)}"
        try:
            self._queue.put_nowait((severity, text))
        except queue.Full:
            return False
        return True

    def notify_snapshot(self, snapshot: dict[str, Any]) -> bool:
        """Ringkasan berkala berdasarkan snapshot dashboard."""
        counts = snapshot.get("members", {})
        agents = snapshot.get("agents", [])
        working = sum(1 for a in agents if a.get("state") in {"working", "ready", "cooldown"})
        limited = sum(1 for a in agents if a.get("state") in {"limited", "flood_wait", "quota"})
        body = (
            f"Diundang: {counts.get('invited', 0)}\n"
            f"Antre: {counts.get('queued', 0)} · Menunggu: {counts.get('pending', 0)}\n"
            f"Gagal: {counts.get('failed', 0)} · Privasi: {counts.get('blocked_privacy', 0)}\n"
            f"Agent aktif: {working} · Dilimitasi: {limited}"
        )
        return self.notify(
            Severity.INFO,
            f"Ringkasan · {snapshot.get('campaign', {}).get('name', '')}",
            body,
            dedupe_key="heartbeat",
        )

    # ----------------------------------------------------------------- worker

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            _, text = item
            self._send(text)

    def _send(self, text: str) -> None:
        url = f"{self.API_ROOT}/bot{self.token}/sendMessage"
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.cfg.chat_id,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
            if body.get("ok"):
                self.sent += 1
            else:
                self.failed += 1
                self.last_error = str(body.get("description", "unknown"))
        except urllib.error.HTTPError as exc:
            self.failed += 1
            self.last_error = f"HTTP {exc.code}"
            if exc.code == 429:
                time.sleep(5)
        except Exception as exc:
            self.failed += 1
            self.last_error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------- diagnostic

    def check(self) -> tuple[bool, str]:
        """Uji koneksi bot (dipakai ``doctor``). Mengembalikan (ok, keterangan)."""
        if not self.cfg.enabled:
            return True, "notifier dinonaktifkan di config"
        if not self.token:
            return False, f"env {self.cfg.bot_token_env} kosong"
        if not self.cfg.chat_id:
            return False, "notifier.chat_id kosong"
        try:
            with urllib.request.urlopen(
                f"{self.API_ROOT}/bot{self.token}/getMe", timeout=10
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return False, f"tidak bisa menghubungi Bot API: {type(exc).__name__}"
        if not body.get("ok"):
            return False, str(body.get("description", "token ditolak"))
        return True, f"terhubung sebagai @{body['result'].get('username', '?')}"

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "available": self.available,
            "sent": self.sent,
            "failed": self.failed,
            "last_error": self.last_error,
        }
