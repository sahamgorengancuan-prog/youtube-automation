"""Offline smoke test: exercises the deterministic media path with NO network
and NO LLM. Builds a research bundle via the fallback synthesis, then runs
validation -> script -> storyboard -> scene plan -> SVG cache -> composition,
and checks cache reuse + SVG validity."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="autostudio_smoke_"))
os.environ["AUTOSTUDIO_ROOT"] = str(ROOT)
for rel in ["cache", "assets", "storyboards", "scenes", "scripts", "research", "output", "logs", "config"]:
    (ROOT / rel).mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autostudio.config import load_config
from autostudio.research import ScientificValidator, ResearchService
from autostudio.scene_planner import ScenePlanner
from autostudio.cache import AssetCache
from autostudio.composer import SceneComposer
from autostudio.storyboard_generator import StoryboardGenerator
from autostudio.script_generator import ScriptGenerator
from autostudio.schemas import ResearchBundle, ResearchFact, SourceRecord, ScriptPackage, ScriptBeat

config = load_config()
print("config loaded; palette keys:", list(config.style.palette))

# --- fake sources + fallback research (no LLM) -----------------------------
sources = [
    SourceRecord(source_id="cro-aaaaaaaaaa", provider="Crossref", title="Rotation and atmosphere", snippet="Earth rotates at about 1670 km/h at the equator.", score=0.9),
    SourceRecord(source_id="ope-bbbbbbbbbb", provider="OpenAlex", title="Ocean redistribution", snippet="Stopping rotation would redistribute oceans toward the poles.", score=0.88),
    SourceRecord(source_id="arx-cccccccccc", provider="arXiv", title="Atmospheric dynamics", snippet="Winds near 1700 km/h would scour the surface.", score=0.92),
    SourceRecord(source_id="wik-dddddddddd", provider="Wikipedia", title="Earth's rotation", snippet="A solar day would last a full year.", score=0.58),
]

llm = None  # force fallback path
service = ResearchService(config, llm, ROOT / "cache" / "research")
bundle = service._fallback("What if Earth suddenly stopped rotating?", sources)
bundle = bundle.model_copy(update={"research_hash": "testhash"})
report = ScientificValidator(config).validate(bundle)
print("validation passed:", report.passed, "coverage:", report.coverage_score, "diversity:", report.source_diversity)

# --- fake script (skip LLM, use timing/repair internals) -------------------
sg = ScriptGenerator(config, llm, ROOT / "cache" / "scripts")
beats = [
    ScriptBeat(beat_id="B01", purpose="cold open", narration="Imagine the Earth freezing mid spin in a single instant.", evidence_refs=["F01"]),
    ScriptBeat(beat_id="B02", purpose="rule", narration="The ground stops but the atmosphere keeps moving at 1670 kilometers per hour.", evidence_refs=["F01"]),
    ScriptBeat(beat_id="B03", purpose="consequence", narration="Continent scale winds tear across every city and forest on the planet.", evidence_refs=["F03"]),
    ScriptBeat(beat_id="B04", purpose="escalation", narration="Oceans surge away from the equator and drown the poles under new seas.", evidence_refs=["F02"]),
    ScriptBeat(beat_id="B05", purpose="ending", narration="One day would now last a full year of blistering light and frozen dark.", evidence_refs=["F04"]),
]
pkg = ScriptPackage(topic=bundle.topic, hook="h", curiosity="c", scientific_explanation="s", escalation="e", ending="end", beats=beats)
pkg = sg._repair(pkg, bundle)
pkg = pkg.model_copy(update={"script_hash": "scripthash"})
print("script words:", pkg.total_words, "duration_s:", pkg.estimated_duration_s, "beats:", len(pkg.beats))

# --- storyboard fallback (no LLM) ------------------------------------------
sb_gen = StoryboardGenerator(config, llm, ROOT / "cache" / "storyboards")
storyboard = sb_gen._fallback(pkg)
storyboard = ScenePlanner(config).plan(storyboard)
print("scenes:", len(storyboard.scenes), "catalog assets:", len(storyboard.asset_catalog))
print("asset types:", sorted({r.asset_type for r in storyboard.asset_catalog}))

# --- asset cache: generate + prove reuse -----------------------------------
cache = AssetCache(config, ROOT / "cache")
asset_paths = {}
for req in storyboard.asset_catalog:
    path, meta = cache.get_or_create(req, storyboard.topic)
    asset_paths[req.asset_id] = path
    assert path.exists() and path.stat().st_size > 0
# second pass -> reuse_counter must increment
reuse_seen = 0
for req in storyboard.asset_catalog:
    path, meta = cache.get_or_create(req, storyboard.topic)
    reuse_seen = max(reuse_seen, meta.reuse_counter)
print("assets generated:", len(asset_paths), "max reuse_counter after 2nd pass:", reuse_seen)
assert reuse_seen >= 1, "cache reuse did not increment"

# --- compose scenes ---------------------------------------------------------
composer = SceneComposer(config)
scene_dir = ROOT / "output" / "scenes"
scene_paths = []
for i, scene in enumerate(storyboard.scenes, start=1):
    out = composer.compose_scene(scene, asset_paths, scene_dir / f"scene{i:02d}.svg", storyboard.canvas_width, storyboard.canvas_height)
    scene_paths.append(out)
    txt = out.read_text()
    assert txt.startswith("<?xml") and "</svg>" in txt
    # forbidden features must not appear
    low = txt.lower()
    assert "lineargradient" not in low and "data:image/png" not in low
print("scenes composed:", len(scene_paths))

sheet = composer.compose_contact_sheet(scene_paths, ROOT / "output" / "contact.svg")
html = composer.compose_preview_html(scene_paths, ROOT / "output" / "preview.html")
print("contact sheet bytes:", sheet.stat().st_size, "| preview html bytes:", html.stat().st_size)

# validate every scene SVG parses as XML
from xml.etree import ElementTree as ET
for p in scene_paths + [sheet]:
    ET.parse(p)
print("all SVGs parse as valid XML")

print("\nSMOKE TEST PASSED ->", ROOT)
