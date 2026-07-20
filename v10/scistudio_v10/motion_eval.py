"""Separated evaluation of an authored animation plan.

Motion energy (pixels changing) does not prove animation quality — rain, a
moving camera and scrolling text all raise it without the *object* actually
animating. So we evaluate the authored DSL structurally along five independent
axes and gate on **causal clarity**, not on how much the frame moves.

Axes:
* object_motion   — the object that carries the causal action actually changes state
* state_change    — that change is a real before→after (pose/mask/deformation), not a loop
* camera_motion   — a camera move was authored (supporting only)
* particle_motion — a physical effect was authored (supporting only)
* text_motion     — a caption/HUD was authored (supporting only)

Primary gate: ``causal_clarity`` — the plan states what changes (causal_summary)
AND shows it (object state change), or is a deliberate, declared hold.
"""

from __future__ import annotations

from typing import Any

_STATE_CHANGE_REPRESENTATIONS = {"replacement_pose", "mask_reveal", "local_deformation"}


def _get(plan: Any, key: str, default: Any = None) -> Any:
    if isinstance(plan, dict):
        return plan.get(key, default)
    return getattr(plan, key, default)


def evaluate_plan(plan: Any) -> dict[str, Any]:
    """Return the five separated motion axes plus the causal-clarity verdict."""
    events = _get(plan, "events", []) or []

    def rep(event: Any) -> str:
        return str(_get(event, "representation", ""))

    def is_secondary(event: Any) -> bool:
        return bool(_get(event, "secondary", False))

    # A *primary* (non-secondary) event must carry the object motion. A shot
    # whose only events are secondary is NOT primary object motion.
    object_motion = any(not is_secondary(e) for e in events)
    # NOTE: this is *planned* state change inferred from the representation name.
    # It is NOT proof the render actually moved anything — post-render vision QC
    # sets ``render_verified`` from real frame evidence.
    state_change_planned = any(rep(e) in _STATE_CHANGE_REPRESENTATIONS and not is_secondary(e) for e in events)

    camera = _get(plan, "camera")
    camera_motion = bool(
        camera is not None
        and str(_get(camera, "move", "hold")) != "hold"
        and float(_get(camera, "magnitude", 0.0) or 0.0) > 0.0
    )
    effects = _get(plan, "effects", []) or []
    particle_motion = any(
        str(_get(fx, "effect", "none")) != "none" and float(_get(fx, "intensity", 0.0) or 0.0) > 0.0 for fx in effects
    )
    captions = _get(plan, "captions", []) or []
    text_motion = any(
        str(_get(cap, "kind", "none")) != "none" and str(_get(cap, "text", "")).strip() for cap in captions
    )

    causal_summary = str(_get(plan, "causal_summary", "")).strip()
    supporting_motion = camera_motion or particle_motion or text_motion
    # Planned clarity: the plan declares the change AND schedules a primary
    # object state change to show it. This is a *necessary* condition, not proof.
    causal_clarity_planned = bool(causal_summary) and state_change_planned
    # A *declared hold* is a summary with NO motion of any kind. A summary with
    # only supporting (camera/particle/text) motion is NOT a hold — it's the
    # anti-pattern this evaluator exists to catch.
    declared_hold = bool(causal_summary) and not object_motion and not supporting_motion

    return {
        "object_motion": object_motion,
        "state_change_planned": state_change_planned,
        "camera_motion": camera_motion,
        "particle_motion": particle_motion,
        "text_motion": text_motion,
        "causal_clarity_planned": causal_clarity_planned,
        # Set only by post-render vision QC from real frame evidence (None = not run).
        "render_verified": None,
        "declared_hold": declared_hold,
        "primary_carries_motion": object_motion,
        # Supporting motion must never be the *only* motion in an action shot.
        "supporting_only_warning": supporting_motion and not object_motion,
    }


def causal_clarity_ok(plan: Any) -> bool:
    """Primary quality gate: the shot either clearly shows the causal state
    change, or is an explicitly declared hold. A shot that only moves the
    camera / particles / text without object motion or a declared hold fails."""
    report = evaluate_plan(plan)
    if report["supporting_only_warning"]:
        return False
    return report["causal_clarity_planned"] or report["declared_hold"]
