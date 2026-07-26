from sias.config import load_config
from sias.research.claims import build_research_pack
from sias.story.beats import build_story_beats, validate_beats
from sias.story.catchphrase import count_occurrences, placement_issues
from sias.story.hooks import generate_hooks, select_hook
from sias.story.spoken_script import FORBIDDEN_PHRASES, assemble_script, validate_script
from sias.story.validators import validate_story

from pathlib import Path

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
TOPIC = "What happens if it rains nonstop for one year?"
CATCH = "Your intuition skipped a page—let's put the science back."


def _pack():
    return build_research_pack(TOPIC)


def test_beats_grammar_offline():
    beats = build_story_beats(_pack())
    assert len(beats) == 8
    assert [b.role for b in beats][0] == "cold_open"
    assert [b.role for b in beats][-1] == "payoff"
    assert validate_beats(beats) == []


def test_beats_validation_catches_missing_payoff():
    beats = build_story_beats(_pack())[:-1]
    issues = validate_beats(beats)
    assert any("payoff" in i for i in issues)


def test_hooks_two_variants_scored_and_selected():
    hooks = generate_hooks(TOPIC)
    kinds = {h.kind for h in hooks}
    assert kinds == {"consequence_first", "contradiction_first"}
    selected = select_hook(hooks)
    assert selected.total > 0
    assert "overclaim_avoidance" in selected.scores


def test_catchphrase_counting_and_placement():
    beats = build_story_beats(_pack())
    script = assemble_script(beats, CATCH)
    assert count_occurrences(script.full_text, CATCH) == 1
    assert placement_issues(script, CATCH) == []
    # injected twice → flagged
    script2 = assemble_script(beats, CATCH)
    script2.full_text += " " + CATCH
    issues = placement_issues(script2, CATCH)
    assert any("2 times" in i for i in issues)


def test_forbidden_phrases_flagged():
    beats = build_story_beats(_pack())
    script = assemble_script(beats, CATCH)
    script.full_text = "In this video we learn things. " + script.full_text
    issues = validate_script(script)
    assert any("forbidden phrase" in i for i in issues)
    assert "in this video" in FORBIDDEN_PHRASES


def test_story_validator_rollup():
    cfg = load_config(CONFIGS / "default.yaml")
    beats = build_story_beats(_pack())
    script = assemble_script(beats, cfg.story.catchphrase)
    checks = validate_story(script, beats, cfg)
    assert not any(c.status == "FAIL" for c in checks)
