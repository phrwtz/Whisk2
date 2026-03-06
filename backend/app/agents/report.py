"""Training report/dashboard helpers from checkpoint lineage manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class GenerationSummary:
    generation: int
    promoted: bool
    candidate_win_rate_vs_best: float
    candidate_win_rate_vs_random: float
    examples: int
    replay_size: int


@dataclass
class TrainingReport:
    total_generations: int
    promoted_generations: List[int]
    promotion_rate: float
    best_candidate_vs_random: float
    latest_candidate_vs_random: float
    candidate_vs_random_delta: float
    latest_replay_size: int
    generations: List[GenerationSummary]

    def to_dict(self) -> Dict[str, object]:
        return {
            "total_generations": self.total_generations,
            "promoted_generations": self.promoted_generations,
            "promotion_rate": self.promotion_rate,
            "best_candidate_vs_random": self.best_candidate_vs_random,
            "latest_candidate_vs_random": self.latest_candidate_vs_random,
            "candidate_vs_random_delta": self.candidate_vs_random_delta,
            "latest_replay_size": self.latest_replay_size,
            "generations": [
                {
                    "generation": g.generation,
                    "promoted": g.promoted,
                    "candidate_win_rate_vs_best": g.candidate_win_rate_vs_best,
                    "candidate_win_rate_vs_random": g.candidate_win_rate_vs_random,
                    "examples": g.examples,
                    "replay_size": g.replay_size,
                }
                for g in self.generations
            ],
        }


def build_training_report(manifest_path: Path) -> TrainingReport:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = sorted(data, key=lambda r: int(r.get("generation", -1)))

    generations: List[GenerationSummary] = []
    last_replay_size = 0
    for row in rows:
        gen = int(row.get("generation", -1))
        if gen <= 0:
            continue
        metrics = row.get("metrics", {}) or {}
        replay_size = int(metrics.get("replay_size", last_replay_size))
        last_replay_size = replay_size
        generations.append(
            GenerationSummary(
                generation=gen,
                promoted=bool(row.get("promoted", False)),
                candidate_win_rate_vs_best=float(metrics.get("candidate_win_rate_vs_best", 0.0)),
                candidate_win_rate_vs_random=float(metrics.get("candidate_win_rate_vs_random", 0.0)),
                examples=int(metrics.get("examples", 0)),
                replay_size=replay_size,
            )
        )

    if not generations:
        return TrainingReport(
            total_generations=0,
            promoted_generations=[],
            promotion_rate=0.0,
            best_candidate_vs_random=0.0,
            latest_candidate_vs_random=0.0,
            candidate_vs_random_delta=0.0,
            latest_replay_size=0,
            generations=[],
        )

    promoted_gens = [g.generation for g in generations if g.promoted]
    best_vs_random = max(g.candidate_win_rate_vs_random for g in generations)
    latest_vs_random = generations[-1].candidate_win_rate_vs_random
    first_vs_random = generations[0].candidate_win_rate_vs_random

    return TrainingReport(
        total_generations=len(generations),
        promoted_generations=promoted_gens,
        promotion_rate=len(promoted_gens) / max(1, len(generations)),
        best_candidate_vs_random=best_vs_random,
        latest_candidate_vs_random=latest_vs_random,
        candidate_vs_random_delta=latest_vs_random - first_vs_random,
        latest_replay_size=generations[-1].replay_size,
        generations=generations,
    )


def render_markdown(report: TrainingReport) -> str:
    lines = []
    lines.append("# Whisk Training Report")
    lines.append("")
    lines.append(f"- Total generations: {report.total_generations}")
    lines.append(f"- Promoted generations: {report.promoted_generations}")
    lines.append(f"- Promotion rate: {report.promotion_rate:.3f}")
    lines.append(f"- Best win rate vs random: {report.best_candidate_vs_random:.3f}")
    lines.append(f"- Latest win rate vs random: {report.latest_candidate_vs_random:.3f}")
    lines.append(f"- Win-rate delta (latest-first): {report.candidate_vs_random_delta:.3f}")
    lines.append(f"- Latest replay size: {report.latest_replay_size}")
    lines.append("")
    lines.append("## Per Generation")
    lines.append("")
    lines.append("| Gen | Promoted | vs Best | vs Random | Examples | Replay |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for g in report.generations:
        lines.append(
            f"| {g.generation} | {g.promoted} | {g.candidate_win_rate_vs_best:.3f} | "
            f"{g.candidate_win_rate_vs_random:.3f} | {g.examples} | {g.replay_size} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report_files(
    manifest_path: Path,
    out_json: Optional[Path] = None,
    out_md: Optional[Path] = None,
) -> Dict[str, str]:
    report = build_training_report(manifest_path)
    written: Dict[str, str] = {}

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        written["json"] = str(out_json)

    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_markdown(report), encoding="utf-8")
        written["markdown"] = str(out_md)

    return written
