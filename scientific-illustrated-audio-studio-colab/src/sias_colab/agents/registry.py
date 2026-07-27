"""The 20 SIAS agents, each a thin single-responsibility wrapper delegating to
the tested `sias` engine. Free agents run offline; paid agents require an
armed supervisor + adapters and otherwise refuse explicitly."""

from __future__ import annotations

from typing import Any

from sias.config import SIASConfig
from sias.research.claims import build_research_pack
from sias.research.source_guard import validate_pack
from sias.schemas import ResearchPack, SceneSpec, SpokenScript, StoryBeat
from sias.story.beats import build_story_beats, validate_beats
from sias.story.spoken_script import assemble_script
from sias.story.validators import validate_story
from sias.style.bible import build_style_bible
from sias.style.prompt_compiler import compile_prompt
from sias.style.reference_pack import select_references
from sias.vision.consensus import combine
from sias.vision.repair import build_repair_request, repair_prompt
from sias.vision.tournament import candidate_count, stable_seed

from ..story.hooks_ext import generate_hooks_v2, select_hook_v2
from ..story.retention_critic import critique_and_revise
from ..supervisor import Agent


def _pack(ctx: dict[str, Any]) -> ResearchPack:
    return ResearchPack.model_validate(ctx["research_planner"]["pack"])


def build_free_agents(cfg: SIASConfig) -> list[Agent]:
    """The offline (cost_class=free) agent set that powers plan mode."""

    def source_auditor(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"note": "see docs/source_audit.md + repo_sources.lock.json", "video_first_components": "rejected"}

    def research_planner(ctx: dict[str, Any]) -> dict[str, Any]:
        pack = build_research_pack(cfg.project.topic, cfg.project.audience, cfg.story.facts_count, ctx.get("llm"))
        return {"pack": pack.model_dump()}

    def fact_verifier(ctx: dict[str, Any]) -> dict[str, Any]:
        pack = _pack(ctx)
        checks = [c.model_dump() for c in validate_pack(pack)]
        unverified = [c.claim_id for c in pack.claims if not c.sources]
        return {"checks": checks, "unverified_claims": unverified,
                "note": "unsourced claims are marked unverified, never presented as facts"}

    def hook_tournament(ctx: dict[str, Any]) -> dict[str, Any]:
        hooks = generate_hooks_v2(cfg.project.topic, ctx.get("llm"))
        return {"hooks": [h.model_dump() for h in hooks], "selected": select_hook_v2(hooks).model_dump()}

    def story_architect(ctx: dict[str, Any]) -> dict[str, Any]:
        beats = build_story_beats(_pack(ctx), ctx.get("llm"))
        issues = validate_beats(beats, cfg.story.min_scenes, cfg.story.max_scenes)
        if issues:
            raise ValueError("; ".join(issues))
        return {"beats": [b.model_dump() for b in beats]}

    def retention_critic(ctx: dict[str, Any]) -> dict[str, Any]:
        beats = [StoryBeat.model_validate(b) for b in ctx["story_architect"]["beats"]]
        script = assemble_script(beats, cfg.story.catchphrase, cfg.project.language)
        result = critique_and_revise(script, beats, cfg)
        return result

    def visual_director(ctx: dict[str, Any]) -> dict[str, Any]:
        from sias.pipeline.orchestrator import build_scene_specs

        beats = [StoryBeat.model_validate(b) for b in ctx["story_architect"]["beats"]]
        scenes = build_scene_specs(beats)
        return {"scenes": [s.model_dump() for s in scenes]}

    def style_canon_guardian(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"style_bible": build_style_bible(cfg).model_dump()}

    def reference_pack_selector(ctx: dict[str, Any]) -> dict[str, Any]:
        scenes = [SceneSpec.model_validate(s) for s in ctx["visual_director"]["scenes"]]
        return {"selections": [select_references(s, ctx.get("reference_assets", []), cfg.visual.max_reference_images) for s in scenes]}

    def prompt_compiler(ctx: dict[str, Any]) -> dict[str, Any]:
        from sias.schemas import StyleBible

        bible = StyleBible.model_validate(ctx["style_canon_guardian"]["style_bible"])
        scenes = [SceneSpec.model_validate(s) for s in ctx["visual_director"]["scenes"]]
        compiled = {s.scene_id: compile_prompt(s, bible) for s in scenes}
        return {"prompts": {k: {"prompt_hash": v["prompt_hash"], "blocks": list(v["blocks"])} for k, v in compiled.items()},
                "full": compiled}

    def timeline_planner(ctx: dict[str, Any]) -> dict[str, Any]:
        script = SpokenScript.model_validate(ctx["retention_critic"]["script"])
        return {"est_duration_s": script.est_duration_s,
                "note": "FINAL timings come only from generated-narration timestamps (audio is the source of truth)"}

    def story_qc(ctx: dict[str, Any]) -> dict[str, Any]:
        beats = [StoryBeat.model_validate(b) for b in ctx["story_architect"]["beats"]]
        script = SpokenScript.model_validate(ctx["retention_critic"]["script"])
        return {"checks": [c.model_dump() for c in validate_story(script, beats, cfg)]}

    return [
        Agent("source_auditor", "inspect existing implementation and third-party repos", [], source_auditor),
        Agent("research_planner", "define claims to verify; never fabricates facts offline", [], research_planner),
        Agent("fact_verifier", "claim pack with source/confidence/caveat/visualizability", ["research_planner"], fact_verifier),
        Agent("hook_tournament", "consequence/contradiction/scale hooks, scored", ["research_planner"], hook_tournament),
        Agent("story_architect", "retention-first 8-beat narrative", ["hook_tournament"], story_architect),
        Agent("retention_critic", "detect slow open/weak escalation; ONE bounded revision", ["story_architect"], retention_critic),
        Agent("visual_director", "one concrete visual objective per beat", ["retention_critic"], visual_director),
        Agent("style_canon_guardian", "maintain the SIAS visual identity", [], style_canon_guardian),
        Agent("reference_pack_selector", "select only relevant references", ["visual_director", "style_canon_guardian"], reference_pack_selector),
        Agent("prompt_compiler", "structured scene spec -> provider prompt", ["reference_pack_selector"], prompt_compiler),
        Agent("timeline_planner", "estimate only; real timing from narration", ["retention_critic"], timeline_planner),
        Agent("story_qc", "story-level QC roll-up", ["retention_critic", "story_architect"], story_qc),
    ]


