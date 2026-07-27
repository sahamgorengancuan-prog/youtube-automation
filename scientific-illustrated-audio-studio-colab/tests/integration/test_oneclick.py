"""One-click integration: run_all() offline must complete the ENTIRE pipeline
in a single call (plan→images→audio→alignment→render→QC→export) with zero paid
calls; the one-click notebook must Run All keyless via nbclient."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


def test_run_all_offline_single_call(tmp_path):
    from sias_colab.oneclick import run_all

    result = run_all("What happens if it rains nonstop for one year?", tmp_path, live=False)
    assert result["mode"] == "PREVIEW"
    assert result["qc_status"] == "PASS"
    assert Path(result["video"]).exists() and Path(result["video"]).stat().st_size > 20000
    assert Path(result["srt"]).exists() and Path(result["manifest"]).exists()
    assert Path(result["zip"]).exists()
    budget = result["budget"]
    assert budget["image_calls"] == 0 and budget["tts_characters"] == 0  # zero paid
    # PREVIEW is honest: placeholders would FAIL production QC
    import json

    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["extra"]["mode"] == "PREVIEW"
    assert any("PREVIEW" in w for w in manifest["warnings"])
    from sias_colab.qc.placeholders import is_placeholder

    scene_dir = Path(result["video"]).parents[1] / "scenes"
    pngs = list(scene_dir.rglob("*.png"))
    assert pngs and all(is_placeholder(p) for p in pngs)


def test_oneclick_notebook_keyless_run_all():
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "execute_oneclick_notebook.py")],
                          capture_output=True, text=True, timeout=1200)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "ONECLICK KEYLESS: PASS" in proc.stdout
