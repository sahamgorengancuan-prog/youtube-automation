from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .asset_registry import AssetRegistry
from .cache_store import ArtifactCache
from .candidate_tournament import CandidateTournament
from .character_registry import CharacterRegistry
from .hybrid_package import HybridPackageBuilder
from .hybrid_render import RemotionHybridExporter
from .job_runtime import ResumableJobRuntime
from .pipeline import ScientificMotionStudioV10
from .provider_registry import ProviderRegistry
from .reference_selector import DynamicReferenceSelector
from .schemas import (
    AnimationPlan,
    AssetQuery,
    AssetRecord,
    CharacterProfile,
    CharacterView,
    DepthPlane,
    DrawingBrief,
    ProviderCapability,
    ProviderSpec,
    SceneIllustrationArchitecture,
    SceneRequest,
    SemanticLayer,
    SemanticLayerContract,
    ShotState,
    TemporalRequest,
)
from .shot_state import ShotStatePlanner
from .sketch_control import ControlSketchBuilder
from .temporal_backends import (
    DeterministicCompositorBackend,
    SketchControlledVideoBackend,
    TemporalBackendRouter,
)
from .tests import FakeLLM, run_regression_tests as run_v9_regression_tests
from .utils import load_json


class Collector:
    def __init__(self):
        self.items = []

    def check(self, name, condition, detail=""):
        self.items.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(f"{name}: {detail}")


class TournamentLLM(FakeLLM):
    def critique_image(self, **kwargs):
        fallback = kwargs.get("fallback", {})
        # Preserve deterministic fallback ranking for candidate boards, but
        # approve the existing V9 art-director tests.
        if "candidate_rank" in str(kwargs.get("namespace", "")):
            return fallback
        return super().critique_image(**kwargs)


