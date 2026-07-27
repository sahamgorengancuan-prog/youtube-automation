"""Build the SIAS Agentic Colab notebooks. Indonesian-first UI, English code.
Every code cell is written so a keyless top-to-bottom run in plan mode SKIPS
paid sections instead of crashing — that exact run is a CI test."""

from __future__ import annotations

import ast
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks"

BOOT = r'''# @title 🚀 Inisialisasi (aman — tanpa panggilan API) { display-mode: "form" }
# Source mode: "github_repository" (klon repo), "standalone" (pip), "uploaded_zip" (unzip ke /content)
SOURCE_MODE   = "github_repository"  # @param ["github_repository", "standalone", "uploaded_zip"]
GITHUB_REPO   = "https://github.com/sahamgorengancuan-prog/youtube-automation"
UI_LANG       = "id"                 # @param ["id", "en"]

import os, sys, subprocess, pathlib
IN_COLAB = "google.colab" in sys.modules or os.path.exists("/content")
REPO_DIR = None
if SOURCE_MODE == "github_repository" and IN_COLAB and not os.path.exists("/content/youtube-automation"):
    subprocess.run(["git", "clone", "--depth", "1", GITHUB_REPO, "/content/youtube-automation"], check=False)
for base in ("/content/youtube-automation", str(pathlib.Path.cwd().parent.parent), str(pathlib.Path.cwd())):
    p = pathlib.Path(base)
    if (p / "scientific-illustrated-audio-studio-colab" / "src").exists():
        REPO_DIR = p
        break
    if (p.name == "scientific-illustrated-audio-studio-colab") and (p / "src").exists():
        REPO_DIR = p.parent
        break
if REPO_DIR is None:
    REPO_DIR = pathlib.Path.cwd()
for rel in ("scientific-illustrated-audio-studio-colab/src", "scientific-illustrated-audio-studio/src"):
    cand = REPO_DIR / rel
    if cand.exists() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))
import sias_colab
from sias_colab.ui.texts import t
print(t("welcome_title", UI_LANG)); print(t("welcome_body", UI_LANG))
print("sias_colab", sias_colab.__version__, "| Colab:", IN_COLAB, "| repo:", REPO_DIR)
'''

