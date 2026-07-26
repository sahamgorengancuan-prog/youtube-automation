"""Server dashboard.

Memakai ``http.server`` dari pustaka standar — tanpa Flask/FastAPI — supaya
``start-dashboard.bat`` bisa jalan di komputer yang hanya punya Python, bahkan
tanpa koneksi internet untuk ``pip install``.

Endpoint:

    GET  /                      halaman dashboard
    GET  /static/<file>         aset (css/js)
    GET  /api/snapshot          potret status lengkap
    GET  /api/events?after=<id> log kejadian sejak id tertentu
    GET  /api/stream            Server-Sent Events (snapshot + log realtime)
    POST /api/control/<aksi>    start | pause | resume | stop | replan
    POST /api/agent/<id>/<aksi> pause | resume
    POST /api/optout            {"user_id": 123} — masukkan ke daftar jangan-undang
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..models import Severity
from ..orchestrator import Engine, EngineRunner
from ..store import Store

STATIC_DIR = Path(__file__).parent / "static"


class DashboardState:
    """Perekat antara HTTP handler dan mesin campaign."""

    def __init__(self, cfg: Config, store: Store, engine: Engine) -> None:
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.runner = EngineRunner(engine)
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- actions

    def control(self, action: str) -> dict[str, Any]:
        with self.lock:
            if action == "start":
                started = self.runner.start()
                return {
                    "ok": started,
                    "message": "Campaign dijalankan." if started else "Campaign sudah berjalan.",
                }
            if action == "pause":
                self.runner.pause()
                return {"ok": True, "message": "Campaign dijeda."}
            if action == "resume":
                self.runner.resume()
                return {"ok": True, "message": "Campaign dilanjutkan."}
            if action == "stop":
                self.runner.stop()
                return {"ok": True, "message": "Campaign dihentikan."}
            if action == "replan":
                if self.runner.running:
                    return {
                        "ok": False,
                        "message": "Hentikan campaign dulu sebelum menyusun ulang batch.",
                    }
                plan = self.engine.build_plan()
                return {
                    "ok": True,
                    "message": f"{len(plan.batches)} batch disusun ulang.",
                    "plan": plan.summary(self.cfg.limits),
                }
        return {"ok": False, "message": f"Aksi '{action}' tidak dikenal."}

    def agent_action(self, agent_id: int, action: str) -> dict[str, Any]:
        if action == "pause":
            ok = self.engine.pause_agent(agent_id)
        elif action == "resume":
            ok = self.engine.resume_agent(agent_id)
        else:
            return {"ok": False, "message": f"Aksi agent '{action}' tidak dikenal."}
        return {"ok": ok, "message": "Berhasil." if ok else "Agent tidak ditemukan."}

    def add_optout(self, user_id: int, reason: str) -> dict[str, Any]:
        self.store.add_optout(self.engine.campaign_id, user_id, reason)
        self.store.log_event(
            campaign_id=self.engine.campaign_id,
            severity=Severity.INFO,
            kind="optout.added",
            message=f"User {user_id} dimasukkan ke daftar jangan-undang.",
            data={"reason": reason},
        )
        return {"ok": True, "message": f"User {user_id} tidak akan diundang."}

    def snapshot(self) -> dict[str, Any]:
        snap = self.engine.snapshot()
        snap["runner"] = {"running": self.runner.running}
        snap["optout_count"] = self.store.count_optout(self.engine.campaign_id)
        return snap


class Handler(BaseHTTPRequestHandler):
    server_version = "TSC/1.0"
    state: DashboardState  # disuntikkan lewat subclass di serve()

    # ----------------------------------------------------------------- utils

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        """Bungkam akses log bawaan; log aplikasi punya kanal sendiri."""

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        ctype, _ = mimetypes.guess_type(path.name)
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ------------------------------------------------------------------- GET

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"

        if route == "/":
            self._send_file(STATIC_DIR / "index.html")
            return
        if route.startswith("/static/"):
            name = Path(route).name  # cegah path traversal
            self._send_file(STATIC_DIR / name)
            return
        if route == "/api/snapshot":
            self._send_json(self.state.snapshot())
            return
        if route == "/api/events":
            params = parse_qs(parsed.query)
            after = int((params.get("after") or ["0"])[0])
            limit = min(500, int((params.get("limit") or ["200"])[0]))
            if after:
                events = self.state.store.events_since(after, limit)
            else:
                events = self.state.store.recent_events(limit)
            self._send_json({"events": events})
            return
        if route == "/api/config":
            data = self.state.cfg.to_dict()
            data.pop("agents", None)  # sembunyikan nama sesi dari halaman
            self._send_json(data)
            return
        if route == "/api/stream":
            self._stream()
            return

        self._send_json({"error": "not found"}, 404)

    def _stream(self) -> None:
        """Server-Sent Events: kirim snapshot tiap 2 detik plus log baru."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_event_id = 0
        recent = self.state.store.recent_events(1)
        if recent:
            last_event_id = max(e["id"] for e in recent)

        try:
            while True:
                snapshot = self.state.snapshot()
                self.wfile.write(
                    f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n".encode()
                )
                events = self.state.store.events_since(last_event_id, 100)
                if events:
                    last_event_id = max(e["id"] for e in events)
                    self.wfile.write(
                        f"event: logs\ndata: {json.dumps(events, default=str)}\n\n".encode()
                    )
                self.wfile.flush()
                time.sleep(2.0)
        except (BrokenPipeError, ConnectionResetError, ValueError):
            return  # klien menutup tab — wajar, bukan error

    # ------------------------------------------------------------------ POST

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path.rstrip("/")
        parts = [p for p in route.split("/") if p]

        if len(parts) == 3 and parts[:2] == ["api", "control"]:
            self._send_json(self.state.control(parts[2]))
            return

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "agent":
            try:
                agent_id = int(parts[2])
            except ValueError:
                self._send_json({"ok": False, "message": "id agent tidak valid"}, 400)
                return
            self._send_json(self.state.agent_action(agent_id, parts[3]))
            return

        if route == "/api/optout":
            payload = self._read_json()
            try:
                user_id = int(payload.get("user_id"))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "message": "user_id wajib angka"}, 400)
                return
            self._send_json(self.state.add_optout(user_id, str(payload.get("reason", ""))))
            return

        self._send_json({"error": "not found"}, 404)


def serve(cfg: Config, store: Store, engine: Engine, *, open_browser: bool | None = None) -> None:
    """Jalankan dashboard sampai Ctrl+C."""
    state = DashboardState(cfg, store, engine)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((cfg.dashboard.host, cfg.dashboard.port), handler)
    url = f"http://{cfg.dashboard.host}:{cfg.dashboard.port}/"

    print(f"\n  Telegram Scraper Center — dashboard aktif")
    print(f"  Buka: {url}")
    print(f"  Mode: {cfg.telegram.mode}   Database: {cfg.database}")
    print("  Tekan Ctrl+C untuk berhenti.\n")

    should_open = cfg.dashboard.open_browser if open_browser is None else open_browser
    if should_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Menghentikan campaign dan menutup dashboard...")
        state.runner.stop()
    finally:
        httpd.server_close()
        store.close()
