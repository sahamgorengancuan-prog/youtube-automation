"""The preflight must actually catch mishandled provider responses — a suite
that always passes would be worse than none."""

from __future__ import annotations

import pytest

from sias.exceptions import AssetIntegrityError, ProviderRequestError, ProviderSchemaError
from sias.providers.bfl import BFLAdapter
from sias.providers.openai_audio import OpenAIAudioAdapter
from sias_colab.preflight.simulation import (
    SCENARIOS,
    PreflightError,
    bfl_transport,
    fake_png_bytes,
    fake_reviewer_json,
    fake_transcription,
    fake_wav_bytes,
    openai_transport,
    run_api_simulation,
)


def test_all_scenarios_pass(tmp_path):
    result = run_api_simulation(log=lambda *_a, **_k: None, work_dir=tmp_path)
    assert result["status"] == "PASS"
    assert result["count"] == len(SCENARIOS) >= 17
    assert {r["scenario"] for r in result["scenarios"]} == {name for name, _ in SCENARIOS}


def test_fake_png_survives_the_integrity_floor():
    # A smooth gradient compresses under 4096 bytes and would fail for the wrong
    # reason; the simulated payload must clear the real download gate.
    assert len(fake_png_bytes()) > 4096


def test_simulated_bfl_generic_endpoint_404s(tmp_path):
    """The simulator must reproduce the live bug, otherwise it proves nothing."""
    adapter = BFLAdapter(api_key="k", transport=bfl_transport())
    with pytest.raises(ProviderRequestError, match="region-specific"):
        adapter.poll("sim-request-1", timeout_s=2.0, interval_s=0.0, sleep=lambda _s: None)


def test_simulated_streamed_wav_reproduces_the_phantom_header(tmp_path):
    from sias.audio.silence import wav_stats

    path = tmp_path / "n.wav"
    path.write_bytes(fake_wav_bytes(3.0))
    stats = wav_stats(path)
    assert stats["header_frames"] == 2_147_483_647
    assert stats["header_mismatch"] is True
    assert stats["duration_s"] == pytest.approx(3.0, abs=0.05)


def test_simulated_transcription_has_word_timestamps():
    raw = fake_transcription("one two three", 3.0)
    assert len(raw["words"]) == 3 and raw["segments"][0]["end"] == 3.0


def test_reviewer_json_is_accepted_by_the_strict_parser():
    from sias.vision.qwen_reviewer import parse_vision_score

    score = parse_vision_score(fake_reviewer_json(), "sim")
    assert score.anatomy == 0.93 and score.hard_fail_reasons == []


def test_preflight_fails_loudly_when_a_response_is_mishandled(monkeypatch, tmp_path):
    """Swap in an adapter that swallows a bad response; the preflight must stop
    the run instead of reporting PASS."""
    import sias_colab.preflight.simulation as sim

    def _broken(_work):
        raise ValueError("parser returned a fabricated success")

    monkeypatch.setattr(sim, "SCENARIOS", [("broken provider", _broken)])
    with pytest.raises(PreflightError, match="unexpected ValueError"):
        sim.run_api_simulation(log=lambda *_a, **_k: None, work_dir=tmp_path)


def test_preflight_rejects_a_silently_accepted_failure(tmp_path):
    import sias_colab.preflight.simulation as sim

    with pytest.raises(PreflightError, match="accepted instead of raising"):
        sim._expect_raises((ProviderRequestError,), lambda: "no exception at all")


def test_tiny_tts_body_is_refused(tmp_path):
    adapter = OpenAIAudioAdapter(api_key="k", transport=openai_transport(wav=b"RIFF"))
    with pytest.raises(ProviderRequestError, match="suspiciously small"):
        adapter.tts("x", tmp_path / "a.wav")


def test_truncated_image_is_refused(tmp_path):
    adapter = BFLAdapter(api_key="k", transport=bfl_transport(png=b"\x89PNG short"))
    with pytest.raises(AssetIntegrityError):
        adapter.generate("x", "flux-2-pro", 1, 512, 512, tmp_path / "a.png")


def test_transcription_without_timestamps_is_refused(tmp_path):
    adapter = OpenAIAudioAdapter(api_key="k", transport=openai_transport(transcript={"text": "hi"}))
    with pytest.raises(ProviderSchemaError):
        adapter.transcribe(tmp_path / "a.wav")
