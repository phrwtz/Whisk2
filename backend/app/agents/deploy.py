"""Milestone-11 release promotion utilities for bot deployment artifacts."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple


@dataclass
class DeploymentConfig:
    strict_gate: bool = True
    artifact_name: str = "whiskbot_latest.pkl"
    metadata_name: str = "whiskbot_release.json"


def _resolve_checkpoint_path(manifest_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    # Check repo/cwd-relative paths first (e.g. "artifacts/checkpoints/generation_003.pkl"),
    # then fallback to manifest-relative paths for local manifests.
    cwd_resolved = candidate.resolve()
    if cwd_resolved.exists():
        return cwd_resolved
    return (manifest_path.parent / candidate).resolve()


def resolve_latest_promoted_checkpoint(manifest_path: Path) -> Tuple[int, Path, Dict[str, object]]:
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    promoted = [r for r in rows if bool(r.get("promoted")) and int(r.get("generation", -1)) >= 0]
    if not promoted:
        raise ValueError("No promoted checkpoint found in manifest")

    latest = max(promoted, key=lambda r: int(r.get("generation", -1)))
    generation = int(latest["generation"])
    ckpt_path = _resolve_checkpoint_path(manifest_path, str(latest["path"]))
    if not ckpt_path.exists():
        raise FileNotFoundError(f"Promoted checkpoint not found: {ckpt_path}")
    return generation, ckpt_path, latest


def promote_release_artifact(
    manifest_path: Path,
    gate_path: Path,
    out_dir: Path,
    cfg: DeploymentConfig | None = None,
) -> Dict[str, object]:
    cfg = cfg or DeploymentConfig()

    gate_payload = json.loads(gate_path.read_text(encoding="utf-8"))
    gate_passed = bool(gate_payload.get("passed", False))
    if cfg.strict_gate and not gate_passed:
        raise ValueError("Release gate failed; refusing to promote artifact in strict mode")

    generation, source_ckpt, manifest_row = resolve_latest_promoted_checkpoint(manifest_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    promoted_ckpt = out_dir / cfg.artifact_name
    shutil.copy2(source_ckpt, promoted_ckpt)

    metadata = {
        "manifest": str(manifest_path),
        "gate": str(gate_path),
        "gate_passed": gate_passed,
        "generation": generation,
        "source_checkpoint": str(source_ckpt),
        "promoted_checkpoint": str(promoted_ckpt),
        "manifest_record": manifest_row,
    }
    metadata_path = out_dir / cfg.metadata_name
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return {
        "checkpoint": str(promoted_ckpt),
        "metadata": str(metadata_path),
        "generation": generation,
        "gate_passed": gate_passed,
    }
