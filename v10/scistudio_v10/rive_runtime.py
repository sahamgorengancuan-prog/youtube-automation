"""Optional Rive template integration — last resort, not core.

The studio's default articulation is the **custom 2D cutout skeletal rig**
(PNG part cutouts driven by forward kinematics on the PIL and Remotion paths).
That is the core path and this module does **not** replace it.

This is a correction to an earlier overstatement in both directions. Saying a
``.riv`` "cannot be made programmatically" was too absolute: a ``.riv`` is a
binary authored in the Rive Editor and there is no external Python/Colab API to
synthesize a whole rig file — **but** Colab can run the Rive Web Runtime,
load a *pre-made* ``human_template.riv``, drive its state machine, and swap image
assets. So a genuine Rive path exists **when the operator supplies a template**.

This module provides exactly that, honestly gated:

* It is **opt-in** (``rive.enabled``) and only activates when a real
  ``rive.template_path`` file is present. With no template it is a no-op and the
  custom rig stays in control — nothing is faked and no binary is invented.
* When a template is supplied, it builds an integration plan mapping each
  character's ``pose_intent`` to that template's state-machine inputs, and emits
  a Remotion loader component (``RiveLayer.tsx``) plus the ``@rive-app/canvas``
  dependency, so the provided ``.riv`` is actually driven in the render.

Because shipping a real ``.riv`` requires the Rive Editor, this repo ships the
integration and the contract, not a binary template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import ensure_dir, save_json

# Default mapping from our pose intents to a template's state-machine inputs.
# The operator overrides this to match whatever inputs their .riv exposes.
_DEFAULT_INPUT_MAP = {
    "idle": {"input": "state", "value": "idle"},
    "gesture": {"input": "state", "value": "gesture"},
    "point": {"input": "state", "value": "point"},
    "walk": {"input": "state", "value": "walk"},
    "float": {"input": "state", "value": "float"},
}

_RIVE_NPM = {"@rive-app/canvas": "^2.17.3"}


def _loader_tsx(state_machine: str) -> str:
    """A minimal, self-contained Remotion component that mounts a provided .riv
    and drives one state-machine input per frame. Only used when enabled."""
    return (
        "import {useEffect, useRef} from 'react';\n"
        "import {useCurrentFrame, useVideoConfig, staticFile} from 'remotion';\n"
        "import {Rive, Layout, Fit, Alignment} from '@rive-app/canvas';\n\n"
        "// Optional Rive template layer. Renders a pre-made .riv via the Rive\n"
        "// Web Runtime; the custom cutout rig remains the default path.\n"
        "export const RiveLayer: React.FC<{src: string; input: string; value: string}> = ({src, input, value}) => {\n"
        "  const canvasRef = useRef<HTMLCanvasElement>(null);\n"
        "  const riveRef = useRef<Rive | null>(null);\n"
        "  const frame = useCurrentFrame();\n"
        "  const {width, height} = useVideoConfig();\n"
        "  useEffect(() => {\n"
        "    if (!canvasRef.current) return;\n"
        "    riveRef.current = new Rive({\n"
        "      src: staticFile(src),\n"
        f"      stateMachines: '{state_machine}',\n"
        "      canvas: canvasRef.current,\n"
        "      autoplay: true,\n"
        "      layout: new Layout({fit: Fit.Contain, alignment: Alignment.Center}),\n"
        "    });\n"
        "    return () => riveRef.current?.cleanup();\n"
        "  }, [src]);\n"
        "  useEffect(() => {\n"
        "    const r = riveRef.current;\n"
        "    if (!r) return;\n"
        f"    const inputs = r.stateMachineInputs('{state_machine}');\n"
        "    const target = inputs?.find((i: any) => i.name === input);\n"
        "    if (target) target.value = value;\n"
        "  }, [frame, input, value]);\n"
        "  return <canvas ref={canvasRef} width={width} height={height} />;\n"
        "};\n"
    )


class RiveIntegration:
    def __init__(self, config: dict[str, Any] | None = None, root: Any = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.state_machine = str(self.config.get("state_machine", "State Machine 1"))
        self.input_map = {**_DEFAULT_INPUT_MAP, **(self.config.get("input_map") or {})}

    @property
    def template_path(self) -> Path | None:
        p = self.config.get("template_path")
        return Path(p) if p else None

    @property
    def enabled(self) -> bool:
        # Opt-in AND a real template file must exist — never invents a .riv.
        tp = self.template_path
        return bool(self.config.get("enabled", False)) and tp is not None and tp.exists()

    def _map_intent(self, pose_intent: str) -> dict[str, str]:
        key = (pose_intent or "idle").strip().lower()
        return self.input_map.get(key, self.input_map["idle"])

    def build_plan(self, characters: list[dict[str, Any]]) -> dict[str, Any]:
        """characters: [{character_id, scene_id, pose_intent}]. Returns the
        integration plan (disabled → the custom rig stays in control)."""
        if not self.enabled:
            return {
                "enabled": False,
                "reason": "no template_path provided or rive.enabled is false; "
                "the custom 2D cutout rig remains the default articulation path.",
                "drivers": [],
            }
        drivers = [
            {
                "character_id": ch.get("character_id", ""),
                "scene_id": ch.get("scene_id", ""),
                "src": self.template_path.name,
                **self._map_intent(str(ch.get("pose_intent", "idle"))),
            }
            for ch in characters
        ]
        plan = {
            "enabled": True,
            "template": str(self.template_path),
            "state_machine": self.state_machine,
            "npm": _RIVE_NPM,
            "drivers": drivers,
        }
        if self.root is not None:
            save_json(self.root / "rive_plan.json", plan)
        return plan

    def emit_remotion_assets(self, remotion_project: Any) -> dict[str, Any]:
        """When enabled, write RiveLayer.tsx + register @rive-app/canvas and copy
        the template into the Remotion project's public/. No-op when disabled."""
        if not self.enabled:
            return {"enabled": False}
        project = Path(remotion_project)
        src = ensure_dir(project / "src")
        (src / "RiveLayer.tsx").write_text(_loader_tsx(self.state_machine), encoding="utf-8")
        public = ensure_dir(project / "public")
        dest = public / self.template_path.name
        dest.write_bytes(self.template_path.read_bytes())
        return {
            "enabled": True,
            "component": str(src / "RiveLayer.tsx"),
            "template_public": str(dest),
            "npm": _RIVE_NPM,
        }