SECTIONS: list[tuple[str, str, str]] = [
    ("1", "Selamat datang & cara kerja sistem / Welcome",
     '''\
from sias_colab.ui.texts import t
print(t("welcome_body", UI_LANG))
print()
print("Alur / Flow: plan (GRATIS) → canary → style lock → persetujuan manusia → pilot 4 adegan → produksi → QC → ekspor")
print("Gambar diam + narasi. Audio adalah sumber kebenaran timeline. Tidak ada video AI.")
'''),
    ("2", "Setup dependensi / Dependency setup",
     '''\
# Instalasi malas: hanya paket ringan; TANPA Node/Remotion/WhisperX kecuali dipilih di Lanjutan.
import importlib, subprocess, sys
def ensure(pkg, pip_name=None):
    try:
        importlib.import_module(pkg)
        print("ok:", pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pip_name or pkg], check=False)
        print("installed:", pip_name or pkg)
for pkg, pip_name in [("pydantic", None), ("yaml", "pyyaml"), ("PIL", "Pillow"), ("ipywidgets", None)]:
    ensure(pkg, pip_name)
import shutil
print("ffmpeg :", shutil.which("ffmpeg") or "MISSING")
print("ffprobe:", shutil.which("ffprobe") or "MISSING")
'''),
    ("3", "Koneksi Google Drive (opsional) / Google Drive",
     '''\
USE_DRIVE = True  # @param {type:"boolean"}
import os
WORKSPACE = "workspace_local"
if USE_DRIVE and IN_COLAB:
    try:
        from google.colab import drive
        drive.mount("/content/drive")
        WORKSPACE = "/content/drive/MyDrive/SIAS"
        os.makedirs(WORKSPACE, exist_ok=True)
        print("Drive terpasang →", WORKSPACE)
    except Exception as exc:
        print("Drive tidak tersedia (", str(exc)[:80], ") — memakai penyimpanan lokal:", WORKSPACE)
else:
    print("Mode tanpa Drive — workspace lokal:", WORKSPACE)
os.makedirs(WORKSPACE, exist_ok=True)
'''),
    ("4", "Kunci API / Secrets",
     '''\
# Prioritas: Colab Secrets (ikon kunci di kiri) → environment variable. Nilai TIDAK pernah ditampilkan.
import os
SECRET_NAMES = ["BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_KEY", "HF_API_KEY", "HF_API_SECRET"]
def read_secret(name):
    try:
        from google.colab import userdata
        v = userdata.get(name)
        if v: return v
    except Exception:
        pass
    return os.environ.get(name, "")
for name in SECRET_NAMES:
    v = read_secret(name)
    if v: os.environ[name] = v
    print(("🔑 " if v else "⛔ ") + name + ("  (tersedia)" if v else "  (belum ada — wajib hanya untuk tahap berbayar)"))
print("Catatan: Higgsfield (HF_*) OPSIONAL — sistem berjalan penuh tanpanya.")
'''),
    ("5", "Konfigurasi proyek / Project configuration",
     '''\
# @title 🎯 Pengaturan utama { display-mode: "form" }
TOPIC        = "What happens if it rains nonstop for one year?"  # @param {type:"string"}
AUDIENCE     = "curious general audience, age 15+"                # @param {type:"string"}
LANGUAGE     = "en"          # @param ["en", "id"]
DURATION_MAX = 75            # @param {type:"integer"}
ASPECT       = "9:16"        # @param ["9:16", "16:9", "1:1"]
VOICE        = "cedar"       # @param {type:"string"}
RUN_MODE     = "plan"        # @param ["plan", "canary", "style_lock", "pilot", "production", "repair", "render_only"]
HARD_BUDGET_IMAGES = 30      # @param {type:"integer"}
ARM_PAID_CALLS = False       # @param {type:"boolean"}

from sias_colab.config import load_studio_config
from sias_colab.studio import Studio
CFG = load_studio_config(
    str(REPO_DIR / "scientific-illustrated-audio-studio-colab" / "configs" / "default.yaml"),
    overrides={"project": {"topic": TOPIC, "audience": AUDIENCE, "language": LANGUAGE,
                            "aspect_ratio": ASPECT, "target_duration_max_s": DURATION_MAX},
               "audio": {"voice": VOICE},
               "budgets": {"max_image_calls": HARD_BUDGET_IMAGES}},
    colab={"run_mode": RUN_MODE, "arm_paid_calls": ARM_PAID_CALLS, "ui_language": UI_LANG},
)
STUDIO = Studio(CFG, WORKSPACE)
print("Episode:", STUDIO.episode_id, "| mode:", CFG.run_mode, "| ARM:", CFG.colab.arm_paid_calls)

def paid_gate(stage, secret, previous_gate=None, budget_kind="image", amount=1, confirmed=False):
    """SKIP (bukan crash) bila belum diizinkan — menampilkan alasan persisnya."""
    from sias_colab.exceptions import PaidCallBlockedError
    from sias_colab.ui.texts import t as _t
    try:
        STUDIO.guard_paid(stage, secret, previous_gate, confirmed, budget_kind, amount)
        print("✅ diizinkan / armed:", stage)
        return True
    except PaidCallBlockedError as exc:
        print("⏭️ ", _t("paid_blocked", UI_LANG)); print("   ", exc)
        return False
'''),
    ("6", "Perencanaan offline — GRATIS / Offline planning",
     '''\
PLAN = STUDIO.plan()
s = PLAN["engine"]["script"]
print("✔", t("plan_done", UI_LANG))
print("Beats:", len(PLAN["engine"]["beats"]), "| Scenes:", len(PLAN["engine"]["scenes"]),
      "| Kata:", s["word_count"], "| ± durasi:", s["est_duration_s"], "dtk | catchphrase:", s["catchphrase_count"])
print("Hook terpilih:", PLAN["agents"]["hook_tournament"]["selected"]["text"])
print("Kritik retensi:", PLAN["agents"]["retention_critic"]["issues"] or "bersih")
print("Panggilan berbayar: 0")
'''),
    ("7", "Tinjau storyboard / Storyboard review",
     '''\
for b in PLAN["engine"]["beats"]:
    print(f"{b['beat_id']} {b['role']:<14} {b['emotional_from']:>13} → {b['emotional_to']:<16} | {b['narration'][:70]}")
print()
print("NASKAH / SCRIPT:")
print(PLAN["engine"]["script"]["full_text"])
'''),
    ("8", "Canary penyedia — berbayar KECIL / Provider canary",
     '''\
# Bukti termurah bahwa semua kunci bekerja: 1 gambar + 1 edit + 2 review + 1 TTS + 1 transkripsi.
CONFIRM_CANARY = False  # @param {type:"boolean"}
if paid_gate("canary", "BFL_API_KEY", previous_gate="plan", confirmed=CONFIRM_CANARY):
    print("Sambungkan adapter live (lihat docs/provider_contracts.md), lalu:")
    print("from sias_colab.providers.canary import run_canary")
    print("report = run_canary(WORKSPACE + '/episodes/' + STUDIO.episode_id + '/provider_canary',")
    print("                    adapters=LIVE_ADAPTERS, budget=STUDIO.budget,")
    print("                    qwen_model=QWEN_SLUG, gemini_model=GEMINI_SLUG)")
    print("⚠ Tidak otomatis lanjut ke style lock — perlu tindakan Anda.")
'''),
    ("9", "Pembuatan Style Lock — berbayar / Style-lock creation",
     '''\
CONFIRM_STYLE_LOCK = False  # @param {type:"boolean"}
if paid_gate("style_lock", "BFL_API_KEY", previous_gate="canary", amount=5, confirmed=CONFIRM_STYLE_LOCK):
    print("Menghasilkan: master_style_board (3 kandidat) + character sheet + prop sheet + environment anchor")
    print("Semua lewat turnamen dual-review (Qwen struktural + Gemini editorial).")
'''),
    ("10", "Persetujuan manusia Style Lock / Human approval",
     '''\
# Dua gerbang terpisah: machine PASS *dan* persetujuan Anda. Pratinjau dulu, lalu setujui.
APPROVE_STYLE_LOCK = False  # @param {type:"boolean"}
from sias_colab.state import save_state
if APPROVE_STYLE_LOCK:
    STUDIO.state.approvals["style_lock_human"] = "APPROVED"
    STUDIO.state.mark("style_lock_human", "PASS")
    save_state(STUDIO.workspace, STUDIO.state)
    print("✅ Style lock DISETUJUI manusia.")
else:
    print("⏸ Belum disetujui — produksi tidak akan berjalan (human_status != APPROVED).")
'''),
    ("11", "Pilot 4 adegan — berbayar / Four-scene pilot",
     '''\
CONFIRM_PILOT = False  # @param {type:"boolean"}
if paid_gate("pilot", "BFL_API_KEY", previous_gate="style_lock_human", amount=10, confirmed=CONFIRM_PILOT):
    from sias.schemas import SceneSpec
    scenes = [SceneSpec.model_validate(x) for x in PLAN["engine"]["scenes"]]
    pilot = STUDIO.engine.pilot_scene_specs(scenes)
    print("Adegan pilot:", [f"{p.scene_id}:{p.beat_role}" for p in pilot])
    print("Lanjut: turnamen kandidat → review → TTS pilot (SATU trek) → alignment → render → QC.")
'''),
    ("12", "Tinjau pilot / Pilot review",
     '''\
import pathlib, json
qc = pathlib.Path(WORKSPACE) / "episodes" / STUDIO.episode_id / "qc" / "pilot_qc.json"
if qc.exists():
    print("Pilot QC:", json.loads(qc.read_text())["status"])
else:
    print("Belum ada pilot QC — jalankan bagian 11 dahulu.")
'''),
    ("13", "Buka kunci produksi / Production unlock",
     '''\
import json, pathlib
from sias.pipeline.modes import check_production_unlock
qc = pathlib.Path(WORKSPACE) / "episodes" / STUDIO.episode_id / "qc" / "pilot_qc.json"
status = json.loads(qc.read_text()).get("status") if qc.exists() else None
try:
    warns = check_production_unlock(status, CFG.engine.allow_production_without_pilot)
    STUDIO.state.mark("production_unlock", "PASS")
    print("🔓 Produksi TERBUKA.", warns or "")
except Exception as exc:
    print("🔒 TERKUNCI:", exc)
'''),
    ("14", "Produksi penuh — berbayar / Full production",
     '''\
CONFIRM_PRODUCTION = False  # @param {type:"boolean"}
if paid_gate("production", "BFL_API_KEY", previous_gate="production_unlock",
             amount=CFG.engine.budgets.max_image_calls, confirmed=CONFIRM_PRODUCTION):
    print("Menghasilkan adegan tersisa + narasi penuh + alignment + render final.")
    print("Checkpoint per adegan; putuskan-sambung aman — buka ulang notebook dan lanjut.")
'''),
    ("15", "QC final / Final QC",
     '''\
import pathlib
video = pathlib.Path(WORKSPACE) / "episodes" / STUDIO.episode_id / "render" / "final.mp4"
if video.exists():
    from sias_colab.qc import check_final_av, build_report, write_report
    dur = float(input("Durasi narasi (detik): ") or 0)
    checks = check_final_av(str(video), dur, CFG.engine.render.duration_tolerance_s)
    report = build_report(checks)
    write_report(report, video.parent.parent / "qc", "final_qc")
    print("Status:", report.status)
else:
    print("Belum ada final.mp4 — QC dilewati.")
'''),
    ("16", "Ekspor & unduh / Export and download",
     '''\
DO_EXPORT = False  # @param {type:"boolean"}
if DO_EXPORT:
    zip_path = STUDIO.export_package()
    print("📦", zip_path)
    if IN_COLAB:
        from google.colab import files
        files.download(str(zip_path))
else:
    print("Centang DO_EXPORT untuk membuat ZIP paket episode.")
'''),
    ("17", "Bantuan & pemecahan masalah / Troubleshooting",
     '''\
print(open(REPO_DIR / "scientific-illustrated-audio-studio-colab" / "docs" / "troubleshooting.md").read()[:2500])
'''),
]

