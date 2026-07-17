"""Scientific Motion Studio V10 — agentic scientific animation pipeline.

Production providers: OpenAI GPT (reasoning/vision) and BFL FLUX Kontext
(image generation/editing). See ``config_models.StudioConfig`` for the
validated configuration surface and execution modes.
"""

from .asset_registry import AssetRegistry
from .bfl_client import BFLClient, BFLError
from .candidate_tournament import CandidateTournament
from .config_models import ExecutionMode, StudioConfig
from .errors import (
    ConfigurationError,
    DownloadPolicyError,
    JobConcurrencyError,
    JobStateError,
    ProviderError,
    ProviderLockViolationError,
    ProviderUnavailableError,
    StageRetryExhaustedError,
    StudioError,
    UnsafeCommandError,
)
from .flux_prompt_system import FluxPromptSystem
from .pipeline import ScientificMotionStudioV10
from .provider_registry import ProviderRegistry
from .schemas import (
    AnimationPlan,
    ArtDirectionBible,
    AssetRecord,
    CandidateTournamentResult,
    ContinuityCanon,
    DirectorChangeOrder,
    DrawingBrief,
    HybridScenePackage,
    JobManifest,
    ReferenceSelection,
    SceneIllustrationArchitecture,
    SemanticLayerContract,
    ShotState,
    TemporalRequest,
    TemporalResult,
)
from .style_canon import build_hard_coded_canon
from .temporal_backends import TemporalBackendRouter

__version__ = "10.2.0"

__all__ = [
    "AnimationPlan",
    "ArtDirectionBible",
    "AssetRecord",
    "AssetRegistry",
    "BFLClient",
    "BFLError",
    "CandidateTournament",
    "CandidateTournamentResult",
    "ConfigurationError",
    "ContinuityCanon",
    "DirectorChangeOrder",
    "DownloadPolicyError",
    "DrawingBrief",
    "ExecutionMode",
    "FluxPromptSystem",
    "HybridScenePackage",
    "JobConcurrencyError",
    "JobManifest",
    "JobStateError",
    "ProviderError",
    "ProviderLockViolationError",
    "ProviderRegistry",
    "ProviderUnavailableError",
    "ReferenceSelection",
    "SceneIllustrationArchitecture",
    "ScientificMotionStudioV10",
    "SemanticLayerContract",
    "ShotState",
    "StageRetryExhaustedError",
    "StudioConfig",
    "StudioError",
    "TemporalBackendRouter",
    "TemporalRequest",
    "TemporalResult",
    "UnsafeCommandError",
    "build_hard_coded_canon",
    "__version__",
]
