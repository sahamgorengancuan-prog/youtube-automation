"""Execute the control-center notebook top-to-bottom in plan mode with NO API
keys, via nbclient. Proves: it runs on a fresh runtime, paid sections SKIP
(never crash), zero paid calls, no secrets in output. Exit non-zero on error."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # Hard guarantee: no keys in the environment for this execution.
    for name in ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_KEY", "HF_API_KEY", "HF_API_SECRET"):
        os.environ.pop(name, None)
    nb_path = ROOT / "notebooks" / "SIAS_Agentic_Colab_Control_Center.ipynb"
    nb = nbf.read(nb_path, as_version=4)
    # Section 15 uses input() for narration duration; patch stdin-free default.
    for cell in nb.cells:
        if cell.cell_type == "code" and "input(" in cell.source:
            cell.source = cell.source.replace('float(input("Durasi narasi (detik): ") or 0)', "0.0")
    client = NotebookClient(nb, timeout=600, kernel_name="python3",
                            resources={"metadata": {"path": str(ROOT / "notebooks")}},
                            allow_errors=False)
    client.execute()
    errors = [o for c in nb.cells for o in c.get("outputs", []) if o.get("output_type") == "error"]
    text = "".join(
        "".join(o.get("text", "")) for c in nb.cells for o in c.get("outputs", []) if "text" in o
    )
    paid_markers = text.count("✅ diizinkan / armed:")
    print("cells:", len(nb.cells), "| errors:", len(errors), "| paid sections armed:", paid_markers)
    if errors or paid_markers:
        return 1
    if "Panggilan berbayar: 0" not in text:
        print("WARNING: plan section did not report zero paid calls")
        return 1
    print("PLAN EXECUTION OK — zero paid calls, no errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
