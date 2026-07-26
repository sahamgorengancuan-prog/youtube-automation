"""Build the SIAS notebooks. The control center is an operational interface —
all reusable logic lives in src/sias. No API call happens on import; every paid
section shows provider/model/estimates/cache/run-mode and an explicit guard."""

from __future__ import annotations

import ast
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks"

GUARD = '''\
def paid_section_guard(section: str, provider: str, model: str, est_calls: int, est_budget_usd: float):
    """Display the paid-call contract and refuse to run unless explicitly armed."""
    print(f"SECTION       : {section}")
    print(f"PROVIDER      : {provider}")
    print(f"MODEL         : {model}")
    print(f"EST. CALLS    : {est_calls}")
    print(f"EST. MAX COST : ${est_budget_usd:.2f}")
    print(f"RUN MODE      : {CFG.run_mode}")
    print(f"CACHE         : {'enabled' if CFG.cache.enabled else 'disabled'}")
    if CFG.run_mode == "plan":
        raise RuntimeError("run_mode=plan forbids paid calls. Change run_mode + set ARM_PAID_CALLS=True deliberately.")
    if not ARM_PAID_CALLS:
        raise RuntimeError("ARM_PAID_CALLS is False — set it to True in the cell above to spend on this section.")
    print("ARMED — proceeding with paid calls under budget caps.")
'''

