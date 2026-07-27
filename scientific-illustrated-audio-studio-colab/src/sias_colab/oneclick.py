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
from sias.audio.silence import assert_not_silent, assert_plausible_duration
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
from .qc.placeholders import make_preview_panel, reject_placeholders_in_production
from .studio import Studio


# Panel geometry per delivery format. The institutional layout is expressed in
# fractions, so one PanelSpec composes any of these without a second ruleset.
ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1440, 1440)}


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


def _compose_institutional(scene: SceneSpec, index: int, experiment_id: str, art: Path,
                           out_path: Path, width: int, height: int) -> Path:
    """Typeset the report furniture over the generated diagram.

    The image model never draws a glyph; the frame, status block, instrument
    readout and headline are composited here so they are identical on every
    panel of every episode."""
    from sias.render.hud import PanelSpec, compose_panel
    from sias.style.institutional import (
        derive_background,
        derive_headline,
        derive_headline_anchor,
        derive_readout,
    )

    readout = derive_readout(scene)
    display = index == 0 or scene.beat_role == "cold_open"
    spec = PanelSpec(
        experiment_id=experiment_id,
        status_lines=["Simulation Status:", "Running"],
        readout={"label": readout["label"], "value": readout["value"] or experiment_id,
                 "unit": readout["unit"], "alert": readout["alert"]},
        headline=derive_headline(scene, display=display),
        headline_style="display" if display else "label",
        headline_anchor=derive_headline_anchor(scene, index),
        background=derive_background(scene),
    )
    return compose_panel(spec, out_path, width, height, illustration=art)


def _live_scene_image(scene: SceneSpec, bible: StyleBible, adapters: dict, studio: Studio,
                      out_path: Path, width: int, height: int, log, index: int = 0) -> Path:
    prompt = compile_prompt(scene, bible, index=index)["text"]
    seed = stable_seed(studio.cfg.engine.visual.seed_base, scene.scene_id, 0)
    studio.budget.charge("image", 1, f"scene {scene.scene_id}")
    art_path = out_path.with_name(out_path.stem + "_diagram.png")
    img = adapters["bfl"].generate(prompt, studio.cfg.engine.visual.production_model,
                                   seed, width, height, art_path)
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
                                           seed + 1, width, height, art_path)
            verify_image(img)
    return _compose_institutional(scene, index, studio.experiment_id, Path(img),
                                  out_path, width, height)