DASHBOARD = '''\
# 📊 Dasbor / Dashboard — jalankan kapan saja untuk melihat status
from sias_colab.ui.dashboard import Dashboard
Dashboard(STUDIO.state, STUDIO.budget.snapshot(), STUDIO.secrets_present(),
          CFG.run_mode, lang=UI_LANG).render()
'''


def build_control_center() -> Path:
    nb = nbf.v4.new_notebook()
    nb.metadata["colab"] = {"name": "SIAS_Agentic_Colab_Control_Center.ipynb"}
    cells = [
        nbf.v4.new_markdown_cell(
            "# 🎬 SIAS — Agentic Colab Control Center\n\n"
            "**ID:** Studio cerita sains dari ilustrasi + narasi. Mode awal `plan` **GRATIS** — "
            "tidak ada panggilan API berbayar tanpa tindakan Anda.\n\n"
            "**EN:** Science-story studio from stills + narration. Default `plan` mode is **FREE** — "
            "no paid API call ever happens without your explicit action.\n\n"
            "Alur: 1→7 gratis · 8 canary kecil · 9–10 style lock + persetujuan · 11–12 pilot · 13–16 produksi & ekspor."
        ),
        nbf.v4.new_code_cell(BOOT),
    ]
    for num, title, code in SECTIONS:
        cells.append(nbf.v4.new_markdown_cell(f"## {num}. {title}"))
        cells.append(nbf.v4.new_code_cell(code))
        if num in ("5", "6", "11", "14"):
            cells.append(nbf.v4.new_code_cell(DASHBOARD))
    nb.cells = cells
    out = NB_DIR / "SIAS_Agentic_Colab_Control_Center.ipynb"
    nbf.write(nb, out)
    return out


