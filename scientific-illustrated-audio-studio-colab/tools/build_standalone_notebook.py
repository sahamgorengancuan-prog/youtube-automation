"""Build SIAS_One_Click_Standalone.ipynb — ONE self-contained notebook.

Standalone means standalone: the whole `sias` engine + `sias_colab` layer +
default config travel *inside* the notebook as an embedded, checksummed
tar.gz. Run All extracts them into Colab and runs the entire pipeline. There is
no git clone, no repo read, no external download of project code.

Four inputs, everything else locked to the best-known configuration:
    TOPIC, LANGUAGE, RUN_LIVE, HUMAN_GATES_APPROVED

Cells:
    0 intro (ID + EN)
    1 inputs (4 params + locked defaults, printed)
    2 embedded source payload (base64 tar.gz)
    3 install + extract + verify + import
    4 secrets (Colab Secrets → env; values never printed)
    5 simulated-API preflight (proves response handling before spending money)
    6 RUN ALL
    7 watch + download
"""

from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ENGINE = REPO / "scientific-illustrated-audio-studio"

NOTEBOOK_NAME = "SIAS_One_Click_Standalone.ipynb"
B64_LINE_WIDTH = 100


# ---------------------------------------------------------------------------
# Source payload
# ---------------------------------------------------------------------------

