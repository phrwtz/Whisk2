#!/usr/bin/env python3
"""Run a short endgame-defense-focused training pass and report deltas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.focus_replay import build_endgame_focused_examples
from backend.app.agents.model import WhiskPolicyValueModel
from backend.app.agents.replay import ReplayBuffer
from backend.app.agents.train import TrainConfig, Trainer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Focused endgame-defense training pass")
    parser.add_argument(
        "--base-checkpoint",
        type=Path,
        default=Path("artifacts/releases/whiskbot_latest.pkl"),
        help="Starting checkpoint for the focused pass",
    )
    parser.add_argument(
        "--replay-in",
        type=Path,
        default=Path("artifacts/checkpoints/replay_buffer.pkl"),
        help="Existing replay buffer used to bootstrap focused replay",
    )
    parser.add_argument(
        "--out-checkpoint",
        type=Path,
        default=Path("artifacts/checkpoints/endgame_defense/whiskbot_endgame_defense_test.pkl"),
    )
    parser.add_argument(
        "--focused-replay",
        type=Path,
        default=Path("artifacts/checkpoints/endgame_defense/replay_endgame_defense.pkl"),
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=Path("artifacts/reports/endgame_defense_pass.json"),
    )

    parser.add_argument("--score-floor", type=int, default=40)
    parser.add_argument("--focus-multiplier", type=int, default=4)
    parser.add_argument("--defense-multiplier", type=int, default=7)
    parser.add_argument("--threat-multiplier", type=int, default=6)
    parser.add_argument("--critical-multiplier", type=int, default=9)
    parser.add_argument("--threat-floor", type=int, default=4)
    parser.add_argument("--near-goal-score", type=int, default=44)
    parser.add_argument("--background-cap", type=int, default=6000)
    parser.add_argument("--max-focused-examples", type=int, default=24000)

    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--games-per-iteration", type=int, default=12)
    parser.add_argument("--selfplay-max-turns", type=int, default=140)
    parser.add_argument("--selfplay-simulations", type=int, default=28)
    parser.add_argument("--selfplay-workers", type=int, default=1)
    parser.add_argument("--eval-games", type=int, default=16)
    parser.add_argument("--eval-max-turns", type=int, default=120)
    parser.add_argument("--promotion-games", type=int, default=16)
    parser.add_argument("--promotion-threshold", type=float, default=0.55)
    parser.add_argument("--replay-capacity", type=int, default=28000)
    parser.add_argument("--replay-sample-size", type=int, default=5000)
    parser.add_argument("--train-passes", type=int, default=3)
    parser.add_argument("--comparison-games", type=int, default=24)

    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def load_base_model(path: Path) -> WhiskPolicyValueModel:
    if path.exists():
        return WhiskPolicyValueModel.load(path)
    return WhiskPolicyValueModel()


def load_replay_items(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    replay = ReplayBuffer.load(path)
    return list(replay.items)


def write_focused_replay(args: argparse.Namespace) -> dict[str, int]:
    replay_items = load_replay_items(args.replay_in)
    focused_examples, focus_stats = build_endgame_focused_examples(
        replay_items,
        score_floor=args.score_floor,
        focus_multiplier=args.focus_multiplier,
        defense_multiplier=args.defense_multiplier,
        threat_multiplier=args.threat_multiplier,
        critical_multiplier=args.critical_multiplier,
        threat_floor=args.threat_floor,
        near_goal_score=args.near_goal_score,
        background_cap=args.background_cap,
        max_examples=min(args.max_focused_examples, args.replay_capacity),
        seed=args.seed,
    )

    focused_buffer = ReplayBuffer(capacity=args.replay_capacity)
    focused_buffer.add_examples(focused_examples)
    focused_buffer.save(args.focused_replay)
    return focus_stats


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    ensure_parent(args.out_checkpoint)
    ensure_parent(args.focused_replay)
    ensure_parent(args.report_out)

    base_model = load_base_model(args.base_checkpoint)
    focus_stats = write_focused_replay(args)

    report: dict[str, object] = {
        "base_checkpoint": str(args.base_checkpoint),
        "focused_replay": str(args.focused_replay),
        "focus_stats": focus_stats,
    }

    if args.prepare_only:
        report["mode"] = "prepare_only"
        args.report_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    cfg = TrainConfig(
        iterations=args.iterations,
        games_per_iteration=args.games_per_iteration,
        selfplay_max_turns=args.selfplay_max_turns,
        selfplay_simulations=args.selfplay_simulations,
        selfplay_workers=args.selfplay_workers,
        eval_games=args.eval_games,
        eval_max_turns=args.eval_max_turns,
        promotion_games=args.promotion_games,
        promotion_threshold=args.promotion_threshold,
        replay_capacity=args.replay_capacity,
        replay_sample_size=args.replay_sample_size,
        train_passes=args.train_passes,
        seed=args.seed,
        resume=False,
        progress=not args.quiet,
    )

    trainer = Trainer(cfg)
    trainer.best_model = base_model.copy()

    train_summary = trainer.train(args.out_checkpoint, replay_path=args.focused_replay)
    trained_model = WhiskPolicyValueModel.load(args.out_checkpoint)

    base_vs_random = trainer.evaluate_vs_random(base_model, seed=args.seed + 401)
    focused_vs_random = trainer.evaluate_vs_random(trained_model, seed=args.seed + 1401)
    head_to_head = trainer.evaluate_candidate_vs_best(
        candidate=trained_model,
        best=base_model,
        games=args.comparison_games,
        seed=args.seed + 2401,
    )

    report.update(
        {
            "mode": "train",
            "out_checkpoint": str(args.out_checkpoint),
            "training": train_summary,
            "comparison": {
                "base_win_rate_vs_random": base_vs_random,
                "focused_win_rate_vs_random": focused_vs_random,
                "focused_minus_base_vs_random": focused_vs_random - base_vs_random,
                "focused_vs_base": head_to_head,
            },
        }
    )
    args.report_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
