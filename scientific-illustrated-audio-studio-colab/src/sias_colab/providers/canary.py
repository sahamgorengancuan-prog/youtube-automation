"""Provider canary — the cheapest possible end-to-end proof that every key and
adapter works, BEFORE any real spend. Produces provider_canary/ artifacts and a
pass/fail report. Never auto-continues to style lock."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sias.audio.silence import wav_stats
from sias.filesystem import atomic_write_bytes, atomic_write_json
from sias.vision.gemini_reviewer import editorial_rubric
from sias.vision.qwen_reviewer import parse_vision_score, structural_rubric

from ..budget import BudgetGuardian
from ..schemas import CanaryReport

CANARY_PROMPT = (
    "Scientific Notebook Cartoon test panel: one smiling scientist (one head, one "
    "torso, two arms, two legs) pointing at a single labelled beaker on warm "
    "off-white notebook paper with a faint grid. One focal idea, safe caption zone."
)
CANARY_EDIT = "Keep everything identical, but circle the beaker with an orange hand-drawn callout."
CANARY_TTS_TEXT = (
    "This is the SIAS voice canary. If you can hear a warm, curious sentence "
    "with natural rhythm, narration is working."
)


def run_canary(
    out_dir: str | Path,
    adapters: dict[str, Any],
    budget: BudgetGuardian,
    qwen_model: str,
    gemini_model: str,
    include_higgsfield: bool = False,
) -> CanaryReport:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = CanaryReport(higgsfield_included=include_higgsfield)
    checks = report.checks

    bfl = adapters["bfl"]
    budget.charge("image", 1, "canary text-to-image")
    img1 = bfl.generate(CANARY_PROMPT, model=adapters.get("bfl_model", "flux-2-pro"), seed=11,
                        width=768, height=1344, out_path=out / "bfl_text_to_image.png")
    checks["bfl_text_to_image"] = {"ok": True, "path": str(img1)}

    budget.charge("image", 1, "canary reference edit")
    img2 = bfl.generate(CANARY_EDIT, model=adapters.get("bfl_model", "flux-2-pro"), seed=12,
                        width=768, height=1344, out_path=out / "bfl_reference_edit.png",
                        reference_paths=[str(img1)])
    checks["bfl_reference_edit"] = {"ok": True, "path": str(img2)}

    router = adapters["openrouter"]
    budget.charge("vision", 1, "canary qwen")
    qwen_raw = router.vision_review(qwen_model, img1, structural_rubric("canary panel", "Scientific Notebook Cartoon"))
    qwen = parse_vision_score(qwen_raw, qwen_model)
    atomic_write_json(out / "qwen_review.json", qwen.model_dump())
    checks["qwen_review"] = {"ok": True, "model": qwen_model}

    budget.charge("vision", 1, "canary gemini")
    gem_raw = router.vision_review(gemini_model, img1, editorial_rubric("canary panel", "Scientific Notebook Cartoon"))
    gemini = parse_vision_score(gem_raw, gemini_model)
    atomic_write_json(out / "gemini_review.json", gemini.model_dump())
    checks["gemini_review"] = {"ok": True, "model": gemini_model}

    audio = adapters["openai_audio"]
    budget.charge("tts_chars", len(CANARY_TTS_TEXT), "canary tts")
    wav = audio.tts(CANARY_TTS_TEXT, out / "narration.wav")
    stats = wav_stats(wav)
    if stats["peak_dbfs"] <= -55:
        raise RuntimeError("canary TTS is effectively silent — hard failure")
    checks["tts_audible"] = {"ok": True, **stats}

    trans = audio.transcribe(wav)
    if not (trans.get("words") or trans.get("segments")):
        raise RuntimeError("canary transcription lacks timestamps")
    atomic_write_json(out / "transcription.json", trans)
    checks["transcription_timestamps"] = {"ok": True}

    if include_higgsfield and adapters.get("higgsfield") is not None:
        hf = adapters["higgsfield"]
        budget.charge("higgsfield", 1, "canary model list")
        models = hf.list_models()
        atomic_write_json(out / "higgsfield_optional_report.json", {"models_seen": len(models)})
        checks["higgsfield_optional"] = {"ok": True, "models": len(models)}
    else:
        atomic_write_bytes(out / "higgsfield_optional_report.json", b'{"skipped": true}')

    report.status = "PASS"
    report.cost_snapshot = budget.snapshot()
    atomic_write_json(out / "canary_report.json", report.model_dump())
    lines = [f"# Canary report — {report.status}", ""]
    for name, result in checks.items():
        lines.append(f"- ✅ {name}: {result}")
    atomic_write_bytes(out / "canary_report.md", "\n".join(lines).encode("utf-8"))
    return report
