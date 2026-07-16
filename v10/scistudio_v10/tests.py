from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .animation_director import AnimationDirector
from .drawing_brief import DrawingBriefCompiler
from .flux_studio import FluxKontextStudio
from .llm import LLMRouter
from .hybrid_package import HybridPackageBuilder
from .hybrid_render import PILHybridRenderer, RemotionHybridExporter
from .schemas import (
    ArtDirectionBible,
    BeautyFrame,
    CanonAnchor,
    ConcreteAdjustment,
    ContinuityCanon,
    DepthPlane,
    DirectorChangeOrder,
    DrawingBrief,
    MotionSeam,
    ReferencePack,
    SceneIllustrationArchitecture,
    SceneRequest,
)
from .semantic import SemanticMaskExtractor, SemanticSeparationPlanner
from .style_canon import base_bible, build_hard_coded_canon
from .utils import load_json


class FakeLLM:
    def generate_json(self, **kwargs):
        return kwargs.get("fallback")

    def critique_image(self, **kwargs):
        scene_id = (
            str(kwargs.get("namespace", "SC")).split("_")[-2] if "_" in str(kwargs.get("namespace", "")) else "SC"
        )
        return DirectorChangeOrder(scene_id=scene_id, status="approve", revision_number=1).model_dump(mode="json")

    def generate_reference_image(self, *, prompt, output_path, init_image=None, force=False):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if init_image and Path(init_image).exists():
            Image.open(init_image).convert("RGB").save(output)
        else:
            Image.new("RGB", (180, 320), "#FAFAF7").save(output)
        return output


class ResultCollector:
    def __init__(self):
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, detail: str = ""):
        self.items.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(f"{name}: {detail}")


