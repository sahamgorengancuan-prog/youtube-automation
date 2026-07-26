"""Antarmuka baris perintah.

    python -m tsc dashboard     buka dashboard (cara utama)
    python -m tsc run           jalankan campaign tanpa dashboard
    python -m tsc validate      cek admin & source saja, tidak mengundang
    python -m tsc plan          susun batch dan tampilkan rencananya
    python -m tsc status        ringkasan kondisi terakhir
    python -m tsc doctor        periksa kesiapan lingkungan
    python -m tsc login         login satu akun agent (mode live)
    python -m tsc optout <id>   masukkan user ke daftar jangan-undang
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .config import Config, ConfigError, load_config, load_dotenv
from .llm import TriageAdvisor
from .models import Severity
from .notifier import TelegramNotifier
from .orchestrator import Engine
from .store import Store

DEFAULT_CONFIG = "config.json"


def _bootstrap(args) -> tuple[Config, Store, Engine]:
    load_dotenv(args.env)
    cfg = load_config(args.config)
    store = Store(cfg.database)
    engine = Engine(cfg, store)
    return cfg, store, engine


# ------------------------------------------------------------------ commands


def cmd_dashboard(args) -> int:
    from .dashboard import serve

    cfg, store, engine = _bootstrap(args)
    serve(cfg, store, engine, open_browser=not args.no_browser)
    return 0


def cmd_run(args) -> int:
    cfg, store, engine = _bootstrap(args)
    print(f"Menjalankan campaign '{cfg.campaign.name}' (mode {cfg.telegram.mode})…")
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        print("\nDihentikan pengguna. Antrean tersimpan, jalankan lagi untuk melanjutkan.")
        return 130
    counts = store.status_breakdown(engine.campaign_id)
    print(f"\nSelesai. {counts}")
    return 0 if engine.status.phase == "done" else 1


def cmd_validate(args) -> int:
    cfg, store, engine = _bootstrap(args)

    async def go():
        await engine.connect_all()
        report = await engine.validate()
        await engine.disconnect_all()
        return report

    report = asyncio.run(go())

    print(f"\nTarget : {cfg.campaign.target}")
    if report.target:
        mark = "OK " if report.target.ok else "GAGAL"
        print(f"  [{mark}] {report.target.title or report.target.chat_ref} "
              f"({report.target.member_count} member)")

    print("\nAgent:")
    for check in report.agents:
        mark = "OK " if check.ok else "TOLAK"
        extra = f" — {check.reason}" if check.reason else ""
        print(f"  [{mark}] {check.label}{extra}")

    print("\nSource:")
    for source in report.sources:
        mark = "OK " if source.ok else "TOLAK"
        extra = f" — {source.reason}" if source.reason else f" (admin: {source.verified_by})"
        print(f"  [{mark}] {source.chat_ref}{extra}")

    if report.warnings:
        print("\nPeringatan:")
        for warning in report.warnings:
            print(f"  ! {warning}")

    if report.blocking:
        print("\nCampaign DIBLOKIR:")
        for reason in report.blocking:
            print(f"  x {reason}")
        return 1

    print("\nSemua validasi lolos.")
    return 0


def cmd_plan(args) -> int:
    cfg, store, engine = _bootstrap(args)
    if store.count_members(engine.campaign_id) == 0:
        print("Belum ada member di database. Jalankan `run` atau `dashboard` "
              "supaya source di-scrape terlebih dahulu.")
        return 1
    plan = engine.build_plan()
    summary = plan.summary(cfg.limits)
    print(f"\nTotal member layak : {summary['total_members']}")
    print(f"Agent dipakai      : {summary['agent_count']}")
    print(f"Ukuran batch       : {summary['batch_size']}")
    print(f"Jumlah batch       : {summary['batch_count']}")
    print(f"Sesi terbanyak     : {summary['max_sessions_per_agent']}")
    print(f"Estimasi durasi    : {summary['estimated_days']} hari")
    print("\nPembagian per agent:")
    for agent in engine.pool.agents:
        share = summary["per_agent"].get(agent.id, 0)
        print(f"  {agent.label:<16} {share:>6} member  [{agent.state.value}]")
    for note in summary["notes"]:
        print(f"\n  · {note}")
    return 0


def cmd_status(args) -> int:
    cfg, store, engine = _bootstrap(args)
    snap = engine.snapshot()
    counts = snap["members"]
    print(f"\nCampaign : {snap['campaign']['name']} → {snap['campaign']['target']}")
    print(f"Status   : {snap['campaign']['state']} · progres {snap['progress'] * 100:.1f}%")
    print(f"Member   : {counts}")
    print(f"Throughput 1 jam: {snap['throughput_1h']}")
    print("\nAgent:")
    for agent in snap["agents"]:
        print(
            f"  {agent['label']:<16} {agent['state']:<12} "
            f"invited={agent['total_invited']:<6} gagal={agent['total_failed']:<5} "
            f"siap dalam {agent['available_in']}s"
        )
    if snap["errors"]:
        print("\nError terbanyak:")
        for err in snap["errors"][:5]:
            print(f"  {err['error_code']:<24} {err['n']}")
    return 0


def cmd_doctor(args) -> int:
    load_dotenv(args.env)
    problems = 0

    print("Memeriksa kesiapan lingkungan…\n")
    print(f"  Python           : {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print("    x Butuh Python 3.10 atau lebih baru.")
        problems += 1

    try:
        cfg = load_config(args.config)
        print(f"  Konfigurasi      : OK ({args.config})")
    except ConfigError as exc:
        print(f"  Konfigurasi      : GAGAL — {exc}")
        return 1

    print(f"  Mode Telegram    : {cfg.telegram.mode}")
    if cfg.telegram.mode == "live":
        try:
            cfg.telegram.credentials()
            print("  Kredensial API   : OK")
        except ConfigError as exc:
            print(f"  Kredensial API   : GAGAL — {exc}")
            problems += 1
        try:
            import telethon  # noqa: F401

            print("  Telethon         : terpasang")
        except ImportError:
            print("  Telethon         : BELUM terpasang (pip install -r requirements.txt)")
            problems += 1
        missing = [
            a.label for a in cfg.agents if not Path(f"{a.session}.session").exists()
        ]
        if missing:
            print(f"  Sesi agent       : {len(missing)} belum login → {', '.join(missing)}")
            print("                     jalankan: python -m tsc login --agent <label>")
            problems += 1
        else:
            print("  Sesi agent       : semua file sesi ada")
    else:
        print("  Kredensial API   : tidak diperlukan (mode simulasi)")

    print(f"  Jumlah agent     : {len(cfg.agents)} (minimal > {cfg.min_agents})")

    notifier = TelegramNotifier(cfg.notifier)
    ok, detail = notifier.check()
    print(f"  Bot notifikasi   : {'OK' if ok else 'GAGAL'} — {detail}")
    if not ok:
        problems += 1

    advisor = TriageAdvisor(cfg.llm)
    if not cfg.llm.enabled:
        print("  Triage LLM       : dinonaktifkan di config (aturan bawaan tetap jalan)")
    elif advisor.available:
        print(f"  Triage LLM       : OK — model {cfg.llm.model}")
    else:
        print(f"  Triage LLM       : env {cfg.llm.api_key_env} kosong "
              "(aturan bawaan tetap jalan)")

    try:
        store = Store(cfg.database)
        store.close()
        print(f"  Database         : OK ({cfg.database})")
    except Exception as exc:
        print(f"  Database         : GAGAL — {exc}")
        problems += 1

    print(f"\n{'Siap dijalankan.' if problems == 0 else f'{problems} masalah perlu dibereskan.'}")
    return 0 if problems == 0 else 1


def cmd_login(args) -> int:
    """Login interaktif satu akun agent (hanya relevan di mode live)."""
    load_dotenv(args.env)
    cfg = load_config(args.config)
    if cfg.telegram.mode != "live":
        print("Perintah ini hanya untuk mode live. Ubah telegram.mode di config.")
        return 1

    agent = next((a for a in cfg.agents if a.label == args.agent), None)
    if agent is None:
        print(f"Agent '{args.agent}' tidak ada di config. "
              f"Pilihan: {', '.join(a.label for a in cfg.agents)}")
        return 1

    try:
        from telethon import TelegramClient
    except ImportError:
        print("Telethon belum terpasang. Jalankan: pip install -r requirements.txt")
        return 1

    api_id, api_hash = cfg.telegram.credentials()
    Path(agent.session).parent.mkdir(parents=True, exist_ok=True)

    async def go():
        client = TelegramClient(agent.session, api_id, api_hash)
        # start() akan menanyakan nomor telepon dan kode OTP di terminal.
        await client.start(phone=agent.phone or None)
        me = await client.get_me()
        print(f"Berhasil login sebagai @{me.username or me.id} untuk agent '{agent.label}'.")
        await client.disconnect()

    asyncio.run(go())
    return 0


def cmd_optout(args) -> int:
    cfg, store, engine = _bootstrap(args)
    store.add_optout(engine.campaign_id, args.user_id, args.reason)
    store.log_event(
        campaign_id=engine.campaign_id,
        severity=Severity.INFO,
        kind="optout.added",
        message=f"User {args.user_id} dimasukkan ke daftar jangan-undang (CLI).",
        data={"reason": args.reason},
    )
    print(f"User {args.user_id} tidak akan diundang. "
          f"Total daftar: {store.count_optout(engine.campaign_id)}")
    return 0


# -------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsc",
        description="Telegram Scraper Center — pemindahan member antar grup yang Anda kelola.",
    )
    parser.add_argument("--config", default=os.environ.get("TSC_CONFIG", DEFAULT_CONFIG),
                        help="path file konfigurasi (default: config.json)")
    parser.add_argument("--env", default=".env", help="path file .env (default: .env)")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dashboard", help="buka dashboard web")
    p.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis")
    p.set_defaults(func=cmd_dashboard)

    sub.add_parser("run", help="jalankan campaign tanpa dashboard").set_defaults(func=cmd_run)
    sub.add_parser("validate", help="cek admin target & source").set_defaults(func=cmd_validate)
    sub.add_parser("plan", help="susun batch dan tampilkan rencana").set_defaults(func=cmd_plan)
    sub.add_parser("status", help="ringkasan kondisi terakhir").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="periksa kesiapan lingkungan").set_defaults(func=cmd_doctor)

    p = sub.add_parser("login", help="login satu akun agent (mode live)")
    p.add_argument("--agent", required=True, help="label agent sesuai config")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("optout", help="masukkan user ke daftar jangan-undang")
    p.add_argument("user_id", type=int)
    p.add_argument("--reason", default="", help="catatan alasan")
    p.set_defaults(func=cmd_optout)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"\nKonfigurasi bermasalah: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
