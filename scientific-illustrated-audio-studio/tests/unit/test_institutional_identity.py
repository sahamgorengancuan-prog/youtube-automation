"""The Institutional Lab Notebook identity: deterministic typesetting, a closed
palette, and a prompt that keeps the image model out of the type department."""

from __future__ import annotations

import pytest
from PIL import Image

from sias.config import SIASConfig
from sias.exceptions import RenderError
from sias.render.hud import PanelSpec, compose_panel
from sias.render.schematic import SCHEMATICS, content_box, draw_schematic
from sias.style.composition import ARCHETYPES, derive_composition
from sias.schemas import SceneSpec
from sias.style.bible import build_style_bible
from sias.style.institutional import (
    IDENTITY_NAME,
    PALETTE,
    RESERVED_BOTTOM,
    RESERVED_TOP,
    build_institutional_bible,
    derive_background,
    derive_headline,
    derive_headline_anchor,
    derive_readout,
    derive_schematic,
)
from sias.style.prompt_compiler import compile_prompt, missing_required_clauses
from sias.vision.gemini_reviewer import editorial_rubric
from sias.vision.qwen_reviewer import structural_rubric


def _cfg() -> SIASConfig:
    return SIASConfig.model_validate({"visual": {"identity_name": IDENTITY_NAME}})


# --- identity dispatch ------------------------------------------------------

def test_config_selects_the_institutional_bible():
    bible = build_style_bible(_cfg())
    assert bible.identity_name == IDENTITY_NAME and bible.version == "5"
    assert bible.palette["primary"] == "#3E7EB8"
    assert "no grid" in bible.paper  # the notebook grid is gone in v5


def test_palette_is_closed_and_every_role_is_a_hex_colour():
    assert len(PALETTE) == 10
    for role, value in PALETTE.items():
        assert value.startswith("#") and len(value) == 7, role
        int(value[1:], 16)


# --- headline derivation ----------------------------------------------------

def test_display_headline_keeps_the_question_and_balances_lines():
    scene = SceneSpec(scene_id="S01", beat_role="cold_open",
                      panel_title="What if Earth suddenly stopped spinning?")
    lines = derive_headline(scene, display=True)
    assert 2 <= len(lines) <= 3
    assert lines[0].startswith("WHAT IF")
    assert all(line == line.upper() for line in lines)
    # Balanced, not ragged: no line more than twice another.
    counts = [len(line.split()) for line in lines]
    assert max(counts) - min(counts) <= 1


def test_label_headline_is_short_and_drops_filler():
    scene = SceneSpec(scene_id="S04", beat_role="fact_2",
                      panel_title="The ocean displacement")
    assert derive_headline(scene) == ["OCEAN DISPLACEMENT"]


def test_headline_preserves_source_word_order():
    """Filler is dropped, words are never reordered — silently rewriting an
    editor's title into "DISPLACEMENT OCEAN" would be worse than a long line."""
    scene = SceneSpec(scene_id="S04", panel_title="The displacement of the ocean")
    assert derive_headline(scene) == ["DISPLACEMENT OCEAN"]


def test_and_survives_as_an_ampersand():
    scene = SceneSpec(scene_id="S06", beat_role="gasp_reveal", panel_title="Static day and night")
    assert derive_headline(scene) == ["STATIC DAY", "& NIGHT"]


def test_headline_falls_back_through_labels_then_objective():
    assert derive_headline(SceneSpec(scene_id="S02", scientific_labels=["wind shear"])) == ["WIND SHEAR"]
    assert derive_headline(SceneSpec(scene_id="S03", visual_objective="a flooded valley")) == ["FLOODED VALLEY"]
    assert derive_headline(SceneSpec(scene_id="S05")) == []


# --- the readout never invents a measurement --------------------------------

def test_readout_uses_a_real_metric_when_the_scene_carries_one():
    scene = SceneSpec(scene_id="S03", beat_role="fact_2",
                      panel_metric={"label": "wind", "value": "1700", "unit": "km/h"})
    assert derive_readout(scene) == {"label": "WIND", "value": "1700", "unit": "km/h", "alert": False}


def test_readout_without_a_metric_reports_status_not_a_number():
    """A fabricated figure on a science panel is worse than no figure at all."""
    for beat in ("cold_open", "fact_1", "gasp_reveal", "payoff", "totally_unknown_beat"):
        readout = derive_readout(SceneSpec(scene_id="S01", beat_role=beat))
        assert not any(ch.isdigit() for ch in str(readout["value"])), beat


def test_critical_beats_raise_the_alert_flag():
    assert derive_readout(SceneSpec(scene_id="S07", beat_role="gasp_reveal"))["alert"] is True
    assert derive_readout(SceneSpec(scene_id="S02", beat_role="fact_1"))["alert"] is False


