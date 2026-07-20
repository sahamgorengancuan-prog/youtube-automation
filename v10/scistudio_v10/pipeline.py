from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .animation_director import AnimationDirector
from .asset_registry import AssetRegistry
from .audio import AudioEngine
from .cache_store import ArtifactCache
from .candidate_tournament import CandidateTournament
from .character_registry import CharacterRegistry
from .director import NarrativeDirector
from .drawing_brief import DrawingBriefCompiler
from .flux_studio import FluxKontextStudio
from .hybrid_package import HybridPackageBuilder
from .hybrid_render import PILHybridRenderer, RemotionHybridExporter
from .job_runtime import ResumableJobRuntime
from .llm import LLMRouter
from .overlay import ScientificOverlayBuilder
from .observability import EventLogger
from .publisher import LocalArchivePublisher, UploadPostPublisher
from .provider_registry import ProviderRegistry
from .reference_director import ReferenceDirector
from .reference_selector import DynamicReferenceSelector
from .research import PublicResearchSearch, ResearchEngine
from .scene_architect import SceneIllustrationArchitect
from .schemas import (
    ArtDirectionBible,
    AssetQuery,
    AssetRecord,
    CanonAnchor,
    CharacterProfile,
    CharacterView,
    ContinuityCanon,
    DrawingBrief,
    PipelineResult,
    ResearchPack,
    ScriptPackage,
    ShotState,
    Storyboard,
    TemporalRequest,
)
from .semantic import SemanticMaskExtractor, SemanticSeparationPlanner
from .shot_state import ShotStatePlanner
from .sketch_control import ControlSketchBuilder
from .studio_director import ExecutiveArtDirector
from .style_reference import StyleReferenceExtractor
from .temporal_backends import (
    DeterministicCompositorBackend,
    SketchControlledVideoBackend,
    TemporalBackendRouter,
)
from .utils import ensure_dir, hash_value, save_json, slugify


