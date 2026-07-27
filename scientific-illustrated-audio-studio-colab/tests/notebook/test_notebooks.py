"""Notebook tests: JSON valid, cells parse, keyless top-to-bottom plan
execution with zero paid calls and no secrets in output."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_notebooks_valid_and_parse():
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_notebook.py")],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_plan_mode_topdown_execution_no_keys_no_paid_calls():
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "execute_plan_notebook.py")],
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "PLAN EXECUTION OK" in proc.stdout


def test_widget_fallback_exists():
    from sias_colab.ui import dashboard

    # The module must expose a working non-widget path regardless of ipywidgets.
    assert hasattr(dashboard, "HAS_WIDGETS")
    fb = dashboard.arm_checkbox("id")
    assert hasattr(fb, "value")
