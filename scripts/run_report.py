#!/usr/bin/env python3
"""Generate Milestone-7 training report from checkpoint manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.report import build_training_report, render_markdown, write_report_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build training report from manifest")
    parser.add_argument("--manifest", type=Path, default=Path("artifacts/checkpoints/manifest.json"))
    parser.add_argument("--out-json", type=Path, default=Path("artifacts/reports/training_report.json"))
    parser.add_argument("--out-md", type=Path, default=Path("artifacts/reports/training_report.md"))
    parser.add_argument("--print-markdown", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_training_report(args.manifest)
    written = write_report_files(args.manifest, out_json=args.out_json, out_md=args.out_md)

    summary = {
        "manifest": str(args.manifest),
        "total_generations": report.total_generations,
        "promoted_generations": report.promoted_generations,
        "promotion_rate": report.promotion_rate,
        "best_candidate_vs_random": report.best_candidate_vs_random,
        "latest_candidate_vs_random": report.latest_candidate_vs_random,
        "candidate_vs_random_delta": report.candidate_vs_random_delta,
        "latest_replay_size": report.latest_replay_size,
        "written": written,
    }
    print(json.dumps(summary, indent=2))

    if args.print_markdown:
        print("\n" + render_markdown(report))


if __name__ == "__main__":
    main()
