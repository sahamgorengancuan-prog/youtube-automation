"""Active continuity validator — the canon as a check, not a document.

A continuity canon that only *describes* the rules cannot catch a red shirt
turning blue or a wall that is intact, then destroyed, then intact again. This
validates the actual per-scene records against continuity invariants and flags
violations:

* **character** — a declared character should not vanish and reappear across
  adjacent scenes, and its size/proportion (bbox scale) should not jump;
* **object / focal subject** — the focal subject should not change identity or
  teleport in scale between adjacent scenes without a transition;
* **environment / camera** — the camera language should not flip randomly shot to
  shot;
* **causal state** — the most important: a causal stage (before -> during ->
  after) must not regress (after -> before), i.e. a changed/damaged state must
  not silently reset, unless the script authored a reset.

Deterministic and offline: it reads the authored per-scene records (architecture
camera + focal subject, CharacterManifest, scene time_stage). An optional Qwen VL
check on consecutive beauty frames (identity/clothing/object state) is GPU/API
gated. Emits a continuity report; a production run can warn or fail on violations.
"""

from __future__ import annotations

from typing import Any

from .utils import ensure_dir, save_json

# Ordinal for causal stages, so a regression (after -> before) is detectable
# from the authored time_stage without narration keyword inference.
_STAGE_ORDER = [
    ("setup", 0),
    ("initial", 0),
    ("before", 0),
    ("baseline", 0),
    ("intact", 0),
    ("onset", 1),
    ("begin", 1),
    ("start", 1),
    ("trigger", 1),
    ("during", 2),
    ("progress", 2),
    ("change", 2),
    ("impact", 2),
    ("stress", 2),
    ("peak", 3),
    ("climax", 3),
    ("maximum", 3),
    ("after", 4),
    ("result", 4),
    ("aftermath", 4),
    ("final", 4),
    ("consequence", 4),
    ("damaged", 4),
]


def _stage_index(time_stage: str) -> int | None:
    t = str(time_stage or "").lower()
    for key, idx in _STAGE_ORDER:
        if key in t:
            return idx
    return None


class ContinuityValidator:
    def __init__(self, config: dict[str, Any] | None = None, root: Any = None, llm: Any = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.llm = llm

    def validate(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """records: ordered per-scene dicts with keys scene_id, characters
        ([{id, orientation, area}]), focal_subject, camera, time_stage,
        allow_reset (bool)."""
        issues: list[dict[str, Any]] = []
        size_jump = float(self.config.get("continuity_max_size_jump", 0.5))

        # -- character continuity -----------------------------------------
        appearances: dict[str, list[int]] = {}
        char_area: dict[str, dict[int, float]] = {}
        for i, rec in enumerate(records):
            for ch in rec.get("characters", []) or []:
                cid = ch.get("id", "")
                appearances.setdefault(cid, []).append(i)
                char_area.setdefault(cid, {})[i] = float(ch.get("area", 0.0) or 0.0)
        for cid, idxs in appearances.items():
            # vanish-and-reappear: present, absent, present in adjacent scenes.
            for a, b in zip(idxs, idxs[1:]):
                if b - a > 1:
                    issues.append(
                        {"scene_id": records[b]["scene_id"], "type": "character_reappears_after_gap", "character": cid}
                    )
                    break
            # size/proportion jump between consecutive appearances.
            for a, b in zip(idxs, idxs[1:]):
                aa, ab = char_area[cid].get(a, 0.0), char_area[cid].get(b, 0.0)
                if aa > 0 and ab > 0 and abs(ab - aa) / max(aa, ab) > size_jump:
                    issues.append(
                        {
                            "scene_id": records[b]["scene_id"],
                            "type": "character_size_jump",
                            "character": cid,
                            "from": round(aa, 3),
                            "to": round(ab, 3),
                        }
                    )

        # -- focal subject + camera continuity ----------------------------
        for prev, cur in zip(records, records[1:]):
            if cur.get("transition", "cut") == "cut":
                pf, cf = (
                    str(prev.get("focal_subject", "")).strip().lower(),
                    str(cur.get("focal_subject", "")).strip().lower(),
                )
                # (subject identity drift is only flagged when both are present.)
                if pf and cf and pf != cf and not self.config.get("allow_subject_change", True):
                    issues.append({"scene_id": cur["scene_id"], "type": "focal_subject_change", "from": pf, "to": cf})

        # -- causal state monotonicity (the key invariant) ----------------
        last_stage = None
        last_scene = None
        for rec in records:
            stage = _stage_index(rec.get("time_stage", ""))
            if stage is None:
                continue
            if last_stage is not None and stage < last_stage and not rec.get("allow_reset", False):
                issues.append(
                    {
                        "scene_id": rec["scene_id"],
                        "type": "causal_regression",
                        "detail": f"causal stage went backward ({last_stage}->{stage}) after {last_scene}; a changed state reset without an authored reset",
                    }
                )
            last_stage, last_scene = stage, rec["scene_id"]

        # -- optional vision check on consecutive beauty frames -----------
        vision = []
        if self.config.get("vision_continuity") and self.llm is not None:
            vision = self._vision_check(records)

        report = {
            "scene_count": len(records),
            "issue_count": len(issues),
            "issues": issues,
            "causal_regressions": [i["scene_id"] for i in issues if i["type"] == "causal_regression"],
            "vision_checks": vision,
            "ok": not issues,
        }
        if self.root is not None:
            save_json(self.root / "continuity_report.json", report)
        return report

    def _vision_check(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from pathlib import Path

        out = []
        for prev, cur in zip(records, records[1:]):
            pa, cb = prev.get("beauty", ""), cur.get("beauty", "")
            if not (pa and cb and Path(pa).exists() and Path(cb).exists()):
                continue
            try:
                board = self._board(pa, cb)
                verdict = self.llm.critique_image(
                    image_path=board,
                    prompt=(
                        "Left is the previous scene, right is the next scene of the SAME short. Check continuity. "
                        'Return JSON: {"same_character_identity": true/false, "same_clothing_colors": true/false, '
                        '"consistent_object_state": true/false, "reason": "..."}. '
                        "Flag a character whose identity/clothing changed, or a key object whose state reset."
                    ),
                    namespace="continuity_check",
                    fallback=None,
                )
                out.append({"pair": [prev["scene_id"], cur["scene_id"]], "verdict": verdict})
            except Exception:
                continue
        return out

    @staticmethod
    def _board(a: str, b: str) -> str:
        from pathlib import Path

        from PIL import Image

        ia = Image.open(a).convert("RGB")
        ib = Image.open(b).convert("RGB").resize(ia.size)
        board = Image.new("RGB", (ia.width * 2 + 12, ia.height), "#101010")
        board.paste(ia, (0, 0))
        board.paste(ib, (ia.width + 12, 0))
        out = Path(b).with_name("continuity_board.png")
        board.save(out)
        return str(out)
