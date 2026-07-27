"""Simulated provider responses — the pre-spend proof that response handling works.

Every paid provider is exercised against *fabricated* HTTP responses shaped like
the real ones, including the shapes that actually broke live runs:

* BFL returns a REGION-SPECIFIC ``polling_url``; polling the generic
  ``/get_result`` gives 404 (fixed in ``sias.providers.bfl``).
* OpenAI streams a WAV whose data-chunk size is the ``0xFFFFFFFF`` placeholder,
  which made ``wave`` report a phantom ~24.8h narration.
* OpenRouter reviewers wrap their JSON in prose and ``` fences.

Nothing here touches the network and nothing here is a fallback: the simulation
only proves the *parsers* are correct. A scenario that should fail must fail —
if a malformed payload were silently accepted, this module raises.
"""

from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any, Callable

from sias.audio.alignment import align_scenes
from sias.audio.silence import assert_not_silent, assert_plausible_duration
from sias.audio.transcription import parse_transcription, transcript_similarity
from sias.exceptions import (
    AssetIntegrityError,
    ProviderRequestError,
    ProviderSchemaError,
    SIASError,
)
from sias.providers.bfl import BFLAdapter
from sias.providers.model_resolver import resolve_model
from sias.providers.openai_audio import OpenAIAudioAdapter
from sias.providers.openrouter import OpenRouterAdapter
from sias.schemas import SceneSpec
from sias.vision.consensus import combine
from sias.vision.qwen_reviewer import parse_vision_score


class PreflightError(SIASError):
    """A simulated provider response was mishandled — do not spend money yet."""


# --------------------------------------------------------------------------
# Synthetic assets (real bytes, so the integrity gates are genuinely exercised)
# --------------------------------------------------------------------------

def fake_png_bytes(width: int = 192, height: int = 192) -> bytes:
    """A real, decodable PNG comfortably above the 4096-byte integrity floor."""
    from PIL import Image

    # Deterministic pseudo-noise: a smooth gradient compresses below the 4096-byte
    # integrity floor, which would make the happy path fail for the wrong reason.
    pixels = bytearray()
    state = 0x2F6F8F
    for _ in range(width * height * 3):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        pixels.append((state >> 16) & 0xFF)
    img = Image.frombytes("RGB", (width, height), bytes(pixels))
    buf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    buf.close()
    img.save(buf.name)
    data = Path(buf.name).read_bytes()
    os.unlink(buf.name)
    return data


