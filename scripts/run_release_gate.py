#!/usr/bin/env python3
"""Run Milestone-10 release-readiness gate on training manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.release_gate import ReleaseGateConfig, evaluate_release_gate
from backend.app.agents.report import build_training_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate release gate from training manifest")
    parser.add_argument("--manifest", type=Path, default=Path("artifacts/checkpoints/manifest.json"))
    parser.add_argument("--min-generations", type=int, default=3)
    parser.add_argument("--min-promotion-rate", type=float, default=0.40)
    parser.add_argument("--min-latest-vs-random", type=float, default=0.55)
    parser.add_argument("--min-best-vs-random", type=float, default=0.60)
    parser.add_argument("--min-latest-replay-size", type=int, default=100)
    parser.add_argument("--out", type=Path, default=Path("artifacts/reports/release_gate.json"))
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if gate fails")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_training_report(args.manifest)
    cfg = ReleaseGateConfig(
        min_generations=args.min_generations,
        min_promotion_rate=args.min_promotion_rate,
        min_latest_vs_random=args.min_latest_vs_random,
        min_best_vs_random=args.min_best_vs_random,
        min_latest_replay_size=args.min_latest_replay_size,
    )

    result = evaluate_release_gate(report, cfg)
    payload = {
        "manifest": str(args.manifest),
        "config": {
            "min_generations": cfg.min_generations,
            "min_promotion_rate": cfg.min_promotion_rate,
            "min_latest_vs_random": cfg.min_latest_vs_random,
            "min_best_vs_random": cfg.min_best_vs_random,
            "min_latest_replay_size": cfg.min_latest_replay_size,
        },
        **result,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

    if args.strict and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
