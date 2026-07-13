from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tqdm.auto import tqdm

from .cache import AssetCache
from .composer import SceneComposer
from .config import StudioConfig
from .exporter import Exporter
from .hardware import detect_hardware
from .hashing import slugify
from .llm import LLMClient
from .logging_utils import configure_logging
from .research import ResearchService
from .scene_planner import ScenePlanner
from .schemas import RunManifest
from .script_generator import ScriptGenerator
from .search import SearchService
from .storyboard_generator import StoryboardGenerator
from .topic import TopicResolver


class StudioPipeline:
    """MVP orchestration: Topic -> Research -> Validation -> Script -> Storyboard
    -> Scene plan -> SVG assets (cached) -> Static scene preview -> Manifest.

    Video rendering is intentionally out of scope (Phase 2). Each stage is also
    callable on its own, so this class is a thin, testable coordinator and drops
    straight into a FastAPI background job.
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
        self.exporter = Exporter(self.root)

    def run(self, mode: str, value: str = "", *, force_refresh: bool = False, strict_validation: bool = False) -> dict:
        topic, candidates = self.topic_resolver.resolve(mode, value)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + slugify(topic, 48)
        run_dir = self.root / "output" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info("Run %s | topic=%s", run_id, topic)
        progress = tqdm(total=8, desc="Scientific animation studio (MVP)", unit="stage")

        # 1. Free web search across all configured providers.
        sources = self.search.search_topic(topic, force_refresh)
        progress.update(1)

        # 2. Grounded research synthesis + deterministic scientific validation.
        research, validation = self.research.collect(topic, sources, force_refresh)
        progress.update(1)
        if strict_validation and not validation.passed:
            progress.close()
            raise RuntimeError("Scientific validation failed in strict mode.")

        # 3. Retention-first script with per-beat timing.
        script = self.script.generate(research, validation, force_refresh)
        progress.update(1)

        # 4. Storyboard JSON, then normalised by the scene planner.
        storyboard = self.planner.plan(self.storyboard.generate(script, research, force_refresh))
        progress.update(1)

        # 5. Generate ONLY the assets this storyboard needs, reusing the cache.
        asset_paths: dict[str, Path] = {}
        asset_hashes: dict[str, str] = {}
        for requirement in tqdm(storyboard.asset_catalog, desc="SVG assets", leave=False):
            path, metadata = self.asset_cache.get_or_create(requirement, topic)
            asset_paths[requirement.asset_id] = path
            asset_hashes[requirement.asset_id] = metadata.asset_hash
        progress.update(1)

        # 6. Compose one static SVG scene per storyboard scene.
        scene_dir = run_dir / "scenes"
        scene_dir.mkdir(parents=True, exist_ok=True)
        scene_paths: list[Path] = []
        for index, scene in enumerate(tqdm(storyboard.scenes, desc="Scene composition", leave=False), start=1):
            scene_paths.append(self.composer.compose_scene(
                scene, asset_paths, scene_dir / f"scene{index:02d}.svg",
                storyboard.canvas_width, storyboard.canvas_height,
            ))
        progress.update(1)

        # 7. Storyboard preview (SVG contact sheet + HTML gallery, optional PNG).
        contact_sheet = self.composer.compose_contact_sheet(
            scene_paths, run_dir / "storyboard_contact_sheet.svg",
            columns=self.config.preview.contact_columns,
            thumb_width=self.config.preview.thumb_width,
            thumb_height=self.config.preview.thumb_height,
        )
        preview_html = self.composer.compose_preview_html(scene_paths, run_dir / "preview.html")
        preview_png = None
        if self.config.preview.rasterize:
            preview_png = self.composer.rasterize(contact_sheet, run_dir / "storyboard_contact_sheet.png")
        progress.update(1)

        # 8. Structured export + manifest + downloadable zip.
        files = self.exporter.export_structured(run_dir, research, validation, script, storyboard)
        copied_assets = self.exporter.copy_assets(run_dir, asset_paths)
        files.update({
            "contact_sheet": str(contact_sheet),
            "preview_html": str(preview_html),
            "scenes_directory": str(scene_dir),
            "assets_directory": str(run_dir / "assets"),
        })
        if preview_png is not None:
            files["contact_sheet_png"] = str(preview_png)

        manifest = RunManifest(
            run_id=run_id,
            topic=topic,
            mode=mode,
            created_at=datetime.now(timezone.utc).isoformat(),
            project_root=str(self.root),
            run_directory=str(run_dir),
            hardware=detect_hardware().to_dict(),
            research_hash=research.research_hash,
            script_hash=script.script_hash,
            storyboard_hash=storyboard.storyboard_hash,
            asset_hashes=asset_hashes,
            files=files,
            validation_passed=validation.passed,
            estimated_duration_s=storyboard.estimated_duration_s,
            warnings=[issue.message for issue in validation.issues],
        )
        manifest_path = self.exporter.write_manifest(run_dir, manifest)
        archive_path = self.exporter.zip_run(run_dir)
        progress.update(1)
        progress.close()

        if self.config.llm.unload_after_pipeline:
            self.llm.unload()

        return {
            "topic": topic,
            "topic_candidates": candidates,
            "sources": sources,
            "research": research,
            "validation": validation,
            "script": script,
            "storyboard": storyboard,
            "asset_paths": asset_paths,
            "copied_assets": copied_assets,
            "scene_paths": scene_paths,
            "contact_sheet": contact_sheet,
            "contact_sheet_png": preview_png,
            "preview_html": preview_html,
            "manifest": manifest,
            "manifest_path": manifest_path,
            "archive_path": archive_path,
            "run_dir": run_dir,
        }
