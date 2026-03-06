"""Milestone-10 release-readiness gate for Whisk agent artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .report import TrainingReport


@dataclass
class ReleaseGateConfig:
    min_generations: int = 3
    min_promotion_rate: float = 0.40
    min_latest_vs_random: float = 0.55
    min_best_vs_random: float = 0.60
    min_latest_replay_size: int = 100


def evaluate_release_gate(report: TrainingReport, cfg: ReleaseGateConfig) -> Dict[str, object]:
    checks: List[Dict[str, object]] = [
        {
            "name": "min_generations",
            "actual": report.total_generations,
            "required": cfg.min_generations,
            "passed": report.total_generations >= cfg.min_generations,
        },
        {
            "name": "min_promotion_rate",
            "actual": report.promotion_rate,
            "required": cfg.min_promotion_rate,
            "passed": report.promotion_rate >= cfg.min_promotion_rate,
        },
        {
            "name": "min_latest_vs_random",
            "actual": report.latest_candidate_vs_random,
            "required": cfg.min_latest_vs_random,
            "passed": report.latest_candidate_vs_random >= cfg.min_latest_vs_random,
        },
        {
            "name": "min_best_vs_random",
            "actual": report.best_candidate_vs_random,
            "required": cfg.min_best_vs_random,
            "passed": report.best_candidate_vs_random >= cfg.min_best_vs_random,
        },
        {
            "name": "min_latest_replay_size",
            "actual": report.latest_replay_size,
            "required": cfg.min_latest_replay_size,
            "passed": report.latest_replay_size >= cfg.min_latest_replay_size,
        },
    ]

    passed = all(bool(c["passed"]) for c in checks)
    return {
        "passed": passed,
        "checks": checks,
        "summary": {
            "total_generations": report.total_generations,
            "promotion_rate": report.promotion_rate,
            "latest_candidate_vs_random": report.latest_candidate_vs_random,
            "best_candidate_vs_random": report.best_candidate_vs_random,
            "latest_replay_size": report.latest_replay_size,
        },
    }