# --- background + schematic derivation --------------------------------------

def test_night_panel_comes_from_the_subject_not_the_drama():
    night = SceneSpec(scene_id="S06", beat_role="fact_1", panel_title="Static day and night")
    tense = SceneSpec(scene_id="S05", beat_role="gasp_reveal", panel_title="Ocean displacement")
    assert derive_background(night) == "split_right"
    assert derive_background(tense) == "light"


def test_explicit_background_overrides_the_derivation():
    scene = SceneSpec(scene_id="S01", panel_background="night", panel_title="Ocean")
    assert derive_background(scene) == "night"


def test_schematic_follows_the_layout_grammar_not_the_subject():
    """The preview must draw the archetype the live prompt asks for. Keying on
    subjects (globe / city / ocean) copied the reference episode and made every
    other topic look wrong."""
    def kind(beat, i=1):
        return derive_schematic(SceneSpec(scene_id="S0", beat_role=beat), i)

    assert kind("cold_open", 0) == "single_subject"
    assert kind("explanation") == "process_flow"
    assert kind("scale_example") == "quantity_row"
    assert kind("gasp_reveal") == "before_after"
    assert set(SCHEMATICS) == set(ARCHETYPES)


def test_headline_anchor_keeps_the_title_card_at_the_top():
    assert derive_headline_anchor(SceneSpec(scene_id="S01", beat_role="cold_open"), 0) == "top_center"
    assert derive_headline_anchor(SceneSpec(scene_id="S09", beat_role="payoff"), 9) == "bottom_center"
    assert derive_headline_anchor(SceneSpec(scene_id="S04", beat_role="fact_2"), 4) == "bottom_left"


# --- the compositor ---------------------------------------------------------

def _panel(tmp_path, **kw) -> Image.Image:
    spec = PanelSpec(experiment_id="#024", headline=["OCEAN", "DISPLACEMENT"], **kw)
    out = compose_panel(spec, tmp_path / "p.png", 640, 360)
    return Image.open(out).convert("RGB")


def test_panel_composes_in_both_orientations(tmp_path):
    spec = PanelSpec(experiment_id="#024", headline=["STATIC DAY", "& NIGHT"])
    for w, h in ((640, 360), (405, 720), (1080, 1920)):
        img = Image.open(compose_panel(spec, tmp_path / f"{w}x{h}.png", w, h))
        assert img.size == (w, h)


def test_light_panel_is_white_and_night_panel_is_not(tmp_path):
    light = _panel(tmp_path, background="light")
    night = _panel(tmp_path, background="night")
    assert light.getpixel((320, 200)) == (255, 255, 255)
    assert night.getpixel((320, 200)) == (0x23, 0x26, 0x2B)


def test_split_panel_is_white_left_and_dark_right(tmp_path):
    img = _panel(tmp_path, background="split_right", split_at=0.56)
    assert img.getpixel((120, 200)) == (255, 255, 255)
    assert img.getpixel((600, 200)) == (0x23, 0x26, 0x2B)


def test_split_panel_headline_stays_on_the_white_side(tmp_path):
    """Ink-on-black is invisible; the headline must be constrained to the seam."""
    spec = PanelSpec(experiment_id="#024", headline=["ATMOSPHERE DISPLACEMENT"],
                     background="split_right", split_at=0.56)
    img = Image.open(compose_panel(spec, tmp_path / "s.png", 640, 360)).convert("RGB")
    seam = int(640 * 0.56)
    # The headline band on the dark side must be empty. (The frame itself does
    # cross the seam, so the crop stays inside it.)
    band = img.crop((seam + 8, int(360 * 0.60), 640 - 20, int(360 * 0.95)))
    assert (0x14, 0x16, 0x1A) not in list(band.getdata())


def test_headline_never_overflows_the_safe_margin(tmp_path):
    long_line = "EXTRAORDINARILY LONG HEADLINE THAT SHOULD BE SHRUNK NOT CLIPPED"
    spec = PanelSpec(experiment_id="#024", headline=[long_line], headline_style="display")
    img = Image.open(compose_panel(spec, tmp_path / "l.png", 640, 360)).convert("RGB")
    for x in (0, 1, 638, 639):
        column = [img.getpixel((x, y)) for y in range(360)]
        assert all(px == (255, 255, 255) for px in column), f"ink reached column {x}"


