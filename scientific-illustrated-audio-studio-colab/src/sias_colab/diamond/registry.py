"""Routed service registry — every repository has a clearly limited role.

13 services, each with ONE default backend (the commercial primary stack),
optional fallbacks/second opinions, license status, hardware requirements,
health check and capability report. No model may approve its own output:
the registry refuses a routing where the same backend is visual architect
AND sole QC reviewer AND final judge.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from ..exceptions import ConfigurationError

LICENSE_STATUSES = (
    "COMMERCIAL_OK",
    "COMMERCIAL_REVIEW_REQUIRED",
    "NONCOMMERCIAL_ONLY",
    "RESEARCH_ONLY",
    "UNKNOWN_BLOCKED",
)

TIERS = ("primary", "fallback", "second_opinion", "objective", "experimental")

SERVICES = [
    "StoryArchitectService",
    "VisualArchitectService",
    "ImageGenerationService",
    "ConsistencyService",
    "VisionQCService",
    "RepairService",
    "TTSService",
    "AudioRepairService",
    "AlignmentService",
    "SubtitleService",
    "MotionGraphicsService",
    "RenderService",
    "FinalAuditService",
]


class BackendDescriptor(BaseModel):
    name: str
    service: str
    tier: str = "fallback"
    repo: str = ""
    code_license: str = ""
    weight_license: str = ""
    license_status: str = "UNKNOWN_BLOCKED"
    min_hardware: str = "cpu"  # cpu | t4_16gb | gpu_24gb | gpu_48gb
    default_enabled: bool = False
    roles: list[str] = Field(default_factory=list)
    known_limitations: str = ""
    availability_check: Any = None  # callable -> bool (module/key presence)

    def available(self) -> bool:
        if self.availability_check is None:
            return False
        try:
            return bool(self.availability_check())
        except Exception:
            return False

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "service": self.service,
            "tier": self.tier,
            "available": self.available(),
            "license_status": self.license_status,
            "enabled": self.default_enabled,
            "min_hardware": self.min_hardware,
        }


class ServiceRegistry:
    def __init__(self, hardware_profile: str = "cpu"):
        self.hardware_profile = hardware_profile
        self.backends: dict[str, list[BackendDescriptor]] = {s: [] for s in SERVICES}

    def register(self, backend: BackendDescriptor) -> None:
        if backend.service not in self.backends:
            raise ConfigurationError(f"unknown service {backend.service!r}", stage="registry")
        if backend.tier not in TIERS:
            raise ConfigurationError(f"unknown tier {backend.tier!r}", stage="registry")
        if backend.license_status not in LICENSE_STATUSES:
            raise ConfigurationError(f"unknown license status {backend.license_status!r}", stage="registry")
        self.backends[backend.service].append(backend)

    # ---- routing -----------------------------------------------------------
    _HW_ORDER = {"cpu": 0, "t4_16gb": 1, "gpu_24gb": 2, "gpu_48gb": 3}

    def _hardware_ok(self, backend: BackendDescriptor) -> bool:
        return self._HW_ORDER.get(backend.min_hardware, 3) <= self._HW_ORDER.get(self.hardware_profile, 0)

    def eligible(self, service: str) -> list[BackendDescriptor]:
        """Enabled + license-safe + hardware-compatible + available, tier-ordered."""
        order = {t: i for i, t in enumerate(TIERS)}
        out = []
        for backend in sorted(self.backends[service], key=lambda b: order.get(b.tier, 9)):
            if not backend.default_enabled:
                continue
            if backend.license_status in ("NONCOMMERCIAL_ONLY", "RESEARCH_ONLY", "UNKNOWN_BLOCKED"):
                continue
            if not self._hardware_ok(backend):
                continue
            if not backend.available():
                continue
            out.append(backend)
        return out

    def route(self, service: str) -> BackendDescriptor:
        eligible = self.eligible(service)
        if not eligible:
            skipped = self.explain_skips(service)
            raise ConfigurationError(
                f"no eligible backend for {service}; skipped: {skipped}", stage="registry"
            )
        return eligible[0]

    def second_opinions(self, service: str) -> list[BackendDescriptor]:
        return [b for b in self.eligible(service) if b.tier in ("second_opinion", "objective")]

    def explain_skips(self, service: str) -> list[dict[str, str]]:
        """Why each registered backend was or wasn't used — shown in the UI."""
        reasons = []
        for backend in self.backends[service]:
            if not backend.default_enabled:
                reason = "disabled by default (enable deliberately)"
            elif backend.license_status in ("NONCOMMERCIAL_ONLY", "RESEARCH_ONLY", "UNKNOWN_BLOCKED"):
                reason = f"license status {backend.license_status} — blocked for production"
            elif not self._hardware_ok(backend):
                reason = f"needs {backend.min_hardware}, profile is {self.hardware_profile}"
            elif not backend.available():
                reason = "dependencies/keys not present"
            else:
                reason = "ELIGIBLE"
            reasons.append({"backend": backend.name, "tier": backend.tier, "reason": reason})
        return reasons

    def capability_report(self) -> dict[str, Any]:
        return {
            "hardware_profile": self.hardware_profile,
            "services": {
                s: {
                    "eligible": [b.name for b in self.eligible(s)],
                    "all": self.explain_skips(s),
                }
                for s in SERVICES
            },
        }

    # ---- independence rule ---------------------------------------------------
    def assert_no_self_approval(self) -> None:
        """The visual architect backend must not also be the sole QC reviewer."""
        try:
            architect = self.route("VisualArchitectService").name
        except ConfigurationError:
            return
        qc = [b.name for b in self.eligible("VisionQCService")]
        if qc and all(name == architect for name in qc):
            raise ConfigurationError(
                f"backend {architect!r} would be visual architect AND sole QC reviewer — "
                "correlated evaluation errors; add an independent reviewer",
                stage="registry",
            )


