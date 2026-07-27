"""Validate every notebook: valid JSON, every code cell parses, and no secret
value patterns in outputs. Exit non-zero on failure."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import nbformat as nbf

SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{16,}|hf_[A-Za-z0-9]{16,}")


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
    except Exception as exc:
        return [f"{path.name}: invalid JSON: {exc}"]
    nb = nbf.read(path, as_version=4)
    for i, cell in enumerate(nb.cells):
        if cell.cell_type == "code":
            try:
                ast.parse(cell.source)
            except SyntaxError as exc:
                errors.append(f"{path.name}[{i}]: syntax error: {exc}")
        for output in cell.get("outputs", []):
            text = json.dumps(output, default=str)
            if SECRET_PATTERN.search(text):
                errors.append(f"{path.name}[{i}]: possible secret in output")
    return errors


def main() -> int:
    nb_dir = Path(__file__).resolve().parents[1] / "notebooks"
    all_errors: list[str] = []
    for path in sorted(nb_dir.glob("*.ipynb")):
        errs = validate(path)
        print(("FAIL " if errs else "OK   ") + path.name)
        all_errors += errs
    for e in all_errors:
        print(" -", e)
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
