from pathlib import Path

import pytest

from sias.schemas import RunBudget, StoryBeat
from sias_colab.budget import BudgetGuardian, assert_paid_call_allowed
from sias_colab.config import load_studio_config
from sias_colab.exceptions import BudgetExceededError, PaidCallBlockedError
from sias_colab.state import load_or_create, save_state, load_state
from sias_colab.story.hooks_ext import generate_hooks_v2, select_hook_v2
from sias_colab.story.retention_critic import critique, critique_and_revise
from sias_colab.supervisor import Agent, Supervisor

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
TOPIC = "What happens if it rains nonstop for one year?"


def _cfg(**colab):
    return load_studio_config(CONFIGS / "default.yaml",
                              overrides={"project": {"topic": TOPIC}}, colab=colab)


def test_studio_config_layers():
    cfg = _cfg(run_mode="plan", ui_language="id")
    assert cfg.run_mode == "plan" and cfg.engine.run_mode == "plan"
    assert cfg.colab.higgsfield.enabled is False
    with pytest.raises(Exception):
        _cfg(run_mode="warp")
    with pytest.raises(Exception):
        _cfg(ui_language="fr")


def test_budget_guardian_higgsfield_cap():
    g = BudgetGuardian(RunBudget(), max_higgsfield_calls=2)
    g.charge("higgsfield")
    g.charge("higgsfield")
    with pytest.raises(BudgetExceededError):
        g.charge("higgsfield")
    g.report_cost(0.12)
    snap = g.snapshot()
    assert snap["higgsfield_calls"] == 2 and snap["reported_cost_usd"] == 0.12


def test_paid_call_requires_all_conditions():
    kwargs = dict(arm_paid_calls=True, secret_present=True, mode_allows_stage=True,
                  budget_ok=True, previous_gate_passed=True, user_action_confirmed=True)
    assert_paid_call_allowed("s", **kwargs)  # all good -> no raise
    for missing in kwargs:
        bad = {**kwargs, missing: False}
        with pytest.raises(PaidCallBlockedError) as exc:
            assert_paid_call_allowed("s", **bad)
        assert str(exc.value)  # exact reason text present


def test_state_resume_roundtrip(tmp_path):
    state = load_or_create(tmp_path, "ep1", TOPIC)
    state.mark("plan", "PASS")
    state.approvals["style_lock_human"] = "APPROVED"
    save_state(tmp_path, state)
    again = load_state(tmp_path, "ep1")
    assert again.status("plan") == "PASS"
    assert again.gate_passed("style_lock_human")
    assert again.next_recommended_action() == "canary"


def test_hooks_v2_three_kinds_and_clickbait():
    hooks = generate_hooks_v2(TOPIC)
    assert {h.kind for h in hooks} == {"consequence_first", "contradiction_first", "scale_first"}
    for h in hooks:
        assert 0.0 <= h.clickbait_risk <= 1.0
    assert select_hook_v2(hooks).total > 0


def test_retention_critic_detects_and_revises_once():
    beats = [
        StoryBeat(beat_id=f"B{i:02d}", role=r, narration=n)
        for i, (r, n) in enumerate([
            ("cold_open", "this is an extremely long opening sentence that keeps going and going with far too many words to hold anyone"),
            ("fact_1", "the ground saturates fast"),
            ("fact_2", "the ground saturates fast"),
            ("fact_3", "systems hit limits"),
            ("explanation", "here is the mechanism"),
            ("scale_example", "scaled up it is absurd"),
            ("gasp_reveal", "the surprise is not water"),
            ("payoff", "In conclusion, as explained above, that is everything."),
        ], start=1)
    ]
    from sias.story.spoken_script import assemble_script

    cfg = _cfg().engine
    script = assemble_script(beats, cfg.story.catchphrase)
    issues = critique(script, beats)
    assert any("slow_opening" in i for i in issues)
    assert any("repeated_fact" in i for i in issues)
    assert any("generic_conclusion" in i for i in issues)
    result = critique_and_revise(script, beats, cfg)
    assert result["revised"] is True
    assert "in conclusion" not in result["beats"][-1]["narration"].lower()
    # bounded: one revision, remaining issues only reported
    assert "remaining_issues" in result


def test_supervisor_dag_order_gating_and_cycle(tmp_path):
    state = load_or_create(tmp_path, "ep2", TOPIC)
    sup = Supervisor(tmp_path, state, BudgetGuardian(RunBudget()), arm_paid_calls=False,
                     allowed_agents={"a", "b"})
    order = []
    sup.register(Agent("a", "", [], lambda ctx: (order.append("a"), {"ok": 1})[1]))
    sup.register(Agent("b", "", ["a"], lambda ctx: (order.append("b"), {"ok": 1})[1]))
    sup.run(["b"])
    assert order == ["a", "b"]
    # paid agent refused when un-armed
    sup2 = Supervisor(tmp_path, state, BudgetGuardian(RunBudget()), arm_paid_calls=False)
    sup2.register(Agent("paid", "", [], lambda ctx: {"ok": 1}, cost_class="paid"))
    with pytest.raises(PaidCallBlockedError):
        sup2.run(["paid"])
    # cycle detection
    sup3 = Supervisor(tmp_path, state, BudgetGuardian(RunBudget()))
    sup3.register(Agent("x", "", ["y"], lambda ctx: {}))
    sup3.register(Agent("y", "", ["x"], lambda ctx: {}))
    with pytest.raises(ValueError, match="cycle"):
        sup3.run(["x"])


def test_studio_plan_offline_and_export(tmp_path):
    from sias_colab.studio import Studio

    studio = Studio(_cfg(), tmp_path)
    out = studio.plan()
    assert out["engine"]["script"]["catchphrase_count"] == 1
    assert len(out["engine"]["beats"]) == 8
    assert out["agents"]["retention_critic"] is not None
    assert studio.state.status("plan") == "PASS"
    assert out["budget"]["image_calls"] == 0  # zero paid
    zip_path = studio.export_package()
    assert zip_path.exists() and zip_path.stat().st_size > 100
