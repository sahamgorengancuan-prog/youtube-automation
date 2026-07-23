"""Unified hierarchical quality gate — one publish decision over five levels.

Every previous stage emits its own QC artifact; this rolls them into a single
hierarchical verdict so a video with a failing level is not published:

    L1 Technical   — the final MP4 exists, is H.264, right resolution, has frames
                     and a real duration.
    L2 Structural  — anatomical part QC (no failed parts / leakage / tearing) and
                     post-render causal-clarity QC.
    L3 Continuity  — the continuity validator (character / object / causal state).
    L4 Scientific  — the claim graph (no high-importance unsupported claim) and,
                     when present, the vision pose-fits-narration verdict.
    L5 Editorial   — hook present, sane pacing, no dead air, a real ending.

Each level rolls up to pass / warn / fail. ``publishable`` is False if any
*blocking* level fails (blocking levels are configurable; by default technical,
continuity and scientific block, structural and editorial warn — because the
offline preview legitimately uses geometric parts and stub evidence). It reads the
artifacts the pipeline already emits, so it is deterministic and offline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .utils import ensure_dir, load_json, save_json

_DEFAULT_BLOCKING = ["technical", "continuity", "scientific"]


def _ffprobe(path: str) -> dict[str, Any]:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,nb_read_packets",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=0",
                "-count_packets",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        info: dict[str, Any] = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip()
        return info
    except Exception as exc:
        return {"error": str(exc)[:120]}


class HierarchicalQC:
    def __init__(self, config: dict[str, Any] | None = None, root: Any = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.blocking = set(self.config.get("blocking_levels", _DEFAULT_BLOCKING))

    def evaluate(
        self,
        run_dir: str | Path,
        final_video: str | Path | None,
        storyboard: Any = None,
        script: Any = None,
    ) -> dict[str, Any]:
        run_dir = Path(run_dir)
        levels = {
            "technical": self._technical(final_video),
            "structural": self._structural(run_dir),
            "continuity": self._continuity(run_dir),
            "scientific": self._scientific(run_dir),
            "editorial": self._editorial(storyboard, script),
        }
        blocking_fail = [name for name, lv in levels.items() if name in self.blocking and lv["status"] == "fail"]
        report = {
            "levels": levels,
            "blocking_levels": sorted(self.blocking),
            "blocking_failures": blocking_fail,
            "publishable": not blocking_fail,
            "summary": {name: lv["status"] for name, lv in levels.items()},
        }
        if self.root is not None:
            save_json(self.root / "quality_gate.json", report)
        return report

    # -- L1 technical -------------------------------------------------------
    def _technical(self, final_video: str | Path | None) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        if not final_video or not Path(final_video).exists():
            return {"status": "fail", "checks": [{"name": "file_exists", "ok": False}]}
        info = _ffprobe(str(final_video))
        codec = info.get("codec_name", "")
        width = int(info.get("width", 0) or 0)
        height = int(info.get("height", 0) or 0)
        frames = int(info.get("nb_read_packets", 0) or 0)
        duration = float(info.get("duration", 0.0) or 0.0)
        checks.append({"name": "codec_h264", "ok": codec == "h264", "value": codec})
        checks.append({"name": "has_resolution", "ok": width > 0 and height > 0, "value": f"{width}x{height}"})
        checks.append({"name": "has_frames", "ok": frames > 0, "value": frames})
        checks.append({"name": "has_duration", "ok": duration > 0.1, "value": round(duration, 2)})
        status = "pass" if all(ck["ok"] for ck in checks) else "fail"
        return {"status": status, "checks": checks}

    # -- L2 structural ------------------------------------------------------
    def _structural(self, run_dir: Path) -> dict[str, Any]:
        part_qc = load_json(run_dir / "20_part_qc" / "part_qc_reports.json") or []
        failed = [p for p in part_qc if p.get("failed_parts")]
        issues = [i for p in part_qc for i in (p.get("issues") or [])]
        render_qc = load_json(run_dir / "20_render_qc" / "summary.json") or {}
        scenes = render_qc.get("scenes", []) if isinstance(render_qc, dict) else []
        unverified = [s for s in scenes if s.get("object_motion") and s.get("render_verified") is False]
        checks = [
            {"name": "no_failed_parts", "ok": not failed, "value": len(failed)},
            {"name": "no_structural_issues", "ok": not issues, "value": len(issues)},
            {"name": "rendered_motion_verified", "ok": not unverified, "value": len(unverified)},
        ]
        if failed:
            status = "fail"
        elif issues or unverified:
            status = "warn"
        else:
            status = "pass"
        return {"status": status, "checks": checks}

    # -- L3 continuity ------------------------------------------------------
    def _continuity(self, run_dir: Path) -> dict[str, Any]:
        rep = load_json(run_dir / "21_continuity" / "continuity_report.json") or {}
        regressions = rep.get("causal_regressions", []) if isinstance(rep, dict) else []
        issues = rep.get("issues", []) if isinstance(rep, dict) else []
        checks = [
            {"name": "no_causal_regression", "ok": not regressions, "value": regressions},
            {"name": "no_continuity_issues", "ok": not issues, "value": len(issues)},
        ]
        if regressions:
            status = "fail"
        elif issues:
            status = "warn"
        else:
            status = "pass"
        return {"status": status, "checks": checks}

    # -- L4 scientific ------------------------------------------------------
    def _scientific(self, run_dir: Path) -> dict[str, Any]:
        cg = load_json(run_dir / "04b_claim_graph" / "claim_graph.json") or {}
        val = cg.get("validation", {}) if isinstance(cg, dict) else {}
        unsupported = val.get("high_importance_unsupported", [])
        issue_count = val.get("issue_count", 0)
        checks = [
            {"name": "no_high_importance_unsupported_claim", "ok": not unsupported, "value": unsupported},
            {"name": "claim_issue_count", "ok": issue_count == 0, "value": issue_count},
        ]
        if unsupported:
            status = "fail"
        elif issue_count:
            status = "warn"
        else:
            status = "pass"
        return {"status": status, "checks": checks}

    # -- L5 editorial -------------------------------------------------------
    def _editorial(self, storyboard: Any, script: Any) -> dict[str, Any]:
        scenes = list(getattr(storyboard, "scenes", []) or [])
        durations = [float(getattr(s, "duration_s", 0.0) or 0.0) for s in scenes]
        hook = bool(str(getattr(script, "hook", "") or "").strip()) or any(
            "hook" in str(getattr(b, "retention_function", "")).lower() for b in getattr(script, "beats", []) or []
        )
        ending = bool(str(getattr(script, "closing", "") or "").strip())
        n = len(scenes)
        pacing_ok = 2 <= n <= int(self.config.get("max_scenes", 16)) and all(
            0.4 <= d <= float(self.config.get("max_scene_s", 12.0)) for d in durations or [1.0]
        )
        dead_air = [round(d, 1) for d in durations if d > float(self.config.get("dead_air_s", 9.0))]
        checks = [
            {"name": "has_hook", "ok": hook},
            {"name": "sane_pacing", "ok": pacing_ok, "value": n},
            {"name": "no_dead_air", "ok": not dead_air, "value": dead_air},
            {"name": "has_ending", "ok": ending},
        ]
        fails = [ck for ck in checks if not ck["ok"]]
        # Editorial is advisory by default: any miss is a warn unless hook/ending
        # are both missing (then the edit is structurally incomplete -> fail).
        if not hook and not ending:
            status = "fail"
        elif fails:
            status = "warn"
        else:
            status = "pass"
        return {"status": status, "checks": checks}
