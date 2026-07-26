"""Golden test for the canonical example topic. Validates STRUCTURE, not exact
generative wording."""

from pathlib import Path

import pytest

from sias.config import load_config
from sias.audio.alignment import align_scenes
from sias.pipeline.orchestrator import PILOT_SCENE_COUNT, Orchestrator
from sias.schemas import AlignmentSegment, AlignmentWord, SceneSpec, SpokenScript
from sias.style.prompt_compiler import BLOCK_ORDER
from sias.filesystem import load_json

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
TOPIC = "What happens if it rains nonstop for one year?"


@pytest.fixture()
def planned(tmp_path):
    cfg = load_config(CONFIGS / "default.yaml", overrides={"project": {"topic": TOPIC}})
    orch = Orchestrator(cfg, tmp_path)
    return orch, orch.plan()


def test_eight_beat_roles_exist(planned):
    _, out = planned
    roles = [b["role"] for b in out["beats"]]
    assert roles == [
        "cold_open", "fact_1", "fact_2", "fact_3",
        "explanation", "scale_example", "gasp_reveal", "payoff",
    ]


def test_catchphrase_appears_once(planned):
    _, out = planned
    assert out["script"]["catchphrase_count"] == 1


def test_pilot_scene_count_is_four(planned):
    orch, out = planned
    scenes = [SceneSpec.model_validate(s) for s in out["scenes"]]
    assert len(orch.pilot_scene_specs(scenes)) == PILOT_SCENE_COUNT


def test_scene_ids_stable(planned):
    orch, out = planned
    ids1 = [s["scene_id"] for s in out["scenes"]]
    out2 = orch.plan()  # cached second run
    assert [s["scene_id"] for s in out2["scenes"]] == ids1 == [f"S{i:02d}" for i in range(1, 9)]


def test_prompt_has_all_required_blocks(planned):
    orch, out = planned
    first = out["scenes"][0]["scene_id"]
    compiled = load_json(orch.workspace / "scenes" / first / "compiled_prompt.json")
    assert list(compiled["blocks"].keys()) == BLOCK_ORDER


def test_final_timeline_equals_narration_duration(planned):
    _, out = planned
    scenes = [SceneSpec.model_validate(s) for s in out["scenes"]]
    script = SpokenScript.model_validate(out["script"])
    # synthetic uniform-word transcript standing in for the real one
    tokens = script.full_text.split()
    duration = script.est_duration_s
    step = duration / len(tokens)
    words = [AlignmentWord(word=t, start_s=i * step, end_s=(i + 1) * step) for i, t in enumerate(tokens)]
    segments = [AlignmentSegment(text=script.full_text, start_s=0, end_s=duration, words=words)]
    timings = align_scenes(scenes, segments, narration_duration_s=duration)
    assert timings[-1].end_s == pytest.approx(duration, abs=0.05)
