"""Execute SIAS_One_Click.ipynb top-to-bottom with NO keys via nbclient.
Must complete with zero errors, zero paid calls, and produce a PREVIEW MP4."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    for name in ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_KEY", "HF_API_KEY", "HF_API_SECRET"):
        os.environ.pop(name, None)
    nb_path = ROOT / "notebooks" / "SIAS_One_Click.ipynb"
    nb = nbf.read(nb_path, as_version=4)
    client = NotebookClient(nb, timeout=900, kernel_name="python3",
                            resources={"metadata": {"path": str(ROOT / "notebooks")}},
                            allow_errors=False)
    client.execute()
    errors = [o for c in nb.cells for o in c.get("outputs", []) if o.get("output_type") == "error"]
    text = "".join("".join(o.get("text", "")) for c in nb.cells for o in c.get("outputs", []) if "text" in o)
    mp4 = ROOT / "notebooks" / "workspace_oneclick"
    videos = list(mp4.rglob("final.mp4")) if mp4.exists() else []
    ok = (not errors) and ("MODE: PREVIEW" in text) and ("QC: PASS" in text or "QC PASS" in text) and videos
    print("errors:", len(errors), "| preview mode:", "MODE: PREVIEW" in text,
          "| mp4:", [str(v) for v in videos][:1])
    print("ONECLICK KEYLESS:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