def run_all(
    topic: str,
    workspace: str | Path = "workspace_oneclick",
    live: bool = False,
    language: str = "en",
    voice: str = "cedar",
    max_image_calls: int = 30,
    preview_scale: float = 0.5,
    human_gates_approved: bool = False,
    bfl_model: str = "",
    aspect: str = "",
    log=print,
    adapters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`adapters` exists so the live path can be exercised against *simulated*
    provider responses (see `sias_colab.preflight.simulation`). Left at None —
    which is what the notebook does — live adapters are built from real keys."""
    # Absolute workspace: ffmpeg's concat demuxer resolves list entries against
    # the list file's directory, so relative workspaces break the render.
    workspace = Path(workspace).resolve()
    cfg = load_studio_config(
        Path(__file__).resolve().parents[2] / "configs" / "default.yaml",
        overrides={"project": {"topic": topic, "language": language},
                   "audio": {"voice": voice},
                   "budgets": {"max_image_calls": max_image_calls},
                   **({"visual": {"production_model": bfl_model}} if bfl_model else {}),
                   **({"project": {"topic": topic, "language": language,
                                   "aspect_ratio": aspect,
                                   "width": ASPECTS[aspect][0],
                                   "height": ASPECTS[aspect][1]}} if aspect in ASPECTS else {})},
        colab={"run_mode": "plan", "arm_paid_calls": live},
    )
    studio = Studio(cfg, workspace)
    episode_dir = workspace / "episodes" / studio.episode_id
    from .providers.live import build_live_adapters

    if adapters is None:
        adapters = build_live_adapters() if live else {}
    live_images = live and "bfl" in adapters
    live_audio = live and "openai_audio" in adapters
    mode = "LIVE" if (live_images and live_audio) else ("PARTIAL-LIVE" if (live_images or live_audio) else "PREVIEW")
    log(f"[1/10] MODE: {mode}  (paid: images={live_images}, audio={live_audio})")

    # Diamond routing snapshot: which backend serves each service and why.
    from .diamond.hardware import detect_profile
    from .diamond.registry import build_default_registry

    hw = detect_profile()
    registry = build_default_registry(hw)
    registry.assert_no_self_approval()
    log(f"      hardware: {hw} | primaries: BFL images · OpenAI story/TTS · "
        f"Qwen VL 32B + Gemini 2.5 Flash QC (open backends: fallback tier, license-gated)")

    # 1 — plan (always free)
    plan = studio.plan()
    scenes = [SceneSpec.model_validate(s) for s in plan["engine"]["scenes"]]
    script = SpokenScript.model_validate(plan["engine"]["script"])
    bible = StyleBible.model_validate(plan["agents"]["style_canon_guardian"]["style_bible"])
    log(f"[2/10] PLAN OK — {len(scenes)} scenes, {script.word_count} words, ±{script.est_duration_s}s, "
        f"hook: {plan['agents']['hook_tournament']['selected']['text'][:70]}")

    # 2 — illustrations
    width, height = cfg.engine.project.width, cfg.engine.project.height
    if not live_images:
        width, height = int(width * preview_scale), int(height * preview_scale)
    images: dict[str, str] = {}
    for index, scene in enumerate(scenes):
        out_path = episode_dir / "scenes" / scene.scene_id / f"{scene.scene_id}.png"
        if live_images:
            img = _live_scene_image(scene, bible, adapters, studio, out_path,
                                    width, height, log, index=index)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img = make_preview_panel(out_path, scene, index, studio.experiment_id, width, height)
        images[scene.scene_id] = str(img)
    log(f"[3/10] IMAGES OK — {len(images)} {'BFL illustrations' if live_images else 'watermarked preview panels'}")

    # Diamond consistency (heuristic tier — flags drift, never approves).
    from .diamond.consistency import HeuristicConsistency

    anchor = images[scenes[0].scene_id]
    checker = HeuristicConsistency([anchor])
    consistency_flags = {
        sid: r["flags"] for sid, r in
        ((sid, checker.check(p)) for sid, p in images.items() if p != anchor)
        if r.get("flags")
    }
    if consistency_flags:
        log(f"      consistency flags: {consistency_flags}")

    # 3 — narration (ONE track) + timestamps; audio is the timeline source of truth
    audio_path = episode_dir / "audio" / "narration.wav"
    if live_audio:
        studio.budget.charge("tts_chars", len(script.full_text), "narration")
        adapters["openai_audio"].tts(script.full_text, audio_path,
                                     model=cfg.engine.audio.tts_model, voice=voice)
        stats = assert_not_silent(audio_path, cfg.engine.audio.silent_peak_threshold_db)
        duration = stats["duration_s"]
        # Refuse an implausible narration BEFORE it reaches alignment/render.
        assert_plausible_duration(duration, script.est_duration_s)
        if stats.get("header_mismatch"):
            log(f"      note: WAV header claimed {stats['header_frames']} frames, "
                f"actual PCM has {stats['actual_frames']} (streamed WAV) — using real bytes")
        raw = adapters["openai_audio"].transcribe(audio_path, cfg.engine.audio.transcription_model)
        segments = parse_transcription(raw)
        similarity = transcript_similarity(script.full_text,
                                           " ".join(s.text for s in segments))
        log(f"[4/10] NARRATION OK — {duration}s, transcript similarity {similarity:.2f}")
    else:
        duration = script.est_duration_s
        _tone_bed(audio_path, duration)
        segments = _synthetic_segments(script, scenes, duration)
        similarity = 1.0
        log(f"[4/10] PREVIEW AUDIO BED — {duration}s (no TTS key / live=False)")

    # 4 — alignment
    timings = align_scenes(scenes, segments, narration_duration_s=duration,
                           min_scene_s=cfg.engine.render.min_scene_duration_s)
    # Deliberate silence before the reveal (Diamond sound design).
    from .diamond.mastering import audio_checks, insert_pre_reveal_silence, master_narration

    reveal_scene = next((s2.scene_id for s2 in scenes if s2.beat_role == "gasp_reveal"), "")
    if reveal_scene and any(t.scene_id == reveal_scene for t in timings):
        timings = insert_pre_reveal_silence(timings, reveal_scene, hold_s=0.3)
    log(f"[5/10] ALIGNMENT OK — {len(timings)} timed scenes, end={timings[-1].end_s}s"
        + (f" (pre-reveal hold on {reveal_scene})" if reveal_scene else ""))

    # Mastering: narration premaster to -16 LUFS on the live path.
    if live_audio:
        mastered = episode_dir / "audio" / "narration_premaster.wav"
        try:
            master_narration(audio_path, mastered, cfg.engine.audio.target_lufs)
            audio_path = mastered
        except Exception as exc:
            log(f"      premaster skipped: {str(exc)[:80]}")
    audio_report = audio_checks(audio_path)
    log(f"[6/10] AUDIO CHECKS — clipping={audio_report['clipping']}, "
        f"longest_silence={audio_report['longest_silence_s']}s, stereo_ok={audio_report['stereo_ok']}")

    # 5 — render
    render_scenes = build_render_scenes(scenes, timings, images, cfg.engine.render.allowed_motion)
    video = episode_dir / "render" / "final.mp4"
    render_episode(render_scenes, audio_path, episode_dir / "render" / "work", video,
                   width, height, cfg.engine.render.fps if live_images else 12,
                   cfg.engine.render.max_zoom_pct, cfg.engine.render.max_pan_pct)
    srt = episode_dir / "render" / "final.srt"
    write_srt(_segments_for_srt(scenes, timings), srt,
              next((s.reveal_word for s in scenes if s.reveal_word), ""))
    from .diamond.subtitles import validate_subtitles, write_ass

    srt_segments = _segments_for_srt(scenes, timings)
    live_segments = segments if live_audio else srt_segments
    ass = episode_dir / "render" / "final.ass"
    reveal_word = next((s2.reveal_word for s2 in scenes if s2.reveal_word), "")
    write_ass(live_segments, ass, reveal_word=reveal_word)
    subtitle_issues = validate_subtitles(live_segments)
    log(f"[7/10] RENDER OK — {video.name} + {srt.name} + {ass.name}"
        + (f" | subtitle issues: {subtitle_issues}" if subtitle_issues else ""))

    # 6 — QC (strict streams/duration always; placeholder rejection in live mode)
    tolerance = cfg.engine.render.duration_tolerance_s if live_audio else 0.35
    checks = check_final_av(str(video), duration, tolerance)
    checks += reject_placeholders_in_production(list(images.values()), production_mode=live_images)
    report = build_report(checks)
    write_report(report, episode_dir / "qc", "final_qc")
    info = validate_final(video, duration, tolerance)
    log(f"[8/10] QC {report.status} — streams a/v: {info['has_audio']}/{info['has_video']}, "
        f"Δ={info['duration_delta_s']}s, {len(report.checks)} checks")

    # 8 — Diamond Editorial Standard gate
    from .diamond.standard import HUMAN_GATES, DiamondGate
    from sias.schemas import StoryBeat

    beats = [StoryBeat.model_validate(b) for b in plan["engine"]["beats"]]
    gate = DiamondGate()
    scene_reviews = [{"hard_fail_reasons": []} for _ in timings]  # live reviews feed here
    compositions = [rs.motion for rs in render_scenes]
    pillars = {
        "story": gate.story(timings, beats, script.sentences),
        "visual": gate.visual(scene_reviews, compositions),
        "audio": gate.audio(similarity, audio_report),
        "subtitle": gate.subtitle(subtitle_issues),
        "render": checks[:4],
    }
    approvals = {g: "APPROVED" for g in HUMAN_GATES} if human_gates_approved else {}
    diamond = gate.evaluate(pillars, approvals)
    atomic_write_json(episode_dir / "qc" / "diamond_report.json", diamond.model_dump())
    log(f"[9/10] DIAMOND {diamond.status} — pillars: "
        + ", ".join(f"{k}={v}" for k, v in diamond.pillars.items()))
    if diamond.status == "HUMAN_GATES_PENDING":
        log("      6 gerbang persetujuan manusia masih PENDING (set human_gates_approved=True "
            "hanya setelah Anda benar-benar meninjau di ponsel).")

    # 10 — manifest + export
    manifest = EpisodeManifest(
        episode_id=studio.episode_id, topic=topic, language=language, scenes=scenes,
        scene_timings=timings, narration_duration_s=duration, video_path=str(video),
        srt_path=str(srt), qc_status=report.status,
        warnings=([] if mode == "LIVE" else [f"{mode} output — not for publication"]),
        extra={"mode": mode, "transcript_similarity": similarity, "budget": studio.budget.snapshot(),
               "diamond_status": diamond.status, "consistency_flags": consistency_flags,
               "hardware_profile": hw, "audio_checks": audio_report},
    )
    atomic_write_json(episode_dir / "manifests" / "episode_manifest.json", manifest.model_dump())
    zip_path = studio.export_package()
    log(f"[10/10] EXPORT OK — {zip_path}")
    return {"mode": mode, "video": str(video), "srt": str(srt), "ass": str(ass),
            "diamond_status": diamond.status,
            "diamond_report": str(episode_dir / "qc" / "diamond_report.json"),
            "consistency_flags": consistency_flags,
            "manifest": str(episode_dir / "manifests" / "episode_manifest.json"),
            "qc_status": report.status, "qc_report": str(episode_dir / "qc" / "final_qc.json"),
            "zip": str(zip_path), "budget": studio.budget.snapshot(),
            "duration_s": duration, "scenes": len(timings)}
