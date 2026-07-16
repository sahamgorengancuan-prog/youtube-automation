from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .asset_registry import AssetRegistry
from .schemas import AssetQuery, AssetRecord, RankedAsset, ReferenceSelection
from .utils import ensure_dir, save_json


class DynamicReferenceSelector:
    """ViMax-inspired retrieval selector using subject, environment, camera and chronology."""

    def __init__(self, registry: AssetRegistry, root: str | Path):
        self.registry = registry
        self.root = ensure_dir(root)

    def select(self, query: AssetQuery) -> ReferenceSelection:
        ranked: list[RankedAsset] = []
        for asset in self.registry.list(approved_only=True):
            score, reasons = self._score(asset, query)
            if score > 0:
                ranked.append(RankedAsset(asset=asset, score=score, reasons=reasons))
        ranked.sort(key=lambda x: (-x.score, x.asset.asset_id))
        chosen = self._diversify(ranked, query.maximum_results)
        selection = ReferenceSelection(scene_id=query.scene_id, query=query, selected=chosen)
        selection.board_path = self.build_board(selection)
        save_json(self.root / f"{query.scene_id}.json", selection)
        return selection

    @staticmethod
    def _score(asset: AssetRecord, query: AssetQuery) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        if query.desired_types and asset.asset_type in query.desired_types:
            score += 2.0
            reasons.append("desired asset type")
        shared = set(query.subject_ids) & set(asset.subject_ids)
        if shared:
            score += 4.0 + 0.5 * len(shared)
            reasons.append("subject identity")
        if query.environment_id and asset.environment_id == query.environment_id:
            score += 3.0
            reasons.append("environment identity")
        if query.camera_view and asset.camera_view == query.camera_view:
            score += 2.5
            reasons.append("camera match")
        if query.perspective and asset.perspective == query.perspective:
            score += 1.5
            reasons.append("perspective match")
        if query.style_hash and asset.style_hash == query.style_hash:
            score += 3.0
            reasons.append("style fingerprint")
        if query.required_roles and set(query.required_roles) & set(asset.role_tags):
            score += 1.5
            reasons.append("role match")
        if query.chronology_index >= 0 and asset.chronology_index >= 0:
            delta = query.chronology_index - asset.chronology_index
            if 0 <= delta <= 2:
                score += 2.0 - 0.4 * delta
                reasons.append("recent approved continuity")
            elif delta < 0:
                return -1000.0, ["future continuity excluded"]
        score += min(1.0, max(0.0, asset.quality_score))
        return score, reasons

    @staticmethod
    def _diversify(ranked: list[RankedAsset], limit: int) -> list[RankedAsset]:
        selected: list[RankedAsset] = []
        type_counts: dict[str, int] = {}
        for item in ranked:
            t = item.asset.asset_type
            cap = 3 if t in {"approved_scene", "subject_view"} else 2
            if type_counts.get(t, 0) >= cap:
                continue
            selected.append(item)
            type_counts[t] = type_counts.get(t, 0) + 1
            if len(selected) >= limit:
                break
        return selected

    def build_board(self, selection: ReferenceSelection) -> str:
        output = self.root / "boards" / f"{selection.scene_id}.png"
        ensure_dir(output.parent)
        items = [x for x in selection.selected if Path(x.asset.path).exists()][:8]
        if not items:
            return ""
        w, h = 1600, 1200
        board = Image.new("RGB", (w, h), "#FAFAF7")
        draw = ImageDraw.Draw(board)
        cols, rows = 4, 2
        cw, ch = w // cols, h // rows
        for i, item in enumerate(items):
            x0 = (i % cols) * cw
            y0 = (i // cols) * ch
            image = Image.open(item.asset.path).convert("RGB")
            image = ImageOps.contain(image, (cw - 30, ch - 80))
            board.paste(image, (x0 + (cw - image.width) // 2, y0 + 15))
            draw.rectangle((x0 + 5, y0 + 5, x0 + cw - 5, y0 + ch - 5), outline="#475157", width=2)
            label = f"{item.asset.asset_type} | {item.score:.1f} | {item.asset.asset_id}"[:54]
            draw.rectangle((x0 + 8, y0 + ch - 54, x0 + cw - 8, y0 + ch - 8), fill="#FAFAF7", outline="#475157")
            draw.text((x0 + 15, y0 + ch - 43), label, fill="#20282D")
        board.save(output)
        return str(output)
