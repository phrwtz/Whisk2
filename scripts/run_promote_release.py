#!/usr/bin/env python3
"""Promote latest gate-approved checkpoint into stable bot artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.deploy import DeploymentConfig, promote_release_artifact


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote latest checkpoint for bot deployment")
    parser.add_argument("--manifest", type=Path, default=Path("artifacts/checkpoints/manifest.json"))
    parser.add_argument("--gate", type=Path, default=Path("artifacts/reports/release_gate.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/releases"))
    parser.add_argument("--artifact-name", default="whiskbot_latest.pkl")
    parser.add_argument("--metadata-name", default="whiskbot_release.json")
    parser.add_argument("--non-strict", action="store_true", help="Allow promotion even if gate failed")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = promote_release_artifact(
        manifest_path=args.manifest,
        gate_path=args.gate,
        out_dir=args.out_dir,
        cfg=DeploymentConfig(
            strict_gate=not args.non_strict,
            artifact_name=args.artifact_name,
            metadata_name=args.metadata_name,
        ),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
