"""Build SIAS_One_Click.ipynb — ONE integrated notebook: fill the form, press
Run All, and the ENTIRE pipeline executes (plan → images → narration →
alignment → render → QC → export). No separate QC/pipeline sections.

Keyless → complete PREVIEW MP4 (watermarked, zero paid calls).
Keys + RUN_LIVE=True → full live episode."""

from __future__ import annotations

import ast
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]

INTRO = """\
# ⚡ SIAS One-Click — Satu Notebook, Sekali Run, Jadi Video

**ID:** Isi topik di bawah → **Runtime ▸ Run all** → seluruh pipeline jalan otomatis:
rencana cerita → ilustrasi → narasi → sinkronisasi audio → render → QC → ZIP.

- **Tanpa API key**: tetap selesai end-to-end sebagai **PREVIEW** (panel ber-watermark + audio bed) — 0 biaya.
- **Dengan key + `RUN_LIVE=True`**: episode live penuh (gambar BFL, narasi OpenAI TTS, review Qwen+Gemini bila ada key OpenRouter).

**EN:** Fill the form → Run all → the whole pipeline runs in one pass. Keyless = free watermarked PREVIEW; with keys + `RUN_LIVE=True` = full live episode.
"""

CELL_FORM = '''\
# @title 1️⃣ Pengaturan — isi lalu Run All { display-mode: "form" }
TOPIC     = "What happens if it rains nonstop for one year?"  # @param {type:"string"}
LANGUAGE  = "en"      # @param ["en", "id"]
VOICE     = "cedar"   # @param {type:"string"}
RUN_LIVE  = False     # @param {type:"boolean"}
MAX_IMAGE_BUDGET = 30 # @param {type:"integer"}
GITHUB_REPO = "https://github.com/sahamgorengancuan-prog/youtube-automation"

print("Topik :", TOPIC)
print("Mode  :", "LIVE (berbayar — perlu API key)" if RUN_LIVE else "PREVIEW (gratis, watermark)")
'''

CELL_SETUP = '''\
# 2️⃣ Setup otomatis (repo + dependensi + kunci) — tanpa panggilan berbayar
import os, sys, subprocess, pathlib

IN_COLAB = "google.colab" in sys.modules or os.path.exists("/content")
if IN_COLAB and not os.path.exists("/content/youtube-automation"):
    subprocess.run(["git", "clone", "--depth", "1", GITHUB_REPO, "/content/youtube-automation"], check=False)

REPO = None
for base in ("/content/youtube-automation", str(pathlib.Path.cwd().parent.parent), str(pathlib.Path.cwd())):
    if (pathlib.Path(base) / "scientific-illustrated-audio-studio-colab" / "src").exists():
        REPO = pathlib.Path(base); break
assert REPO is not None, "Repo tidak ditemukan — jalankan dari repo atau biarkan sel meng-klon."
for rel in ("scientific-illustrated-audio-studio-colab/src", "scientific-illustrated-audio-studio/src"):
    p = str(REPO / rel)
    if p not in sys.path: sys.path.insert(0, p)

import importlib
for pkg, pip_name in [("pydantic", None), ("yaml", "pyyaml"), ("PIL", "Pillow")]:
    try: importlib.import_module(pkg)
    except ImportError: subprocess.run([sys.executable, "-m", "pip", "install", "-q", pip_name or pkg], check=False)

# Kunci: Colab Secrets dulu, lalu environment. Nilai TIDAK pernah ditampilkan.
def _get(name):
    try:
        from google.colab import userdata
        v = userdata.get(name)
        if v: return v
    except Exception: pass
    return os.environ.get(name, "")
for name in ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
    v = _get(name)
    if v: os.environ[name] = v
    print(("🔑" if v else "⛔"), name, "" if v else "(preview tetap jalan tanpa ini)")

WORKSPACE = "/content/drive/MyDrive/SIAS_OneClick" if False else "workspace_oneclick"
import shutil as _sh
print("ffmpeg:", "ok" if _sh.which("ffmpeg") else "MISSING")
import sias_colab
print("sias_colab", sias_colab.__version__, "siap.")
'''

CELL_RUN = '''\
# 3️⃣ 🚀 JALANKAN SEMUA — plan → gambar → narasi → alignment → render → QC → ekspor
from sias_colab.oneclick import run_all

RESULT = run_all(
    topic=TOPIC,
    workspace=WORKSPACE,
    live=RUN_LIVE,
    language=LANGUAGE,
    voice=VOICE,
    max_image_calls=MAX_IMAGE_BUDGET,
)

print()
print("=" * 60)
print("SELESAI —", RESULT["mode"], "| QC:", RESULT["qc_status"],
      "|", RESULT["scenes"], "adegan |", RESULT["duration_s"], "detik")
print("Video   :", RESULT["video"])
print("Caption :", RESULT["srt"])
print("Manifest:", RESULT["manifest"])
print("ZIP     :", RESULT["zip"])
if RESULT["mode"] != "LIVE":
    print("⚠ PREVIEW/PARTIAL — watermark, bukan untuk publikasi. Isi key + RUN_LIVE=True untuk episode penuh.")
'''

CELL_SHOW = '''\
# 4️⃣ Tonton hasil + unduh
from IPython.display import Video, display
import os
display(Video(RESULT["video"], embed=False, width=360))
print(open(RESULT["qc_report"]).read()[:600])
try:
    from google.colab import files
    files.download(RESULT["zip"])
except Exception:
    print("Non-Colab / unduh manual: ZIP tersedia di", RESULT["zip"])
'''


def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.metadata["colab"] = {"name": "SIAS_One_Click.ipynb"}
    nb.cells = [
        nbf.v4.new_markdown_cell(INTRO),
        nbf.v4.new_code_cell(CELL_FORM),
        nbf.v4.new_code_cell(CELL_SETUP),
        nbf.v4.new_code_cell(CELL_RUN),
        nbf.v4.new_code_cell(CELL_SHOW),
    ]
    out = ROOT / "notebooks" / "SIAS_One_Click.ipynb"
    nbf.write(nb, out)
    for cell in nbf.read(out, as_version=4).cells:
        if cell.cell_type == "code":
            ast.parse(cell.source)
    print("built + validated:", out.name)


if __name__ == "__main__":
    main()