SECTIONS: list[tuple[str, str]] = [
    ("1. Environment and dependency checks", '''\
import shutil, sys
print("python :", sys.version.split()[0])
for tool in ("ffmpeg", "ffprobe"):
    print(f"{tool:7}:", shutil.which(tool) or "MISSING (required for render)")
import sias
print("sias   :", sias.__version__)
'''),
    ("2. Secrets validation", '''\
import os
from sias.logging_utils import register_secret
KEYS = ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")
for k in KEYS:
    v = os.environ.get(k, "")
    if v:
        register_secret(v)
    print(f"{k}: {'present' if v else 'MISSING'}")
print("Secrets are never printed or logged; redaction is registered.")
'''),
    ("3. Configuration loading", '''\
from pathlib import Path
from sias.config import load_config
CONFIG_PATH = Path("../configs/default.yaml")
CFG = load_config(CONFIG_PATH)
ARM_PAID_CALLS = False  # flip deliberately, per paid section, never globally
print("run_mode:", CFG.run_mode, "| identity:", CFG.visual.identity_name)
'''),
    ("4. Project workspace initialization", '''\
from sias.pipeline.orchestrator import Orchestrator
TOPIC = "What happens if it rains nonstop for one year?"
CFG = CFG.model_copy(update={"project": CFG.project.model_copy(update={"topic": TOPIC})})
ORCH = Orchestrator(CFG, "../workspace")
print("workspace:", ORCH.workspace)
'''),
    ("5. Source audit", '''\
print(open("../docs/source_audit.md").read()[:1500])
'''),
    ("6. Topic and audience setup", '''\
print("topic   :", CFG.project.topic)
print("audience:", CFG.project.audience)
print("duration:", CFG.project.target_duration_min_s, "-", CFG.project.target_duration_max_s, "s")
'''),
    ("7. Research claim inspection", '''\
PLAN = ORCH.plan()
for c in PLAN["research_pack"]["claims"]:
    print(c["claim_id"], "|", c["statement"][:80], "| sources:", len(c["sources"]), "| caveat:", bool(c["caveat"]))
for g in PLAN["source_guard"]:
    print(g["status"], g["check_id"], g.get("detail", ""))
'''),
    ("8. Hook generation and selection", '''\
for h in PLAN["hooks"]:
    print(h["hook_id"], h["kind"], f"total={h['total']}", "|", h["text"])
print("SELECTED:", PLAN["selected_hook"]["text"])
'''),
    ("9. Story-beat generation", '''\
for b in PLAN["beats"]:
    print(b["beat_id"], b["role"], "|", b["emotional_from"], "→", b["emotional_to"], "|", b["narration"][:60])
'''),
    ("10. Spoken-script review", '''\
s = PLAN["script"]
print("words:", s["word_count"], "| est duration:", s["est_duration_s"], "s | catchphrase:", s["catchphrase_count"])
print()
print(s["full_text"])
for q in PLAN["story_qc"]:
    print(q["status"], q["check_id"], q.get("detail", ""))
'''),
    ("11. Style-bible review", '''\
from sias.filesystem import load_json
bible = load_json(ORCH.workspace / "style_lock" / "style_bible.json")
print("identity:", bible["identity_name"])
print("palette :", bible["palette"])
print("motifs  :", ", ".join(bible["motifs"][:5]), "…")
'''),
    ("12. Style-lock generation (PAID)", '''\
paid_section_guard("style_lock", "BFL", CFG.visual.production_model,
                   est_calls=5, est_budget_usd=0.60)
# Live path: build BFL adapter with a real transport, generate
# master_style_board (3 candidates), character_sheet, prop_sheet.
# See docs/provider_contracts.md for the transport contract.
'''),
    ("13. Style-lock approval", '''\
STYLE_LOCK = {"machine_status": "PENDING", "human_status": "PENDING"}
# After dual vision review PASSes, set human_status = "APPROVED" here — a human
# decision, deliberately manual. Production cannot begin before that.
print(STYLE_LOCK)
'''),
    ("14. Pilot scene generation (PAID)", '''\
paid_section_guard("pilot_scenes", "BFL", CFG.visual.production_model,
                   est_calls=4 * CFG.visual.candidates_normal, est_budget_usd=1.00)
from sias.schemas import SceneSpec
scenes = [SceneSpec.model_validate(s) for s in PLAN["scenes"]]
PILOT_SCENES = ORCH.pilot_scene_specs(scenes)
print([s.scene_id for s in PILOT_SCENES])
'''),
    ("15. Candidate tournament", '''\
# Runs automatically inside the pilot generation loop via
# sias.vision.tournament.run_tournament (dual review + consensus + repair).
from sias.vision.tournament import candidate_count
for s in (PILOT_SCENES if 'PILOT_SCENES' in dir() else []):
    print(s.scene_id, s.beat_role, "candidates:", candidate_count(s.beat_role))
'''),
    ("16. Pilot TTS (PAID)", '''\
paid_section_guard("pilot_tts", "OpenAI", CFG.audio.tts_model,
                   est_calls=1, est_budget_usd=0.05)
# One full narration track (never per-scene): sias.audio.tts.synthesize_narration
'''),
    ("17. Alignment inspection", '''\
# After transcription: sias.audio.alignment.align_scenes derives scene timings
# from word timestamps; final end == narration duration.
print("alignment artifacts land in", ORCH.workspace / "alignment")
'''),
    ("18. Pilot render", '''\
# render_only is unpaid: ORCH.render_only(scenes, timings, approved_images,
#                                         audio_path, narration_duration)
print("pilot render output:", ORCH.workspace / "render" / "pilot.mp4")
'''),
    ("19. Pilot QC report", '''\
from sias.qc.report import build_report, write_report
# checks = story + audio + visual + final_av checks collected from the pilot
# report = build_report(checks); write_report(report, ORCH.workspace / "qc", "pilot_qc")
print("pilot QC gate: production unlocks only on PASS")
'''),
    ("20. Production unlock", '''\
from sias.pipeline.modes import check_production_unlock
import json, pathlib
qc_path = ORCH.workspace / "qc" / "pilot_qc.json"
status = json.loads(qc_path.read_text())["status"] if qc_path.exists() else None
try:
    warnings = check_production_unlock(status, CFG.allow_production_without_pilot)
    print("production unlocked", warnings or "")
except Exception as exc:
    print("LOCKED:", exc)
'''),
    ("21. Remaining scene generation (PAID)", '''\
paid_section_guard("production_scenes", "BFL", CFG.visual.production_model,
                   est_calls=CFG.budgets.max_image_calls, est_budget_usd=3.00)
'''),
    ("22. Repair queue (PAID)", '''\
paid_section_guard("repair", "BFL", CFG.visual.production_model,
                   est_calls=CFG.visual.max_repairs_per_scene, est_budget_usd=0.50)
# sias.vision.repair: conservative local edits, max 2 attempts, then
# HUMAN_DECISION_REQUIRED.
'''),
    ("23. Full narration (PAID)", '''\
paid_section_guard("full_tts", "OpenAI", CFG.audio.tts_model,
                   est_calls=1, est_budget_usd=0.10)
'''),
    ("24. Full alignment", '''\
print("full alignment: transcribe generated narration -> align_scenes -> scene timings")
'''),
    ("25. Final render", '''\
print("final render: ORCH.render_only(...) -> render/final.mp4 (ffmpeg baseline)")
'''),
    ("26. Final QC", '''\
from sias.qc.final_av import check_final_av
print("final QC: file/stream/duration integrity + blank frames + audio + story + visual")
'''),
    ("27. Export package", '''\
print("package dir:", ORCH.workspace / "package")
print("contents: final.mp4, final.srt, episode_manifest.json, final_qc.json, final_qc.md")
'''),
]