def test_watermark_bar_does_not_cover_the_artwork(tmp_path):
    spec = PanelSpec(experiment_id="#024", headline=["OCEAN"], watermark="PREVIEW")
    img = Image.open(compose_panel(spec, tmp_path / "w.png", 640, 360)).convert("RGB")
    assert img.getpixel((320, 356)) == (0xC0, 0x39, 0x2B)   # bar at the very bottom
    assert img.getpixel((320, 180)) == (255, 255, 255)      # middle of the panel is clear


def test_illustration_is_composited_underneath(tmp_path):
    art = draw_schematic(tmp_path / "art.png", 640, 360, "water_terrain")
    spec = PanelSpec(experiment_id="#024", headline=["OCEAN"])
    img = Image.open(compose_panel(spec, tmp_path / "c.png", 640, 360, illustration=art)).convert("RGB")
    assert (0x3E, 0x7E, 0xB8) in img.getdata()  # the blue water survived


def test_compose_rejects_an_unusable_panel_size(tmp_path):
    with pytest.raises(RenderError, match="too small"):
        compose_panel(PanelSpec(), tmp_path / "x.png", 10, 10)


def test_panel_spec_rejects_unknown_modes():
    with pytest.raises(ValueError, match="background"):
        PanelSpec(background="rainbow")
    with pytest.raises(ValueError, match="headline_anchor"):
        PanelSpec(headline_anchor="middle_left")


def test_headline_is_capped_at_three_lines_and_uppercased():
    spec = PanelSpec(headline=["one", "two", "three", "four"])
    assert spec.headline == ["ONE", "TWO", "THREE"]


# --- schematics -------------------------------------------------------------

def test_every_schematic_renders(tmp_path):
    for kind in SCHEMATICS:
        out = draw_schematic(tmp_path / f"{kind}.png", 640, 360, kind)
        assert Image.open(out).size == (640, 360)


def test_content_box_moves_out_of_the_headline_band():
    top_anchored = content_box(640, 360, "top_center")
    bottom_anchored = content_box(640, 360, "bottom_left")
    assert top_anchored[1] > bottom_anchored[1]      # art pushed down under a top headline
    assert bottom_anchored[3] < top_anchored[3]      # art lifted above a bottom headline
    assert bottom_anchored[1] >= int(360 * RESERVED_TOP)
    assert bottom_anchored[3] <= int(360 * (1 - RESERVED_BOTTOM))


# --- the prompt keeps the model out of the type department ------------------

def test_institutional_prompt_forbids_typography_and_locks_the_palette():
    bible = build_institutional_bible(_cfg())
    scene = SceneSpec(scene_id="S03", beat_role="fact_2", panel_title="Atmosphere displacement",
                      visual_objective="wind tearing across a city")
    compiled = compile_prompt(scene, bible, index=3)
    text = compiled["text"].lower()
    assert compiled["identity"] == IDENTITY_NAME
    assert "render no typography" in text
    assert "any text you draw is a defect" in text
    assert "#3e7eb8" in text                      # closed palette is spelled out
    assert f"top {int(RESERVED_TOP * 100)}%" in text
    assert missing_required_clauses(compiled["text"]) == []


def test_legacy_identity_still_compiles_its_own_prompt():
    """The v5 identity is a dispatch, not a deletion — older episodes still build."""
    from sias.style.bible import build_style_bible as legacy

    bible = legacy(SIASConfig())
    compiled = compile_prompt(SceneSpec(scene_id="S01", narration="x"), bible)
    assert "identity" not in compiled
    assert missing_required_clauses(compiled["text"]) == []


def test_reviewers_are_told_the_diagram_carries_no_text():
    structural = structural_rubric("wind across a city", IDENTITY_NAME)
    editorial = editorial_rubric("wind across a city", IDENTITY_NAME)
    assert "HF_TEXT_HALLUCINATION" in structural and "typeset" in structural.lower()
    assert "#3E7EB8" in structural                      # palette gate is enforceable
    assert "cannot read any label" in editorial
    # The legacy rubric is untouched.
    assert "notebook cartoon" in structural_rubric("x", "Scientific Notebook Cartoon").lower()


def test_apostrophes_survive_the_headline():
    """Splitting on apostrophes produced "REAL SURPRISE ISN T" in a real run."""
    scene = SceneSpec(scene_id="S07", narration="The real surprise isn't what your gut expected.")
    lines = derive_headline(scene)
    assert "ISN'T" in " ".join(lines)
    assert " T" not in " ".join(lines)  # the old split produced "ISN T"


def test_dangling_ampersands_are_trimmed():
    scene = SceneSpec(scene_id="S01", beat_role="cold_open",
                      narration="Imagine what if Earth suddenly stopped spinning and it doesn't stop.")
    lines = derive_headline(scene, display=True)
    joined = " ".join(lines)
    assert not joined.startswith("&") and not joined.endswith("&")


