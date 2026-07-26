"""Pemuatan dan validasi konfigurasi.

Format konfigurasi adalah JSON biasa (lihat ``config.example.json``) supaya
bisa diedit dari dashboard maupun Notepad tanpa dependency YAML.

Rahasia (api_hash, bot token, API key) TIDAK disimpan di file config —
hanya nama environment variable-nya. Lihat ``.env.example``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Konfigurasi tidak valid — pesan error ditujukan ke operator."""


@dataclass(slots=True)
class Limits:
    """Pagar keamanan throughput. Default-nya sengaja konservatif.

    Telegram membatasi penambahan member per akun per hari. Angka default di
    bawah dipilih agar akun tidak cepat kena PeerFlood. ``session_size`` boleh
    besar (mis. 500) — itu ukuran unit kerja, bukan target harian; kuota harian
    yang menentukan berapa yang benar-benar dieksekusi per hari.
    """

    session_size: int = 500
    min_batch_size: int = 25
    max_batch_size: int = 1000
    invites_per_day_per_agent: int = 40
    invites_per_hour_per_agent: int = 15
    min_gap_seconds: int = 45
    gap_jitter_seconds: int = 25
    flood_wait_margin_seconds: int = 15
    peer_flood_park_hours: float = 24.0
    max_consecutive_failures: int = 5
    max_attempts_per_member: int = 2
    scrape_page_size: int = 200

    def validate(self) -> None:
        if self.session_size < 1:
            raise ConfigError("limits.session_size harus >= 1")
        if not (self.min_batch_size <= self.max_batch_size):
            raise ConfigError("limits.min_batch_size harus <= max_batch_size")
        if self.invites_per_hour_per_agent > self.invites_per_day_per_agent:
            raise ConfigError(
                "limits.invites_per_hour_per_agent tidak boleh melebihi kuota harian"
            )
        if self.min_gap_seconds < 0 or self.gap_jitter_seconds < 0:
            raise ConfigError("limits.min_gap_seconds/gap_jitter_seconds harus >= 0")
        if self.max_attempts_per_member < 1:
            raise ConfigError("limits.max_attempts_per_member harus >= 1")


@dataclass(slots=True)
class TelegramConfig:
    """Kredensial MTProto.

    ``mode`` = "simulate" menjalankan seluruh pipeline memakai klien tiruan —
    berguna untuk latihan, demo, dan test tanpa menyentuh Telegram sama sekali.
    """

    mode: str = "simulate"           # "simulate" | "live"
    api_id_env: str = "TSC_API_ID"
    api_hash_env: str = "TSC_API_HASH"
    session_dir: str = "sessions"
    simulate_seed: int = 7
    simulate_members_per_source: int = 1200

    def validate(self) -> None:
        if self.mode not in {"simulate", "live"}:
            raise ConfigError("telegram.mode harus 'simulate' atau 'live'")

    def credentials(self) -> tuple[int, str]:
        """Ambil api_id/api_hash dari environment. Hanya dipakai di mode live."""
        raw_id = os.environ.get(self.api_id_env, "").strip()
        api_hash = os.environ.get(self.api_hash_env, "").strip()
        if not raw_id or not api_hash:
            raise ConfigError(
                f"Mode live butuh env {self.api_id_env} dan {self.api_hash_env}. "
                "Salin .env.example lalu isi kredensial dari my.telegram.org."
            )
        try:
            api_id = int(raw_id)
        except ValueError as exc:
            raise ConfigError(f"{self.api_id_env} harus berupa angka") from exc
        return api_id, api_hash


@dataclass(slots=True)
class AgentConfig:
    label: str
    session: str
    phone: str = ""

    def validate(self) -> None:
        if not self.label.strip():
            raise ConfigError("agents[].label wajib diisi")
        if not self.session.strip():
            raise ConfigError(f"agents[{self.label}].session wajib diisi")


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = True
    model: str = "claude-opus-5"
    effort: str = "medium"           # low | medium | high | xhigh | max
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_calls_per_hour: int = 40
    timeout_seconds: float = 30.0

    def validate(self) -> None:
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ConfigError("llm.effort tidak dikenal")
        if self.max_calls_per_hour < 0:
            raise ConfigError("llm.max_calls_per_hour harus >= 0")


