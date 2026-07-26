import pytest

from sias.cli import main
from sias.exceptions import ConfigurationError
from sias.pipeline.modes import check_production_unlock, mode_allows, mode_may_spend


def test_plan_mode_never_spends():
    assert not mode_may_spend("plan")
    assert not mode_may_spend("render_only")
    assert mode_may_spend("pilot")


def test_mode_stage_gating():
    assert mode_allows("plan", "story_beats")
    assert not mode_allows("plan", "pilot_render")
    assert mode_allows("production", "anything_via_complete_pipeline")


def test_production_locked_until_pilot_pass():
    with pytest.raises(ConfigurationError):
        check_production_unlock(None)
    with pytest.raises(ConfigurationError):
        check_production_unlock("FAIL")
    assert check_production_unlock("PASS") == []
    warnings = check_production_unlock("FAIL", allow_production_without_pilot=True)
    assert warnings and "OVERRIDE" in warnings[0]


def test_cli_audit_and_plan_dry(tmp_path, capsys):
    assert main(["audit", "--json", "--workspace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert '"paid_calls_this_command": false' in out

    episode = tmp_path / "ep.yaml"
    episode.write_text("project:\n  topic: What happens if it rains nonstop for one year?\n")
    assert main(["plan", "--workspace", str(tmp_path / "ws"), "--episode", str(episode), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"paid_calls": 0' in out


def test_cli_pilot_dry_run_never_calls_paid(tmp_path, capsys):
    assert main(["pilot", "--dry-run", "--workspace", str(tmp_path), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"paid_calls_executed": 0' in out
    assert main(["style-lock", "--dry-run", "--workspace", str(tmp_path), "--json"]) == 0
