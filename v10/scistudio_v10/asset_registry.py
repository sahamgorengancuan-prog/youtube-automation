from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schemas import AssetRecord
from .utils import ensure_dir


class AssetRegistry:
    """Searchable visual continuity registry backed by SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            asset_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            approved INTEGER NOT NULL,
            scene_id TEXT,
            chronology_index INTEGER,
            payload TEXT NOT NULL
        )""")
        self.conn.commit()

    def upsert(self, asset: AssetRecord) -> None:
        payload = json.dumps(asset.model_dump(mode="json"), ensure_ascii=False)
        self.conn.execute(
            "INSERT OR REPLACE INTO assets(asset_id,path,asset_type,approved,scene_id,chronology_index,payload) VALUES(?,?,?,?,?,?,?)",
            (
                asset.asset_id,
                asset.path,
                asset.asset_type,
                int(asset.approved),
                asset.scene_id,
                asset.chronology_index,
                payload,
            ),
        )
        self.conn.commit()

    def get(self, asset_id: str) -> AssetRecord | None:
        row = self.conn.execute("SELECT payload FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        return AssetRecord.model_validate(json.loads(row[0])) if row else None

    def list(self, *, approved_only: bool = True) -> list[AssetRecord]:
        query = "SELECT payload FROM assets" + (" WHERE approved=1" if approved_only else "")
        return [AssetRecord.model_validate(json.loads(row[0])) for row in self.conn.execute(query)]

    def close(self) -> None:
        self.conn.close()
