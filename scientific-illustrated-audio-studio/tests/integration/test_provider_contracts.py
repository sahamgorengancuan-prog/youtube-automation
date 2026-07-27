"""Provider contract tests — mocked transports only; no live keys required."""

import json

import pytest

from sias.exceptions import AssetIntegrityError, ProviderRequestError, ProviderSchemaError
from sias.providers.bfl import BFLAdapter, build_payload
from sias.providers.model_resolver import resolve_model
from sias.providers.openai_audio import OpenAIAudioAdapter
from sias.providers.openai_text import OpenAITextAdapter
from sias.providers.openrouter import OpenRouterAdapter


def test_offline_adapters_fail_explicitly():
    with pytest.raises(ProviderRequestError):
        BFLAdapter().submit("flux-2-pro", {})
    with pytest.raises(ProviderRequestError):
        OpenAITextAdapter().structured_json(system="s", prompt="p")
    with pytest.raises(ProviderRequestError):
        OpenAIAudioAdapter().tts("hello", "out.wav")
    with pytest.raises(ProviderRequestError):
        OpenRouterAdapter().vision_review("qwen/x", __file__, "rubric")


def test_bfl_submit_poll_download(tmp_path):
    calls = []
    png = b"\x89PNG" + b"0" * 8000

    def transport(method, url, headers, body):
        calls.append((method, url))
        if url.endswith("/flux-2-pro"):
            assert body["prompt"] and "seed" in body
            return 200, {"id": "req-1"}
        if "get_result" in url:
            return 200, {"status": "Ready", "result": {"sample": "https://cdn/img.png"}}
        return 200, png

    adapter = BFLAdapter(api_key="k", transport=transport)
    out = adapter.generate("a prompt", "flux-2-pro", seed=7, width=64, height=64, out_path=tmp_path / "img.png")
    assert out.exists() and out.stat().st_size > 4096
    assert [m for m, _ in calls] == ["POST", "GET", "GET"]


def test_bfl_uses_region_specific_polling_url(tmp_path):
    """Regression for a real live failure: BFL returns a REGION-SPECIFIC
    polling_url (api.us1.bfl.ai). Constructing `{base}/get_result?id=` 404s.
    generate() must poll the URL the API handed back."""
    png = b"\x89PNG" + b"0" * 8000
    seen: list[str] = []

    def transport(method, url, headers, body):
        seen.append(url)
        if url.endswith("/flux-2-pro"):
            assert headers.get("accept") == "application/json"
            assert body["seed"] < 2_147_483_647  # 32-bit-range seed
            return 200, {"id": "req-9", "polling_url": "https://api.us1.bfl.ai/v1/get_result?id=req-9"}
        if url.startswith("https://api.us1.bfl.ai/"):
            return 200, {"status": "Ready", "result": {"sample": "https://cdn/i.png"}}
        if "get_result" in url:  # the OLD constructed URL — the live 404
            return 404, {"detail": "Not Found"}
        return 200, png

    out = BFLAdapter(api_key="k", transport=transport).generate(
        "p", "flux-2-pro", seed=2**47, width=768, height=1344, out_path=tmp_path / "i.png")
    assert out.exists()
    assert any(u.startswith("https://api.us1.bfl.ai/") for u in seen), "region polling_url not used"
    assert not any(u == "https://api.bfl.ai/v1/get_result?id=req-9" for u in seen)


def test_bfl_poll_404_is_actionable():
    adapter = BFLAdapter(api_key="k", transport=lambda m, u, h, b: (404, {"detail": "Not Found"}))
    with pytest.raises(ProviderRequestError, match="region-specific"):
        adapter.poll("req-1", polling_url="https://api.us1.bfl.ai/v1/get_result?id=req-1")


def test_bfl_submit_404_names_the_model():
    adapter = BFLAdapter(api_key="k", transport=lambda m, u, h, b: (404, {"detail": "Not Found"}))
    with pytest.raises(ProviderRequestError, match="flux-2-pro-preview"):
        adapter.submit_job("flux-2-bogus", {})