def build_aux() -> list[Path]:
    outs = []
    for name, title, body in [
        ("SIAS_Style_Lock_Workbench.ipynb", "Style Lock Workbench",
         "import sys, pathlib\n"
         "for rel in ('scientific-illustrated-audio-studio-colab/src', 'scientific-illustrated-audio-studio/src'):\n"
         "    p = pathlib.Path.cwd().parent / rel\n"
         "    if p.exists(): sys.path.insert(0, str(p))\n"
         "from sias_colab.style import build_style_bible\n"
         "from sias.config import load_config\n"
         "cfg = load_config(pathlib.Path.cwd().parent / 'scientific-illustrated-audio-studio-colab/configs/default.yaml')\n"
         "print(build_style_bible(cfg).model_dump_json(indent=2))"),
        ("SIAS_QC_Inspector.ipynb", "QC Inspector",
         "import json, pathlib\n"
         "for p in sorted(pathlib.Path('.').rglob('*/qc/*.json')):\n"
         "    print(p, '->', json.loads(p.read_text()).get('status'))"),
    ]:
        nb = nbf.v4.new_notebook()
        nb.cells = [nbf.v4.new_markdown_cell(f"# {title}"), nbf.v4.new_code_cell(body)]
        out = NB_DIR / name
        nbf.write(nb, out)
        outs.append(out)
    return outs


def validate(path: Path) -> None:
    nb = nbf.read(path, as_version=4)
    for cell in nb.cells:
        if cell.cell_type == "code":
            ast.parse(cell.source)


if __name__ == "__main__":
    for p in [build_control_center(), *build_aux()]:
        validate(p)
        print("built + validated:", p.name)
