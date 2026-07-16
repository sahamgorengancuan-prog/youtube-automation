from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .pipeline import ScientificMotionStudioV10
from .utils import save_json


def run_batch(
    studio: ScientificMotionStudioV10, jobs: Iterable[dict[str, Any]], output_manifest: str | Path
) -> list[dict[str, Any]]:
    results = []
    for item in jobs:
        try:
            result = studio.run(
                item["topic"],
                item["reference_video"],
                plan_only=bool(item.get("plan_only", True)),
                render_video=bool(item.get("render_video", False)),
                force=bool(item.get("force", False)),
                job_id=item.get("job_id"),
            )
            results.append({"ok": True, "result": result})
        except Exception as exc:
            results.append(
                {"ok": False, "topic": item.get("topic", ""), "error": str(exc), "error_type": type(exc).__name__}
            )
    save_json(output_manifest, results)
    return results
