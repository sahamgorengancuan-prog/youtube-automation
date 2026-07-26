import pytest

from sias.exceptions import ProviderSchemaError, RepairLimitError
from sias.schemas import CandidateRecord, SceneSpec, VisionScore
from sias.style.bible import build_style_bible
from sias.style.prompt_compiler import BLOCK_ORDER, compile_prompt, missing_required_clauses
from sias.vision.consensus import combine, weighted_score
from sias.vision.qwen_reviewer import parse_vision_score
from sias.vision.repair import build_repair_request, repair_prompt, status_after_failure
from sias.vision.tournament import candidate_count, stable_seed
from sias.config import load_config
from pathlib import Path

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def _score(v=0.9, hard=None):
    return VisionScore(
        model_name="m", anatomy=v, style_fidelity=v, identity_consistency=v,
        composition=v, science_accuracy=v, story_clarity=v, novelty=v,
        hard_fail_reasons=hard or [],
    )


def test_weighted_score_uses_blueprint_weights():
    assert weighted_score(_score(1.0)) == pytest.approx(1.0)
    uneven = _score(0.0)
    uneven.style_fidelity = 1.0
    assert weighted_score(uneven) == pytest.approx(0.18)


def test_hard_fail_vetoes_approval():
    verdict = combine(_score(0.99, hard=["HF_ANATOMY_MISSING_LIMB"]), _score(0.99))
    assert verdict["status"] == "REPAIR"
    assert "HF_ANATOMY_MISSING_LIMB" in verdict["hard_fail_reasons"]


def test_disagreement_marks_review_required():
    verdict = combine(_score(0.95), _score(0.55), disagreement_threshold=0.18)
    assert verdict["status"] == "REVIEW_REQUIRED"


def test_consensus_approves_above_threshold():
    verdict = combine(_score(0.9), _score(0.88), approval_threshold=0.82)
    assert verdict["status"] == "APPROVED"


def test_reviewer_json_strict_parse():
    with pytest.raises(ProviderSchemaError):
        parse_vision_score({"anatomy": "high"}, "m")
    with pytest.raises(ProviderSchemaError):
        parse_vision_score({**_score().model_dump(), "hard_fail_reasons": ["HF_MADE_UP"]}, "m")
    ok = parse_vision_score(_score(0.7).model_dump(), "m")
    assert ok.anatomy == 0.7


def test_stable_seed_deterministic_and_distinct():
    a = stable_seed(270726, "S01", 0)
    assert a == stable_seed(270726, "S01", 0)
    assert a != stable_seed(270726, "S01", 1)
    assert a != stable_seed(270726, "S02", 0)


def test_candidate_counts_per_role():
    assert candidate_count("cold_open") == 3
    assert candidate_count("gasp_reveal") == 3
    assert candidate_count("payoff") == 3
    assert candidate_count("fact_1") == 2


def test_prompt_compiler_blocks_and_required_clauses():
    cfg = load_config(CONFIGS / "default.yaml")
    bible = build_style_bible(cfg)
    scene = SceneSpec(scene_id="S01", beat_role="cold_open", narration="It rains. Forever.",
                      visual_objective="a town under permanent rain")
    compiled = compile_prompt(scene, bible)
    assert list(compiled["blocks"].keys()) == BLOCK_ORDER
    assert missing_required_clauses(compiled["text"]) == []
    assert compiled["prompt_hash"]


def test_repair_bounded_then_human_decision():
    rec = CandidateRecord(candidate_id="S03_C02", scene_id="S03",
                          qwen=_score(0.4, hard=["HF_ANATOMY_DUPLICATED_LIMB"]), gemini=_score(0.5))
    req = build_repair_request(rec, attempt=1)
    text = repair_prompt(req)
    assert "preserve character identity" in text
    assert "do not redesign the entire image" in text
    with pytest.raises(RepairLimitError):
        build_repair_request(rec, attempt=3)
    assert status_after_failure(2) == "HUMAN_DECISION_REQUIRED"
