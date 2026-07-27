import math
import struct
import wave
from pathlib import Path

import pytest

from sias.audio.alignment import align_scenes
from sias.audio.silence import assert_not_silent, wav_stats
from sias.audio.transcription import parse_transcription, transcript_similarity
from sias.cache import stage_is_reusable
from sias.exceptions import SilentAudioError
from sias.manifests import write_manifest
from sias.render.ffmpeg import clip_cmd, mux_cmd
from sias.render.timeline import build_render_scenes
from sias.render.transitions import transition_for
from sias.schemas import AlignmentSegment, AlignmentWord, RenderScene, SceneSpec, StageManifest


def _write_wav(path: Path, seconds: float = 1.0, freq: float = 440.0, amplitude: float = 0.5, rate: int = 16000):
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(n)
        )
        wf.writeframes(frames)
    return path


def _make_extensible(path: Path, sub_format_tag: int) -> Path:
    """Rewrite a classic PCM WAV's `fmt ` chunk as the 40-byte
    WAVE_FORMAT_EXTENSIBLE form FFmpeg emits for filtered output."""
    from sias.audio.wavio import WAVE_FORMAT_EXTENSIBLE

    raw = bytearray(path.read_bytes())
    classic = bytes(raw[20:36])  # 16-byte fmt body
    guid = struct.pack("<I", sub_format_tag) + bytes.fromhex("00001000800000aa00389b71")
    ext = (struct.pack("<H", WAVE_FORMAT_EXTENSIBLE) + classic[2:]
           + struct.pack("<HHI", 22, 16, 0x4) + guid)
    rebuilt = bytearray(raw[:16]) + struct.pack("<I", len(ext)) + ext + raw[36:]
    struct.pack_into("<I", rebuilt, 4, len(rebuilt) - 8)
    path.write_bytes(bytes(rebuilt))
    return path


def test_silence_detection(tmp_path):
    loud = _write_wav(tmp_path / "loud.wav", amplitude=0.5)
    silent = _write_wav(tmp_path / "silent.wav", amplitude=0.00001)
    stats = assert_not_silent(loud)
    assert stats["duration_s"] == pytest.approx(1.0, abs=0.01)
    with pytest.raises(SilentAudioError):
        assert_not_silent(silent)


def test_streamed_wav_placeholder_header_duration(tmp_path):
    """Regression for a real live failure: OpenAI TTS streams a WAV whose data
    chunk size is a placeholder (0xFFFFFFFF). `wave` then reports 2**31-1 frames
    -> a phantom 89478s (~24.8h) narration that made ffmpeg render for hours.
    Duration must come from the PCM actually present."""
    path = _write_wav(tmp_path / "streamed.wav", seconds=2.0, rate=24000)
    raw = bytearray(path.read_bytes())
    raw[40:44] = (0xFFFFFFFF).to_bytes(4, "little")  # data chunk size placeholder
    raw[4:8] = (0xFFFFFFFF).to_bytes(4, "little")    # RIFF size placeholder
    path.write_bytes(bytes(raw))

    stats = wav_stats(path)
    assert stats["header_frames"] == 2_147_483_647       # the phantom
    assert stats["header_mismatch"] is True
    assert stats["duration_s"] == pytest.approx(2.0, abs=0.05)  # the truth
    assert stats["duration_s"] < 10  # never the 89478s that broke the live run


def test_duration_plausibility_gate():
    from sias.audio.silence import assert_plausible_duration
    from sias.exceptions import AssetIntegrityError

    assert_plausible_duration(31.0, 31.35)  # fine
    with pytest.raises(AssetIntegrityError, match="hard cap"):
        assert_plausible_duration(89478.485, 31.35)
    with pytest.raises(AssetIntegrityError, match="implausible"):
        assert_plausible_duration(300.0, 31.35)
    with pytest.raises(AssetIntegrityError, match="zero"):
        assert_plausible_duration(0.0, 31.35)


def test_render_refuses_absurd_clip_duration():
    from sias.exceptions import RenderError

    scene = RenderScene(scene_id="S08", image_path="i.png", start_s=0, end_s=89450.7, motion="hold")
    with pytest.raises(RenderError, match="sanity cap"):
        clip_cmd(scene, "out.mp4", 1080, 1920, 30)


def test_wav_stats_fields(tmp_path):
    stats = wav_stats(_write_wav(tmp_path / "a.wav"))
    assert set(stats) >= {"duration_s", "peak_dbfs", "rms_dbfs", "clipping"}


def _segments(words_text: str, total_s: float) -> list[AlignmentSegment]:
    tokens = words_text.split()
    step = total_s / len(tokens)
    words = [AlignmentWord(word=t, start_s=round(i * step, 3), end_s=round((i + 1) * step, 3)) for i, t in enumerate(tokens)]
    return [AlignmentSegment(text=words_text, start_s=0.0, end_s=total_s, words=words)]


def test_alignment_final_end_equals_narration_duration():
    scenes = [
        SceneSpec(scene_id="S01", narration="the rain begins and never stops falling today"),
        SceneSpec(scene_id="S02", narration="the ground gives up soaking anything more water"),
        SceneSpec(scene_id="S03", narration="then the real surprise arrives from below quietly"),
    ]
    text = " ".join(s.narration for s in scenes)
    timings = align_scenes(scenes, _segments(text, 24.0), narration_duration_s=24.0)
    assert timings[0].start_s == 0.0
    assert timings[-1].end_s == pytest.approx(24.0, abs=0.01)
    for a, b in zip(timings, timings[1:]):
        assert b.start_s == pytest.approx(a.end_s, abs=1e-6)
        assert (a.end_s - a.start_s) >= 2.4 - 1e-6