def _env(name: str) -> Callable[[], bool]:
    import os

    return lambda: bool(os.environ.get(name))


def _importable(module: str) -> Callable[[], bool]:
    import importlib.util

    return lambda: importlib.util.find_spec(module) is not None


def build_default_registry(hardware_profile: str = "cpu") -> ServiceRegistry:
    """The operator-directed routing: commercial primaries, open-source as
    fallback/second-opinion/objective. Open weights stay disabled until their
    weight license is audited in model_weights.lock.json."""
    reg = ServiceRegistry(hardware_profile)
    B = BackendDescriptor

    # ---- primaries (current stack; enabled) --------------------------------
    reg.register(B(name="openai-gpt", service="StoryArchitectService", tier="primary",
                   repo="openai/openai-python", code_license="Apache-2.0",
                   license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["research", "beats", "script"], availability_check=_env("OPENAI_API_KEY")))
    reg.register(B(name="qwen3-vl-32b@openrouter", service="VisualArchitectService", tier="primary",
                   repo="QwenLM/Qwen3-VL", code_license="Apache-2.0", weight_license="hosted",
                   license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["scene_planning"], availability_check=_env("OPENROUTER_API_KEY")))
    reg.register(B(name="bfl-flux", service="ImageGenerationService", tier="primary",
                   repo="black-forest-labs (hosted API)", license_status="COMMERCIAL_OK",
                   default_enabled=True, roles=["text_to_image", "style_board", "scene_generation",
                                                "reference_edit"],
                   availability_check=_env("BFL_API_KEY")))
    reg.register(B(name="qwen3-vl-32b@openrouter", service="VisionQCService", tier="primary",
                   repo="QwenLM/Qwen3-VL", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["structural_review"], availability_check=_env("OPENROUTER_API_KEY")))
    reg.register(B(name="gemini-2.5-flash@openrouter", service="VisionQCService", tier="second_opinion",
                   repo="google (hosted)", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["editorial_review"], availability_check=_env("OPENROUTER_API_KEY")))
    reg.register(B(name="bfl-conservative-edit", service="RepairService", tier="primary",
                   repo="black-forest-labs (hosted API)", license_status="COMMERCIAL_OK",
                   default_enabled=True, roles=["conservative_edit"], availability_check=_env("BFL_API_KEY")))
    reg.register(B(name="openai-tts", service="TTSService", tier="primary",
                   repo="openai/openai-python", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["narration"], availability_check=_env("OPENAI_API_KEY")))
    reg.register(B(name="openai-whisper-1", service="AlignmentService", tier="primary",
                   repo="openai/openai-python", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["transcription", "word_timestamps"], availability_check=_env("OPENAI_API_KEY")))
    reg.register(B(name="sias-subtitle-designer", service="SubtitleService", tier="primary",
                   repo="libass/libass + FFmpeg", code_license="ISC/LGPL",
                   license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["srt", "ass", "burn_in"], availability_check=lambda: True))
    reg.register(B(name="ffmpeg", service="RenderService", tier="primary",
                   repo="FFmpeg/FFmpeg", code_license="LGPL/GPL",
                   license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["compositing", "mastering", "export"],
                   availability_check=_importable("shutil")))
    reg.register(B(name="ffmpeg-mastering", service="AudioRepairService", tier="primary",
                   repo="FFmpeg/FFmpeg", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["loudnorm", "mix", "silence_design"], availability_check=lambda: True))
    reg.register(B(name="sias-diamond-gate", service="FinalAuditService", tier="primary",
                   repo="(internal)", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["editorial_gate"], availability_check=lambda: True))
    reg.register(B(name="sias-heuristic-consistency", service="ConsistencyService", tier="primary",
                   repo="(internal, PIL)", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["palette_drift", "structure_drift"], availability_check=lambda: True,
                   known_limitations="heuristic tier — weaker than DINOv2/DreamSim; flags, never approves"))
    reg.register(B(name="ffmpeg-drift", service="MotionGraphicsService", tier="primary",
                   repo="FFmpeg/FFmpeg", license_status="COMMERCIAL_OK", default_enabled=True,
                   roles=["camera_drift", "transitions"], availability_check=lambda: True))

    # ---- open-source fallbacks / second opinions / objective tier ----------
    # Weights unaudited-live → default_enabled=False. Enabling is a deliberate,
    # recorded act after model_weights.lock.json marks the weight COMMERCIAL_OK.
    open_specs = [
        ("qwen-image", "ImageGenerationService", "fallback", "QwenLM/Qwen-Image", "Apache-2.0",
         "Apache-2.0 (verify revision)", "COMMERCIAL_REVIEW_REQUIRED", "gpu_48gb",
         ["text_to_image", "style_board", "typography_scene"], "diffusers", ""),
        ("qwen-image-edit", "RepairService", "fallback", "QwenLM/Qwen-Image", "Apache-2.0",
         "Apache-2.0 (verify revision)", "COMMERCIAL_REVIEW_REQUIRED", "gpu_48gb",
         ["multi_reference_edit", "pose_change", "style_preservation"], "diffusers", ""),
        ("step1x-edit", "RepairService", "fallback", "stepfun-ai/Step1X-Edit", "Apache-2.0",
         "review weights", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["hand_repair", "text_removal", "diagram_correction"], "torch",
         "use only AFTER a candidate is selected; never on every candidate"),
        ("omnigen2", "ImageGenerationService", "experimental", "VectorSpaceLab/OmniGen2", "Apache-2.0",
         "review weights", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["multi_subject_composition"], "torch", "not required by the main pipeline"),
        ("storydiffusion", "ConsistencyService", "experimental", "HVision-NKU/StoryDiffusion", "Apache-2.0",
         "SDXL ecosystem — review", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["long_range_character_sequences"], "torch", "image-first only; video component unused"),
        ("pulid", "ConsistencyService", "experimental", "ToTheBeginning/PuLID", "Apache-2.0",
         "InsightFace dep is NONCOMMERCIAL", "NONCOMMERCIAL_ONLY", "gpu_24gb",
         ["human_likeness"], "torch", "requires consent gates; blocked for production"),
        ("instantid", "ConsistencyService", "experimental", "instantX-research/InstantID", "Apache-2.0",
         "InsightFace dep is NONCOMMERCIAL", "NONCOMMERCIAL_ONLY", "gpu_24gb",
         ["human_likeness"], "torch", "blocked for production"),
        ("dinov2", "ConsistencyService", "objective", "facebookresearch/dinov2", "Apache-2.0",
         "Apache-2.0", "COMMERCIAL_OK", "t4_16gb",
         ["character_consistency", "style_consistency", "environment_continuity"], "torch",
         "calibrate per-axis thresholds from approved+rejected examples"),
        ("dreamsim", "ConsistencyService", "objective", "ssundaram21/dreamsim", "MIT",
         "review checkpoint", "COMMERCIAL_REVIEW_REQUIRED", "t4_16gb",
         ["perceptual_drift"], "torch", ""),
        ("grounding-dino", "VisionQCService", "objective", "IDEA-Research/GroundingDINO", "Apache-2.0",
         "Apache-2.0", "COMMERCIAL_OK", "t4_16gb",
         ["object_presence", "object_count", "character_count"], "torch", ""),
        ("sam2-masks", "RepairService", "objective", "facebookresearch/sam2", "Apache-2.0",
         "Apache-2.0", "COMMERCIAL_OK", "t4_16gb",
         ["repair_masks", "region_isolation"], "torch", ""),
        ("mmpose-rtm", "VisionQCService", "objective", "open-mmlab/mmpose", "Apache-2.0",
         "Apache-2.0", "COMMERCIAL_OK", "t4_16gb",
         ["anatomy_evidence"], "torch",
         "evidence only — never reject solely on low detector confidence"),
        ("hpsv2", "VisionQCService", "objective", "tgxs002/HPSv2", "Apache-2.0",
         "review checkpoint", "COMMERCIAL_REVIEW_REQUIRED", "t4_16gb",
         ["tie_break_ranking"], "torch", "never overrides science/anatomy/identity/story"),
        ("internvl", "VisionQCService", "second_opinion", "OpenGVLab/InternVL", "MIT",
         "review weights", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["editorial_second_opinion"], "torch", "or via remote endpoint"),
        ("chatterbox", "TTSService", "fallback", "resemble-ai/chatterbox", "MIT",
         "review weights", "COMMERCIAL_REVIEW_REQUIRED", "t4_16gb",
         ["narration_multilingual"], "torch", "one consented reference voice per series"),
        ("f5-tts", "TTSService", "fallback", "SWivid/F5-TTS", "MIT",
         "CC-BY-NC-4.0 (published checkpoints)", "NONCOMMERCIAL_ONLY", "t4_16gb",
         ["comparison_backend"], "torch", "weights noncommercial — blocked for monetized output"),
        ("step-audio-editx", "AudioRepairService", "fallback", "stepfun-ai/Step-Audio-EditX", "Apache-2.0",
         "review weights", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["segment_emotion_repair", "emphasis_repair"], "torch",
         "segment-level repair + crossfade; never regenerate a whole narration"),
        ("fish-speech", "TTSService", "experimental", "fishaudio/fish-speech", "Fish Audio Research License",
         "research only", "RESEARCH_ONLY", "t4_16gb", ["experimental_research_only"], "torch",
         "exposed only under experimental_research_only: true"),
        ("whisperx", "AlignmentService", "fallback", "m-bain/whisperX", "BSD-2-Clause",
         "model weights vary", "COMMERCIAL_REVIEW_REQUIRED", "t4_16gb",
         ["advanced_alignment"], "whisperx", ""),
        ("faster-whisper", "AlignmentService", "fallback", "SYSTRAN/faster-whisper", "MIT",
         "CTranslate2 weights — review", "COMMERCIAL_REVIEW_REQUIRED", "cpu",
         ["cpu_transcription"], "faster_whisper", "lightweight CPU fallback"),
        ("stable-ts", "AlignmentService", "objective", "jianfch/stable-ts", "MIT",
         "n/a", "COMMERCIAL_OK", "cpu", ["timestamp_refinement"], "stable_whisper", ""),
        ("motion-canvas", "MotionGraphicsService", "fallback", "motion-canvas/motion-canvas", "MIT",
         "n/a", "COMMERCIAL_OK", "cpu", ["vector_overlays", "diagram_reveals"], "shutil",
         "needs Node; never animates character limbs"),
        ("manim", "MotionGraphicsService", "fallback", "ManimCommunity/manim", "MIT",
         "n/a", "COMMERCIAL_OK", "cpu", ["scientific_diagrams"], "manim", ""),
        ("pyscenedetect", "FinalAuditService", "objective", "Breakthrough/PySceneDetect", "BSD-3-Clause",
         "n/a", "COMMERCIAL_OK", "cpu", ["cut_audit", "transition_audit"], "scenedetect", ""),
        ("mmaudio", "AudioRepairService", "experimental", "SonyResearch/MMAudio", "MIT",
         "dataset/weight suitability NOT guaranteed", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["generated_sfx"], "torch", "enabled:false, production_allowed:false by default"),
        ("comfyui", "ImageGenerationService", "experimental", "Comfy-Org/ComfyUI", "GPL-3.0",
         "n/a (workflow tool)", "COMMERCIAL_REVIEW_REQUIRED", "gpu_24gb",
         ["visual_laboratory"], "torch",
         "GPL isolated; lab only — approved workflows get translated to pinned adapters"),
    ]
    for (name, service, tier, repo, code_lic, weight_lic, status, hw, roles, module, note) in open_specs:
        reg.register(B(name=name, service=service, tier=tier, repo=repo, code_license=code_lic,
                       weight_license=weight_lic, license_status=status, min_hardware=hw,
                       roles=roles, known_limitations=note, default_enabled=False,
                       availability_check=_importable(module)))
    return reg