def test_bfl_moderation_is_explicit_failure():
    def transport(method, url, headers, body):
        if url.endswith("/flux-2-pro"):
            return 200, {"id": "req-1"}
        return 200, {"status": "Request Moderated"}

    adapter = BFLAdapter(api_key="k", transport=transport)
    with pytest.raises(ProviderRequestError, match="Moderated"):
        adapter.poll(adapter.submit("flux-2-pro", build_payload("p", "flux-2-pro", 1, 64, 64)))


def test_bfl_tiny_download_rejected(tmp_path):
    adapter = BFLAdapter(api_key="k", transport=lambda m, u, h, b: (200, b"tiny"))
    with pytest.raises(AssetIntegrityError):
        adapter.download("https://cdn/x.png", tmp_path / "x.png")


def test_openai_text_json_contract():
    good = {"choices": [{"message": {"content": json.dumps({"beats": []})}}]}
    adapter = OpenAITextAdapter(api_key="k", transport=lambda m, u, h, b: (200, good))
    assert adapter.structured_json(system="s", prompt="p") == {"beats": []}
    bad = {"choices": [{"message": {"content": "not json"}}]}
    adapter_bad = OpenAITextAdapter(api_key="k", transport=lambda m, u, h, b: (200, bad))
    with pytest.raises(ProviderSchemaError):
        adapter_bad.structured_json(system="s", prompt="p")


def test_openai_tts_and_transcription(tmp_path):
    wav = b"RIFF" + b"0" * 4000
    audio = OpenAIAudioAdapter(api_key="k", transport=lambda m, u, h, b: (200, wav))
    out = audio.tts("hello world", tmp_path / "n.wav")
    assert out.read_bytes().startswith(b"RIFF")

    verbose = {"text": "hello", "segments": [{"text": "hello", "start": 0, "end": 1}], "words": []}
    trans = OpenAIAudioAdapter(api_key="k", transport=lambda m, u, h, b: (200, verbose))
    got = trans.transcribe(tmp_path / "n.wav")
    assert "segments" in got

    no_ts = OpenAIAudioAdapter(api_key="k", transport=lambda m, u, h, b: (200, {"text": "hello"}))
    with pytest.raises(ProviderSchemaError):
        no_ts.transcribe(tmp_path / "n.wav")


def test_openrouter_vision_strict_json(tmp_path):
    img = tmp_path / "i.png"
    img.write_bytes(b"\x89PNG" + b"0" * 100)
    payload_seen = {}

    def transport(method, url, headers, body):
        payload_seen.update(body or {})
        return 200, {"choices": [{"message": {"content": 'ok {"anatomy": 1.0} trailing'}}]}

    adapter = OpenRouterAdapter(api_key="k", transport=transport, deny_data_collection=True)
    parsed = adapter.vision_review("qwen/qwen3-vl", img, "rubric")
    assert parsed == {"anatomy": 1.0}
    assert payload_seen.get("provider", {}).get("data_collection") == "deny"

    bad = OpenRouterAdapter(api_key="k", transport=lambda m, u, h, b: (200, {"choices": [{"message": {"content": "no json here"}}]}))
    with pytest.raises(ProviderSchemaError):
        bad.vision_review("qwen/x", img, "rubric")


def test_model_resolver_filters_and_ranks():
    catalog = [
        {"id": "qwen/qwen3-vl-235b", "architecture": {"modality": "text+image->text"}},
        {"id": "qwen/qwen3-coder", "architecture": {"modality": "text->text"}},
        {"id": "google/gemini-2.5-flash", "architecture": {"modality": "text+image->text"}},
    ]
    assert resolve_model(catalog, "qwen/", ["vl"]) == "qwen/qwen3-vl-235b"
    assert resolve_model(catalog, "google/", ["flash"]).startswith("google/")
    with pytest.raises(ProviderRequestError):
        resolve_model(catalog, "meta/", [])