def build_control_center() -> Path:
    nb = nbf.v4.new_notebook()
    cells = [
        nbf.v4.new_markdown_cell(
            "# SIAS Control Center\n\n"
            "Operational interface for the Scientific Illustrated Audio Studio. "
            "All logic lives in `src/sias`; this notebook only orchestrates.\n\n"
            "**No API call happens on import.** Default `run_mode: plan` performs zero paid calls."
        ),
        nbf.v4.new_code_cell("import sys; sys.path.insert(0, '../src')\n" + GUARD),
    ]
    for title, code in SECTIONS:
        cells.append(nbf.v4.new_markdown_cell(f"## {title}"))
        cells.append(nbf.v4.new_code_cell(code))
    nb.cells = cells
    out = NB_DIR / "SIAS_Control_Center.ipynb"
    nbf.write(nb, out)
    return out


def build_workbench(name: str, title: str, body: str) -> Path:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(f"# {title}"),
        nbf.v4.new_code_cell("import sys; sys.path.insert(0, '../src')"),
        nbf.v4.new_code_cell(body),
    ]
    out = NB_DIR / name
    nbf.write(nb, out)
    return out


def validate(path: Path) -> None:
    nb = nbf.read(path, as_version=4)
    for cell in nb.cells:
        if cell.cell_type == "code":
            ast.parse(cell.source)


def main() -> None:
    paths = [build_control_center()]
    paths.append(build_workbench(
        "01_Style_Lock_Workbench.ipynb", "Style Lock Workbench",
        "from sias.config import load_config\n"
        "from sias.style.bible import build_style_bible\n"
        "CFG = load_config('../configs/default.yaml')\n"
        "print(build_style_bible(CFG).model_dump_json(indent=2))",
    ))
    paths.append(build_workbench(
        "02_Story_Workbench.ipynb", "Story Workbench",
        "from sias.research.claims import build_research_pack\n"
        "from sias.story.beats import build_story_beats\n"
        "from sias.story.hooks import generate_hooks\n"
        "pack = build_research_pack('What happens if it rains nonstop for one year?')\n"
        "for b in build_story_beats(pack): print(b.beat_id, b.role, '|', b.narration)\n"
        "for h in generate_hooks(pack.topic): print(h.hook_id, h.total, h.text)",
    ))
    paths.append(build_workbench(
        "03_QC_Inspection.ipynb", "QC Inspection",
        "import json, pathlib\n"
        "for p in sorted(pathlib.Path('../workspace').glob('*/qc/*.json')):\n"
        "    print(p, '->', json.loads(p.read_text()).get('status'))",
    ))
    for p in paths:
        validate(p)
        print("built + validated:", p.name)


if __name__ == "__main__":
    main()