def run_regression_tests(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root) if root else Path(tempfile.mkdtemp(prefix="scistudio_v10_tests_"))
    base.mkdir(parents=True, exist_ok=True)
    r = ResultCollector()

    canon = build_hard_coded_canon()
    r.check("canon identity locked", canon.canon_id == "experiment-ledger-editorial-ink-v1")
    r.check("canon forbids procedural hero", "procedural SVG hero illustration" in canon.forbidden)
    r.check("canon has stable palette", canon.color.paper == "#FAFAF7" and canon.color.blue_primary == "#2E77A6")
    r.check(
        "canon motion defaults hold", canon.motion.default_state == "hold" and not canon.motion.default_zoom_allowed
    )

    bible = base_bible("Test topic", "reference.png")
    r.check("bible embeds locked canon", bible.locked_canon.canon_id == canon.canon_id)
    tampered = ArtDirectionBible(
        topic="x",
        canon_id="other",
        locked_canon={"canon_id": "experiment-ledger-editorial-ink-v1", "display_name": "tampered"},
    )
    r.check("LLM cannot rename canon", tampered.locked_canon.display_name == "Experiment Ledger Editorial Ink")

    continuity = ContinuityCanon(
        palette_lock=canon.color.model_dump(mode="json"),
        line_lock=canon.line.model_dump(mode="json"),
        typography_lock=canon.typography.model_dump(mode="json"),
        ui_lock=canon.recurring_ui,
        continuity_rules=["same line and palette"],
        prohibited_drift=["style reset"],
    )
    architecture = SceneIllustrationArchitecture(
        scene_id="SC01",
        beat_id="B01",
        narrative_claim="Rain accumulates",
        visual_thesis="One integrated city under persistent rain",
        depth_planes=[
            DepthPlane(plane_id="bg", depth="background", contents="sky"),
            DepthPlane(plane_id="city", depth="midground", contents="city"),
            DepthPlane(plane_id="rain", depth="foreground", contents="rain", movement_role="primary"),
        ],
        motion_seams=[
            MotionSeam(
                seam_id="rain-field",
                subject="continuous rain",
                method="texture_loop",
                region="rain strokes and water veil",
                resting_overlap_rule="masked by atmospheric layer",
                required_variants=["initial", "changed"],
            )
        ],
        animation_representation=["texture_loop"],
        required_pose_variants=["initial", "changed"],
    )
    r.check("scene is beauty-first", architecture.beauty_frame_first and architecture.semantic_split_after_approval)
    r.check("scene forbids procedural hero", architecture.procedural_hero_allowed is False)

    brief_compiler = DrawingBriefCompiler({"seed_base": 1234, "aspect_ratio": "9:16"}, base / "briefs")
    pack = ReferencePack(scene_id="SC01", board_path=str(base / "board.png"))
    Image.new("RGB", (180, 320), "#FAFAF7").save(pack.board_path)
    scene = SceneRequest(
        scene_id="SC01",
        beat_id="B01",
        duration_s=1.0,
        narration="Rain continues.",
        headline="NONSTOP RAIN",
        visual_event="city in rain",
        desired_change="rain never stops",
    )
    brief = brief_compiler.beauty_frame(architecture, bible, continuity, pack, scene.narration, scene.headline, 0)
    prompt_low = brief.positive_prompt.lower()
    r.check("brief is scene-first", "one integrated" in prompt_low and "isolated icon" in brief.negative_prompt.lower())
    r.check("brief hard-codes line system", "variable pressure" in prompt_low and "purposeful breaks" in prompt_low)
    r.check("brief forbids office-shape look", "microsoft word shape assembly" in brief.negative_prompt.lower())
    r.check("brief uses stable seed policy", brief.seed == 1234)
    try:
        DrawingBrief(
            brief_id="bad",
            scene_id="x",
            positive_prompt="procedural SVG hero",
            negative_prompt="",
            kontext_instruction="assemble from primitive",
        )
        bad_rejected = False
    except Exception:
        bad_rejected = True
    r.check("procedural hero brief rejected", bad_rejected)

    try:
        DirectorChangeOrder(scene_id="SC", status="revise", adjustments=[])
        vague_rejected = False
    except Exception:
        vague_rejected = True
    r.check("vague revision order rejected", vague_rejected)
    concrete = DirectorChangeOrder(
        scene_id="SC",
        status="revise",
        adjustments=[
            ConcreteAdjustment(
                adjustment_id="A1",
                target_region="hand",
                problem="oval hand",
                instruction="construct palm and knuckle plane",
            )
        ],
    )
    r.check("concrete director revision accepted", concrete.adjustments[0].target_region == "hand")

    approved_path = base / "approved.png"
    im = Image.new("RGB", (180, 320), "#FAFAF7")
    d = ImageDraw.Draw(im)
    d.rectangle([40, 60, 140, 260], fill="#475157")
    im.save(approved_path)
    beauty = BeautyFrame(
        scene_id="SC01", image_path=str(approved_path), approved=True, approval_source="vision-llm-art-director"
    )
    pose_dir = base / "poses"
    pose_dir.mkdir()
    pose = im.copy()
    ImageDraw.Draw(pose).rectangle([75, 80, 150, 240], fill="#2E77A6")
    pose_path = pose_dir / "changed.png"
    pose.save(pose_path)
    semantic_planner = SemanticSeparationPlanner({}, base / "semantic")
    contract = semantic_planner.plan(
        architecture, beauty, {"rain-field-initial": str(approved_path), "rain-field-changed": str(pose_path)}
    )
    r.check("semantic split after approval", contract.separation_occurs_after_approval)
    r.check("beauty pixels preserved", contract.preserve_original_beauty)

    def mask_gen(beauty_path, region, output):
        mask = Image.new("L", (180, 320), 0)
        ImageDraw.Draw(mask).rectangle([40, 60, 140, 260], fill=255)
        mask.save(output)
        return output

    extractor = SemanticMaskExtractor(FakeLLM(), {}, base / "semantic" / "masks", mask_generator=mask_gen)
    contract = extractor.extract_all(contract, architecture, force=True)
    moving = next(x for x in contract.layers if x.layer_id == "rain-field")
    r.check("mask extracted without redrawing beauty", Path(moving.mask_path).exists())

    animation = AnimationDirector(FakeLLM(), {}, base / "animation").plan(scene, architecture, contract, fps=10)
    r.check("beauty base never animated", all(e.target_layer != "beauty-base" for e in animation.events))
    r.check("no default camera motion", animation.camera_locked)
    r.check("motion has causal reason", all(e.reason_id for e in animation.events))

    overlay = base / "overlay.svg"
    overlay.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320"><text x="5" y="20">TEST</text></svg>'
    )
    package = HybridPackageBuilder({"width": 180, "height": 320}, base / "hybrid").build(
        scene, contract, animation, str(overlay)
    )
    base_layer = next(x for x in package.layers if x.layer_id == "beauty-base")
    r.check("hybrid package keeps raster beauty", base_layer.kind == "raster" and Path(base_layer.path).exists())
    r.check("SVG limited to overlay", any(x.kind == "svg_overlay" for x in package.layers))

    remotion = RemotionHybridExporter({}, base / "remotion").create_project([package])
    ts = (Path(remotion) / "src/index.tsx").read_text()
    r.check("remotion supports raster layers", "<Img" in ts)
    r.check("remotion supports replacement poses", "replacement_pose" in ts)

    video = base / "preview.mp4"
    PILHybridRenderer({"crf": 30}, base / "render").render([package], video)
    r.check("deterministic H264 preview renders", video.exists() and video.stat().st_size > 1000)

    # Option 3: production FLUX prompting system.
    r.check("prompt stack compiled", brief.prompt_stack is not None and bool(brief.compiled_prompt))
    r.check("prompt diagnostics pass", bool(brief.prompt_diagnostics and brief.prompt_diagnostics.valid))
    r.check(
        "prompt is natural language not JSON", "{" not in brief.compiled_prompt and "}" not in brief.compiled_prompt
    )
    r.check("prompt length is directed not bloated", 80 <= brief.prompt_diagnostics.word_count <= 380)
    first_55 = " ".join(brief.compiled_prompt.lower().split()[:55])
    r.check("style stated before scene detail", "scientific editorial" in first_55 and "illustration" in first_55)
    r.check("Kontext prompt upsampling disabled", brief.prompt_upsampling is False)
    r.check(
        "single input strategy recorded",
        brief.init_strategy in {"reference_board", "master_style_anchor", "previous_approved_scene"},
    )
    r.check(
        "style fingerprint is immutable",
        bool(brief.style_fingerprint_hash) and brief.prompt_stack.immutable_style_hash == brief.style_fingerprint_hash,
    )

    architecture_2 = architecture.model_copy(deep=True)
    architecture_2.scene_id = "SC02"
    architecture_2.visual_thesis = "The same city after drains overflow"
    pack_2 = pack.model_copy(deep=True)
    pack_2.scene_id = "SC02"
    pack_2.previous_approved_scene = str(approved_path)
    brief_2 = brief_compiler.beauty_frame(
        architecture_2,
        bible,
        continuity,
        pack_2,
        "Water rises.",
        "DRAINAGE OVERFLOW",
        1,
        style_source_path=str(approved_path),
        init_strategy="previous_approved_scene",
    )
    r.check("style hash identical across scenes", brief_2.style_fingerprint_hash == brief.style_fingerprint_hash)
    r.check(
        "scene delta changes without style reset",
        brief_2.prompt_stack.scene_delta_hash != brief.prompt_stack.scene_delta_hash,
    )
    r.check(
        "previous approved scene drives continuity",
        brief_2.init_image_path == str(approved_path) and brief_2.init_strategy == "previous_approved_scene",
    )

    change_order = DirectorChangeOrder(
        scene_id="SC01",
        revision_number=1,
        status="revise",
        adjustments=[
            ConcreteAdjustment(
                adjustment_id="A_HAND",
                target_region="right hand",
                problem="oval hand",
                instruction="construct a palm mass, four knuckle plane and locked thumb aligned with the wrist",
                preserve=["head", "torso", "background"],
                priority="critical",
            ),
            ConcreteAdjustment(
                adjustment_id="A_WALL",
                target_region="wall surface",
                problem="flat rectangle",
                instruction="add perspective convergence and irregular concrete edge wear without changing its position",
                preserve=["character", "impact point"],
                priority="high",
            ),
        ],
        immutable_preserve_list=["camera", "palette", "line rhythm"],
    )
    revision_briefs = brief_compiler.revision_passes(brief, change_order, str(approved_path), 1)
    r.check("director changes become sequential local passes", len(revision_briefs) == 2)
    r.check("revision chain uses prior pass", revision_briefs[1].init_image_path == revision_briefs[0].output_path)
    r.check(
        "revision explicitly scopes target", all(b.prompt_diagnostics.has_local_edit_scope for b in revision_briefs)
    )
    r.check(
        "revision explicitly preserves unaffected image",
        all(b.prompt_diagnostics.has_explicit_preservation for b in revision_briefs),
    )
    r.check(
        "revision avoids vague quality language",
        all(not b.prompt_diagnostics.vague_language_found for b in revision_briefs),
    )
    r.check("revision prompts stay concise", all(b.prompt_diagnostics.word_count <= 180 for b in revision_briefs))

    pose_brief = brief_compiler.pose_variant(
        brief,
        str(approved_path),
        "impact",
        "rotate only the forearm and construct the compressed fist at the wall contact point",
    )
    r.check("pose edit uses approved frame", pose_brief.init_strategy == "approved_beauty_frame")
    r.check(
        "pose edit is local and preservation-first",
        pose_brief.prompt_diagnostics.has_local_edit_scope and pose_brief.prompt_diagnostics.has_explicit_preservation,
    )

    # The Flux studio sends only the compiled prompt and records a reproducible request manifest.
    generated_root = base / "flux_request_test"

    def generator_fixture(drawing_brief):
        out = Path(drawing_brief.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (180, 320), "#FAFAF7").save(out)
        return out

    flux_fixture = FluxKontextStudio(FakeLLM(), brief_compiler, {}, generated_root, image_generator=generator_fixture)
    generated = flux_fixture.generate(brief, force=True)
    request_manifest = load_json(generated_root / "requests" / f"{brief.brief_id}.json")
    r.check("Flux request manifest exists", generated is not None and bool(request_manifest))
    r.check("request contains one compiled prompt", request_manifest["prompt"] == brief.compiled_prompt)
    r.check(
        "request locks prompt parameters",
        request_manifest["prompt_upsampling"] is False and request_manifest["output_format"] == "png",
    )

    # The vision art director reviews MASTER / PREVIOUS / CURRENT on one comparison board.
    continuity_with_images = continuity.model_copy(deep=True)
    continuity_with_images.anchors = [
        CanonAnchor(anchor_id="master", role="master_style_anchor", path=str(approved_path)),
        CanonAnchor(anchor_id="previous", role="approved_scene", path=str(pose_path), scene_id="SC00"),
    ]
    compare_path = flux_fixture._comparison_board(str(approved_path), "SC01", continuity_with_images, 1)
    compare_image = Image.open(compare_path)
    r.check("director comparison board generated", compare_image.size == (1536, 1024))

    # Verify the real BFL payload contract without making a network call.
    # The fixture serves a real PNG with image headers: the hardened client
    # validates Content-Type and verifies the payload with Pillow, so an
    # unrealistic body would (correctly) be rejected.
    import io
    from unittest.mock import patch

    png_buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "#2E77A6").save(png_buffer, format="PNG")
    real_png_bytes = png_buffer.getvalue()

    class FakeResponse:
        def __init__(self, data=None, content=b"", headers=None, status_code=200):
            self._data = data or {}
            self.content = content
            self.headers = headers or {}
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    post_calls = []

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        post_calls.append({"url": url, "json": json})
        return FakeResponse({"id": "REQ", "polling_url": "https://poll.local/result"})

    def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
        if "poll.local" in url:
            return FakeResponse({"status": "Ready", "result": {"sample": "https://delivery.local/sample"}})
        return FakeResponse(content=real_png_bytes, headers={"Content-Type": "image/png"})

    bfl_router = LLMRouter(
        {
            "bfl_model": "flux-kontext-pro",
            "bfl_aspect_ratio": "9:16",
            "bfl_seed": 99,
            "bfl_prompt_upsampling": False,
            "bfl_safety_tolerance": 2,
            "bfl_output_format": "png",
            "bfl_allowed_url_hosts": ["api.bfl.ai", "poll.local", "delivery.local"],
        },
        {"BFL_API_KEY": "TEST-BFL-KEY"},
        base / "bfl_cache",
    )
    bfl_out = base / "bfl_mock.png"
    with patch("requests.post", side_effect=fake_post), patch("requests.get", side_effect=fake_get):
        result_path = bfl_router._bfl_flux_image(
            "A directed test prompt", bfl_out, init_image=approved_path, force=True
        )
    payload = post_calls[0]["json"]
    r.check("BFL mock request succeeds", result_path == bfl_out and bfl_out.exists())
    r.check("BFL downloaded image is a valid PNG", bfl_out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")
    r.check("BFL payload uses Kontext input image", "input_image" in payload and payload["aspect_ratio"] == "9:16")
    r.check(
        "BFL payload locks deterministic prompt policy",
        payload["prompt_upsampling"] is False and payload["safety_tolerance"] == 2 and payload["seed"] == 99,
    )
    r.check(
        "BFL safe manifest excludes base64",
        "input_image" not in load_json(next((base / "bfl_cache" / "bfl_images").glob("*.request.json")))["payload"],
    )

    report = {"passed": all(x["passed"] for x in r.items), "count": len(r.items), "tests": r.items, "root": str(base)}
    (base / "regression_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
