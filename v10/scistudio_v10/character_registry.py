from __future__ import annotations

from pathlib import Path

from .schemas import CharacterProfile, CharacterView
from .utils import ensure_dir, load_json, save_json


class CharacterRegistry:
    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)
        self.path = self.root / "characters.json"
        self.characters: dict[str, CharacterProfile] = {}
        if self.path.exists():
            self.characters = {x["subject_id"]: CharacterProfile.model_validate(x) for x in load_json(self.path)}

    def upsert(self, profile: CharacterProfile) -> None:
        self.characters[profile.subject_id] = profile
        self._save()

    def add_view(self, subject_id: str, view: CharacterView) -> None:
        if subject_id not in self.characters:
            self.characters[subject_id] = CharacterProfile(subject_id=subject_id, display_name=subject_id)
        profile = self.characters[subject_id]
        profile.views = [x for x in profile.views if x.view_id != view.view_id] + [view]
        self._save()

    def get(self, subject_id: str) -> CharacterProfile | None:
        return self.characters.get(subject_id)

    def _save(self) -> None:
        save_json(self.path, [x.model_dump(mode="json") for x in self.characters.values()])