def paid_agent_stubs() -> list[Agent]:
    """Paid agents (tournament/reviews/repair/TTS/align/render/final QC) are
    registered with cost_class='paid'; the supervisor refuses them un-armed.
    Their run functions require adapters in context and fail explicitly."""

    def needs_adapter(name: str):
        def run(ctx: dict[str, Any]) -> dict[str, Any]:
            adapter = ctx.get("adapters", {}).get(name)
            if adapter is None:
                raise RuntimeError(f"adapter {name!r} not configured — refusing to fake a paid result")
            return {"adapter": name, "note": "executed via notebook live sections"}

        return run

    specs = [
        ("candidate_tournament", "generate + rank image candidates", ["prompt_compiler"], "bfl"),
        ("qwen_structural_reviewer", "anatomy/limbs/diagram/science checks", ["candidate_tournament"], "openrouter"),
        ("gemini_editorial_reviewer", "interest/identity/humor/reveal checks", ["candidate_tournament"], "openrouter"),
        ("consensus_judge", "combine reviewers; hard fail vetoes", ["qwen_structural_reviewer", "gemini_editorial_reviewer"], "openrouter"),
        ("repair_agent", "conservative local repair, max 2/scene", ["consensus_judge"], "bfl"),
        ("audio_director", "one fluid narration track", ["retention_critic"], "openai_audio"),
        ("pronunciation_agent", "names/terms/pauses/catchphrase delivery", ["audio_director"], "openai_audio"),
        ("timeline_aligner", "scene timing from narration timestamps", ["pronunciation_agent"], "openai_audio"),
        ("render_engineer", "deterministic still composition (ffmpeg)", ["timeline_aligner"], None),
        ("final_qc_auditor", "story/visual/audio/timeline/streams/publication", ["render_engineer"], None),
    ]
    agents = []
    for aid, desc, deps, adapter in specs:
        cost = "paid" if adapter else "free"
        agents.append(Agent(aid, desc, deps, needs_adapter(adapter) if adapter else (lambda ctx: {"note": "local stage; run via notebook"}), cost_class=cost))
    return agents


CONSENSUS = combine
CANDIDATE_COUNT = candidate_count
STABLE_SEED = stable_seed
BUILD_REPAIR = build_repair_request
REPAIR_PROMPT = repair_prompt
