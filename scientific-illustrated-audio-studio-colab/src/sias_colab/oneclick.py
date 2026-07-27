"""One-click pipeline: run_all() executes the ENTIRE flow in one call —
plan → images → narration → alignment → render → captions → QC → export.

Two modes, decided by keys + the live flag:
* PREVIEW (no keys or live=False): zero paid calls. Watermarked placeholder
  illustrations, a synthesized audio bed, an estimated timeline — a complete,
  honest end-to-end MP4 clearly marked NOT FOR PRODUCTION.
* LIVE (keys present and live=True): BFL illustrations (one candidate per
  scene, optional dual vision review + one bounded repair), ONE OpenAI TTS
  narration track, real transcription, word-timestamp alignment (audio is the
  timeline source of truth), full-resolution render, strict QC.

Every step prints progress; every failure is explicit; budget caps hard-stop.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any

from sias.audio.alignment import align_scenes
from sias.audio.silence import assert_not_silent
from sias.audio.transcription import parse_transcription, transcript_similarity
from sias.filesystem import atomic_write_json
from sias.render.captions import write_srt
from sias.render.ffmpeg import render_episode
from sias.render.timeline import build_render_scenes
from sias.render.validator import validate_final
from sias.schemas import (
    AlignmentSegment,
    AlignmentWord,
    EpisodeManifest,
    SceneSpec,
    SpokenScript,
    StyleBible,
)
from sias.style.prompt_compiler import compile_prompt
from sias.vision.gemini_reviewer import editorial_rubric
from sias.vision.qwen_reviewer import parse_vision_score, structural_rubric
from sias.vision.consensus import combine
from sias.vision.tournament import stable_seed, verify_image
from sias.qc.report import build_report, write_report
from sias.qc.final_av import check_final_av

from .config import load_studio_config
from .qc.placeholders import make_placeholder, reject_placeholders_in_production
from .studio import Studio


def _tone_bed(path: Path, seconds: float, rate: int = 16000) -> Path:
    """Soft preview audio bed (two mellow tones, gently pulsed) — audible, so
    the silence gate passes, and obviously not narration."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        env = 0.25 + 0.15 * math.sin(2 * math.pi * 0.4 * t)
        sample = env * (math.sin(2 * math.pi * 220 * t) + 0.4 * math.sin(2 * math.pi * 330 * t))
        frames += struct.pack("<h", int(max(-1, min(1, sample)) * 32767 * 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path


def _synthetic_segments(script: SpokenScript, scenes: list[SceneSpec], duration: float) -> list[AlignmentSegment]:
    tokens = script.full_text.split()
    step = duration / max(1, len(tokens))
    words = [AlignmentWord(word=t, start_s=i * step, end_s=(i + 1) * step) for i, t in enumerate(tokens)]
    return [AlignmentSegment(text=script.full_text, start_s=0.0, end_s=duration, words=words)]


def _segments_for_srt(scenes: list[SceneSpec], timings) -> list[AlignmentSegment]:
    by_id = {t.scene_id: t for t in timings}
    out = []
    for scene in scenes:
        t = by_id.get(scene.scene_id)
        if t:
            out.append(AlignmentSegment(text=scene.narration, start_s=t.start_s, end_s=t.end_s))
    return out


def _live_scene_image(scene: SceneSpec, bible: StyleBible, adapters: dict, studio: Studio,
                      out_path: Path, width: int, height: int, log) -> Path:
    prompt = compile_prompt(scene, bible)["text"]
    seed = stable_seed(studio.cfg.engine.visual.seed_base, scene.scene_id, 0)
    studio.budget.charge("image", 1, f"scene {scene.scene_id}")
    img = adapters["bfl"].generate(prompt, studio.cfg.engine.visual.production_model,
                                   seed, width, height, out_path)
    verify_image(img)
    if adapters.get("openrouter"):
        studio.budget.charge("vision", 2, f"dual review {scene.scene_id}")
        qwen = parse_vision_score(
            adapters["openrouter"].vision_review(adapters["qwen_model"], img,
                                                 structural_rubric(scene.narration, bible.identity_name)),
            adapters["qwen_model"])
        gemini = parse_vision_score(
            adapters["openrouter"].vision_review(adapters["gemini_model"], img,
                                                 editorial_rubric(scene.narration, bible.identity_name)),
            adapters["gemini_model"])
        verdict = combine(qwen, gemini,
                          studio.cfg.engine.vision.approval_threshold,
                          studio.cfg.engine.vision.disagreement_threshold)
        log(f"   review {scene.scene_id}: {verdict['status']} (consensus {verdict['consensus_score']})")
        if verdict["status"] == "REPAIR" and verdict["hard_fail_reasons"]:
            # ONE bounded repair: regenerate with explicit corrections.
            fix = prompt + "\n[REPAIR] Correct strictly: " + "; ".join(
                verdict["repair_instructions"] or verdict["hard_fail_reasons"])
            studio.budget.charge("image", 1, f"repair {scene.scene_id}")
            img = adapters["bfl"].generate(fix, studio.cfg.engine.visual.production_model,
                                           seed + 1, width, height, out_path)
            verify_image(img)
    return Path(img)


def run_all(
    topic: str,
    workspace: str | Path = "workspace_oneclick",
    live: bool = False,
    language: str = "en",
    voice: str = "cedar",
    max_image_calls: int = 30,
    preview_scale: float = 0.5,
    log=print,
) -> dict[str, Any]:
    # Absolute workspace: ffmpeg's concat demuxer resolves list entries against
    # the list file's directory, so relative workspaces break the render.
    workspace = Path(workspace).resolve()
    cfg = load_studio_config(
        Path(__file__).resolve().parents[2] / "configs" / "default.yaml",
        overrides={"project": {"topic": topic, "language": language},
                   "audio": {"voice": voice},
                   "budgets": {"max_image_calls": max_image_calls}},
        colab={"run_mode": "plan", "arm_paid_calls": live},
    )
    studio = Studio(cfg, workspace)
    episode_dir = workspace / "episodes" / studio.episode_id
    from .providers.live import build_live_adapters

    adapters = build_live_adapters() if live else {}
    live_images = live and "bfl" in adapters
    live_audio = live and "openai_audio" in adapters
    mode = "LIVE" if (live_images and live_audio) else ("PARTIAL-LIVE" if (live_images or live_audio) else "PREVIEW")
    log(f"[1/8] MODE: {mode}  (paid: images={live_images}, audio={live_audio})")

    # 1 — plan (always free)
    plan = studio.plan()
    scenes = [SceneSpec.model_validate(s) for s in plan["engine"]["scenes"]]
    script = SpokenScript.model_validate(plan["engine"]["script"])
    bible = StyleBible.model_validate(plan["agents"]["style_canon_guardian"]["style_bible"])
    log(f"[2/8] PLAN OK — {len(scenes)} scenes, {script.word_count} words, ±{script.est_duration_s}s, "
        f"hook: {plan['agents']['hook_tournament']['selected']['text'][:70]}")

    # 2 — illustrations
    width, height = cfg.engine.project.width, cfg.engine.project.height
    if not live_images:
        width, height = int(width * preview_scale), int(height * preview_scale)
    images: dict[str, str] = {}
    for scene in scenes:
        out_path = episode_dir / "scenes" / scene.scene_id / f"{scene.scene_id}.png"
        if live_images:
            img = _live_scene_image(scene, bible, adapters, studio, out_path, width, height, log)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img = make_placeholder(out_path, f"{scene.scene_id} · {scene.beat_role}", width, height)
        images[scene.scene_id] = str(img)
    log(f"[3/8] IMAGES OK — {len(images)} {'BFL illustrations' if live_images else 'watermarked preview panels'}")

    # 3 — narration (ONE track) + timestamps; audio is the timeline source of truth
    audio_path = episode_dir / "audio" / "narration.wav"
    if live_audio:
        studio.budget.charge("tts_chars", len(script.full_text), "narration")
        adapters["openai_audio"].tts(script.full_text, audio_path,
                                     model=cfg.engine.audio.tts_model, voice=voice)
        stats = assert_not_silent(audio_path, cfg.engine.audio.silent_peak_threshold_db)
        duration = stats["duration_s"]
        raw = adapters["openai_audio"].transcribe(audio_path, cfg.engine.audio.transcription_model)
        segments = parse_transcription(raw)
        similarity = transcript_similarity(script.full_text,
                                           " ".join(s.text for s in segments))
        log(f"[4/8] NARRATION OK — {duration}s, transcript similarity {similarity:.2f}")
    else:
        duration = script.est_duration_s
        _tone_bed(audio_path, duration)
        segments = _synthetic_segments(script, scenes, duration)
        similarity = 1.0
        log(f"[4/8] PREVIEW AUDIO BED — {duration}s (no TTS key / live=False)")

    # 4 — alignment
    timings = align_scenes(scenes, segments, narration_duration_s=duration,
                           min_scene_s=cfg.engine.render.min_scene_duration_s)
    log(f"[5/8] ALIGNMENT OK — {len(timings)} timed scenes, end={timings[-1].end_s}s")

    # 5 — render
    render_scenes = build_render_scenes(scenes, timings, images, cfg.engine.render.allowed_motion)
    video = episode_dir / "render" / "final.mp4"
    render_episode(render_scenes, audio_path, episode_dir / "render" / "work", video,
                   width, height, cfg.engine.render.fps if live_images else 12,
                   cfg.engine.render.max_zoom_pct, cfg.engine.render.max_pan_pct)
    srt = episode_dir / "render" / "final.srt"
    write_srt(_segments_for_srt(scenes, timings), srt,
              next((s.reveal_word for s in scenes if s.reveal_word), ""))
    log(f"[6/8] RENDER OK — {video.name} + {srt.name}")

    # 6 — QC (strict streams/duration always; placeholder rejection in live mode)
    tolerance = cfg.engine.render.duration_tolerance_s if live_audio else 0.35
    checks = check_final_av(str(video), duration, tolerance)
    checks += reject_placeholders_in_production(list(images.values()), production_mode=live_images)
    report = build_report(checks)
    write_report(report, episode_dir / "qc", "final_qc")
    info = validate_final(video, duration, tolerance)
    log(f"[7/8] QC {report.status} — streams a/v: {info['has_audio']}/{info['has_video']}, "
        f"Δ={info['duration_delta_s']}s, {len(report.checks)} checks")

    # 7 — manifest + export
    manifest = EpisodeManifest(
        episode_id=studio.episode_id, topic=topic, language=language, scenes=scenes,
        scene_timings=timings, narration_duration_s=duration, video_path=str(video),
        srt_path=str(srt), qc_status=report.status,
        warnings=([] if mode == "LIVE" else [f"{mode} output — not for publication"]),
        extra={"mode": mode, "transcript_similarity": similarity, "budget": studio.budget.snapshot()},
    )
    atomic_write_json(episode_dir / "manifests" / "episode_manifest.json", manifest.model_dump())
    zip_path = studio.export_package()
    log(f"[8/8] EXPORT OK — {zip_path}")
    return {"mode": mode, "video": str(video), "srt": str(srt),
            "manifest": str(episode_dir / "manifests" / "episode_manifest.json"),
            "qc_status": report.status, "qc_report": str(episode_dir / "qc" / "final_qc.json"),
            "zip": str(zip_path), "budget": studio.budget.snapshot(),
            "duration_s": duration, "scenes": len(timings)}