def test_planner_scaffolding_never_reaches_a_headline():
    scene = SceneSpec(scene_id="S02", beat_role="fact_1",
                      visual_objective="fact_1 beat for What if Earth suddenly stopped spinning?",
                      narration="At first, everything looks almost normal.")
    lines = derive_headline(scene)
    assert "BEAT" not in " ".join(lines) and "FACT_1" not in " ".join(lines)
    assert lines == ["FIRST EVERYTHING", "LOOKS NORMAL"]


def test_unknown_beats_rotate_instead_of_repeating():
    """Eight identical layouts read as a template, not a story."""
    scenes = [SceneSpec(scene_id=f"S0{i}", beat_role="unmapped_beat") for i in range(6)]
    kinds = {derive_schematic(s, i) for i, s in enumerate(scenes)}
    assert len(kinds) == 6


def test_archetypes_are_topic_agnostic():
    """The same beat gives the same layout whatever the episode is about — that
    is what lets an auto-generated vaccine episode share a grammar with a
    monsoon one."""
    for topic in ("how mRNA vaccines work", "why black holes bend light",
                  "what if it rained for a year"):
        scene = SceneSpec(scene_id="S05", beat_role="scale_example",
                          panel_title=topic, narration=topic)
        assert derive_schematic(scene, 5) == "quantity_row"


def test_explicit_archetype_overrides_the_beat_mapping():
    scene = SceneSpec(scene_id="S02", beat_role="cold_open",
                      composition_archetype="cross_section")
    assert derive_composition(scene, 0) == "cross_section"


def test_prompt_carries_the_layout_archetype():
    bible = build_institutional_bible(_cfg())
    scene = SceneSpec(scene_id="S04", beat_role="scale_example", narration="a lot of water")
    text = compile_prompt(scene, bible, index=4)["text"]
    assert "quantity_row" in text and ARCHETYPES["quantity_row"][:30] in text


# --- consistency of auto-generated illustrations ----------------------------

def test_style_anchor_prompt_has_no_subject_and_no_text():
    """The anchor is referenced by every scene, so any subject in it would leak
    into all twelve panels — which is exactly why scene 1 is a bad anchor."""
    from sias.style.style_lock import style_anchor_prompt

    prompt = style_anchor_prompt(build_institutional_bible(_cfg()))
    assert "language samples, not illustrations of anything" in prompt
    assert "Render NO lettering" in prompt
    assert "#3E7EB8" in prompt  # the anchor establishes the closed palette


def test_reference_pack_puts_the_style_anchor_first(tmp_path):
    """Reference order is authority order: the style board defines the language,
    the previous panel only carries short-range continuity."""
    from sias.style.reference_pack import select_references
    from sias.style.style_lock import anchor_assets

    anchor = draw_schematic(tmp_path / "anchor.png", 320, 180, "single_subject")
    previous = draw_schematic(tmp_path / "prev.png", 320, 180, "process_flow")
    scene = SceneSpec(scene_id="S03", beat_role="fact_2", continuity_refs=["S02"])

    record = select_references(scene, anchor_assets(anchor, previous))
    kinds = [r["kind"] for r in record["selected"]]
    assert kinds == ["master_style_board", "previous_scene"]
    assert all(r["sha256"] for r in record["selected"])  # provenance recorded


def test_reference_pack_survives_a_missing_previous_panel(tmp_path):
    from sias.style.reference_pack import select_references
    from sias.style.style_lock import anchor_assets

    anchor = draw_schematic(tmp_path / "anchor.png", 320, 180, "single_subject")
    record = select_references(SceneSpec(scene_id="S01"), anchor_assets(anchor, None))
    assert [r["kind"] for r in record["selected"]] == ["master_style_board"]


def test_drift_directive_names_what_broke_and_preserves_the_subject():
    from sias.style.style_lock import drift_repair_directive

    directive = drift_repair_directive(["palette drift 0.61 > 0.45"])
    assert "palette drift 0.61" in directive
    assert "Keep the subject and" in directive  # a drift repair is not a redesign


def test_truncation_does_not_strand_a_conjunction():
    """"... WORK & DOESN'T" is a cut clause; "STATIC DAY & NIGHT" is complete.
    Only a truncated list may lose its trailing "& X"."""
    cut = SceneSpec(scene_id="S01", beat_role="cold_open",
                    narration="Imagine how do mRNA vaccines work and it doesn't stop.")
    assert "&" not in " ".join(derive_headline(cut, display=True))
    intact = SceneSpec(scene_id="S06", panel_title="Static day and night")
    assert derive_headline(intact) == ["STATIC DAY", "& NIGHT"]
