"""Guards for the standalone notebook: it must stay self-contained and must not
drift from the sources it claims to embed."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import re
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks" / "SIAS_One_Click_Standalone.ipynb"
BUILDER = ROOT / "tools" / "build_standalone_notebook.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_standalone_notebook", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells(nb: dict) -> list[str]:
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def _payload(nb: dict) -> bytes:
    source = next(s for s in _code_cells(nb) if "SIAS_PAYLOAD_B64" in s and "= (" in s)
    b64 = "".join(re.findall(r'"([A-Za-z0-9+/=]+)"', source.split("SIAS_PAYLOAD_B64 = (", 1)[1]))
    return base64.b64decode(b64)


def test_notebook_never_reaches_for_the_repo(notebook):
    joined = "\n".join(_code_cells(notebook))
    for token in ("git clone", "raw.githubusercontent", "github.com/", "wget ", "curl "):
        assert token not in joined, f"standalone notebook still references {token!r}"


def test_payload_checksum_matches_its_own_declaration(notebook):
    declared = re.search(r'SIAS_PAYLOAD_SHA256 = "([0-9a-f]{64})"',
                         "\n".join(_code_cells(notebook))).group(1)
    assert hashlib.sha256(_payload(notebook)).hexdigest() == declared


def test_payload_is_in_sync_with_the_repo_sources():
    """Release-drift guard: a notebook shipping stale engine code is the exact
    failure mode this whole project keeps hitting. Rebuild and compare."""
    builder = _load_builder()
    _, fresh_digest = builder.build_payload(builder.collect_sources())
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    declared = re.search(r'SIAS_PAYLOAD_SHA256 = "([0-9a-f]{64})"',
                         "\n".join(_code_cells(nb))).group(1)
    assert declared == fresh_digest, (
        "notebook payload is stale — run tools/build_standalone_notebook.py"
    )


def test_payload_carries_the_engine_the_config_and_the_preflight(notebook):
    with tarfile.open(fileobj=io.BytesIO(_payload(notebook)), mode="r:gz") as tar:
        names = set(tar.getnames())
    for required in ("src/sias/__init__.py", "src/sias_colab/oneclick.py",
                     "src/sias_colab/preflight/simulation.py", "src/sias/providers/bfl.py",
                     "src/sias/audio/silence.py", "configs/default.yaml"):
        assert required in names, f"payload is missing {required}"
    assert not any(name.startswith("/") or ".." in name for name in names)


def test_inputs_stay_minimal_and_defaults_stay_locked(notebook):
    form = next(s for s in _code_cells(notebook) if "@param" in s)
    params = re.findall(r"^(\w+)\s*=.*?#\s*@param", form, flags=re.MULTILINE)
    assert params == ["TOPIC", "LANGUAGE", "RUN_LIVE", "HUMAN_GATES_APPROVED"]
    for locked in ("bfl_model", "voice", "max_image_calls", "preview_scale", "workspace"):
        assert f'"{locked}"' in form, f"{locked} should be a locked default, not a prompt"
    # No open-source/API backend choice is exposed to the user.
    assert "open_source" not in form and "SOURCE_MODE" not in form


def test_preflight_runs_before_any_paid_stage(notebook):
    cells = _code_cells(notebook)
    preflight = next(i for i, s in enumerate(cells) if "run_api_simulation" in s)
    run_all = next(i for i, s in enumerate(cells) if "run_all(" in s)
    assert preflight < run_all, "the API simulation must gate the run, not follow it"
    assert 'PREFLIGHT["status"] == "PASS"' in cells[preflight]


def test_no_secret_value_is_ever_printed(notebook):
    secrets_cell = next(s for s in _code_cells(notebook) if "BFL_API_KEY" in s)
    assert "print(value" not in secrets_cell and "print(v)" not in secrets_cell
    assert "bool(value)" in secrets_cell  # presence only, never the value
