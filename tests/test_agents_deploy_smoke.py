import json
from pathlib import Path

import pytest

from backend.app.agents.deploy import DeploymentConfig, promote_release_artifact


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_promote_release_artifact_strict_pass(tmp_path: Path):
    ckpt = tmp_path / "checkpoints" / "generation_001.pkl"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(b"checkpoint-bytes")

    manifest = tmp_path / "checkpoints" / "manifest.json"
    _write_json(
        manifest,
        [
            {"generation": 0, "path": str(ckpt), "promoted": False, "metrics": {}},
            {"generation": 1, "path": str(ckpt), "promoted": True, "metrics": {"replay_size": 200}},
        ],
    )
    gate = tmp_path / "reports" / "release_gate.json"
    _write_json(gate, {"passed": True})

    out_dir = tmp_path / "releases"
    result = promote_release_artifact(manifest, gate, out_dir, DeploymentConfig(strict_gate=True))

    promoted_path = Path(result["checkpoint"])
    meta_path = Path(result["metadata"])
    assert promoted_path.exists()
    assert promoted_path.read_bytes() == b"checkpoint-bytes"
    assert meta_path.exists()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["generation"] == 1
    assert meta["gate_passed"] is True


def test_promote_release_artifact_strict_fail_gate(tmp_path: Path):
    ckpt = tmp_path / "checkpoints" / "generation_001.pkl"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_bytes(b"checkpoint-bytes")

    manifest = tmp_path / "checkpoints" / "manifest.json"
    _write_json(manifest, [{"generation": 1, "path": str(ckpt), "promoted": True, "metrics": {}}])
    gate = tmp_path / "reports" / "release_gate.json"
    _write_json(gate, {"passed": False})

    with pytest.raises(ValueError, match="Release gate failed"):
        promote_release_artifact(manifest, gate, tmp_path / "releases", DeploymentConfig(strict_gate=True))
