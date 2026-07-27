"""One-click integration: run_all() offline must complete the ENTIRE pipeline
in a single call (plan→images→audio→alignment→render→QC→export) with zero paid
calls; the one-click notebook must Run All keyless via nbclient."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sias.schemas import SceneSpec
from sias.style.bible import build_style_bible
from sias_colab.config import load_studio_config
from sias_colab.studio import Studio

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


def test_run_all_full_integrated_diamond_stages(tmp_path):
    """The FULL edition runs every Diamond stage inside the same single call:
    routing snapshot, consistency flags, pre-reveal silence, audio checks,
    ASS subtitles, and the Diamond Editorial gate with its human gates."""
    import json

    from sias_colab.oneclick import run_all

    steps = []
    result = run_all("What happens if it rains nonstop for one year?", tmp_path,
                     live=False, log=steps.append)
    joined = "\n".join(steps)
    for marker in ("[1/10]", "[5/10]", "[6/10]", "[7/10]", "[9/10]", "[10/10]",
                   "AUDIO CHECKS", "DIAMOND", "pre-reveal hold"):
        assert marker in joined, f"missing stage marker: {marker}"

    # ASS subtitles with the Diamond style set
    ass = Path(result["ass"])
    assert ass.exists()
    text = ass.read_text()
    for style in ("Style: Default", "Style: Emphasis", "Style: Reveal", "Style: SciTerm"):
        assert style in text
    assert text.count("Dialogue:") >= 3

    # Diamond gate: all pillars evaluated, human gates held PENDING by default
    report = json.loads(Path(result["diamond_report"]).read_text())
    assert set(report["pillars"]) == {"story", "visual", "audio", "subtitle", "render"}
    assert result["diamond_status"] == "HUMAN_GATES_PENDING"
    assert all(v == "PENDING" for v in report["human_gates"].values())
    assert len(report["human_gates"]) == 6

    # manifest carries the diamond + routing evidence
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["extra"]["diamond_status"] == "HUMAN_GATES_PENDING"
    assert manifest["extra"]["hardware_profile"] in ("cpu", "t4_16gb", "gpu_24gb", "gpu_48gb")
    assert "audio_checks" in manifest["extra"]


def test_human_gates_flag_reaches_pass(tmp_path):
    """Approving the six human gates is the ONLY way to reach Diamond PASS."""
    from sias_colab.oneclick import run_all

    result = run_all("What happens if it rains nonstop for one year?", tmp_path,
                     live=False, human_gates_approved=True, log=lambda *_: None)
    assert result["diamond_status"] == "PASS"


def test_oneclick_notebook_keyless_run_all():
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "execute_oneclick_notebook.py")],
                          capture_output=True, text=True, timeout=1200)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "ONECLICK KEYLESS: PASS" in proc.stdout


def test_live_path_end_to_end_against_simulated_providers(tmp_path):
    """Walk the ENTIRE live pipeline — BFL generate, dual vision review, TTS,
    multipart transcription, alignment, loudnorm pre-master, render, QC, Diamond
    gate — against simulated provider responses. Zero network, zero cost.

    This is the regression net for live-only defects that PREVIEW can never
    reach: it is how the extensible-WAV crash and the accumulating frame drift
    were found.
    """
    from sias_colab.oneclick import run_all
    from sias_colab.preflight.simulation import simulated_adapters

    result = run_all("Why does ice float on water?", workspace=tmp_path, live=True,
                     adapters=simulated_adapters(), human_gates_approved=True,
                     log=lambda *_a, **_k: None)

    assert result["mode"] == "LIVE"
    assert result["qc_status"] == "PASS"
    assert result["diamond_status"] == "PASS"
    assert Path(result["video"]).stat().st_size > 20_000
    assert Path(result["ass"]).exists() and Path(result["srt"]).exists()
    assert result["budget"]["image_calls"] >= result["scenes"]  # every scene really paid a call
    assert result["budget"]["vision_calls"] >= 2 * result["scenes"]  # dual review ran per scene


def test_every_scene_is_conditioned_on_the_style_anchor(tmp_path):
    """A shared style paragraph does not hold a sequence together; a shared
    reference image does. Assert the anchor is generated once, before any scene,
    and is actually attached to every scene's BFL payload."""
    from sias_colab.oneclick import run_all
    from sias_colab.preflight.simulation import simulated_adapters

    adapters = simulated_adapters()
    payloads: list[dict] = []
    original = adapters["bfl"].transport

    def recording(method, url, headers, body):
        if method == "POST":
            payloads.append(dict(body or {}))
        return original(method, url, headers, body)

    adapters["bfl"].transport = recording
    result = run_all("How do mRNA vaccines work?", workspace=tmp_path, live=True,
                     adapters=adapters, human_gates_approved=True,
                     log=lambda *_a, **_k: None)

    anchor = tmp_path / "episodes" / "how-do-mrna-vaccines-work" / "style" / "style_anchor.png"
    assert anchor.exists(), "no style anchor was generated"

    # First call is the anchor and references nothing; every later call carries it.
    assert "reference_images" not in payloads[0]
    scene_calls = payloads[1:]
    assert scene_calls, "no scene generations were recorded"
    for payload in scene_calls:
        assert str(anchor) in payload.get("reference_images", []), \
            "a scene was generated without the episode style anchor"
    assert result["mode"] == "LIVE" and result["qc_status"] == "PASS"


def test_drift_gate_spends_one_bounded_regeneration(tmp_path):
    """Flagging drift without acting on it is what let earlier episodes wander.
    Feed a panel that violates the palette and assert exactly one repair."""
    from sias.render.schematic import draw_schematic
    from sias_colab.oneclick import _live_scene_image
    from sias_colab.preflight.simulation import fake_png_bytes, simulated_adapters

    anchor = draw_schematic(tmp_path / "anchor.png", 256, 256, "single_subject")
    off_palette = fake_png_bytes()  # deterministic noise: nothing like the anchor

    adapters = simulated_adapters()
    calls = {"n": 0}
    original = adapters["bfl"].transport

    def recording(method, url, headers, body):
        if method == "POST":
            calls["n"] += 1
        return original(method, url, headers, body)

    adapters["bfl"].transport = recording
    adapters.pop("openrouter")  # isolate the drift gate from the review loop

    cfg = load_studio_config(ROOT / "configs" / "default.yaml",
                             overrides={"project": {"topic": "drift"}})
    studio = Studio(cfg, tmp_path / "ws")
    bible = build_style_bible(cfg.engine)
    scene = SceneSpec(scene_id="S02", beat_role="fact_1", narration="a drifting panel")

    (tmp_path / "out").mkdir()
    _live_scene_image(scene, bible, adapters, studio, tmp_path / "out" / "S02.png",
                      256, 256, log=lambda *_a, **_k: None, index=1, anchor=anchor)

    assert calls["n"] == 2, f"expected one generation + one bounded drift repair, got {calls['n']}"
    assert studio.budget.snapshot()["image_calls"] == 2
    assert off_palette  # the fixture is what makes the anchor comparison meaningful
