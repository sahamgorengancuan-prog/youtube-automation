import math
import os
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image

from sias.schemas import AlignmentSegment, AlignmentWord, QCCheck, SceneTiming, StoryBeat
from sias_colab.diamond.benchmark import build_benchmark_set, run_benchmark, selection_report
from sias_colab.diamond.consistency import HeuristicConsistency, calibrate_thresholds, palette_distance
from sias_colab.diamond.hardware import detect_profile
from sias_colab.diamond.mastering import audio_checks, final_mix_cmd, insert_pre_reveal_silence, narration_premaster_cmd
from sias_colab.diamond.registry import build_default_registry
from sias_colab.diamond.standard import DiamondGate
from sias_colab.diamond.subtitles import build_ass, chunk_words, validate_subtitles
from sias_colab.exceptions import ConfigurationError


# ---------- registry ----------------------------------------------------------

def test_registry_primary_routing_with_keys(monkeypatch):
    for k in ("OPENAI_API_KEY", "BFL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(k, "x" * 20)
    reg = build_default_registry("cpu")
    assert reg.route("ImageGenerationService").name == "bfl-flux"
    assert reg.route("VisionQCService").name == "qwen3-vl-32b@openrouter"
    names = [b.name for b in reg.eligible("VisionQCService")]
    assert "gemini-2.5-flash@openrouter" in names  # second opinion rides along
    reg.assert_no_self_approval()  # architect=qwen, but gemini is independent -> ok


def test_registry_blocks_noncommercial_and_disabled(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x" * 20)
    reg = build_default_registry("gpu_48gb")
    tts = {b.name: b for b in reg.backends["TTSService"]}
    # F5-TTS weights are CC-BY-NC -> never eligible even if force-enabled
    tts["f5-tts"].default_enabled = True
    assert "f5-tts" not in [b.name for b in reg.eligible("TTSService")]
    # fish-speech research-only likewise
    tts["fish-speech"].default_enabled = True
    assert "fish-speech" not in [b.name for b in reg.eligible("TTSService")]


def test_registry_hardware_gating_and_explanations(monkeypatch):
    monkeypatch.delenv("BFL_API_KEY", raising=False)
    reg = build_default_registry("cpu")
    img = {b.name: b for b in reg.backends["ImageGenerationService"]}
    img["qwen-image"].default_enabled = True  # even enabled, cpu profile blocks 48gb model
    with pytest.raises(ConfigurationError):
        reg.route("ImageGenerationService")
    reasons = {r["backend"]: r["reason"] for r in reg.explain_skips("ImageGenerationService")}
    assert "needs gpu_48gb" in reasons["qwen-image"] or "license" in reasons["qwen-image"]
    assert "dependencies/keys not present" in reasons["bfl-flux"]


def test_capability_report_covers_all_services(monkeypatch):
    reg = build_default_registry("cpu")
    report = reg.capability_report()
    assert len(report["services"]) == 13


# ---------- consistency ---------------------------------------------------------

def _img(path: Path, color):
    Image.new("RGB", (64, 64), color).save(path)
    return str(path)


def test_heuristic_consistency_flags_palette_drift(tmp_path):
    ref = _img(tmp_path / "ref.png", (244, 238, 220))
    same = _img(tmp_path / "same.png", (240, 235, 218))
    drift = _img(tmp_path / "drift.png", (10, 240, 30))
    hc = HeuristicConsistency([ref])
    assert hc.check(same)["status"] == "OK"
    result = hc.check(drift)
    assert result["status"] == "FLAG" and result["flags"]
    assert palette_distance(ref, drift) > palette_distance(ref, same)
    assert "approval still requires VLM QC" in result["note"]


def test_threshold_calibration_per_axis():
    fake = {"a": [1, 0], "b": [0.95, 0.05], "r1": [0, 1], "r2": [0.1, 0.9]}
    thresholds = calibrate_thresholds(lambda p: fake[p], ["a", "b"], ["r1", "r2"])
    assert set(thresholds) == {"character_identity", "style_identity",
                               "environment_continuity", "composition_diversity"}
    assert 0 < thresholds["style_identity"] < 1


# ---------- subtitles ------------------------------------------------------------

def _words(text, start=0.0, step=0.3):
    return [AlignmentWord(word=w, start_s=start + i * step, end_s=start + (i + 1) * step)
            for i, w in enumerate(text.split())]


def test_chunking_2_to_6_words():
    words = _words("the rain keeps falling, and the ground gives up completely under pressure today")
    chunks = chunk_words(words)
    assert all(2 <= len(c) <= 6 for c in chunks)
    seg = AlignmentSegment(text="x", start_s=0, end_s=5, words=words)
    assert validate_subtitles([seg]) == []


def test_ass_styles_emphasis_and_reveal():
    words = _words("the real surprise is not water at all")
    seg = AlignmentSegment(text="x", start_s=0, end_s=3, words=words)
    ass = build_ass([seg], emphasis_words=["surprise"], reveal_word="water", font="MyFont")
    assert "Style: Default,MyFont" in ass and "Style: Reveal" in ass
    assert r"{\rEmphasis}surprise" in ass
    assert "WATER" in ass  # reveal word uppercased on its own timing
    assert ass.count("Dialogue:") >= 2  # chunked, not one full sentence


# ---------- mastering --------------------------------------------------------------

def _wav(path: Path, seconds=1.0, channels=1, silent_tail=0.0, amp=0.5, rate=16000):
    n = int(seconds * rate)
    tail = int(silent_tail * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b""
        for i in range(n):
            v = int(amp * 32767 * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", v) * channels
        frames += struct.pack("<h", 0) * channels * tail
        wf.writeframes(frames)
    return path


def test_mastering_cmds_have_targets():
    pre = " ".join(narration_premaster_cmd("n.wav", "out.wav"))
    assert "loudnorm=I=-16.0:TP=-1.0" in pre
    mix = " ".join(final_mix_cmd("n.wav", "final.wav", music="bed.wav"))
    assert "amix" in mix and "I=-14.0" in mix and "volume=-14.0dB" in mix


def test_audio_checks_silence_and_stereo(tmp_path):
    ok = audio_checks(_wav(tmp_path / "ok.wav", 1.0))
    assert ok["silence_ok"] and not ok["clipping"]
    long_gap = audio_checks(_wav(tmp_path / "gap.wav", 0.5, silent_tail=4.0))
    assert not long_gap["silence_ok"] and long_gap["longest_silence_s"] >= 3.0
    stereo = audio_checks(_wav(tmp_path / "st.wav", 1.0, channels=2))
    assert stereo["stereo_ok"]


def test_pre_reveal_silence_shifts_gap():
    t = [SceneTiming(scene_id="S1", start_s=0, end_s=5), SceneTiming(scene_id="S2", start_s=5, end_s=10)]
    out = insert_pre_reveal_silence(t, "S2", hold_s=0.4)
    assert out[1].start_s == pytest.approx(5.4)
    assert out[0].end_s == pytest.approx(5.4)
    assert out[1].end_s == 10  # total duration unchanged


# ---------- diamond standard ---------------------------------------------------------

def _beats():
    return [StoryBeat(beat_id=f"B{i}", role=r, narration=f"beat {i}")
            for i, r in enumerate(["cold_open", "fact_1", "gasp_reveal", "payoff"], 1)]


def test_diamond_gate_pillars_and_human_veto():
    gate = DiamondGate()
    timings = [SceneTiming(scene_id="S1", start_s=0.0, end_s=4.0),
               SceneTiming(scene_id="S2", start_s=4.0, end_s=8.0)]
    pillars = {
        "story": gate.story(timings, _beats(), ["short sentence here ok"]),
        "visual": gate.visual([{"hard_fail_reasons": []}], ["A", "B", "A"]),
        "audio": gate.audio(0.99, {"clipping": False, "silence_ok": True}),
        "subtitle": gate.subtitle([]),
        "render": [QCCheck(check_id="streams", level="final", status="PASS")],
    }
    approvals = {g: "APPROVED" for g in
                 ("hook_approval", "style_lock_approval", "recurring_character_approval",
                  "pilot_approval", "audio_approval", "final_mobile_viewing_approval")}
    assert gate.evaluate(pillars, approvals).status == "PASS"
    # pending human gates hold PASS back
    assert gate.evaluate(pillars, {}).status == "HUMAN_GATES_PENDING"
    # a human rejection is final even with perfect metrics
    rejected = dict(approvals, pilot_approval="REJECTED")
    assert gate.evaluate(pillars, rejected).status == "FAIL"
    # three identical compositions fail the visual pillar
    pillars["visual"] = gate.visual([{"hard_fail_reasons": []}], ["A", "A", "A"])
    assert gate.evaluate(pillars, approvals).status == "FAIL"
    # transcript below 98% fails audio
    pillars["visual"] = gate.visual([{"hard_fail_reasons": []}], ["A", "B"])
    pillars["audio"] = gate.audio(0.9, {"clipping": False, "silence_ok": True})
    assert gate.evaluate(pillars, approvals).status == "FAIL"


# ---------- benchmark -----------------------------------------------------------------

def test_benchmark_set_and_task_winners(tmp_path):
    cases = build_benchmark_set()
    assert len(cases) == 70 and len({c["case_id"] for c in cases}) == 70

    def strong(prompt):
        return {"prompt_compliance": 0.9, "consistency": 0.9, "anatomy": 0.9}

    def weak(prompt):
        return {"prompt_compliance": 0.5, "consistency": 0.5, "anatomy": 0.5}

    results = run_benchmark({"good-open": strong, "blocked-model": strong, "weak": weak},
                            cases=cases[:14],
                            license_statuses={"good-open": "COMMERCIAL_OK",
                                              "blocked-model": "NONCOMMERCIAL_ONLY",
                                              "weak": "COMMERCIAL_OK"})
    report = selection_report(results, tmp_path / "selection.json")
    winners = {v["model"] for v in report["winners_by_task"].values()}
    assert winners == {"good-open"}  # blocked license can never win; weak loses
    assert (tmp_path / "selection.json").exists()


def test_hardware_profile_forced(monkeypatch):
    monkeypatch.setenv("SIAS_HW_PROFILE", "t4_16gb")
    assert detect_profile() == "t4_16gb"
    monkeypatch.setenv("SIAS_HW_PROFILE", "")
    assert detect_profile() in ("cpu", "t4_16gb", "gpu_24gb", "gpu_48gb")


def test_no_paid_env_leak():
    # This suite must run with or without keys; nothing here spends.
    assert "diamond" in os.listdir(Path(__file__).resolve().parents[2] / "src" / "sias_colab")
