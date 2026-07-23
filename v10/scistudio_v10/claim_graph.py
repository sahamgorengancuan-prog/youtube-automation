"""Scientific Claim Graph — evidence lineage for every on-screen statement.

The differentiator: a video whose visuals *look* scientific but whose narration
is not actually supported by evidence is worse than no video. This assembles the
lineage

    Scene -> Beat (spoken sentence) -> Scientific claim -> Evidence (Fact+Source)
                                                              -> confidence
                                                          -> Visualization

from artifacts the pipeline already authors (ResearchPack facts/sources with
confidence, ScriptPackage beats with evidence_refs, Storyboard scenes with a
scientific_claim + visual_event), so it is deterministic and runs offline — no
extra model call. It then *validates* the graph and flags:

* ``unsupported_claim``     — a scene states a scientific claim with no supporting
  evidence;
* ``low_confidence``        — the claim's aggregate evidence confidence is weak;
* ``visual_without_claim``  — a visual event that is not tied to any claim (risk of
  a scientific-looking but unsupported visual);
* ``unsourced_evidence``    — an evidence ref that resolves to a fact with no source.

A production run can WARN or (with ``require_evidence``) fail on high-importance
unsupported claims, so a misleading visualization is caught before publish.
"""

from __future__ import annotations

from typing import Any

from .utils import ensure_dir, save_json


def _supports_from_confidence(conf: float) -> str:
    if conf >= 0.8:
        return "full"
    if conf >= 0.5:
        return "partial"
    return "weak"


def _source_type(source: Any) -> str:
    provider = str(getattr(source, "provider", "") or "").lower()
    authority = float(getattr(source, "authority_score", 0.0) or 0.0)
    if (
        "openalex" in provider
        or "crossref" in provider
        or "pubmed" in provider
        or "semantic" in provider
        or authority >= 0.8
    ):
        return "peer_reviewed"
    if "arxiv" in provider or "preprint" in provider:
        return "preprint"
    if "wiki" in provider:
        return "encyclopedic"
    return "web"


class ScientificClaimGraph:
    def __init__(self, config: dict[str, Any] | None = None, root: Any = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None

    def build(self, research: Any, script: Any, storyboard: Any) -> dict[str, Any]:
        facts = {getattr(f, "fact_id", str(i)): f for i, f in enumerate(getattr(research, "facts", []) or [])}
        # Also index facts by a normalized claim text for evidence_refs that name
        # the claim rather than the id.
        facts_by_text = {_norm(getattr(f, "claim", "")): f for f in facts.values()}
        sources = {getattr(s, "source_id", str(i)): s for i, s in enumerate(getattr(research, "sources", []) or [])}
        beats = {getattr(b, "beat_id", str(i)): b for i, b in enumerate(getattr(script, "beats", []) or [])}

        claims: list[dict[str, Any]] = []
        for scene in getattr(storyboard, "scenes", []) or []:
            beat = beats.get(getattr(scene, "beat_id", ""))
            sentence = (getattr(beat, "spoken_line", "") if beat else "") or getattr(scene, "narration", "")
            claim_text = getattr(scene, "scientific_claim", "") or ""
            visualization = getattr(scene, "visual_event", "") or getattr(scene, "desired_change", "")
            refs = list(getattr(beat, "evidence_refs", []) or []) if beat else []

            evidence: list[dict[str, Any]] = []
            confidences: list[float] = []
            for ref in refs:
                fact = facts.get(ref) or facts_by_text.get(_norm(str(ref)))
                if fact is None:
                    continue
                conf = float(getattr(fact, "confidence", 0.0) or 0.0)
                confidences.append(conf)
                src_ids = list(getattr(fact, "source_ids", []) or [])
                if not src_ids:
                    evidence.append(
                        {
                            "fact_id": getattr(fact, "fact_id", ref),
                            "source_id": "",
                            "source_type": "none",
                            "supports": "unsourced",
                            "confidence": conf,
                        }
                    )
                for sid in src_ids:
                    src = sources.get(sid)
                    evidence.append(
                        {
                            "fact_id": getattr(fact, "fact_id", ref),
                            "source_id": sid,
                            "source_type": _source_type(src) if src else "unknown",
                            "authority": float(getattr(src, "authority_score", 0.0) or 0.0) if src else 0.0,
                            "supports": _supports_from_confidence(conf),
                            "confidence": conf,
                        }
                    )
            claim_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
            importance = self._importance(beat, claim_conf)
            claims.append(
                {
                    "claim_id": f"C_{getattr(scene, 'scene_id', len(claims) + 1)}",
                    "scene_id": getattr(scene, "scene_id", ""),
                    "beat_id": getattr(scene, "beat_id", ""),
                    "sentence": sentence,
                    "text": claim_text,
                    "importance": importance,
                    "visualization": visualization,
                    "evidence": evidence,
                    "confidence": claim_conf,
                }
            )

        report = self.validate(claims)
        graph = {"claims": claims, "validation": report}
        if self.root is not None:
            save_json(self.root / "claim_graph.json", graph)
        return graph

    @staticmethod
    def _importance(beat: Any, conf: float) -> str:
        role = str(getattr(beat, "retention_function", "") or getattr(beat, "purpose", "")).lower()
        if any(k in role for k in ("hook", "payoff", "key", "reveal", "thesis", "climax")):
            return "high"
        return "high" if conf >= 0.85 else "medium"

    def validate(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        min_conf = float(self.config.get("claim_min_confidence", 0.5))
        issues: list[dict[str, Any]] = []
        for cl in claims:
            supported = [e for e in cl["evidence"] if e.get("supports") in ("full", "partial")]
            if cl["text"].strip() and not supported:
                issues.append(
                    {
                        "claim_id": cl["claim_id"],
                        "scene_id": cl["scene_id"],
                        "issue": "unsupported_claim",
                        "importance": cl["importance"],
                    }
                )
            elif cl["text"].strip() and cl["confidence"] < min_conf:
                issues.append(
                    {
                        "claim_id": cl["claim_id"],
                        "scene_id": cl["scene_id"],
                        "issue": "low_confidence",
                        "confidence": cl["confidence"],
                    }
                )
            if cl["visualization"].strip() and not cl["text"].strip():
                issues.append({"claim_id": cl["claim_id"], "scene_id": cl["scene_id"], "issue": "visual_without_claim"})
            for e in cl["evidence"]:
                if e.get("supports") == "unsourced":
                    issues.append(
                        {
                            "claim_id": cl["claim_id"],
                            "scene_id": cl["scene_id"],
                            "issue": "unsourced_evidence",
                            "fact_id": e.get("fact_id"),
                        }
                    )
        high_unsupported = [i for i in issues if i["issue"] == "unsupported_claim" and i.get("importance") == "high"]
        return {
            "claim_count": len(claims),
            "issue_count": len(issues),
            "issues": issues,
            "high_importance_unsupported": [i["claim_id"] for i in high_unsupported],
            "ok": not high_unsupported,
        }


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())[:120]