def fake_wav_bytes(seconds: float = 4.0, rate: int = 24000, streamed_header: bool = True) -> bytes:
    """WAV bytes shaped like an OpenAI TTS stream. With ``streamed_header`` the
    data-chunk and RIFF sizes carry the 0xFFFFFFFF placeholder that produced a
    phantom 89478s duration in a real run."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        sample = 0.45 * math.sin(2 * math.pi * 180 * t) + 0.2 * math.sin(2 * math.pi * 320 * t)
        frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    raw = bytearray(Path(tmp.name).read_bytes())
    os.unlink(tmp.name)
    if streamed_header:
        raw[4:8] = (0xFFFFFFFF).to_bytes(4, "little")
        raw[40:44] = (0xFFFFFFFF).to_bytes(4, "little")
    return bytes(raw)


def fake_transcription(text: str, duration_s: float) -> dict[str, Any]:
    """OpenAI ``verbose_json`` with word + segment granularity."""
    tokens = text.split() or ["silence"]
    step = duration_s / len(tokens)
    words = [
        {"word": tok, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
        for i, tok in enumerate(tokens)
    ]
    return {
        "task": "transcribe",
        "language": "english",
        "duration": duration_s,
        "text": text,
        "segments": [{"id": 0, "text": text, "start": 0.0, "end": duration_s}],
        "words": words,
    }


def fake_reviewer_json(scores: dict[str, float] | None = None,
                       hard_fail: list[str] | None = None) -> dict[str, Any]:
    """The reviewer object itself, exactly as the rubric asks models to return it."""
    body: dict[str, Any] = {
        "anatomy": 0.93, "style_fidelity": 0.9, "identity_consistency": 0.91,
        "composition": 0.88, "science_accuracy": 0.9, "story_clarity": 0.89,
        "novelty": 0.8, "hard_fail_reasons": list(hard_fail or []),
        "repair_instructions": [], "summary": "clean structural read",
    }
    body.update(scores or {})
    return body


def fake_reviewer_payload(scores: dict[str, float] | None = None,
                          hard_fail: list[str] | None = None,
                          wrap: bool = True) -> dict[str, Any]:
    """An OpenRouter chat completion whose content embeds the reviewer JSON —
    optionally wrapped in prose and a ``` fence, as models really do."""
    text = json.dumps(fake_reviewer_json(scores, hard_fail))
    if wrap:
        text = f"Here is my review.\n```json\n{text}\n```\nLet me know if you need more detail."
    return {"id": "gen-sim", "model": "sim", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}


def fake_model_catalog() -> dict[str, Any]:
    return {
        "data": [
            {"id": "qwen/qwen3-vl-32b-instruct",
             "architecture": {"modality": "text+image->text", "input_modalities": ["text", "image"]}},
            {"id": "qwen/qwen2.5-7b-instruct", "architecture": {"modality": "text->text", "input_modalities": ["text"]}},
            {"id": "google/gemini-2.5-flash",
             "architecture": {"modality": "text+image->text", "input_modalities": ["text", "image"]}},
            {"id": "google/gemma-2-9b-it", "architecture": {"modality": "text->text", "input_modalities": ["text"]}},
        ]
    }


# --------------------------------------------------------------------------
# Simulated transports
# --------------------------------------------------------------------------

def bfl_transport(region: str = "us1", pending_polls: int = 2, png: bytes | None = None,
                  final_status: str = "Ready", known_models: tuple[str, ...] = ("flux-2-pro", "flux-2-flex")):
    """Mimics BFL: submit hands back a region-specific polling_url; the generic
    ``api.bfl.ai/v1/get_result`` path 404s exactly like production."""
    png = png if png is not None else fake_png_bytes()
    state = {"polls": 0}

    def transport(method: str, url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
        if method == "POST":
            model = url.rsplit("/", 1)[-1]
            if model not in known_models:
                return 404, {"detail": "Not Found"}
            return 200, {"id": "sim-request-1",
                         "polling_url": f"https://api.{region}.bfl.ai/v1/get_result?id=sim-request-1"}
        if "get_result" in url:
            if f"api.{region}.bfl.ai" not in url:
                return 404, {"detail": "Not Found"}  # generic endpoint — the live 404
            state["polls"] += 1
            if state["polls"] <= pending_polls:
                return 200, {"id": "sim-request-1", "status": "Pending"}
            if final_status != "Ready":
                return 200, {"id": "sim-request-1", "status": final_status}
            return 200, {"id": "sim-request-1", "status": "Ready",
                         "result": {"sample": "https://delivery.bfl.ai/sim/sample.png"}}
        if "delivery.bfl.ai" in url:
            return 200, png
        return 404, {"detail": "Not Found"}

    return transport


def openai_transport(wav: bytes | None = None, transcript: dict[str, Any] | None = None, wpm: float = 155.0):
    """Coherent OpenAI stand-in: unless overridden, the synthesized WAV length
    follows the narration text at a realistic speaking rate, and the
    transcription echoes that same text and duration back — so alignment gets a
    self-consistent pair, exactly like the real API."""
    spoken: dict[str, Any] = {"text": "simulated narration text", "duration": 4.0}

    def transport(method: str, url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
        if url.endswith("/audio/speech"):
            if wav is not None:
                return 200, wav
            text = str((body or {}).get("input", "")) or spoken["text"]
            seconds = max(1.0, round(len(text.split()) / wpm * 60.0, 2))
            spoken.update(text=text, duration=seconds)
            return 200, fake_wav_bytes(seconds)
        if url.endswith("/audio/transcriptions"):
            if transcript is not None:
                return 200, transcript
            return 200, fake_transcription(spoken["text"], spoken["duration"])
        return 404, {"error": {"message": "unknown route"}}

    return transport


def openrouter_transport(payload: dict[str, Any] | None = None, catalog: dict[str, Any] | None = None):
    def transport(method: str, url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
        if url.endswith("/models"):
            return 200, catalog if catalog is not None else fake_model_catalog()
        if url.endswith("/chat/completions"):
            return 200, payload if payload is not None else fake_reviewer_payload()
        return 404, {"error": {"message": "unknown route"}}

    return transport


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

def _expect_raises(exc_types: tuple[type, ...], fn: Callable[[], Any], contains: str = "") -> str:
    try:
        fn()
    except exc_types as raised:
        message = str(raised)
        if contains and contains.lower() not in message.lower():
            raise PreflightError(
                f"expected the refusal to mention {contains!r}, got: {message}", stage="preflight"
            ) from raised
        return message
    raise PreflightError(
        f"a malformed/failing provider response was accepted instead of raising {exc_types}",
        stage="preflight",
    )


def _s_bfl_happy_path(work: Path) -> str:
    adapter = BFLAdapter(api_key="sim-key", transport=bfl_transport())
    out = adapter.generate("a scientific notebook cartoon panel", "flux-2-pro",
                           seed=987654321012345, width=1080, height=1920,
                           out_path=work / "bfl.png")
    size = Path(out).stat().st_size
    if size < 4096:
        raise PreflightError(f"simulated BFL image too small ({size} bytes)", stage="preflight")
    return f"submit → region polling_url → Ready → download ok ({size} bytes)"


def _s_bfl_region_url_required(work: Path) -> str:
    """The exact live bug: polling the generic endpoint 404s. The adapter must
    use the handed-back polling_url, and say so clearly if it ever gets a 404."""
    adapter = BFLAdapter(api_key="sim-key", transport=bfl_transport())
    detail = _expect_raises(
        (ProviderRequestError,),
        lambda: adapter.poll("sim-request-1", timeout_s=4.0, interval_s=0.0, sleep=lambda _s: None),
        contains="region-specific",
    )
    adapter.poll("sim-request-1", timeout_s=8.0, interval_s=0.0, sleep=lambda _s: None,
                 polling_url="https://api.us1.bfl.ai/v1/get_result?id=sim-request-1")
    return f"generic /get_result → explicit 404 refusal; region url → Ready ({detail[:48]}…)"


def _s_bfl_unknown_model(work: Path) -> str:
    adapter = BFLAdapter(api_key="sim-key", transport=bfl_transport())
    _expect_raises((ProviderRequestError,),
                   lambda: adapter.submit_job("flux-9-imaginary", {"prompt": "x"}),
                   contains="check the model name")
    return "unknown model → named endpoint + valid model hints"


def _s_bfl_moderated(work: Path) -> str:
    adapter = BFLAdapter(api_key="sim-key", transport=bfl_transport(final_status="Content Moderated"))
    _expect_raises((ProviderRequestError,),
                   lambda: adapter.poll("sim-request-1", timeout_s=4.0, interval_s=0.0, sleep=lambda _s: None,
                                        polling_url="https://api.us1.bfl.ai/v1/get_result?id=sim-request-1"),
                   contains="Content Moderated")
    return "moderated result → hard failure, no placeholder substituted"


def _s_bfl_truncated_download(work: Path) -> str:
    adapter = BFLAdapter(api_key="sim-key", transport=bfl_transport(png=b"\x89PNG\r\n\x1a\n truncated"))
    _expect_raises((AssetIntegrityError,),
                   lambda: adapter.generate("x", "flux-2-pro", 1, 1080, 1920, work / "trunc.png"),
                   contains="suspiciously small")
    return "truncated image download → AssetIntegrityError"


def _s_bfl_offline(work: Path) -> str:
    _expect_raises((ProviderRequestError,),
                   lambda: BFLAdapter(api_key="", transport=None).submit_job("flux-2-pro", {}),
                   contains="offline mode performs no paid calls")
    return "no transport → refuses instead of fabricating an image"


def _s_tts_streamed_header(work: Path) -> str:
    """OpenAI's streamed WAV header lies. Duration must come from real PCM."""
    adapter = OpenAIAudioAdapter(api_key="sim-key", transport=openai_transport(wav=fake_wav_bytes(4.0)))
    path = adapter.tts("simulated narration text", work / "narration.wav")
    stats = assert_not_silent(path)
    if not stats["header_mismatch"]:
        raise PreflightError("the streamed-WAV placeholder header was not detected", stage="preflight")
    if stats["header_frames"] != 2_147_483_647:
        raise PreflightError(f"unexpected phantom header frames: {stats['header_frames']}", stage="preflight")
    if abs(stats["duration_s"] - 4.0) > 0.1:
        raise PreflightError(f"PCM-derived duration wrong: {stats['duration_s']}s", stage="preflight")
    assert_plausible_duration(stats["duration_s"], 4.2)
    return (f"header claimed {stats['header_frames']} frames (~89478s phantom); "
            f"real PCM gives {stats['duration_s']}s")


def _s_duration_gate(work: Path) -> str:
    _expect_raises((AssetIntegrityError,), lambda: assert_plausible_duration(89478.485, 31.35), contains="hard cap")
    _expect_raises((AssetIntegrityError,), lambda: assert_plausible_duration(300.0, 31.35), contains="implausible")
    _expect_raises((AssetIntegrityError,), lambda: assert_plausible_duration(0.0, 31.35), contains="zero")
    return "phantom / truncated / zero narration durations all rejected pre-render"


def _s_tts_tiny_body(work: Path) -> str:
    adapter = OpenAIAudioAdapter(api_key="sim-key", transport=openai_transport(wav=b"RIFF short"))
    _expect_raises((ProviderRequestError,), lambda: adapter.tts("x", work / "tiny.wav"),
                   contains="suspiciously small")
    return "tiny TTS body → refused before it reaches the renderer"


def _s_transcription_alignment(work: Path) -> str:
    text = ("Rain keeps falling for a whole year and the ground gives up. "
            "Then the real surprise arrives quietly from far below the surface.")
    duration = 12.0
    adapter = OpenAIAudioAdapter(api_key="sim-key",
                                 transport=openai_transport(transcript=fake_transcription(text, duration)))
    raw = adapter.transcribe(work / "narration.wav")
    segments = parse_transcription(raw)
    if not segments or not segments[0].words:
        raise PreflightError("verbose_json parsed without word timestamps", stage="preflight")
    similarity = transcript_similarity(text, " ".join(s.text for s in segments))
    scenes = [
        SceneSpec(scene_id="S01", beat_role="cold_open",
                  narration="Rain keeps falling for a whole year and the ground gives up."),
        SceneSpec(scene_id="S02", beat_role="gasp_reveal",
                  narration="Then the real surprise arrives quietly from far below the surface."),
    ]
    timings = align_scenes(scenes, segments, narration_duration_s=duration)
    if abs(timings[-1].end_s - duration) > 0.08:
        raise PreflightError(
            f"timeline end {timings[-1].end_s}s != narration duration {duration}s", stage="preflight"
        )
    return (f"verbose_json → {len(segments[0].words)} word timestamps, similarity {similarity:.2f}, "
            f"timeline ends exactly at {timings[-1].end_s}s")


def _s_transcription_without_timestamps(work: Path) -> str:
    adapter = OpenAIAudioAdapter(api_key="sim-key", transport=openai_transport(transcript={"text": "no timings"}))
    _expect_raises((ProviderSchemaError,), lambda: adapter.transcribe(work / "narration.wav"),
                   contains="timestamps")
    return "transcription without timestamps → schema error, no guessed timeline"


def _s_model_resolution(work: Path) -> str:
    router = OpenRouterAdapter(api_key="sim-key", transport=openrouter_transport())
    catalog = router.list_models()
    qwen = resolve_model(catalog, "qwen/", ["vl"])
    gemini = resolve_model(catalog, "google/", ["flash", "vision"])
    if "vl" not in qwen or "flash" not in gemini:
        raise PreflightError(f"model resolution picked {qwen} / {gemini}", stage="preflight")
    return f"catalog → structural={qwen}, editorial={gemini} (text-only models filtered out)"


def _s_dual_review_consensus(work: Path) -> str:
    image = work / "review.png"
    image.write_bytes(fake_png_bytes())
    qwen_router = OpenRouterAdapter(api_key="sim-key", transport=openrouter_transport(fake_reviewer_payload()))
    gemini_router = OpenRouterAdapter(
        api_key="sim-key",
        transport=openrouter_transport(fake_reviewer_payload({"story_clarity": 0.94, "novelty": 0.86}, wrap=False)),
    )
    qwen = parse_vision_score(qwen_router.vision_review("qwen/qwen3-vl-32b-instruct", image, "rubric"),
                              "qwen/qwen3-vl-32b-instruct")
    gemini = parse_vision_score(gemini_router.vision_review("google/gemini-2.5-flash", image, "rubric"),
                                "google/gemini-2.5-flash")
    verdict = combine(qwen, gemini)
    if verdict["status"] != "APPROVED":
        raise PreflightError(f"clean dual review did not approve: {verdict}", stage="preflight")
    return (f"fenced JSON + bare JSON both parsed → {verdict['status']} "
            f"(consensus {verdict['consensus_score']})")


def _s_review_hard_fail_veto(work: Path) -> str:
    image = work / "review.png"
    if not image.exists():
        image.write_bytes(fake_png_bytes())
    router = OpenRouterAdapter(
        api_key="sim-key",
        transport=openrouter_transport(fake_reviewer_payload(hard_fail=["HF_ANATOMY_DUPLICATED_LIMB"])),
    )
    flagged = parse_vision_score(router.vision_review("qwen/qwen3-vl-32b-instruct", image, "rubric"), "qwen")
    clean = parse_vision_score(fake_reviewer_json(), "gemini")
    verdict = combine(flagged, clean)
    if verdict["status"] == "APPROVED":
        raise PreflightError("a hard-fail review was approved — the veto is not wired", stage="preflight")
    return f"hard-fail code vetoes high scores → {verdict['status']}"


def _s_review_malformed(work: Path) -> str:
    image = work / "review.png"
    if not image.exists():
        image.write_bytes(fake_png_bytes())
    prose = {"choices": [{"message": {"content": "The image looks good to me, roughly 9 out of 10."}}]}
    _expect_raises((ProviderSchemaError,),
                   lambda: OpenRouterAdapter(api_key="k", transport=openrouter_transport(prose))
                   .vision_review("m", image, "rubric"),
                   contains="no JSON object")
    out_of_range = fake_reviewer_payload({"anatomy": 4.7})
    _expect_raises((ProviderSchemaError,),
                   lambda: parse_vision_score(
                       OpenRouterAdapter(api_key="k", transport=openrouter_transport(out_of_range))
                       .vision_review("m", image, "rubric"), "m"),
                   contains="anatomy")
    bad_code = fake_reviewer_payload(hard_fail=["HF_TOTALLY_MADE_UP"])
    _expect_raises((ProviderSchemaError,),
                   lambda: parse_vision_score(
                       OpenRouterAdapter(api_key="k", transport=openrouter_transport(bad_code))
                       .vision_review("m", image, "rubric"), "m"),
                   contains="unknown hard-fail")
    return "prose-only / out-of-range score / invented hard-fail code all rejected"


def _s_openrouter_offline(work: Path) -> str:
    _expect_raises((ProviderRequestError,), lambda: OpenRouterAdapter(transport=None).list_models(),
                   contains="offline mode performs no paid calls")
    _expect_raises((ProviderRequestError,),
                   lambda: OpenAIAudioAdapter(transport=None).tts("x", work / "n.wav"),
                   contains="offline mode performs no paid calls")
    return "every offline adapter refuses rather than inventing a response"


def _s_live_multipart_transcription(work: Path) -> str:
    """The live class builds a multipart upload by hand; its response handling
    must be proven too, not just the JSON adapter's."""
    from ..providers.live import LiveOpenAIAudio

    audio = work / "multipart.wav"
    audio.write_bytes(fake_wav_bytes(2.0))
    adapter = LiveOpenAIAudio(api_key="sim-key", transport=openai_transport())
    raw = adapter.transcribe(audio)
    segments = parse_transcription(raw)
    _expect_raises(
        (ProviderRequestError,),
        lambda: LiveOpenAIAudio(api_key="", transport=openai_transport()).transcribe(audio),
        contains="OPENAI_API_KEY missing",
    )
    return f"multipart verbose_json parsed ({len(segments)} segment) · missing key refused"


SCENARIOS: list[tuple[str, Callable[[Path], str]]] = [
    ("bfl.generate happy path", _s_bfl_happy_path),
    ("bfl.poll region-specific url", _s_bfl_region_url_required),
    ("bfl.submit unknown model", _s_bfl_unknown_model),
    ("bfl.poll moderated", _s_bfl_moderated),
    ("bfl.download truncated", _s_bfl_truncated_download),
    ("bfl offline refusal", _s_bfl_offline),
    ("openai.tts streamed-WAV header", _s_tts_streamed_header),
    ("narration duration gate", _s_duration_gate),
    ("openai.tts tiny body", _s_tts_tiny_body),
    ("openai.transcribe → alignment", _s_transcription_alignment),
    ("openai.transcribe no timestamps", _s_transcription_without_timestamps),
    ("openai multipart transcription", _s_live_multipart_transcription),
    ("openrouter model resolution", _s_model_resolution),
    ("openrouter dual review consensus", _s_dual_review_consensus),
    ("openrouter hard-fail veto", _s_review_hard_fail_veto),
    ("openrouter malformed reviews", _s_review_malformed),
    ("openrouter/openai offline refusal", _s_openrouter_offline),
]


def simulated_adapters(region: str = "us1") -> dict[str, Any]:
    """The exact adapter dict `build_live_adapters()` returns, but wired to
    simulated transports. Pass it to `run_all(adapters=...)` to walk the entire
    LIVE code path — BFL generate, dual vision review, TTS, transcription,
    alignment, mastering, QC — for zero cost and with no network.

    This is a *test double*, never a fallback: nothing in the notebook's live
    path can reach it, because `run_all` only accepts it as an explicit argument.
    """
    from ..providers.live import LiveOpenAIAudio

    return {
        # pending_polls=0: the wait loop is already proven by its own scenario;
        # here we want the pipeline walked, not sleep() exercised.
        "bfl": BFLAdapter(api_key="sim-key", transport=bfl_transport(region=region, pending_polls=0)),
        "openai_audio": LiveOpenAIAudio(api_key="sim-key", transport=openai_transport()),
        "openrouter": OpenRouterAdapter(api_key="sim-key", transport=openrouter_transport()),
        "qwen_model": "qwen/qwen3-vl-32b-instruct",
        "gemini_model": "google/gemini-2.5-flash",
    }


def run_api_simulation(log=print, work_dir: str | Path | None = None) -> dict[str, Any]:
    """Run every provider scenario against simulated responses.

    Returns a summary dict. Raises ``PreflightError`` on the first scenario that
    mishandles a response — a failing preflight means the live run would break,
    so it must stop the notebook rather than warn.
    """
    tmp = tempfile.TemporaryDirectory() if work_dir is None else None
    work = Path(work_dir or tmp.name)
    work.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    try:
        for name, scenario in SCENARIOS:
            try:
                detail = scenario(work)
            except PreflightError:
                raise
            except Exception as exc:  # a real parser bug surfaced by the simulation
                raise PreflightError(
                    f"scenario {name!r} raised an unexpected {type(exc).__name__}: {exc}",
                    stage="preflight",
                ) from exc
            results.append({"scenario": name, "detail": detail})
            log(f"  ✅ {name:38s} {detail}")
    finally:
        if tmp is not None:
            tmp.cleanup()
    log(f"  Preflight: {len(results)}/{len(SCENARIOS)} simulated provider scenarios handled correctly.")
    return {"status": "PASS", "scenarios": results, "count": len(results)}
