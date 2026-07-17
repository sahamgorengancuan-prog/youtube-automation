"""Command-line interface.

``scistudio-v10 TOPIC REFERENCE_VIDEO --config config.json`` runs plan-only
by default (no paid image generation); pass ``--live`` to run the paid FLUX
studio path and ``--render`` to render the final video.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config_models import StudioConfig
from .security import redact_secrets, redacted_exception_text

__version__ = "10.2.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scistudio-v10",
        description="Agentic scientific animation studio (OpenAI GPT reasoning, BFL FLUX Kontext art).",
        epilog="Plan-only is the default; use --live for paid generation.",
    )
    parser.add_argument("topic", nargs="?", help="Video topic / question")
    parser.add_argument("reference_video", nargs="?", help="Path to the style reference MP4")
    parser.add_argument("--config", help="Path to the studio config JSON")
    parser.add_argument("--live", action="store_true", help="Run live generation (paid provider calls)")
    parser.add_argument("--render", action="store_true", help="Render the final video")
    parser.add_argument("--force", action="store_true", help="Ignore stage caches")
    parser.add_argument("--job-id", default=None, help="Explicit job identifier")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.topic or not args.reference_video or not args.config:
        parser.print_usage(sys.stderr)
        print("error: topic, reference_video and --config are required", file=sys.stderr)
        return 2

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"error: config file not found: {config_path}", file=sys.stderr)
        return 2
    try:
        settings = StudioConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
    except Exception as exc:
        print(f"error: invalid configuration: {redacted_exception_text(exc, 500)}", file=sys.stderr)
        return 2

    reference = Path(args.reference_video)
    if not reference.is_file():
        print(f"error: reference video not found: {reference}", file=sys.stderr)
        return 2

    from .pipeline import ScientificMotionStudioV10

    try:
        result = ScientificMotionStudioV10(settings).run(
            args.topic,
            str(reference),
            plan_only=not args.live,
            render_video=args.render,
            force=args.force,
            job_id=args.job_id,
        )
    except Exception as exc:
        print(f"error [{type(exc).__name__}]: {redacted_exception_text(exc, 800)}", file=sys.stderr)
        return 1

    print(json.dumps(redact_secrets(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
