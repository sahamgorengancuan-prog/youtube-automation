from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tqdm.auto import tqdm

from .audio import AudioMixer, VoiceoverEngine
from .cache import AssetCache
from .captions import CaptionGenerator
from .composer import SceneComposer
from .config import StudioConfig
from .exporter import Exporter
from .hardware import detect_hardware
from .hashing import file_sha256, slugify
from .llm import LLMClient
from .logging_utils import configure_logging
from .renderer import VideoRenderer
from .research import ResearchService
from .scene_planner import ScenePlanner
from .schemas import RunManifest
from .script_generator import ScriptGenerator
from .search import SearchService
from .seo import SEOGenerator
from .storyboard_generator import StoryboardGenerator
from .topic import TopicResolver


class StudioPipeline:
    """Full end-to-end orchestration (Phase 1 + Phase 2):

    Topic -> Research -> Validation -> Script -> Storyboard -> Scene plan
    -> SVG assets (cached) -> Static scene composition
    -> Voice-over -> Captions -> Music/SFX mix
    -> Animated 1080x1920 MP4 (ffprobe-validated) -> SEO -> Manifest + zip.

    Every stage is also usable on its own; this class is a thin, testable
    coordinator that drops straight into a FastAPI background job.
    """

    def __init__(self, config: StudioConfig):
        self.config = config
        self.root = Path(config.project.root).resolve()
        self.cache_root = self.root / "cache"
        self.logger = configure_logging("autostudio.pipeline")
        self.llm = LLMClient(config, self.cache_root / "llm")
        self.search = SearchService(config, self.cache_root)
        self.topic_resolver = TopicResolver(self.llm, self.search)
        self.research = ResearchService(config, self.llm, self.cache_root / "research")
        self.script = ScriptGenerator(config, self.llm, self.cache_root / "scripts")
        self.storyboard = StoryboardGenerator(config, self.llm, self.cache_root / "storyboards")
        self.planner = ScenePlanner(config)
        self.asset_cache = AssetCache(config, self.cache_root)
        self.composer = SceneComposer(config)
        self.voice = VoiceoverEngine(config, self.cache_root)
        self.captions = CaptionGenerator(config)
        self.audio_mixer = AudioMixer(config)
        self.renderer = VideoRenderer(config, self.cache_root)
        self.seo = SEOGenerator(config, self.llm, self.cache_root)
        self.exporter = Exporter(self.root)

    def run(self, mode: str, value: str = "", *, force_refresh: bool = False, strict_validation: bool = False) -> dict:
        topic, candidates = self.topic_resolver.resolve(mode, value)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + slugify(topic, 48)
        run_dir = self.root / "output" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info("Run %s | topic=%s", run_id, topic)
        progress = tqdm(total=14, desc="Scientific animation studio", unit="stage")

        # --- Phase 1: research -> storyboard -> static scenes --------------- #
        sources = self.search.search_topic(topic, force_refresh)
        progress.update(1)
        research, validation = self.research.collect(topic, sources, force_refresh)
        progress.update(1)
        if strict_validation and not validation.passed:
            progress.close()
            raise RuntimeError("Scientific validation failed in strict mode.")

        script = self.script.generate(research, validation, force_refresh)
        progress.update(1)
        storyboard = self.planner.plan(self.storyboard.generate(script, research, force_refresh))
        progress.update(1)

        asset_paths: dict[str, Path] = {}
        asset_hashes: dict[str, str] = {}
        for requirement in tqdm(storyboard.asset_catalog, desc="SVG assets", leave=False):
            path, metadata = self.asset_cache.get_or_create(requirement, topic)
            asset_paths[requirement.asset_id] = path
            asset_hashes[requirement.asset_id] = metadata.asset_hash
        progress.update(1)

        scene_dir = run_dir / "scenes"
        scene_dir.mkdir(parents=True, exist_ok=True)
        scene_paths: list[Path] = []
        for index, scene in enumerate(tqdm(storyboard.scenes, desc="Static scene composition", leave=False), start=1):
            scene_paths.append(self.composer.compose_scene(
                scene, asset_paths, scene_dir / f"scene{index:02d}.svg",
                storyboard.canvas_width, storyboard.canvas_height,
            ))
        progress.update(1)

        contact_sheet = self.composer.compose_contact_sheet(scene_paths, run_dir / "storyboard_contact_sheet.svg")
        preview_html = self.composer.compose_preview_html(scene_paths, run_dir / "preview.html")
        progress.update(1)

        # --- Phase 2: voice -> captions -> mix -> video -------------------- #
        timeline, storyboard = self.voice.build_timeline(storyboard, run_dir)
        progress.update(1)
        srt_path, ass_path, caption_segments = self.captions.write(timeline, run_dir)
        progress.update(1)
        final_audio = self.audio_mixer.mix(timeline, run_dir)
        progress.update(1)

        # Re-compose scenes so their durations match the synthesised audio.
        scene_paths = [
            self.composer.compose_scene(
                scene, asset_paths, scene_dir / f"scene{index:02d}.svg",
                storyboard.canvas_width, storyboard.canvas_height,
            )
            for index, scene in enumerate(storyboard.scenes, start=1)
        ]
        video_path, render_report = self.renderer.render(storyboard, scene_paths, final_audio, ass_path, run_dir)
        progress.update(1)
        seo = self.seo.generate(script, research, force_refresh)
        progress.update(1)

        # --- Export + manifest --------------------------------------------- #
        files = self.exporter.export_structured(run_dir, research, validation, script, storyboard, timeline, seo)
        copied_assets = self.exporter.copy_assets(run_dir, asset_paths)
        files.update({
            "contact_sheet": str(contact_sheet),
            "preview_html": str(preview_html),
            "scenes_directory": str(scene_dir),
            "assets_directory": str(run_dir / "assets"),
            "voice_track": timeline.voice_track,
            "final_audio": str(final_audio),
            "captions_srt": str(srt_path),
            "captions_ass": str(ass_path),
            "video": str(video_path),
            "render_report": str(run_dir / "video" / "render_report.json"),
        })

        manifest = RunManifest(
            run_id=run_id, topic=topic, mode=mode,
            created_at=datetime.now(timezone.utc).isoformat(),
            project_root=str(self.root), run_directory=str(run_dir),
            hardware=detect_hardware().to_dict(),
            research_hash=research.research_hash, script_hash=script.script_hash,
            storyboard_hash=storyboard.storyboard_hash, asset_hashes=asset_hashes,
            files=files, validation_passed=validation.passed,
            estimated_duration_s=storyboard.estimated_duration_s,
            warnings=[issue.message for issue in validation.issues],
            video_hash=file_sha256(video_path),
            render_report=render_report.model_dump(mode="json"),
        )
        manifest_path = self.exporter.write_manifest(run_dir, manifest)
        archive_path = self.exporter.zip_run(run_dir)
        progress.update(1)
        progress.close()

        if self.config.llm.unload_after_pipeline:
            self.llm.unload()

        return {
            "topic": topic, "topic_candidates": candidates, "sources": sources,
            "research": research, "validation": validation, "script": script,
            "storyboard": storyboard, "asset_paths": asset_paths, "copied_assets": copied_assets,
            "scene_paths": scene_paths, "contact_sheet": contact_sheet, "preview_html": preview_html,
            "timeline": timeline, "caption_segments": caption_segments,
            "captions_srt": srt_path, "captions_ass": ass_path, "final_audio": final_audio,
            "video_path": video_path, "render_report": render_report, "seo": seo,
            "manifest": manifest, "manifest_path": manifest_path, "archive_path": archive_path,
            "run_dir": run_dir,
        }
