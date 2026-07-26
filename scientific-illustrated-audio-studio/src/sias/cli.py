"""SIAS command-line interface. --dry-run NEVER calls paid APIs; every command
prints its plan (provider, model, estimated calls, budget) before doing work."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import SIASConfig, load_config
from .exceptions import SIASError
from .pipeline.orchestrator import PILOT_SCENE_COUNT, Orchestrator

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "default.yaml"


def _load(args: argparse.Namespace, mode: str | None = None) -> SIASConfig:
    paths = [args.config or str(DEFAULT_CONFIG)]
    overrides: dict[str, Any] = {}
    if getattr(args, "episode", None):
        import yaml

        episode = yaml.safe_load(Path(args.episode).read_text(encoding="utf-8")) or {}
        overrides = episode
    if mode:
        overrides["run_mode"] = mode
    return load_config(*paths, overrides=overrides)


def _print(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        for key, value in payload.items():
            print(f"{key}: {value if not isinstance(value, (dict, list)) else json.dumps(value, default=str)[:200]}")


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load(args)
    orch = Orchestrator(cfg, args.workspace)
    _print(args, {"initialized": str(orch.workspace), "episode_id": orch.episode_id})
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = _load(args)
    report = {
        "version": __version__,
        "run_mode": cfg.run_mode,
        "ffmpeg": bool(__import__("shutil").which("ffmpeg")),
        "ffprobe": bool(__import__("shutil").which("ffprobe")),
        "keys": {
            "BFL_API_KEY": bool(os.environ.get("BFL_API_KEY")),
            "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")),
            "OPENROUTER_API_KEY": bool(os.environ.get("OPENROUTER_API_KEY")),
        },
        "paid_calls_this_command": False,
    }
    _print(args, report)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _load(args, mode="plan")
    orch = Orchestrator(cfg, args.workspace)
    result = orch.plan(force=args.force)
    _print(
        args,
        {
            "episode_id": result["episode_id"],
            "workspace": str(orch.workspace),
            "beats": len(result.get("beats", [])),
            "scenes": len(result.get("scenes", [])),
            "selected_hook": result.get("selected_hook", {}).get("text", ""),
            "script_words": result.get("script", {}).get("word_count", 0),
            "est_duration_s": result.get("script", {}).get("est_duration_s", 0),
            "paid_calls": 0,
        },
    )
    return 0


def _paid_preview(cfg: SIASConfig, stage: str, calls: dict[str, int]) -> dict[str, Any]:
    return {
        "stage": stage,
        "dry_run": True,
        "paid_calls_executed": 0,
        "planned_calls": calls,
        "budget_caps": {
            "max_image_calls": cfg.budgets.max_image_calls,
            "max_vision_calls": cfg.budgets.max_vision_calls,
            "max_tts_characters": cfg.budgets.max_tts_characters,
        },
        "note": "live execution requires API keys, the matching run_mode, and no --dry-run",
    }


def cmd_style_lock(args: argparse.Namespace) -> int:
    cfg = _load(args, mode="style_lock")
    calls = {"bfl_images": 3 + 1 + 1, "vision_reviews": 2 * 5}
    if args.dry_run:
        _print(args, _paid_preview(cfg, "style_lock", calls))
        return 0
    print("live style-lock requires configured adapters; wire transports in notebooks/SIAS_Control_Center.ipynb", file=sys.stderr)
    return 2


def cmd_pilot(args: argparse.Namespace) -> int:
    cfg = _load(args, mode="pilot")
    calls = {
        "bfl_images": PILOT_SCENE_COUNT * cfg.visual.candidates_normal + 2,
        "vision_reviews": 2 * (PILOT_SCENE_COUNT * cfg.visual.candidates_normal),
        "tts_characters": 1200,
        "transcriptions": 1,
    }
    if args.dry_run:
        _print(args, _paid_preview(cfg, "pilot", calls))
        return 0
    print("live pilot requires configured adapters; use the control-center notebook sections 14-19", file=sys.stderr)
    return 2


def cmd_produce(args: argparse.Namespace) -> int:
    cfg = _load(args, mode="production")
    if args.dry_run:
        _print(args, _paid_preview(cfg, "production", {"note_gate": 1}))
        return 0
    from .pipeline.modes import check_production_unlock

    qc_path = Path(args.workspace) / "qc" / "pilot_qc.json"
    status = None
    if qc_path.exists():
        status = json.loads(qc_path.read_text()).get("status")
    warnings = check_production_unlock(status, cfg.allow_production_without_pilot)
    _print(args, {"production_unlocked": True, "warnings": warnings})
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    cfg = _load(args, mode="render_only")
    _print(args, {"mode": "render_only", "paid_calls": 0, "backend": cfg.render.backend,
                  "note": "supply scenes/timings/audio via the notebook or API; smoke test: pytest tests/integration/test_smoke_render.py"})
    return 0


def cmd_qc(args: argparse.Namespace) -> int:
    cfg = _load(args)
    video = Path(args.workspace) / "render" / "final.mp4"
    if not video.exists():
        _print(args, {"qc": "SKIP", "reason": f"no final render at {video}"})
        return 1
    from .qc.final_av import check_final_av
    from .qc.report import build_report, write_report

    duration = float(args.narration_duration or 0)
    checks = check_final_av(str(video), duration, cfg.render.duration_tolerance_s)
    report = build_report(checks)
    write_report(report, Path(args.workspace) / "qc", "final_qc")
    _print(args, {"status": report.status, "checks": len(report.checks)})
    return 0 if report.status.startswith("PASS") else 1


def cmd_report(args: argparse.Namespace) -> int:
    qc_dir = Path(args.workspace) / "qc"
    reports = sorted(qc_dir.glob("*.json")) if qc_dir.exists() else []
    _print(args, {"reports": [str(p) for p in reports]})
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    cfg = _load(args, mode="repair")
    if args.dry_run:
        _print(args, _paid_preview(cfg, "repair", {"bfl_images": 1, "vision_reviews": 2}))
        return 0
    _print(args, {"note": f"repair of scene {args.scene} requires configured adapters (notebook section 22)"})
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sias", description="Scientific Illustrated Audio Studio")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true", help="never call paid APIs")
        p.add_argument("--config", default="", help="config YAML (default: configs/default.yaml)")
        p.add_argument("--workspace", default="workspace", help="workspace base directory")
        p.add_argument("--resume", action="store_true", help="reuse valid cached stages (default behavior)")
        p.add_argument("--force", action="store_true", help="ignore caches and re-run")
        p.add_argument("--json", action="store_true", help="JSON output")
        p.add_argument("--episode", default="", help="episode YAML overriding project/story fields")

    for name, fn in [
        ("init", cmd_init), ("audit", cmd_audit), ("plan", cmd_plan),
        ("style-lock", cmd_style_lock), ("pilot", cmd_pilot), ("produce", cmd_produce),
        ("render", cmd_render), ("qc", cmd_qc), ("report", cmd_report),
    ]:
        p = sub.add_parser(name)
        common(p)
        if name == "qc":
            p.add_argument("--narration-duration", default="0")
        p.set_defaults(func=fn)

    p = sub.add_parser("repair")
    common(p)
    p.add_argument("--scene", required=True)
    p.set_defaults(func=cmd_repair)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SIASError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
