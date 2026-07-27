"""Execute SIAS_One_Click_Standalone.ipynb headlessly, keyless, and assert it
actually produced an episode.

This is the proof that "works" is not a claim: the notebook runs top to bottom
with no API keys in the environment, in a scratch directory, importing ONLY the
sources embedded in the notebook itself — the repo is not on sys.path.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "SIAS_One_Click_Standalone.ipynb"


def _cell_text(cell) -> str:
    parts = []
    for out in cell.get("outputs", []):
        if out.output_type == "stream":
            parts.append(out.text)
        elif out.output_type in ("execute_result", "display_data"):
            parts.append(str(out.get("data", {}).get("text/plain", "")))
        elif out.output_type == "error":
            parts.append("\n".join(out.traceback))
    return "".join(parts)


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("ffmpeg missing — the render stage cannot be proven; aborting", file=sys.stderr)
        return 2

    nb = nbf.read(NOTEBOOK, as_version=4)
    work = Path(tempfile.mkdtemp(prefix="sias_standalone_"))

    # Keyless by construction, and the repo must NOT be importable: the notebook
    # has to stand on its embedded payload alone.
    env = dict(os.environ)
    for key in ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "PYTHONPATH"):
        env.pop(key, None)
    old_env, os.environ = dict(os.environ), env
    try:
        client = NotebookClient(nb, timeout=1800, kernel_name="python3",
                                resources={"metadata": {"path": str(work)}}, allow_errors=True)
        client.execute()
    finally:
        os.environ = old_env

    errors = [(i, out) for i, cell in enumerate(nb.cells) if cell.cell_type == "code"
              for out in cell.get("outputs", []) if out.output_type == "error"]
    if errors:
        for index, out in errors:
            print(f"\n--- cell {index} raised {out.ename}: {out.evalue}", file=sys.stderr)
            print("\n".join(out.traceback[-25:]), file=sys.stderr)
        return 1

    text = "".join(_cell_text(c) for c in nb.cells if c.cell_type == "code")
    required = [
        ("payload sha256 verified", "sha256 payload terverifikasi"),
        ("API simulation preflight", "simulated provider scenarios handled correctly"),
        ("keyless PREVIEW mode", "MODE: PREVIEW"),
        ("pipeline completed", "[10/10] EXPORT OK"),
        ("preview honesty warning", "BUKAN untuk publikasi"),
    ]
    missing = [label for label, token in required if token not in text]
    if missing:
        print(f"notebook ran but did not prove: {missing}", file=sys.stderr)
        return 1

    videos = list((work / "sias_workspace").rglob("final.mp4"))
    if not videos or videos[0].stat().st_size < 20_000:
        print(f"no usable MP4 produced under {work}", file=sys.stderr)
        return 1

    print(f"PASS — {len(nb.cells)} cells, 0 errors, MP4 {videos[0].stat().st_size // 1024} KB at {videos[0]}")
    for line in text.splitlines():
        if line.startswith(("[1/10]", "[4/10]", "[8/10]", "[9/10]", "[10/10]", "SELESAI")):
            print("   ", line)
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