@dataclass(slots=True)
class NotifierConfig:
    enabled: bool = True
    bot_token_env: str = "TSC_BOT_TOKEN"
    chat_id: str = ""
    min_severity: str = "info"
    throttle_seconds: int = 20
    heartbeat_minutes: int = 30

    def validate(self) -> None:
        if self.enabled and not self.chat_id:
            raise ConfigError(
                "notifier.chat_id wajib diisi kalau notifier.enabled = true"
            )


@dataclass(slots=True)
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    open_browser: bool = True

    def validate(self) -> None:
        if not (1 <= self.port <= 65535):
            raise ConfigError("dashboard.port di luar rentang")


@dataclass(slots=True)
class CampaignConfig:
    name: str = "kampanye-tanpa-nama"
    target: str = ""
    sources: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.target.strip():
            raise ConfigError("campaign.target wajib diisi (grup tujuan invite)")
        if not self.sources:
            raise ConfigError("campaign.sources minimal berisi satu grup")
        dupes = {s for s in self.sources if self.sources.count(s) > 1}
        if dupes:
            raise ConfigError(f"campaign.sources duplikat: {sorted(dupes)}")
        if self.target in self.sources:
            raise ConfigError("campaign.target tidak boleh sekaligus jadi source")


@dataclass(slots=True)
class Config:
    campaign: CampaignConfig = field(default_factory=CampaignConfig)
    agents: list[AgentConfig] = field(default_factory=list)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    limits: Limits = field(default_factory=Limits)
    llm: LLMConfig = field(default_factory=LLMConfig)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    database: str = "data/tsc.db"

    # Jumlah minimum agent. Requirement menyebut "lebih dari 5 user telegram".
    min_agents: int = 5

    def validate(self) -> None:
        self.campaign.validate()
        self.telegram.validate()
        self.limits.validate()
        self.llm.validate()
        self.notifier.validate()
        self.dashboard.validate()

        if len(self.agents) <= self.min_agents:
            raise ConfigError(
                f"Butuh lebih dari {self.min_agents} agent, "
                f"saat ini hanya {len(self.agents)}."
            )
        labels = [a.label for a in self.agents]
        dupes = {label for label in labels if labels.count(label) > 1}
        if dupes:
            raise ConfigError(f"agents[].label duplikat: {sorted(dupes)}")
        sessions = [a.session for a in self.agents]
        dupes = {s for s in sessions if sessions.count(s) > 1}
        if dupes:
            raise ConfigError(f"agents[].session duplikat: {sorted(dupes)}")
        for agent in self.agents:
            agent.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build(section: type, raw: Any, name: str):
    if raw is None:
        return section()
    if not isinstance(raw, dict):
        raise ConfigError(f"Bagian '{name}' harus berupa object JSON")
    known = {f for f in section.__dataclass_fields__}  # type: ignore[attr-defined]
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"Field tidak dikenal di '{name}': {sorted(unknown)}")
    return section(**raw)


def load_config(path: str | Path) -> Config:
    """Baca file konfigurasi JSON dan kembalikan objek Config tervalidasi."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"File konfigurasi tidak ditemukan: {path}. "
            "Salin config.example.json menjadi config.json lalu sesuaikan."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config JSON rusak di baris {exc.lineno}: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Isi config harus berupa object JSON")

    known = set(Config.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"Field tidak dikenal di config: {sorted(unknown)}")

    agents_raw = raw.get("agents") or []
    if not isinstance(agents_raw, list):
        raise ConfigError("'agents' harus berupa list")

    cfg = Config(
        campaign=_build(CampaignConfig, raw.get("campaign"), "campaign"),
        agents=[_build(AgentConfig, a, f"agents[{i}]") for i, a in enumerate(agents_raw)],
        telegram=_build(TelegramConfig, raw.get("telegram"), "telegram"),
        limits=_build(Limits, raw.get("limits"), "limits"),
        llm=_build(LLMConfig, raw.get("llm"), "llm"),
        notifier=_build(NotifierConfig, raw.get("notifier"), "notifier"),
        dashboard=_build(DashboardConfig, raw.get("dashboard"), "dashboard"),
        database=raw.get("database", "data/tsc.db"),
        min_agents=raw.get("min_agents", 5),
    )
    cfg.validate()
    return cfg


def load_dotenv(path: str | Path = ".env") -> int:
    """Muat file .env sederhana ke ``os.environ`` (tidak menimpa yang sudah ada).

    Mengembalikan jumlah variabel yang dimuat. Sengaja tanpa dependency
    python-dotenv agar ``start-dashboard.bat`` jalan hanya dengan Python.
    """
    path = Path(path)
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