class ScientificMotionStudioV10:
    """Production-oriented agentic scientific animation studio.

    V10 combines:
    - a persistent, resumable job runtime and provider shell;
    - retrieval-based visual continuity and a searchable asset registry;
    - multi-candidate FLUX tournaments before director-led revisions;
    - explicit first/last shot states and character/environment canon;
    - hybrid authored beauty art, scientific overlays and optional
      sketch-controlled temporal video backends.

    Hero art is never replaced with procedural SVG or clip-art. When a required
    high-complexity temporal backend is unavailable, the pipeline stops rather
    than degrading the scene into PowerPoint-like motion.
    """

    def __init__(
        self,
        config: dict[str, Any] | Any,
        secrets: dict[str, Any] | None = None,
        *,
        llm_router: Any | None = None,
        image_generator: Any | None = None,
        mask_generator: Any | None = None,
        temporal_backends: list[Any] | None = None,
    ):
        from .config_models import ExecutionMode, StudioConfig
        from .errors import ProviderLockViolationError

        raw_config = config if isinstance(config, dict) else None
        self.settings = StudioConfig.from_dict(config)
        self.execution_mode = self.settings.execution_mode.value
        # Preserve the user's raw dict shape (modules read nested dicts), but
        # guarantee the validated execution mode is propagated everywhere.
        self.config = dict(raw_config) if raw_config is not None else self.settings.as_runtime_dict()
        self.config["execution_mode"] = self.execution_mode
        llm_config = dict(self.config.get("llm") or {})
        llm_config["execution_mode"] = self.execution_mode
        self.config["llm"] = llm_config

        if self.settings.execution_mode == ExecutionMode.production and any(
            x is not None for x in (llm_router, image_generator, mask_generator, temporal_backends)
        ):
            raise ProviderLockViolationError(
                "Injected provider implementations (llm_router/image_generator/"
                "mask_generator/temporal_backends) are not allowed in production "
                "mode; deterministic or fake providers are test/development only."
            )

        self.secrets = secrets or {}
        self.workspace = ensure_dir(self.config.get("workspace", "./scientific_motion_v10"))
        self.cache = ArtifactCache(self.workspace / "cache" / "artifacts")
        self.llm = llm_router or LLMRouter(llm_config, self.secrets, self.workspace / "cache" / "llm")
        self.image_generator = image_generator
        self.mask_generator = mask_generator
        self.temporal_backends = temporal_backends
        self.providers = ProviderRegistry.from_config(self.config, self.workspace / "state" / "providers")

    def run(
        self,
        topic: str,
        reference_video: str | Path,
        *,
        plan_only: bool = True,
        render_video: bool = False,
        force: bool = False,
        job_id: str | None = None,
        progress_callback: Any | None = None,
        cancellation_token: Any | None = None,
    ) -> dict[str, Any]:
        """Execute the pipeline; the job lock is always released on exit.

        ``progress_callback(stage_id, event, record)`` (optional) receives
        "cached"/"started"/"completed"/"failed" stage events and must not
        raise. ``cancellation_token`` (optional) is any object with a truthy
        ``cancelled`` attribute; setting it stops the run after the current
        safe stage with :class:`JobCancelledError` — the job stays resumable.
        Both parameters are optional, preserving the original signature.
        """
        reference_video = str(Path(reference_video))
        job_id = (
            job_id
            or self.config.get("job_id")
            or (slugify(topic, 40) + "-" + hash_value({"topic": topic, "reference": reference_video}, 10))
        )
        run_dir = ensure_dir(self.workspace / "jobs" / job_id)
        limits = self.config.get("limits") or {}
        runtime = ResumableJobRuntime(
            run_dir,
            job_id=job_id,
            topic=topic,
            config=self.config,
            max_stage_attempts=int(limits.get("max_stage_attempts", 5)),
            debug_tracebacks=bool(self.config.get("debug_tracebacks", False)),
            on_stage_event=progress_callback,
            cancellation_token=cancellation_token,
        )
        try:
            return self._run_stages(
                topic,
                reference_video,
                run_dir,
                runtime,
                job_id,
                plan_only=plan_only,
                render_video=render_video,
                force=force,
            )
        finally:
            runtime.release_lock()

    def _run_stages(
        self,
        topic: str,
        reference_video: str,
        run_dir: Path,
        runtime: ResumableJobRuntime,
        job_id: str,
        *,
        plan_only: bool,
        render_video: bool,
        force: bool,
    ) -> dict[str, Any]:
        events = EventLogger(run_dir / "observability")
        self.llm.events = events
        events.emit(
            "job.started",
            job_id=job_id,
            topic=topic,
            plan_only=plan_only,
            render_video=render_video,
            execution_mode=self.execution_mode,
        )
        warnings: list[str] = []

        # ------------------------------------------------------------------
        # Foundation stages: resumable and input-hash aware.
        # ------------------------------------------------------------------
        style_ref = StyleReferenceExtractor(run_dir / "01_style_reference", self.config.get("style_reference", {}))
        reference_profile = runtime.execute(
            "01_style_reference",
            {"reference_video": reference_video, "config": self.config.get("style_reference", {})},
            lambda: style_ref.extract(reference_video, force=force),
            force=force,
        )
        reference_board = reference_profile["board_path"]

        search = PublicResearchSearch(self.config.get("research_search", {}), run_dir / "02_research" / "search")
        sources = (
            search.search(topic, force=force) if self.config.get("research_search", {}).get("enabled", True) else []
        )
        research = ResearchPack.model_validate(
            runtime.execute(
                "02_research",
                {"topic": topic, "sources": sources},
                lambda: ResearchEngine(self.llm, run_dir / "02_research").build(topic, sources, force=force),
                force=force,
            )
        )
        narrative = NarrativeDirector(self.llm, self.config.get("narrative", {}), run_dir / "03_narrative")
        script = ScriptPackage.model_validate(
            runtime.execute(
                "03_script",
                research.model_dump(mode="json"),
                lambda: narrative.script(research, force=force),
                force=force,
            )
        )
        storyboard = Storyboard.model_validate(
            runtime.execute(
                "04_storyboard",
                {"script": script.model_dump(mode="json"), "research_hash": research.research_hash},
                lambda: narrative.storyboard(script, research, force=force),
                force=force,
            )
        )

        audio_manifest: dict[str, Any] = {
            "voice": {},
            "word_timing": {},
            "support_bed": "",
            "duration_s": storyboard.estimated_duration_s,
        }
        audio_enabled = bool(self.config.get("audio", {}).get("enabled", True))
        if audio_enabled and (not plan_only or self.config.get("audio", {}).get("build_in_plan_mode", False)):
            audio = AudioEngine(self.config.get("audio", {}), run_dir / "05_audio")
            script, storyboard, audio_manifest = audio.build(script, storyboard)

        executive = ExecutiveArtDirector(self.llm, self.config.get("art_director", {}), run_dir / "06_art_direction")
        bible = ArtDirectionBible.model_validate(
            runtime.execute(
                "06_art_bible",
                {
                    "topic": topic,
                    "script": script.script_hash,
                    "storyboard": storyboard.storyboard_hash,
                    "reference": reference_board,
                },
                lambda: executive.art_bible(topic, research, script, storyboard, reference_board, force=force),
                force=force,
            )
        )
        continuity = ContinuityCanon.model_validate(
            runtime.execute(
                "07_continuity_canon",
                {"bible": bible.model_dump(mode="json"), "storyboard": storyboard.model_dump(mode="json")},
                lambda: executive.continuity_canon(bible, storyboard, reference_board, force=force),
                force=force,
            )
        )
        architect = SceneIllustrationArchitect(
            self.llm, self.config.get("scene_architect", {}), run_dir / "08_scene_architecture"
        )
        architectures_raw = runtime.execute(
            "08_scene_architectures",
            {"storyboard": storyboard.model_dump(mode="json"), "bible": bible.model_dump(mode="json")},
            lambda: architect.plan_all(storyboard, bible, continuity, force=force),
            force=force,
        )
        from .schemas import SceneIllustrationArchitecture

        architectures = [SceneIllustrationArchitecture.model_validate(x) for x in architectures_raw]

        shot_planner = ShotStatePlanner(self.llm, self.config.get("shot_state", {}), run_dir / "09_shot_states")
        shot_states: list[ShotState] = []
        for scene, architecture in zip(storyboard.scenes, architectures):
            state = ShotState.model_validate(
                runtime.execute(
                    f"09_shot_state_{scene.scene_id}",
                    {"scene": scene.model_dump(mode="json"), "architecture": architecture.model_dump(mode="json")},
                    lambda s=scene, a=architecture: shot_planner.plan(s, a, force=force),
                    force=force,
                )
            )
            shot_states.append(state)
        save_json(run_dir / "09_shot_states" / "shot_states.json", shot_states)

        # ------------------------------------------------------------------
        # Searchable continuity databases.
        # ------------------------------------------------------------------
        registry = AssetRegistry(run_dir / "state" / "asset_registry.sqlite")
        characters = CharacterRegistry(run_dir / "state" / "character_registry")
        style_hash = bible.locked_canon.style_hash or hash_value(bible.locked_canon.model_dump(mode="json"), 20)
        registry.upsert(
            AssetRecord(
                asset_id="reference-video-board",
                path=reference_board,
                asset_type="style_anchor",
                role_tags=["style", "reference_video"],
                style_hash=style_hash,
                quality_score=1.0,
            )
        )
        for recurring in continuity.recurring_subjects:
            if not characters.get(recurring.subject_id):
                characters.upsert(
                    CharacterProfile(
                        subject_id=recurring.subject_id,
                        display_name=recurring.description or recurring.subject_id,
                        immutable_traits=recurring.immutable_traits,
                        style_hash=style_hash,
                    )
                )

        selector = DynamicReferenceSelector(registry, run_dir / "10_reference_retrieval")
        refs = ReferenceDirector(self.llm, self.config.get("references", {}), run_dir / "10_reference_plans")
        brief_compiler = DrawingBriefCompiler(self.config.get("drawing", {}), run_dir / "11_drawing_briefs")
        master_brief = brief_compiler.master_anchor(topic, bible, reference_board)
        reference_packs = []
        beauty_briefs: list[DrawingBrief] = []
        selections = []
        candidate_plans = []

        if plan_only:
            for index, (scene, architecture, state) in enumerate(zip(storyboard.scenes, architectures, shot_states)):
                query = self._asset_query(scene.scene_id, index, architecture, style_hash)
                selection = selector.select(query)
                selections.append(selection)
                pack = refs.plan(architecture, bible, continuity, previous_approved_scene="", force=force)
                selected_paths = [x.asset.path for x in selection.selected]
                pack.subject_anchor_paths = selected_paths
                pack.environment_anchor_paths = []
                pack.board_path = selection.board_path or reference_board
                brief = brief_compiler.beauty_frame(
                    architecture,
                    bible,
                    continuity,
                    pack,
                    scene.narration,
                    scene.headline,
                    index,
                    style_source_path=reference_board,
                    init_strategy="reference_board",
                )
                brief.request_metadata.update(
                    {
                        "shot_state": state.model_dump(mode="json"),
                        "reference_asset_ids": [x.asset.asset_id for x in selection.selected],
                        "candidate_count": int(self.config.get("candidate_tournament", {}).get("candidate_count", 4)),
                    }
                )
                reference_packs.append(pack)
                beauty_briefs.append(brief)
                candidate_plans.append(
                    {
                        "scene_id": scene.scene_id,
                        "candidate_seeds": [
                            (brief.seed or 0) + i * 9973
                            for i in range(
                                max(2, int(self.config.get("candidate_tournament", {}).get("candidate_count", 4)))
                            )
                        ],
                        "ranking_dimensions": [
                            "style_consistency",
                            "subject_consistency",
                            "composition_fitness",
                            "motion_readiness",
                            "causal_clarity",
                        ],
                        "shot_state": state.model_dump(mode="json"),
                    }
                )
            save_json(run_dir / "10_reference_retrieval" / "selections.json", selections)
            save_json(run_dir / "10_reference_plans" / "reference_packs.json", reference_packs)
            save_json(run_dir / "11_drawing_briefs" / "beauty_briefs.json", beauty_briefs)
            save_json(run_dir / "12_candidate_tournaments" / "candidate_plans.json", candidate_plans)
            result = PipelineResult(
                topic=topic,
                mode="plan_only",
                run_dir=str(run_dir),
                reference_board=reference_board,
                art_direction_bible=str(run_dir / "06_art_direction" / "art_direction_bible.json"),
                continuity_canon=str(run_dir / "06_art_direction" / "continuity_canon.json"),
                architectures=str(run_dir / "08_scene_architecture" / "scene_architectures.json"),
                drawing_briefs=str(run_dir / "11_drawing_briefs" / "beauty_briefs.json"),
                warnings=warnings,
                job_manifest=str(runtime.manifest_path),
                asset_registry=str(registry.path),
                character_registry=str(characters.path),
                shot_states=str(run_dir / "09_shot_states" / "shot_states.json"),
                reference_selections=str(run_dir / "10_reference_retrieval" / "selections.json"),
                candidate_plans=str(run_dir / "12_candidate_tournaments" / "candidate_plans.json"),
                provider_registry=self.providers.snapshot(),
            )
            self._write_provenance(
                run_dir,
                runtime,
                job_id,
                topic,
                {
                    "reference_board": reference_board,
                    "script": str(run_dir / "stage_outputs" / "03_script.json"),
                    "storyboard": str(run_dir / "stage_outputs" / "04_storyboard.json"),
                    "art_direction_bible": result.art_direction_bible,
                    "drawing_briefs": result.drawing_briefs,
                    "shot_states": result.shot_states,
                    "candidate_plans": result.candidate_plans,
                },
                warnings,
            )
            save_json(run_dir / "result.json", result)
            runtime.mark_completed()
            registry.close()
            events.emit("job.completed", job_id=job_id, mode="plan_only")
            return result.model_dump(mode="json")

        # ------------------------------------------------------------------
        # Live studio: tournament -> revision -> semantic separation -> motion.
        # ------------------------------------------------------------------
        flux = FluxKontextStudio(
            self.llm,
            brief_compiler,
            self.config.get("flux_studio", {}),
            run_dir / "13_flux_studio",
            image_generator=self.image_generator,
        )
        master_anchor = flux.create_master_anchor(master_brief, force=force)
        continuity.anchors.append(
            CanonAnchor(
                anchor_id="master-style-anchor",
                role="master_style_anchor",
                path=master_anchor,
                notes="Primary generated visual canon for all scene generations.",
            )
        )
        registry.upsert(
            AssetRecord(
                asset_id="master-style-anchor",
                path=master_anchor,
                asset_type="style_anchor",
                role_tags=["style", "master"],
                style_hash=style_hash,
                quality_score=1.0,
            )
        )
        save_json(run_dir / "06_art_direction" / "continuity_canon_live.json", continuity)

        semantic_planner = SemanticSeparationPlanner(self.config.get("semantic", {}), run_dir / "14_semantic")
        mask_extractor = SemanticMaskExtractor(
            self.llm,
            self.config.get("semantic", {}),
            run_dir / "14_semantic" / "masks",
            mask_generator=self.mask_generator,
        )
        overlay_builder = ScientificOverlayBuilder(
            {"width": storyboard.width, "height": storyboard.height, **self.config.get("overlay", {})},
            run_dir / "15_overlays",
        )
        animation_director = AnimationDirector(self.llm, self.config.get("animation", {}), run_dir / "16_animation")
        package_builder = HybridPackageBuilder(
            {"width": storyboard.width, "height": storyboard.height, **self.config.get("hybrid", {})},
            run_dir / "18_hybrid_packages",
        )
        sketch_builder = ControlSketchBuilder(run_dir / "17_temporal" / "control_sketches")
        temporal_backend_list = self.temporal_backends or [
            SketchControlledVideoBackend(
                self.config.get("temporal", {}).get("sketch_backend", {}),
                run_dir / "17_temporal" / "sketch_backend",
                execution_mode=self.execution_mode,
            ),
            DeterministicCompositorBackend(run_dir / "17_temporal" / "deterministic"),
        ]
        temporal_router = TemporalBackendRouter(temporal_backend_list, run_dir / "17_temporal" / "results")
        tournament = CandidateTournament(
            self.llm, self.config.get("candidate_tournament", {}), run_dir / "12_candidate_tournaments"
        )

        beauty_frames = []
        semantic_contracts = []
        animation_plans = []
        hybrid_scenes = []
        temporal_results = []
        previous_approved = ""
        for index, (scene, architecture, state) in enumerate(zip(storyboard.scenes, architectures, shot_states)):
            query = self._asset_query(scene.scene_id, index, architecture, style_hash)
            selection = selector.select(query)
            selections.append(selection)
            pack = refs.plan(architecture, bible, continuity, previous_approved_scene=previous_approved, force=force)
            pack.style_anchor_ids = ["reference-video-board", "master-style-anchor", *pack.style_anchor_ids]
            selected_paths = [x.asset.path for x in selection.selected]
            pack.subject_anchor_paths = selected_paths
            pack.board_path = selection.board_path or refs.build_board(pack, continuity)
            style_source = previous_approved or master_anchor
            brief = brief_compiler.beauty_frame(
                architecture,
                bible,
                continuity,
                pack,
                scene.narration,
                scene.headline,
                index,
                style_source_path=style_source,
                init_strategy="previous_approved_scene" if previous_approved else "master_style_anchor",
            )
            brief.request_metadata.update(
                {
                    "shot_state": state.model_dump(mode="json"),
                    "reference_asset_ids": [x.asset.asset_id for x in selection.selected],
                }
            )
            reference_packs.append(pack)
            beauty_briefs.append(brief)

            tour = tournament.run(brief, architecture, state, lambda b: flux.generate(b, force=force), force=force)
            beauty = flux.direct_scene(
                brief, architecture, bible, continuity, initial_path=tour.winner_path, force=force
            )
            if not beauty.approved:
                raise RuntimeError(f"Scene {scene.scene_id} was not explicitly approved by the art director")
            beauty_frames.append(beauty)
            previous_approved = beauty.image_path
            continuity = refs.add_approved_anchor(continuity, scene.scene_id, beauty.image_path)
            registry.upsert(
                AssetRecord(
                    asset_id=f"approved-{scene.scene_id}",
                    path=beauty.image_path,
                    asset_type="approved_scene",
                    scene_id=scene.scene_id,
                    subject_ids=[f.figure_id for f in architecture.figure_construction],
                    environment_id=self._environment_id(architecture),
                    camera_view=architecture.perspective.view,
                    perspective=architecture.perspective.lens_language,
                    chronology_index=index,
                    role_tags=["approved", "continuity"],
                    style_hash=style_hash,
                    quality_score=1.0,
                )
            )
            for figure in architecture.figure_construction:
                characters.add_view(
                    figure.figure_id,
                    CharacterView(
                        view_id=f"{scene.scene_id}-{figure.body_orientation}",
                        angle=self._angle(figure.body_orientation),
                        path=beauty.image_path,
                        notes=f"Approved whole-scene view; crop/isolation can be derived later for {figure.figure_id}.",
                    ),
                )
                registry.upsert(
                    AssetRecord(
                        asset_id=f"{figure.figure_id}-{scene.scene_id}",
                        path=beauty.image_path,
                        asset_type="subject_view",
                        scene_id=scene.scene_id,
                        subject_ids=[figure.figure_id],
                        camera_view=architecture.perspective.view,
                        perspective=architecture.perspective.lens_language,
                        chronology_index=index,
                        role_tags=["character", "approved"],
                        style_hash=style_hash,
                        quality_score=0.95,
                    )
                )
            save_json(run_dir / "06_art_direction" / "continuity_canon_live.json", continuity)

            pose_variants = flux.create_pose_variants(beauty, brief, architecture, force=force)
            contract = semantic_planner.plan(architecture, beauty, pose_variants)
            contract = mask_extractor.extract_all(contract, architecture, force=force)
            semantic_contracts.append(contract)
            overlay_path = overlay_builder.build(scene, index + 1, len(storyboard.scenes))
            timing = {"words": audio_manifest.get("word_timing", {}).get(scene.beat_id, [])}
            animation = animation_director.plan(
                scene,
                architecture,
                contract,
                fps=storyboard.fps,
                audio_timing=timing,
                reference_motion=reference_profile.get("motion"),
                force=force,
            )
            animation_plans.append(animation)

            temporal_clip = ""
            temporal_backend = ""
            use_temporal = self._should_use_temporal(state)
            if use_temporal:
                end_path = next(iter(pose_variants.values()), beauty.image_path)
                first_sketch, last_sketch = sketch_builder.build_pair(
                    scene.scene_id, beauty.image_path, end_path, state
                )
                request = TemporalRequest(
                    scene_id=scene.scene_id,
                    backend_preference=self.config.get("temporal", {}).get(
                        "backend_preference", ["sketch-controlled-video", "deterministic-compositor"]
                    ),
                    beauty_start=beauty.image_path,
                    beauty_end=end_path,
                    control_sketch_start=first_sketch.output_path,
                    control_sketch_end=last_sketch.output_path,
                    preserve_masks=[x.mask_path for x in contract.layers if x.locked and x.mask_path],
                    motion_prompt="; ".join(state.motion_bridge),
                    duration_frames=animation.duration_frames,
                    fps=animation.fps,
                    complexity=state.temporal_complexity,
                    output_path=str(run_dir / "17_temporal" / "clips" / f"{scene.scene_id}.mp4"),
                )
                result = temporal_router.generate(request)
                temporal_results.append(result)
                temporal_clip = result.output_path
                temporal_backend = result.backend_id

            voice_path = audio_manifest.get("voice", {}).get(scene.beat_id, "")
            hybrid_scenes.append(
                package_builder.build(
                    scene,
                    contract,
                    animation,
                    overlay_path,
                    voice_path,
                    temporal_clip_path=temporal_clip,
                    temporal_backend=temporal_backend,
                )
            )

        save_json(run_dir / "10_reference_retrieval" / "selections.json", selections)
        save_json(run_dir / "10_reference_plans" / "reference_packs.json", reference_packs)
        save_json(run_dir / "11_drawing_briefs" / "beauty_briefs.json", beauty_briefs)
        save_json(run_dir / "13_flux_studio" / "beauty_frames.json", beauty_frames)
        save_json(run_dir / "14_semantic" / "semantic_contracts.json", semantic_contracts)
        save_json(run_dir / "16_animation" / "animation_plans.json", animation_plans)
        save_json(run_dir / "17_temporal" / "temporal_results.json", temporal_results)

        remotion = RemotionHybridExporter(self.config.get("render", {}), run_dir / "19_remotion")
        remotion.create_project(hybrid_scenes, audio_manifest.get("support_bed", ""))
        video_path = ""
        mode = "live_generation"
        if render_video:
            output = run_dir / "scientific_motion_v10.mp4"
            backend = str(self.config.get("render", {}).get("backend", "remotion")).lower()
            if backend in {"pil", "deterministic", "preview"}:
                PILHybridRenderer(self.config.get("render", {}), run_dir / "19_preview_render").render(
                    hybrid_scenes, output
                )
            else:
                self._render_remotion(run_dir / "19_remotion", output)
            video_path = str(output)
            mode = "rendered"

        publish_manifest = {}
        publish_config = self.config.get("publishing", {})
        if video_path and publish_config.get("enabled", False):
            provider = str(publish_config.get("provider", "local-archive"))
            metadata = {"title": script.title or topic, "topic": topic, "job_id": job_id}
            if provider == "local-archive":
                publisher = LocalArchivePublisher(publish_config.get("archive_dir", run_dir / "published"))
            elif provider == "upload-post":
                publisher = UploadPostPublisher(
                    str(publish_config.get("endpoint", "")),
                    str(self.secrets.get("UPLOAD_POST_TOKEN", publish_config.get("token", ""))),
                    allowed_hosts=list(publish_config.get("allowed_hosts") or []),
                )
            else:
                raise RuntimeError(f"Unknown publishing provider: {provider}")
            publish_manifest = publisher.publish(video_path, metadata)
            save_json(run_dir / "publishing_manifest.json", publish_manifest)

        result = PipelineResult(
            topic=topic,
            mode=mode,
            run_dir=str(run_dir),
            reference_board=reference_board,
            art_direction_bible=str(run_dir / "06_art_direction" / "art_direction_bible.json"),
            continuity_canon=str(run_dir / "06_art_direction" / "continuity_canon_live.json"),
            architectures=str(run_dir / "08_scene_architecture" / "scene_architectures.json"),
            drawing_briefs=str(run_dir / "11_drawing_briefs" / "beauty_briefs.json"),
            beauty_frames=str(run_dir / "13_flux_studio" / "beauty_frames.json"),
            semantic_contracts=str(run_dir / "14_semantic" / "semantic_contracts.json"),
            animation_plans=str(run_dir / "16_animation" / "animation_plans.json"),
            video=video_path,
            warnings=warnings,
            job_manifest=str(runtime.manifest_path),
            asset_registry=str(registry.path),
            character_registry=str(characters.path),
            shot_states=str(run_dir / "09_shot_states" / "shot_states.json"),
            reference_selections=str(run_dir / "10_reference_retrieval" / "selections.json"),
            candidate_tournaments=str(run_dir / "12_candidate_tournaments"),
            temporal_results=str(run_dir / "17_temporal" / "temporal_results.json"),
            provider_registry=self.providers.snapshot(),
            publishing_manifest=publish_manifest,
        )
        self._write_provenance(
            run_dir,
            runtime,
            job_id,
            topic,
            {
                "reference_board": reference_board,
                "script": str(run_dir / "stage_outputs" / "03_script.json"),
                "storyboard": str(run_dir / "stage_outputs" / "04_storyboard.json"),
                "art_direction_bible": result.art_direction_bible,
                "continuity_canon": result.continuity_canon,
                "drawing_briefs": result.drawing_briefs,
                "beauty_frames": result.beauty_frames,
                "semantic_contracts": result.semantic_contracts,
                "animation_plans": result.animation_plans,
                "temporal_results": result.temporal_results,
                "video": video_path,
            },
            warnings,
        )
        save_json(run_dir / "result.json", result)
        runtime.mark_completed()
        registry.close()
        events.emit("job.completed", job_id=job_id, mode=mode, video=video_path)
        return result.model_dump(mode="json")

    def _write_provenance(
        self,
        run_dir: Path,
        runtime: ResumableJobRuntime,
        job_id: str,
        topic: str,
        artifacts: dict[str, Any],
        warnings: list[str],
    ) -> None:
        from .provenance import build_provenance_manifest, write_provenance_manifest

        manifest = build_provenance_manifest(
            job_id=job_id,
            topic=topic,
            execution_mode=self.execution_mode,
            config_hash=runtime.manifest.config_hash,
            artifacts=artifacts,
            providers={
                "reasoning": "openai-gpt",
                "vision_review": "openai-gpt",
                "image_generation": "bfl-flux-kontext",
                "execution_mode": self.execution_mode,
            },
            warnings=warnings,
        )
        write_provenance_manifest(run_dir, manifest)

    def _asset_query(self, scene_id: str, index: int, architecture: Any, style_hash: str) -> AssetQuery:
        return AssetQuery(
            scene_id=scene_id,
            subject_ids=[x.figure_id for x in architecture.figure_construction],
            environment_id=self._environment_id(architecture),
            camera_view=architecture.perspective.view,
            perspective=architecture.perspective.lens_language,
            desired_types=["style_anchor", "approved_scene", "subject_view", "environment", "material"],
            required_roles=["style", "continuity", "approved"],
            style_hash=style_hash,
            chronology_index=index,
            maximum_results=int(self.config.get("references", {}).get("maximum_retrieved_assets", 8)),
        )

    @staticmethod
    def _environment_id(architecture: Any) -> str:
        contents = " ".join(p.contents for p in architecture.depth_planes if p.depth in {"background", "midground"})
        return slugify(contents or architecture.visual_thesis, 40)

    @staticmethod
    def _angle(body_orientation: str) -> str:
        text = body_orientation.lower()
        if "rear" in text or "back" in text:
            return "rear"
        if "side" in text or "profile" in text:
            return "side"
        if "three" in text:
            return "three_quarter"
        return "front"

    def _should_use_temporal(self, state: ShotState) -> bool:
        config = self.config.get("temporal", {})
        if not config.get("enabled", True):
            return False
        if state.temporal_complexity in {"organic", "deformation"}:
            return True
        if state.temporal_complexity == "articulated":
            return bool(config.get("use_for_articulated", False))
        return False

    def _render_remotion(self, project: Path, output: Path) -> None:
        if not shutil.which("npm") or not shutil.which("npx"):
            raise RuntimeError("npm/npx are required for production Remotion rendering")
        render_config = self.config.get("render", {})
        timeout = float(render_config.get("render_timeout", 2400))
        if render_config.get("install_dependencies", True):
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=project, check=True, timeout=timeout)
        subprocess.run(
            [
                "npx",
                "remotion",
                "render",
                "src/index.tsx",
                "ScientificMotionV10",
                str(output),
                "--codec",
                "h264",
                "--pixel-format",
                "yuv420p",
            ],
            cwd=project,
            check=True,
            timeout=timeout,
        )
