#!/usr/bin/env python3
"""Run milestone-6 self-play training and emit long-run summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.train import TrainConfig, Trainer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Whisk policy-value model via self-play")
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--games-per-iteration", type=int, default=8)
    parser.add_argument("--selfplay-max-turns", type=int, default=120)
    parser.add_argument("--selfplay-simulations", type=int, default=20)
    parser.add_argument("--selfplay-workers", type=int, default=1)
    parser.add_argument("--eval-games", type=int, default=12)
    parser.add_argument("--eval-max-turns", type=int, default=100)
    parser.add_argument("--promotion-games", type=int, default=12)
    parser.add_argument("--promotion-threshold", type=float, default=0.55)
    parser.add_argument("--replay-capacity", type=int, default=20000)
    parser.add_argument("--replay-sample-size", type=int, default=4000)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Disable periodic progress logs")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("artifacts/checkpoints/m6_best.pkl"))
    parser.add_argument("--replay", type=Path, default=Path("artifacts/checkpoints/replay_buffer.pkl"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
        seed=args.seed,
        resume=args.resume,
        progress=not args.quiet,
    )
    trainer = Trainer(cfg)
    summary = trainer.train(args.out, replay_path=args.replay)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
