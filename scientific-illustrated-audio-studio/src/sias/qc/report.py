"""QC roll-up: PASS / PASS_WITH_WARNINGS / FAIL / HUMAN_DECISION_REQUIRED.
A final package may be published only with PASS (or an explicit, documented
human override)."""

from __future__ import annotations

from pathlib import Path

from ..filesystem import atomic_write_bytes, atomic_write_json
from ..schemas import QCCheck, QCReport


def build_report(checks: list[QCCheck], human_decision_required: bool = False) -> QCReport:
    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]
    if human_decision_required:
        status = "HUMAN_DECISION_REQUIRED"
    elif fails:
        status = "FAIL"
    elif warns:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"
    return QCReport(status=status, checks=checks, warnings=[c.detail for c in warns])


def write_report(report: QCReport, out_dir: str | Path, name: str = "final_qc") -> tuple[Path, Path]:
    out = Path(out_dir)
    js = atomic_write_json(out / f"{name}.json", report.model_dump())
    lines = [f"# QC report — {report.status}", ""]
    for check in report.checks:
        mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[check.status]
        lines.append(f"- {mark} `{check.check_id}` ({check.level}) {check.detail}".rstrip())
    md = atomic_write_bytes(out / f"{name}.md", "\n".join(lines).encode("utf-8"))
    return js, md


def publishable(report: QCReport, human_override: bool = False) -> bool:
    return report.status == "PASS" or (human_override and report.status != "FAIL")