def collect_sources() -> list[tuple[str, bytes]]:
    """(archive_path, bytes) for everything the pipeline imports at runtime.

    The archive mirrors the repo layout — `src/` on sys.path with `configs/`
    beside it — because `oneclick.run_all` resolves its config relative to the
    package. Same layout in Colab, same code path as the repo.
    """
    members: list[tuple[str, bytes]] = []
    for src_root, package in ((ENGINE / "src", "sias"), (ROOT / "src", "sias_colab")):
        for path in sorted((src_root / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            members.append((f"src/{path.relative_to(src_root).as_posix()}", path.read_bytes()))
    for cfg in sorted((ROOT / "configs").glob("*.yaml")):
        members.append((f"configs/{cfg.name}", cfg.read_bytes()))
    if not any(m[0] == "configs/default.yaml" for m in members):
        raise SystemExit("configs/default.yaml is required by run_all but was not found")
    return members


def build_payload(members: list[tuple[str, bytes]]) -> tuple[str, str]:
    """Deterministic tar.gz → base64. Returns (b64, sha256 of the raw archive)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0  # deterministic: identical sources → identical notebook
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    packed = gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)
    return base64.b64encode(packed).decode("ascii"), hashlib.sha256(packed).hexdigest()


def payload_cell(b64: str, digest: str, members: list[tuple[str, bytes]]) -> str:
    lines = [b64[i:i + B64_LINE_WIDTH] for i in range(0, len(b64), B64_LINE_WIDTH)]
    body = "\n".join(f'    "{line}"' for line in lines)
    return (
        f"# 2️⃣ Kode sumber SIAS tertanam di notebook ini ({len(members)} berkas, "
        f"{len(b64) // 1024} KB base64).\n"
        "# Kode proyek tidak diambil dari repositori mana pun saat dijalankan. Jangan diedit.\n"
        f'SIAS_PAYLOAD_SHA256 = "{digest}"\n'
        "SIAS_PAYLOAD_B64 = (\n"
        f"{body}\n"
        ")\n"
        f'print("payload:", len(SIAS_PAYLOAD_B64), "karakter base64 ·", '
        f"{len(members)}, \"berkas sumber\")\n"
    )


# ---------------------------------------------------------------------------
# Static cells
# ---------------------------------------------------------------------------

INTRO = """\
# 💎 SIAS One-Click **Standalone** — satu notebook, sekali *Run All*

**ID:** Isi **4 isian** di sel berikutnya → **Runtime ▸ Run all**. Seluruh pipeline berjalan
dalam satu jalur: rencana cerita → ilustrasi → narasi → sinkronisasi audio → render →
subtitle → QC teknis → Diamond Editorial Gate → manifest → ZIP.

* **Standalone**: seluruh kode mesin SIAS tertanam di dalam notebook ini. Tidak membaca
  GitHub, tidak mengunduh kode proyek dari mana pun — semuanya dipasang di Colab.
* **Identitas visual terkunci**: *SIAS Institutional Lab Notebook* — panel laporan putih,
  bingkai tipis, blok status kiri-atas, pembacaan instrumen kanan-atas, judul kapital tebal,
  dan diagram vektor datar berpalet tertutup. **Seluruh huruf dan angka di-typeset oleh kode**,
  bukan oleh model gambar, sehingga tidak ada lagi salah eja seperti "STOPPED STOPPED SPINNING".
* **Tanpa API key** → selesai penuh sebagai **PREVIEW** ber-watermark, **0 biaya**.
* **Dengan key + `RUN_LIVE = True`** → episode LIVE (gambar BFL, narasi OpenAI TTS,
  QC ganda Qwen VL + Gemini 2.5 Flash).
* **Preflight simulasi API** dijalankan lebih dulu: setiap bentuk jawaban provider
  (termasuk yang pernah merusak run nyata) diuji tanpa jaringan, sehingga kesalahan
  parsing tidak mungkin muncul di tengah run berbayar.

Semua pilihan teknis lain sudah dikunci ke konfigurasi terbaik — tidak ada opsi
open-source/API yang perlu Anda pilih.

**EN:** Fill 4 fields → Run all. The entire engine is embedded in this notebook (no GitHub
reads). Keyless = free watermarked PREVIEW; keys + `RUN_LIVE=True` = full live episode.
A no-network API simulation runs first so provider-response parsing cannot fail mid-run.
"""

CELL_INPUTS = '''\
# @title 1️⃣ Isian — hanya empat, sisanya sudah optimal { display-mode: "form" }
TOPIC = "What happens if it rains nonstop for one year?"  # @param {type:"string"}
LANGUAGE = "en"  # @param ["en", "id"]
# 16:9 is the Institutional Lab Notebook reference format; 9:16 for shorts.
ASPECT = "16:9"  # @param ["16:9", "9:16", "1:1"]
# LIVE memakai API berbayar (BFL + OpenAI + OpenRouter). False = PREVIEW gratis.
RUN_LIVE = False  # @param {type:"boolean"}
# Setel True HANYA setelah Anda benar-benar meninjau hasilnya (hook, gaya, karakter,
# pilot, audio, tonton di ponsel). Enam gerbang manusia tidak boleh dilewati otomatis.
HUMAN_GATES_APPROVED = False  # @param {type:"boolean"}

# ---- Konfigurasi terkunci (sudah pilihan terbaik — tidak perlu diubah) -------
import os
_BASE = "/content" if os.path.isdir("/content") else os.getcwd()
LOCKED = {
    "workspace": os.path.join(_BASE, "sias_workspace"),
    "bfl_model": "flux-2-pro",       # ilustrasi utama
    "voice": "cedar",                # OpenAI TTS
    "max_image_calls": 30,           # batas keras biaya gambar
    "preview_scale": 0.5,            # render preview setengah resolusi (cepat, gratis)
}
print("Topik :", TOPIC)
print("Bahasa:", LANGUAGE, "· Rasio:", ASPECT)
print("Mode  :", "LIVE (berbayar)" if RUN_LIVE else "PREVIEW (gratis, watermark)")
print("Kunci :", ", ".join(f"{k}={v}" for k, v in LOCKED.items()))
'''

CELL_SETUP = '''\
# 3️⃣ Pasang dependensi + bongkar kode sumber tertanam (tanpa jaringan untuk kode)
import base64, hashlib, importlib, io, os, subprocess, sys, tarfile, shutil
from pathlib import Path

RUNTIME = Path("/content/sias_runtime") if Path("/content").exists() else Path.cwd() / "sias_runtime"

packed = base64.b64decode(SIAS_PAYLOAD_B64)
digest = hashlib.sha256(packed).hexdigest()
assert digest == SIAS_PAYLOAD_SHA256, f"payload rusak: {digest} != {SIAS_PAYLOAD_SHA256}"

if RUNTIME.exists():
    shutil.rmtree(RUNTIME)
RUNTIME.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as tar:
    for member in tar.getmembers():          # tolak path absolut / traversal
        target = (RUNTIME / member.name).resolve()
        assert str(target).startswith(str(RUNTIME.resolve())), f"jalur tidak aman: {member.name}"
    tar.extractall(RUNTIME)

SRC = str(RUNTIME / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

for module, pip_name in [("pydantic", "pydantic"), ("yaml", "pyyaml"), ("PIL", "Pillow")]:
    try:
        importlib.import_module(module)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pip_name], check=True)

import sias_colab
n_files = sum(1 for _ in Path(SRC).rglob("*.py"))
print("sumber   :", n_files, "modul ->", SRC)
print("ffmpeg   :", "ok" if shutil.which("ffmpeg") else "TIDAK ADA (render akan gagal)")
print("sias_colab", sias_colab.__version__, "siap — sha256 payload terverifikasi.")
'''

CELL_SECRETS = '''\
# 4️⃣ Kunci API — Colab Secrets lalu environment. Nilai TIDAK pernah dicetak.
import os

def _read_secret(name):
    try:
        from google.colab import userdata
        value = userdata.get(name)
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(name, "")

PRESENT = {}
for name in ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
    value = _read_secret(name)
    if value:
        os.environ[name] = value
    PRESENT[name] = bool(value)
    print(("🔑" if value else "⛔"), name, "" if value else "(PREVIEW tetap berjalan penuh tanpa ini)")

if RUN_LIVE and not (PRESENT["BFL_API_KEY"] and PRESENT["OPENAI_API_KEY"]):
    print("\\n⚠ RUN_LIVE=True tetapi kunci gambar/suara belum lengkap — "
          "run akan turun ke PARTIAL-LIVE atau PREVIEW, bukan gagal diam-diam.")
'''

CELL_PREFLIGHT = '''\
# 5️⃣ Preflight: simulasi jawaban SETIAP provider (tanpa jaringan, tanpa biaya)
# Membuktikan penanganan respons benar SEBELUM ada uang keluar — termasuk bentuk
# jawaban yang dulu merusak run nyata (polling_url regional BFL, header WAV
# streaming 0xFFFFFFFF, JSON reviewer terbungkus prosa).
from sias_colab.preflight import run_api_simulation

PREFLIGHT = run_api_simulation()
assert PREFLIGHT["status"] == "PASS"
'''

CELL_RUN = '''\
# 6️⃣ 🚀 JALANKAN SEMUA — plan → gambar → narasi → alignment → render → QC → ekspor
from sias_colab.oneclick import run_all

RESULT = run_all(
    topic=TOPIC,
    workspace=LOCKED["workspace"],
    live=RUN_LIVE,
    language=LANGUAGE,
    voice=LOCKED["voice"],
    max_image_calls=LOCKED["max_image_calls"],
    preview_scale=LOCKED["preview_scale"],
    human_gates_approved=HUMAN_GATES_APPROVED,
    bfl_model=LOCKED["bfl_model"],
    aspect=ASPECT,
)

print()
print("=" * 64)
print("SELESAI —", RESULT["mode"], "| QC:", RESULT["qc_status"],
      "|", RESULT["scenes"], "adegan |", RESULT["duration_s"], "detik")
print("Video    :", RESULT["video"])
print("Subtitle :", RESULT["ass"], "(ASS Diamond) +", RESULT["srt"])
print("Diamond  :", RESULT["diamond_status"], "->", RESULT["diamond_report"])
print("Manifest :", RESULT["manifest"])
print("ZIP      :", RESULT["zip"])
if RESULT.get("consistency_flags"):
    print("⚑ Konsistensi:", RESULT["consistency_flags"])
if RESULT["mode"] != "LIVE":
    print("⚠ PREVIEW/PARTIAL — ber-watermark, BUKAN untuk publikasi. "
          "Isi kunci API + RUN_LIVE=True untuk episode penuh.")
'''

CELL_SHOW = '''\
# 7️⃣ Tonton hasil + unduh paket
from IPython.display import Video, display

display(Video(RESULT["video"], embed=False, width=360))
print(open(RESULT["qc_report"]).read()[:800])
try:
    from google.colab import files
    files.download(RESULT["zip"])
except Exception:
    print("Di luar Colab — paket tersedia di:", RESULT["zip"])
'''


def main() -> None:
    members = collect_sources()
    b64, digest = build_payload(members)

    nb = nbf.v4.new_notebook()
    nb.metadata["colab"] = {"name": NOTEBOOK_NAME}
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3"}
    nb.cells = [
        nbf.v4.new_markdown_cell(INTRO),
        nbf.v4.new_code_cell(CELL_INPUTS),
        nbf.v4.new_code_cell(payload_cell(b64, digest, members)),
        nbf.v4.new_code_cell(CELL_SETUP),
        nbf.v4.new_code_cell(CELL_SECRETS),
        nbf.v4.new_code_cell(CELL_PREFLIGHT),
        nbf.v4.new_code_cell(CELL_RUN),
        nbf.v4.new_code_cell(CELL_SHOW),
    ]

    out = ROOT / "notebooks" / NOTEBOOK_NAME
    nbf.write(nb, out)

    written = nbf.read(out, as_version=4)
    for index, cell in enumerate(written.cells):
        if cell.cell_type == "code":
            ast.parse(cell.source)  # every code cell must be valid Python
    forbidden = ("git clone", "wget ", "curl ", "raw.githubusercontent", "github.com/")
    joined = "\n".join(c.source for c in written.cells if c.cell_type == "code")
    hits = [token for token in forbidden if token in joined]
    if hits:
        raise SystemExit(f"notebook is not standalone — found {hits}")

    print(f"built {out.name}: {len(written.cells)} cells, {len(members)} embedded files, "
          f"{out.stat().st_size // 1024} KB, payload sha256 {digest[:16]}…")


if __name__ == "__main__":
    main()
