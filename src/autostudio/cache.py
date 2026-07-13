from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import StudioConfig
from .hashing import atomic_write_json, file_sha256, hash_value, read_json
from .logging_utils import configure_logging
from .schemas import AssetMetadata, AssetRequirement
from .svg_assets import AssetFactory


class AssetCache:
    """Content-addressed SVG asset cache.

    The asset hash is derived from everything that changes the pixels (type,
    variant, palette, stroke, generator version) but NOT from the scene it
    appears in — so the same "planet" is generated once and reused across
    scenes and across future videos. Each hit bumps a reuse counter.
    """

    def __init__(self, config: StudioConfig, cache_root: Path):
        self.config = config
        self.cache_root = cache_root / "assets"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_root / "index.json"
        self.logger = configure_logging("autostudio.asset_cache")
        self.factory = AssetFactory(config.style, config.svg.asset_viewbox)

    def _validate_svg(self, path: Path) -> None:
        root = ET.parse(path).getroot()
        if root.tag.split("}")[-1] != "svg":
            raise ValueError(f"{path} is not an SVG document.")
        text = path.read_text(encoding="utf-8").lower()
        forbidden = ["<lineargradient", "<radialgradient", "<image", "data:image/png", "data:image/jpeg"]
        violations = [token for token in forbidden if token in text]
        if violations:
            raise ValueError(f"Forbidden SVG features: {violations}")

    def _update_index(self, asset_id: str, asset_hash: str) -> None:
        index = read_json(self.index_path, {}) or {}
        index[asset_id] = asset_hash
        atomic_write_json(self.index_path, index)

    def get_or_create(self, requirement: AssetRequirement, topic: str) -> tuple[Path, AssetMetadata]:
        prompt_hash = hash_value(requirement.source_prompt)
        asset_hash = hash_value({
            "asset_type": requirement.asset_type, "variant": requirement.variant,
            "label": requirement.label, "palette": self.config.style.palette,
            "stroke_width": self.config.style.stroke_width,
            "generator_version": self.config.svg.generator_version,
        })
        svg_path = self.cache_root / f"{asset_hash}.svg"
        metadata_path = self.cache_root / f"{asset_hash}.metadata.json"
        now = datetime.now(timezone.utc).isoformat()
        if svg_path.exists() and metadata_path.exists() and self.config.cache.reuse_assets:
            metadata = AssetMetadata.model_validate(read_json(metadata_path))
            metadata = metadata.model_copy(update={"reuse_counter": metadata.reuse_counter + 1, "updated_at": now})
            atomic_write_json(metadata_path, metadata)
            self._update_index(requirement.asset_id, asset_hash)
            self.logger.info("Cache hit %s (%s), reuse=%d", requirement.asset_id, requirement.asset_type, metadata.reuse_counter)
            return svg_path, metadata
        self.factory.generate(requirement, svg_path)
        if self.config.svg.validate_xml:
            self._validate_svg(svg_path)
        metadata = AssetMetadata(
            asset_hash=asset_hash, prompt_hash=prompt_hash, svg_hash=file_sha256(svg_path),
            asset_id=requirement.asset_id, asset_type=requirement.asset_type,
            source_prompt=requirement.source_prompt, topic=topic, created_at=now,
            updated_at=now, reuse_counter=0, embedding=None,
            generator_version=self.config.svg.generator_version,
        )
        atomic_write_json(metadata_path, metadata)
        self._update_index(requirement.asset_id, asset_hash)
        self.logger.info("Generated %s (%s)", requirement.asset_id, requirement.asset_type)
        return svg_path, metadata
