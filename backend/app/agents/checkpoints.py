"""Checkpoint lineage utilities for generation-based training."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List

from .model import WhiskPolicyValueModel


@dataclass
class CheckpointRecord:
    generation: int
    path: str
    promoted: bool
    metrics: Dict[str, float | int]


class CheckpointManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root_dir / "manifest.json"

    def save_generation(
        self,
        model: WhiskPolicyValueModel,
        generation: int,
        promoted: bool,
        metrics: Dict[str, float | int],
    ) -> Path:
        ckpt_path = self.root_dir / f"generation_{generation:03d}.pkl"
        model.save(ckpt_path)

        record = CheckpointRecord(
            generation=generation,
            path=str(ckpt_path),
            promoted=promoted,
            metrics=metrics,
        )
        manifest = self.load_manifest()
        manifest = [m for m in manifest if int(m.get("generation", -1)) != generation]
        manifest.append(asdict(record))
        manifest.sort(key=lambda m: int(m["generation"]))
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return ckpt_path

    def best_path(self) -> Path | None:
        manifest = self.load_manifest()
        promoted = [m for m in manifest if m.get("promoted")]
        if not promoted:
            return None
        return Path(promoted[-1]["path"])

    def last_generation(self) -> int:
        manifest = self.load_manifest()
        if not manifest:
            return -1
        return max(int(m["generation"]) for m in manifest)

    def load_manifest(self) -> List[Dict[str, object]]:
        if not self.manifest_path.exists():
            return []
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))
