from __future__ import annotations

import shutil
from pathlib import Path

from .hashing import atomic_write_json
from .schemas import ResearchBundle, RunManifest, ScriptPackage, Storyboard, ValidationReport


class Exporter:
    """Writes the per-run deliverables in the canonical layout:

    run_dir/
      research/{research.json, validation.json}
      scripts/script.json
      storyboards/storyboard.json
      assets/<asset_id>.svg
      scenes/<scene_id>.svg
      manifest.json  (+ a zipped copy of the whole run)
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def export_structured(
        self,
        run_dir: Path,
        research: ResearchBundle,
        validation: ValidationReport,
        script: ScriptPackage,
        storyboard: Storyboard,
    ) -> dict[str, str]:
        research_dir = run_dir / "research"
        scripts_dir = run_dir / "scripts"
        storyboard_dir = run_dir / "storyboards"
        for directory in (research_dir, scripts_dir, storyboard_dir):
            directory.mkdir(parents=True, exist_ok=True)
        files = {
            "research": research_dir / "research.json",
            "validation": research_dir / "validation.json",
            "script": scripts_dir / "script.json",
            "storyboard": storyboard_dir / "storyboard.json",
        }
        atomic_write_json(files["research"], research)
        atomic_write_json(files["validation"], validation)
        atomic_write_json(files["script"], script)
        atomic_write_json(files["storyboard"], storyboard)
        return {key: str(path) for key, path in files.items()}

    def copy_assets(self, run_dir: Path, asset_paths: dict[str, Path]) -> dict[str, str]:
        assets_dir = run_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        output: dict[str, str] = {}
        for asset_id, source in asset_paths.items():
            destination = assets_dir / f"{asset_id}.svg"
            shutil.copy2(source, destination)
            output[asset_id] = str(destination)
        return output

    def write_manifest(self, run_dir: Path, manifest: RunManifest) -> Path:
        path = run_dir / "manifest.json"
        atomic_write_json(path, manifest)
        return path

    def zip_run(self, run_dir: Path) -> Path:
        return Path(shutil.make_archive(str(run_dir), "zip", root_dir=run_dir))