def _image(path: Path, color: str, detail: int = 1) -> str:
    image = Image.new("RGB", (180, 320), "#FAFAF7")
    d = ImageDraw.Draw(image)
    d.rectangle((25, 40, 155, 280), fill=color)
    for i in range(detail):
        d.line((30, 70 + i * 12, 150, 50 + i * 17), fill="#20282D", width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return str(path)


def _ffprobe(path: str | Path) -> dict[str, Any]:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(raw)


def run_v10_regression_tests(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="scistudio_v10_tests_"))
    base.mkdir(parents=True, exist_ok=True)
    c = Collector()

    prior = run_v9_regression_tests(base / "v9")
    c.check("V9.1 regression retained", prior["passed"] and prior["count"] >= 53)

    providers = ProviderRegistry(base / "providers")
    providers.register(
        ProviderSpec(
            provider_id="slow",
            provider_type="image",
            implementation="x",
            priority=50,
            capabilities=[ProviderCapability(name="reference_edit", supports_images=True)],
        ),
        object(),
    )
    fast_impl = object()
    providers.register(
        ProviderSpec(
            provider_id="fast",
            provider_type="image",
            implementation="y",
            priority=10,
            capabilities=[ProviderCapability(name="reference_edit", supports_images=True)],
        ),
        fast_impl,
    )
    spec, impl = providers.resolve("image", require="reference_edit")
    c.check("provider registry resolves priority", spec.provider_id == "fast" and impl is fast_impl)
    c.check("provider snapshot exists", Path(providers.snapshot()).exists())
    try:
        providers.resolve("publisher")
        missing = False
    except RuntimeError:
        missing = True
    c.check("missing provider fails explicitly", missing)

    cache = ArtifactCache(base / "cache")
    key = cache.key("brief", {"a": 1})
    cache.put_json(key, {"ok": True})
    c.check("content cache JSON roundtrip", cache.get_json(key) == {"ok": True})
    src = base / "source.bin"
    src.write_bytes(b"abc")
    cached = cache.put_file(key, src)
    c.check("content cache file roundtrip", Path(cached).read_bytes() == b"abc")

    calls = {"n": 0}
    runtime = ResumableJobRuntime(base / "job", job_id="J1", topic="topic", config={"x": 1})

    def stage():
        calls["n"] += 1
        return {"value": 7}

    c.check("job stage first execution", runtime.execute("stage", {"x": 1}, stage) == {"value": 7})
    c.check(
        "job stage resume reuses output", runtime.execute("stage", {"x": 1}, stage) == {"value": 7} and calls["n"] == 1
    )
    c.check(
        "job input change reruns stage", runtime.execute("stage", {"x": 2}, stage) == {"value": 7} and calls["n"] == 2
    )
    runtime.mark_completed()
    c.check("job manifest completed", load_json(runtime.manifest_path)["status"] == "completed")

    registry = AssetRegistry(base / "assets.sqlite")
    style_path = _image(base / "style.png", "#475157", 2)
    subject_path = _image(base / "subject.png", "#2E77A6", 5)
    future_path = _image(base / "future.png", "#D8483E", 4)
    registry.upsert(
        AssetRecord(
            asset_id="style",
            path=style_path,
            asset_type="style_anchor",
            role_tags=["style"],
            style_hash="S",
            quality_score=1,
        )
    )
    registry.upsert(
        AssetRecord(
            asset_id="subject",
            path=subject_path,
            asset_type="subject_view",
            subject_ids=["human"],
            camera_view="side",
            perspective="moderate",
            chronology_index=1,
            role_tags=["approved"],
            style_hash="S",
            quality_score=0.9,
        )
    )
    registry.upsert(
        AssetRecord(
            asset_id="future",
            path=future_path,
            asset_type="approved_scene",
            subject_ids=["human"],
            camera_view="side",
            perspective="moderate",
            chronology_index=9,
            role_tags=["approved"],
            style_hash="S",
            quality_score=1,
        )
    )
    c.check("asset registry get", registry.get("subject").subject_ids == ["human"])
    selector = DynamicReferenceSelector(registry, base / "selector")
    selection = selector.select(
        AssetQuery(
            scene_id="SC02",
            subject_ids=["human"],
            camera_view="side",
            perspective="moderate",
            style_hash="S",
            chronology_index=2,
            desired_types=["style_anchor", "subject_view", "approved_scene"],
            maximum_results=8,
        )
    )
    selected_ids = [x.asset.asset_id for x in selection.selected]
    c.check("dynamic selector retrieves identity reference", "subject" in selected_ids)
    c.check("dynamic selector excludes future continuity", "future" not in selected_ids)
    c.check("reference board built", Path(selection.board_path).exists())

    characters = CharacterRegistry(base / "characters")
    characters.upsert(
        CharacterProfile(subject_id="human", display_name="Adult subject", immutable_traits=["adult proportions"])
    )
    characters.add_view("human", CharacterView(view_id="side-1", angle="side", path=subject_path))
    c.check(
        "character registry persists views",
        CharacterRegistry(base / "characters").get("human").views[0].angle == "side",
    )

    architecture = SceneIllustrationArchitecture(
        scene_id="SC01",
        beat_id="B01",
        visual_thesis="integrated rain city",
        depth_planes=[DepthPlane(plane_id="city", depth="midground", contents="city", movement_role="hold")],
        motion_seams=[],
    )
    scene = SceneRequest(scene_id="SC01", beat_id="B01", duration_s=1, narration="Hold.", visual_event="city")
    state = ShotStatePlanner(FakeLLM(), {}, base / "shot").plan(scene, architecture, force=True)
    c.check("shot state defines first and last", bool(state.first_frame_description and state.last_frame_description))
    c.check(
        "hold shot does not request control sketch",
        state.temporal_complexity == "hold" and not state.control_sketch_required,
    )

    brief = DrawingBrief(
        brief_id="SC01-beauty",
        scene_id="SC01",
        positive_prompt="One integrated mature scientific editorial city scene with fluid authored linework and coherent perspective.",
        negative_prompt="isolated icons",
        kontext_instruction="Preserve the studio identity.",
        output_path=str(base / "candidate.png"),
        seed=100,
    )
    tournament = CandidateTournament(TournamentLLM(), {"candidate_count": 4}, base / "tournament")

    def generate(candidate_brief):
        color = ["#475157", "#2E77A6", "#4190C3", "#D8483E"][candidate_brief.request_metadata["candidate_index"]]
        return _image(Path(candidate_brief.output_path), color, candidate_brief.request_metadata["candidate_index"] + 1)

    result = tournament.run(brief, architecture, state, generate, force=True)
    c.check("candidate tournament generates four", len(result.candidates) == 4)
    c.check("candidate tournament unique seeds", len({x.seed for x in result.candidates}) == 4)
    c.check(
        "candidate tournament selects valid winner", result.winner_id in {x.candidate_id for x in result.candidates}
    )
    c.check("candidate comparison board exists", Path(result.comparison_board).exists())

    sketch = ControlSketchBuilder(base / "sketch")
    first, last = sketch.build_pair(
        "SC01",
        subject_path,
        future_path,
        ShotState(
            scene_id="SC01",
            first_frame_description="a",
            last_frame_description="b",
            control_sketch_regions=["arm"],
            motion_bridge=["arm extends"],
            control_sketch_required=True,
            temporal_complexity="articulated",
        ),
    )
    c.check("control sketch pair generated", Path(first.output_path).exists() and Path(last.output_path).exists())

    det = DeterministicCompositorBackend(base / "temporal_det")
    request = TemporalRequest(
        scene_id="SC01",
        beauty_start=subject_path,
        duration_frames=15,
        fps=15,
        complexity="simple",
        output_path=str(base / "simple.mp4"),
    )
    temporal = det.generate(request)
    info = _ffprobe(temporal.output_path)
    c.check("deterministic temporal clip encoded", info["streams"][0]["codec_name"] == "h264")
    sketch_backend = SketchControlledVideoBackend({"command": ""}, base / "sketch_backend")
    c.check("unconfigured sketch backend unavailable", not sketch_backend.available())
    router = TemporalBackendRouter([sketch_backend, det], base / "router")
    c.check("router chooses deterministic for simple", router.select(request).backend_id == "deterministic-compositor")
    organic = request.model_copy(
        update={"scene_id": "SC02", "complexity": "organic", "control_sketch_start": first.output_path}
    )
    try:
        router.select(organic)
        wrongly_fell_back = True
    except RuntimeError:
        wrongly_fell_back = False
    c.check("organic motion never degrades to still compositor", not wrongly_fell_back)

    contract = SemanticLayerContract(
        scene_id="SC01",
        beauty_frame_path=subject_path,
        layers=[
            SemanticLayer(
                layer_id="beauty-base",
                description="base",
                source_region="full",
                extraction_method="full_frame",
                locked=True,
            )
        ],
    )
    animation = AnimationPlan(scene_id="SC01", duration_frames=15, fps=15, camera_locked=True, events=[])
    overlay = base / "overlay.svg"
    overlay.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320"></svg>')
    package = HybridPackageBuilder({"width": 180, "height": 320}, base / "package").build(
        scene,
        contract,
        animation,
        str(overlay),
        temporal_clip_path=temporal.output_path,
        temporal_backend=temporal.backend_id,
    )
    c.check(
        "temporal clip replaces beauty layer",
        package.layers[0].kind == "video_clip" and package.temporal_backend == "deterministic-compositor",
    )
    project = RemotionHybridExporter({}, base / "remotion").create_project([package])
    ts = (project / "src" / "index.tsx").read_text()
    c.check("Remotion supports temporal video layer", "OffthreadVideo" in ts and "ScientificMotionV10" in ts)

    # Full plan-only execution from the integrated V10 pipeline.
    ref_frame = _image(base / "ref_frame.png", "#20282D", 8)
    ref_video = base / "reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            ref_frame,
            "-t",
            "1",
            "-r",
            "12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(ref_video),
        ],
        check=True,
    )
    config = {
        "workspace": str(base / "studio"),
        "research_search": {"enabled": False},
        "llm": {"provider_order": [], "vision_provider_order": []},
        "drawing": {
            "seed_base": 123,
            "aspect_ratio": "9:16",
            "min_initial_prompt_words": 40,
            "max_initial_prompt_words": 500,
        },
        "candidate_tournament": {"candidate_count": 3},
        "references": {"maximum_retrieved_assets": 8},
        "audio": {"build_in_plan_mode": False},
        "temporal": {"enabled": True},
        "providers": [],
    }
    pipeline_result = ScientificMotionStudioV10(config).run(
        "What happens under nonstop rain?", ref_video, plan_only=True, job_id="plan-test", force=True
    )
    c.check("full V10 plan-only pipeline completed", pipeline_result["mode"] == "plan_only")
    c.check("full pipeline emits shot states", Path(pipeline_result["shot_states"]).exists())
    c.check("full pipeline emits reference selections", Path(pipeline_result["reference_selections"]).exists())
    c.check("full pipeline emits candidate plans", Path(pipeline_result["candidate_plans"]).exists())
    c.check(
        "full pipeline emits persistent job manifest",
        load_json(pipeline_result["job_manifest"])["status"] == "completed",
    )

    # Full live-generation integration with injected deterministic providers.
    live_root = base / "live_studio"

    def fake_flux(brief):
        out = Path(brief.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (180, 320), "#FAFAF7")
        draw = ImageDraw.Draw(image)
        seed = brief.seed or 0
        colors = ["#475157", "#2E77A6", "#4190C3", "#D8483E", "#F3BD38"]
        draw.polygon([(15, 280), (45, 80), (90, 35), (160, 100), (170, 280)], fill=colors[seed % len(colors)])
        for i in range(18):
            draw.line((20, 50 + i * 11, 160, 40 + i * 13), fill="#20282D", width=1 + (i % 3))
        draw.text((10, 10), brief.scene_id, fill="#20282D")
        image.save(out)
        return out

    def fake_mask(beauty_path, region, output):
        beauty = Image.open(beauty_path)
        mask = Image.new("L", beauty.size, 0)
        ImageDraw.Draw(mask).rectangle((35, 55, 155, 285), fill=255)
        output.parent.mkdir(parents=True, exist_ok=True)
        mask.save(output)
        return output

    live_config = {
        **config,
        "workspace": str(live_root),
        "audio": {"enabled": False},
        "candidate_tournament": {"candidate_count": 2},
        "temporal": {"enabled": False},
        "flux_studio": {"maximum_director_revisions": 1, "require_vision_director": True},
        "semantic": {"require_approved_beauty": True},
    }
    live_result = ScientificMotionStudioV10(
        live_config, llm_router=FakeLLM(), image_generator=fake_flux, mask_generator=fake_mask
    ).run(
        "What happens under nonstop rain?",
        ref_video,
        plan_only=False,
        render_video=False,
        job_id="live-test",
        force=True,
    )
    c.check("full live fixture reaches live_generation", live_result["mode"] == "live_generation")
    c.check("full live fixture creates beauty frames", Path(live_result["beauty_frames"]).exists())
    c.check("full live fixture creates semantic contracts", Path(live_result["semantic_contracts"]).exists())
    c.check(
        "full live fixture creates candidate tournaments",
        any(Path(live_result["candidate_tournaments"]).glob("SC*.json")),
    )
    c.check("full live fixture persists approved assets", len(AssetRegistry(live_result["asset_registry"]).list()) > 1)
    registry.close()

    passed = all(x["passed"] for x in c.items)
    return {
        "passed": passed,
        "count": len(c.items) + prior["count"],
        "v10_count": len(c.items),
        "v9_count": prior["count"],
        "tests": [*prior["tests"], *c.items],
        "root": str(base),
    }
