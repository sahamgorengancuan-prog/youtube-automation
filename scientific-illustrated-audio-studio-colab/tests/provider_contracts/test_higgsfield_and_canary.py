"""Mocked contract tests: Higgsfield adapter/CLI + the full canary routine.
No live keys, no network."""

import json
from pathlib import Path

import pytest

from sias.schemas import RunBudget
from sias_colab.budget import BudgetGuardian
from sias_colab.exceptions import ProviderRequestError, ProviderSchemaError
from sias_colab.providers.higgsfield import HiggsfieldAdapter, parse_cli_model_list, resolve_credentials
from sias_colab.providers.canary import run_canary


def test_credentials_resolution():
    assert resolve_credentials({"HF_KEY": "k" * 20})["scheme"] == "key"
    both = resolve_credentials({"HF_API_KEY": "a" * 20, "HF_API_SECRET": "b" * 20})
    assert both["scheme"] == "key_secret"
    assert resolve_credentials({}) == {}


def test_higgsfield_offline_refuses():
    with pytest.raises(ProviderRequestError):
        HiggsfieldAdapter().list_models()


def test_higgsfield_models_cost_generate(tmp_path):
    png = b"\x89PNG" + b"0" * 8000

    def transport(method, url, headers, body):
        if url.endswith("/models"):
            return 200, {"models": [{"id": "flux-2", "type": "image"}]}
        if "/cost" in url:
            return 200, {"usd": 0.04}
        if url.endswith("/generate"):
            return 200, {"url": "https://cdn/x.png"}
        return 200, png

    hf = HiggsfieldAdapter({"scheme": "key", "key": "k"}, transport)
    assert hf.list_models()[0]["id"] == "flux-2"
    assert hf.estimate_cost("flux-2")["usd"] == 0.04
    out = hf.generate_image("prompt", "flux-2", tmp_path / "img.png")
    assert out.stat().st_size > 4096


def test_higgsfield_job_failure_and_virality_schema(tmp_path):
    hf_fail = HiggsfieldAdapter({"scheme": "key", "key": "k"}, lambda m, u, h, b: (500, {}))
    with pytest.raises(ProviderRequestError):
        hf_fail.generate_image("p", "m", tmp_path / "x.png")
    hf_bad = HiggsfieldAdapter({"scheme": "key", "key": "k"}, lambda m, u, h, b: (200, {"hook": 0.7}))
    with pytest.raises(ProviderSchemaError):
        hf_bad.analyze_video("v.mp4")
    ok = HiggsfieldAdapter({"scheme": "key", "key": "k"},
                           lambda m, u, h, b: (200, {"hook": 0.7, "attention": 0.6, "retention": 0.5}))
    assert ok.analyze_video("v.mp4")["hook"] == 0.7


def test_cli_model_list_parsing():
    assert parse_cli_model_list(json.dumps([{"id": "m1"}]))[0]["id"] == "m1"
    assert parse_cli_model_list(json.dumps({"models": [{"id": "m2"}]}))[0]["id"] == "m2"
    with pytest.raises(ProviderSchemaError):
        parse_cli_model_list("not json")


def _wav_bytes(seconds=1.0, rate=16000):
    import io
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            struct.pack("<h", int(0.4 * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(seconds * rate))))
    return buf.getvalue()


def test_canary_end_to_end_mocked(tmp_path):
    from PIL import Image
    import io

    img_buf = io.BytesIO()
    Image.new("RGB", (768, 1344), "#F4EEDC").save(img_buf, format="PNG")
    png = img_buf.getvalue()
    review = json.dumps({f: 0.9 for f in
                         ("anatomy", "style_fidelity", "identity_consistency", "composition",
                          "science_accuracy", "story_clarity", "novelty")}
                        | {"hard_fail_reasons": [], "repair_instructions": [], "summary": "ok"})

    def bfl_transport(method, url, headers, body):
        if method == "POST":
            return 200, {"id": "req"}
        if "get_result" in url:
            return 200, {"status": "Ready", "result": {"sample": "https://cdn/i.png"}}
        return 200, png

    def or_transport(method, url, headers, body):
        return 200, {"choices": [{"message": {"content": review}}]}

    wav = _wav_bytes()

    class AudioMock:
        def tts(self, text, out_path, **kw):
            Path(out_path).write_bytes(wav)
            return Path(out_path)

        def transcribe(self, path, **kw):
            return {"segments": [{"text": "canary", "start": 0, "end": 1}],
                    "words": [{"word": "canary", "start": 0, "end": 1}]}

    from sias.providers.bfl import BFLAdapter
    from sias.providers.openrouter import OpenRouterAdapter

    adapters = {
        "bfl": BFLAdapter(api_key="k", transport=bfl_transport),
        "openrouter": OpenRouterAdapter(api_key="k", transport=or_transport),
        "openai_audio": AudioMock(),
    }
    budget = BudgetGuardian(RunBudget(max_image_calls=5, max_vision_calls=5, max_tts_characters=500))
    report = run_canary(tmp_path / "provider_canary", adapters, budget,
                        qwen_model="qwen/x", gemini_model="google/y")
    assert report.status == "PASS"
    for f in ("bfl_text_to_image.png", "bfl_reference_edit.png", "qwen_review.json",
              "gemini_review.json", "narration.wav", "transcription.json",
              "canary_report.json", "canary_report.md", "higgsfield_optional_report.json"):
        assert (tmp_path / "provider_canary" / f).exists(), f
    assert report.checks["tts_audible"]["ok"]
