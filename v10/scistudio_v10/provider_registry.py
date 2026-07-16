from __future__ import annotations

from pathlib import Path
from typing import Any

from .schemas import ProviderSpec
from .utils import ensure_dir, save_json


class ProviderRegistry:
    """Central registry for production providers.

    Providers are resolved by capability and priority rather than hard-coded
    conditionals spread across the pipeline. Implementations can be callables,
    objects, local commands or remote adapters.
    """

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)
        self.specs: dict[str, ProviderSpec] = {}
        self.implementations: dict[str, Any] = {}

    def register(self, spec: ProviderSpec, implementation: Any = None) -> None:
        self.specs[spec.provider_id] = spec
        if implementation is not None:
            self.implementations[spec.provider_id] = implementation
        self.snapshot()

    def resolve(self, provider_type: str, *, require: str | None = None) -> tuple[ProviderSpec, Any]:
        options = [s for s in self.specs.values() if s.enabled and s.provider_type == provider_type]
        if require:
            options = [s for s in options if any(c.name == require and c.available for c in s.capabilities)]
        if not options:
            raise RuntimeError(f"No enabled provider for type={provider_type!r}, capability={require!r}")
        spec = sorted(options, key=lambda x: (x.priority, x.provider_id))[0]
        return spec, self.implementations.get(spec.provider_id)

    def available(self, provider_type: str) -> list[ProviderSpec]:
        return sorted(
            [s for s in self.specs.values() if s.enabled and s.provider_type == provider_type],
            key=lambda x: (x.priority, x.provider_id),
        )

    def snapshot(self) -> str:
        path = self.root / "provider_registry.json"
        save_json(path, [s.model_dump(mode="json") for s in self.specs.values()])
        return str(path)

    @classmethod
    def from_config(cls, config: dict[str, Any], root: str | Path) -> ProviderRegistry:
        registry = cls(root)
        for raw in config.get("providers", []):
            registry.register(ProviderSpec.model_validate(raw))
        return registry
