from pathlib import Path

import pytest

from sias.budget import BudgetLedger
from sias.config import load_config
from sias.exceptions import BudgetExceededError, ConfigurationError
from sias.schemas import RunBudget

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def test_default_config_valid():
    cfg = load_config(CONFIGS / "default.yaml")
    assert cfg.run_mode == "plan"
    assert cfg.visual.palette["ink"] == "#252525"
    assert cfg.render.max_zoom_pct <= 4.0


def test_invalid_run_mode_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("run_mode: warp_speed\n")
    with pytest.raises(ConfigurationError):
        load_config(bad)


def test_zoom_policy_cap_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("render:\n  max_zoom_pct: 12\n")
    with pytest.raises(ConfigurationError):
        load_config(CONFIGS / "default.yaml", bad)


def test_overlay_merge():
    cfg = load_config(CONFIGS / "default.yaml", CONFIGS / "pilot.yaml")
    assert cfg.run_mode == "pilot"
    assert cfg.budgets.max_image_calls == 14
    assert cfg.visual.identity_name == "Scientific Notebook Cartoon"


def test_budget_hard_stop():
    ledger = BudgetLedger(RunBudget(max_image_calls=2), hard_stop=True)
    ledger.charge("image")
    ledger.charge("image")
    with pytest.raises(BudgetExceededError):
        ledger.charge("image")
    assert ledger.budget.image_calls == 2


def test_budget_tts_chars_cap():
    ledger = BudgetLedger(RunBudget(max_tts_characters=100), hard_stop=True)
    ledger.charge("tts_chars", 90)
    with pytest.raises(BudgetExceededError):
        ledger.charge("tts_chars", 20)
