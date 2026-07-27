# Decisions

1. **Branch.** The blueprint asks for `claude/sias-agentic-colab-v2` when the
   current branch is protected. The session's designated working branch
   `claude/scientific-animation-studio-mvp-65rpn4` is NOT protected and the
   standing instruction for this session forbids pushing elsewhere — so the
   build lands there, in its own top-level directory. Nothing existing is
   replaced.
2. **Engine reuse, no duplication.** `sias_colab` imports the proven `sias`
   package (sibling `scientific-illustrated-audio-studio/src`) rather than
   copying its modules. A small path bootstrap (`sias_colab.bootstrap`) makes
   both `src` trees importable in all three source modes (standalone /
   github_repository / uploaded_zip).
3. **Third-party audit method.** GitHub's REST API is blocked by the egress
   proxy; raw.githubusercontent + `git ls-remote` are reachable. Licenses were
   fetched raw and commits pinned via ls-remote. No large repo was cloned; no
   live provider call was made. Every integration is therefore marked
   `live_verified: false` in `repo_sources.lock.json`.
4. **Higgsfield optional.** Adapter implemented against the documented client
   surface (HF_KEY / HF_API_KEY+HF_API_SECRET), mock-tested only. Disabled by
   default; SIAS runs fully without it. Virality Predictor `enabled: false`.
5. **Plan-mode notebook execution.** Paid sections use a guard that SKIPS (not
   raises) in plan mode, so the mandated top-to-bottom plan execution test can
   run the whole notebook with zero keys and zero paid calls.
6. **Placeholder rejection.** Placeholders are watermarked visibly AND stamped
   with a PNG tEXt marker (`sias_placeholder`); production QC rejects on the
   metadata marker, so a renamed placeholder still cannot pass.
7. **FLUX prompting.** Per current BFL practice, compiled prompts phrase
   desired outcomes positively; the forbidden-outcomes list is used as QC
   rejection criteria (and hard negatives remain a prompt block, not a separate
   negative-prompt parameter).
8. **Diamond routing (operator-directed).** Primaries stay OpenAI + BFL +
   OpenRouter Qwen VL 32B (structural) + Gemini 2.5 Flash (editorial). All
   open-source backends registered as fallback/second-opinion/objective/
   experimental tiers, default-disabled, license-gated in code. ADR-0009.
9. **LangGraph / PydanticAI / Prefect.** Structured contracts already enforced
   via pydantic across every agent boundary (no unvalidated dicts). The
   internal Supervisor covers DAG/checkpoint/resume today; LangGraph is a
   sanctioned optional migration path and Prefect is batch-scale-only — both
   deliberately NOT hard dependencies of the Colab notebook (kept lean per
   §18 isolation rule; roles recorded in license_matrix.md).
10. **Objective QC tiers are honest.** The always-available consistency tier is
    an explicitly-labeled PIL heuristic that only FLAGS drift; DINOv2/DreamSim/
    GroundingDINO/MMPose/SAM2/HPSv2 adapters activate when installed + license-
    cleared, with per-axis calibrated thresholds (never one universal number)
    and MMPose used as evidence, never a sole rejector.
