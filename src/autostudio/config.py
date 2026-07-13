from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProjectConfig(ConfigModel):
    name: str = "autonomous-scientific-animation-studio"
    root: str = ""
    random_seed: int = 42


class LLMConfig(ConfigModel):
    # Priority 1: local Qwen. Priority 2: OpenAI, used only if local fails.
    gpu_model: str = "Qwen/Qwen2.5-7B-Instruct"
    cpu_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    openai_model: str = "gpt-4.1-mini"
    use_openai_fallback: bool = True
    prefer_local: bool = True
    quantization: str = "4bit"  # 4bit | none
    temperature: float = 0.15
    max_new_tokens: int = 2400
    context_tokens: int = 7000
    unload_after_pipeline: bool = True


class SearchConfig(ConfigModel):
    providers: list[str] = Field(default_factory=lambda: ["ddgs", "wikipedia", "crossref", "openalex", "arxiv"])
    max_results_per_provider: int = 5
    timeout_seconds: int = 20
    min_sources: int = 5
    rss_feeds: list[str] = Field(default_factory=lambda: [
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "https://www.sciencedaily.com/rss/top/science.xml",
        "https://phys.org/rss-feed/",
    ])


class ResearchConfig(ConfigModel):
    minimum_supported_facts: int = 5
    minimum_non_wikipedia_sources: int = 2
    strict_numeric_validation: bool = True


class ScriptConfig(ConfigModel):
    target_duration_seconds: float = 55.0
    target_words_min: int = 105
    target_words_max: int = 155
    words_per_second: float = 2.55
    scene_count_min: int = 7
    scene_count_max: int = 10


class CanvasConfig(ConfigModel):
    width: int = 1080
    height: int = 1920
    safe_margin: int = 64


class StyleConfig(ConfigModel):
    name: str = "scientific-simulation-flat"
    palette: dict[str, str] = Field(default_factory=lambda: {
        "paper": "#F7F7F4", "ink": "#17202A", "primary": "#2D9CDB",
        "secondary": "#566573", "dark": "#202A33", "warning": "#E74C3C",
        "sun": "#F5C542", "white": "#FFFFFF",
    })
    stroke_width: float = 5.0
    font_family: str = "DejaVu Sans"
    title_weight: int = 800
    body_weight: int = 500
    no_gradients: bool = True
    no_textures: bool = True
    no_raster: bool = True


class CacheConfig(ConfigModel):
    enabled: bool = True
    reuse_assets: bool = True
    reuse_structured_outputs: bool = True
    optional_embeddings: bool = False


class SVGConfig(ConfigModel):
    generator_version: str = "svg-template-v1"
    asset_viewbox: int = 1000
    max_scene_objects: int = 12
    validate_xml: bool = True


class PreviewConfig(ConfigModel):
    # Static storyboard preview settings (no animation yet — Phase 2).
    rasterize: bool = True  # CairoSVG -> PNG contact sheet for reliable inline display
    contact_columns: int = 3
    thumb_width: int = 360
    thumb_height: int = 640


class StudioConfig(ConfigModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    script: ScriptConfig = Field(default_factory=ScriptConfig)
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    style: StyleConfig = Field(default_factory=StyleConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    svg: SVGConfig = Field(default_factory=SVGConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)


DEFAULT_CONFIG = StudioConfig().model_dump(mode="json")


def ensure_default_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False), encoding="utf-8")
    return path


def load_config(path: Path | None = None) -> StudioConfig:
    root = Path(os.environ.get("AUTOSTUDIO_ROOT", ".")).resolve()
    config_path = path or root / "config" / "config.yaml"
    ensure_default_config(config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data.setdefault("project", {})["root"] = str(root)
    return StudioConfig.model_validate(data)
