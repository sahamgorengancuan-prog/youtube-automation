from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageOps, ImageStat

from .schemas import (
    CandidateFrame,
    CandidateScore,
    CandidateTournamentResult,
    DrawingBrief,
    SceneIllustrationArchitecture,
    ShotState,
)
from .utils import ensure_dir, hash_value, save_json


class CandidateTournament:
    """Generate multiple low-resolution candidates, rank them, then revise the winner."""

    def __init__(self, llm: Any, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def run(
        self,
        brief: DrawingBrief,
        architecture: SceneIllustrationArchitecture,
        shot_state: ShotState,
        generator: Callable[[DrawingBrief], str | Path | None],
        *,
        force: bool = False,
    ) -> CandidateTournamentResult:
        # Adaptive mode (opt-in): generate one candidate first and, if it is
        # already strong on the deterministic quality prior, accept it without
        # spending a second image — so easy scenes cost one generation, not two.
        # With adaptive off the tournament keeps its two-candidate minimum so the
        # comparison ranking stays meaningful.
        requested = int(self.config.get("candidate_count", 4))
        adaptive = bool(self.config.get("adaptive_candidates", False))
        accept_score = float(self.config.get("adaptive_accept_score", 0.72))
        hard_max = max(1 if adaptive else 2, requested)
        candidates: list[CandidateFrame] = []
        for i in range(hard_max):
            variant = brief.model_copy(deep=True)
            variant.brief_id = f"{brief.brief_id}-C{i + 1:02d}"
            variant.seed = (brief.seed or 0) + i * 9973
            variant.output_path = str(self.root / "candidates" / f"{brief.scene_id}_C{i + 1:02d}.{brief.output_format}")
            variant.request_metadata = {**variant.request_metadata, "candidate_index": i, "tournament": True}
            path = generator(variant)
            if path:
                candidates.append(
                    CandidateFrame(
                        candidate_id=f"C{i + 1:02d}",
                        scene_id=brief.scene_id,
                        image_path=str(path),
                        seed=variant.seed,
                        prompt_hash=hash_value(variant.compiled_prompt or variant.positive_prompt, 20),
                        metrics=self._image_metrics(path),
                    )
                )
                # Early-accept: first strong candidate ends the tournament.
                if adaptive and self._fallback_score(candidates[-1]) >= accept_score:
                    break
        if not candidates:
            raise RuntimeError(f"Candidate tournament produced no frames for {brief.scene_id}")
        if len(candidates) == 1:
            # Adaptive single-candidate accept — skip the comparison board/rank.
            only = candidates[0]
            score = CandidateScore(
                candidate_id=only.candidate_id,
                total_score=round(self._fallback_score(only), 4),
                style_consistency=0.75,
                subject_consistency=0.75,
                composition_fitness=0.75,
                motion_readiness=0.75,
                causal_clarity=0.75,
                rationale="Adaptive single-candidate accept: the first candidate met the "
                "quality threshold, so no second image was generated.",
            )
            result = CandidateTournamentResult(
                scene_id=brief.scene_id,
                candidates=candidates,
                scores=[score],
                winner_id=only.candidate_id,
                winner_path=only.image_path,
                comparison_board="",
                ranking_source="adaptive_single",
            )
            save_json(self.root / f"{brief.scene_id}.json", result)
            return result
        board = self._board(candidates, brief.scene_id)
        scores, source = self._rank(board, candidates, architecture, shot_state, force=force)
        scores.sort(key=lambda x: (-x.total_score, x.candidate_id))
        winner = scores[0]
        winner_path = next(c.image_path for c in candidates if c.candidate_id == winner.candidate_id)
        result = CandidateTournamentResult(
            scene_id=brief.scene_id,
            candidates=candidates,
            scores=scores,
            winner_id=winner.candidate_id,
            winner_path=winner_path,
            comparison_board=board,
            ranking_source=source,
        )
        save_json(self.root / f"{brief.scene_id}.json", result)
        return result

    def _rank(
        self,
        board: str,
        candidates: list[CandidateFrame],
        architecture: SceneIllustrationArchitecture,
        shot_state: ShotState,
        *,
        force: bool,
    ) -> tuple[list[CandidateScore], str]:
        fallback = [
            CandidateScore(
                candidate_id=c.candidate_id,
                total_score=self._fallback_score(c),
                style_consistency=0.7,
                subject_consistency=0.7,
                composition_fitness=0.7,
                motion_readiness=0.7,
                causal_clarity=0.7,
                rationale="Deterministic fallback ranking from image information and motion-readiness priors.",
            )
            for c in candidates
        ]
        raw = self.llm.critique_image(
            image_path=board,
            namespace=f"v10_candidate_rank_{architecture.scene_id}",
            force=force,
            fallback={"scores": [x.model_dump(mode="json") for x in fallback]},
            prompt=f"""Rank candidate panels for one scientific editorial shot. Return JSON with scores only.
Architecture: {json.dumps(architecture.model_dump(mode="json"), ensure_ascii=False)}
Shot state: {json.dumps(shot_state.model_dump(mode="json"), ensure_ascii=False)}
Judge studio-style continuity, subject identity, coherent perspective, causal clarity, adult authored illustration,
and whether the first frame can reach the specified last frame without destructive redraw. Penalize AI artifacts,
maskot anatomy, sticker composition, random detail and hidden motion seams that will tear.""",
        )
        try:
            values = raw.get("scores", raw) if isinstance(raw, dict) else raw
            parsed = [CandidateScore.model_validate(x) for x in values]
            if {x.candidate_id for x in parsed} == {x.candidate_id for x in candidates}:
                return parsed, "vision-director"
        except Exception:
            pass
        return fallback, "deterministic-fallback"

    @staticmethod
    def _image_metrics(path: str | Path) -> dict[str, float]:
        image = Image.open(path).convert("RGB").resize((128, 128))
        stat = ImageStat.Stat(image)
        contrast = sum(stat.stddev) / 3 / 128
        entropy = sum(image.getchannel(c).entropy() for c in range(3)) / 3 / 8
        return {"contrast": float(contrast), "entropy": float(entropy)}

    @staticmethod
    def _fallback_score(c: CandidateFrame) -> float:
        return (
            0.55
            + 0.22 * c.metrics.get("entropy", 0)
            + 0.18 * c.metrics.get("contrast", 0)
            + (0.001 * (c.seed or 0) % 0.04)
        )

    def _board(self, candidates: list[CandidateFrame], scene_id: str) -> str:
        cols = 2
        rows = (len(candidates) + 1) // 2
        cw, ch = 600, 900
        canvas = Image.new("RGB", (cols * cw, rows * ch), "#E6E8E7")
        draw = ImageDraw.Draw(canvas)
        for i, c in enumerate(candidates):
            x = (i % cols) * cw
            y = (i // cols) * ch
            im = Image.open(c.image_path).convert("RGB")
            im = ImageOps.contain(im, (cw - 30, ch - 80))
            canvas.paste(im, (x + (cw - im.width) // 2, y + 45))
            draw.rectangle((x + 5, y + 5, x + cw - 5, y + ch - 5), outline="#475157", width=3)
            draw.text((x + 18, y + 15), f"{c.candidate_id} | SEED {c.seed}", fill="#20282D")
        out = self.root / "boards" / f"{scene_id}.png"
        ensure_dir(out.parent)
        canvas.save(out)
        return str(out)