def test_alignment_merges_too_short_scene():
    scenes = [
        SceneSpec(scene_id="S01", narration="one two three four five six seven eight nine ten"),
        SceneSpec(scene_id="S02", narration="tiny"),
        SceneSpec(scene_id="S03", narration="eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"),
    ]
    text = " ".join(s.narration for s in scenes)
    timings = align_scenes(scenes, _segments(text, 21.0), narration_duration_s=21.0)
    ids = [t.scene_id for t in timings]
    assert "S02" not in ids  # merged into previous
    assert timings[-1].end_s == pytest.approx(21.0, abs=0.01)


def test_transcript_similarity():
    assert transcript_similarity("The rain begins.", "the rain begins") > 0.9
    assert transcript_similarity("The rain begins.", "cats are nice") < 0.5


def test_parse_transcription_words_grouped():
    raw = {"segments": [{"text": "hello world", "start": 0.0, "end": 1.0}],
           "words": [{"word": "hello", "start": 0.0, "end": 0.4}, {"word": "world", "start": 0.5, "end": 1.0}]}
    segs = parse_transcription(raw)
    assert len(segs) == 1 and len(segs[0].words) == 2


def test_cache_rejects_corrupt_artifact(tmp_path):
    artifact = tmp_path / "a.json"
    artifact.write_text("{}")
    manifest = StageManifest(stage_id="s", status="PASS", input_hash="h", artifact_path=str(artifact))
    write_manifest(tmp_path, manifest)
    ok, _ = stage_is_reusable(manifest, "h")
    assert ok
    artifact.write_text("{tampered}")
    ok, reason = stage_is_reusable(manifest, "h")
    assert not ok and "sha256 mismatch" in reason
    ok, reason = stage_is_reusable(manifest, "other")
    assert not ok and "input hash" in reason


def test_clip_cmd_motion_policy():
    scene = RenderScene(scene_id="S01", image_path="img.png", start_s=0, end_s=3, motion="slow_push_in")
    cmd = clip_cmd(scene, "out.mp4", 1080, 1920, 30, max_zoom_pct=4.0)
    joined = " ".join(cmd)
    assert "zoompan" in joined and "1.0400" in joined  # zoom capped at 4%
    hold = clip_cmd(RenderScene(scene_id="S02", image_path="i.png", start_s=0, end_s=2, motion="hold"), "o.mp4", 1080, 1920, 30)
    assert "zoompan" not in " ".join(hold)


def test_mux_maps_audio_and_video():
    cmd = mux_cmd("v.mp4", "a.wav", "out.mp4")
    assert "-map" in cmd and "1:a:0" in cmd and "aac" in cmd


def test_transition_policy():
    assert transition_for(None, "cold_open") == "cut"
    assert transition_for("fact_3", "explanation") == "page_turn"
    assert transition_for("explanation", "scale_example") == "dissolve"
    assert transition_for("scale_example", "gasp_reveal") == "page_turn"


def test_timeline_respects_allowed_motion(tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(b"png")
    scenes = [SceneSpec(scene_id="S01", beat_role="cold_open", narration="x")]
    from sias.schemas import SceneTiming

    out = build_render_scenes(scenes, [SceneTiming(scene_id="S01", start_s=0, end_s=3)], {"S01": str(img)}, ["hold"])
    assert out[0].motion == "hold"  # push_in not in allowed list -> hold


def test_extensible_wav_from_ffmpeg_is_readable(tmp_path):
    """FFmpeg writes WAVE_FORMAT_EXTENSIBLE (0xFFFE) for filtered output — e.g.
    the loudnorm pre-master. `wave` refuses it outright ("unknown format:
    65534"), which crashed the live audio gates. The payload is plain PCM, so we
    normalise the fmt chunk instead of failing."""
    from sias.audio.wavio import open_wav

    path = _write_wav(tmp_path / "ext.wav", seconds=1.0, rate=24000)
    _make_extensible(path, sub_format_tag=1)  # KSDATAFORMAT_SUBTYPE_PCM

    with pytest.raises(wave.Error, match="65534"):
        wave.open(str(path), "rb")
    with open_wav(path) as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == 24000
    assert wav_stats(path)["duration_s"] == pytest.approx(1.0, abs=0.02)


def test_non_pcm_extensible_is_refused(tmp_path):
    from sias.audio.wavio import open_wav
    from sias.exceptions import AssetIntegrityError

    path = _write_wav(tmp_path / "float.wav")
    _make_extensible(path, sub_format_tag=3)  # KSDATAFORMAT_SUBTYPE_IEEE_FLOAT

    with pytest.raises(AssetIntegrityError, match="not PCM"):
        open_wav(path)


def test_clip_frames_do_not_drift_across_an_episode():
    """Rounding each clip on its own loses up to half a frame per scene; over an
    episode that made the video shorter than the narration and tripped the ±0.08s
    final duration gate."""
    from sias.audio.silence import wav_stats  # noqa: F401  (kept close to the audio contract)
    from sias.render.ffmpeg import quantized_frames

    fps = 30
    boundaries = [0.0, 4.317, 8.902, 13.44, 18.113, 22.7, 26.05, 30.19]
    total = sum(quantized_frames(a, b, fps) for a, b in zip(boundaries, boundaries[1:]))
    assert total == round(boundaries[-1] * fps)
    assert abs(total / fps - boundaries[-1]) <= 0.02


def test_clip_cmd_pins_an_exact_frame_count():
    scene = RenderScene(scene_id="S01", image_path="i.png", start_s=4.317, end_s=8.902, motion="hold")
    cmd = clip_cmd(scene, "o.mp4", 1080, 1920, 30)
    assert "-frames:v" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == str(round(8.902 * 30) - round(4.317 * 30))
