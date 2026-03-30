#!/usr/bin/env python3
"""Run a time-budgeted endgame-defense experiment with iterative reporting."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.focus_replay import build_endgame_focused_examples
from backend.app.agents.model import WhiskPolicyValueModel
from backend.app.agents.replay import ReplayBuffer
from backend.app.agents.train import TrainConfig, Trainer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Time-budgeted overnight endgame-defense experiment")
    parser.add_argument("--hours", type=float, default=16.0, help="Total runtime budget in hours")
    parser.add_argument("--seed", type=int, default=0)

    parser.add_argument(
        "--base-checkpoint",
        type=Path,
        default=Path("artifacts/releases/whiskbot_latest.pkl"),
    )
    parser.add_argument(
        "--replay-in",
        type=Path,
        default=Path("artifacts/checkpoints/replay_buffer.pkl"),
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("artifacts/experiments/endgame_defense_overnight"),
    )

    parser.add_argument("--score-floor", type=int, default=40)
    parser.add_argument("--focus-multiplier", type=int, default=4)
    parser.add_argument("--defense-multiplier", type=int, default=7)
    parser.add_argument("--background-cap", type=int, default=6000)
    parser.add_argument("--max-focused-examples", type=int, default=26000)
    parser.add_argument("--replay-capacity", type=int, default=30000)

    parser.add_argument("--games-per-cycle", type=int, default=10)
    parser.add_argument("--selfplay-max-turns", type=int, default=140)
    parser.add_argument("--selfplay-simulations", type=int, default=24)
    parser.add_argument("--selfplay-workers", type=int, default=1)
    parser.add_argument("--eval-games", type=int, default=12)
    parser.add_argument("--eval-max-turns", type=int, default=120)
    parser.add_argument("--promotion-games", type=int, default=12)
    parser.add_argument("--promotion-threshold", type=float, default=0.55)
    parser.add_argument("--replay-sample-size", type=int, default=5000)
    parser.add_argument("--train-passes", type=int, default=3)

    parser.add_argument("--comparison-games", type=int, default=32)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def _log(enabled: bool, message: str) -> None:
    if enabled:
        print(message, flush=True)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_model(path: Path) -> WhiskPolicyValueModel:
    if path.exists():
        return WhiskPolicyValueModel.load(path)
    return WhiskPolicyValueModel()


def _prepare_focused_replay(args: argparse.Namespace, replay_out: Path) -> Dict[str, int]:
    replay_items: List[Dict[str, object]] = []
    if args.replay_in.exists():
        replay_items = list(ReplayBuffer.load(args.replay_in).items)

    focused_examples, stats = build_endgame_focused_examples(
        replay_items,
        score_floor=args.score_floor,
        focus_multiplier=args.focus_multiplier,
        defense_multiplier=args.defense_multiplier,
        background_cap=args.background_cap,
        max_examples=min(args.max_focused_examples, args.replay_capacity),
        seed=args.seed,
    )

    replay = ReplayBuffer(capacity=args.replay_capacity)
    replay.add_examples(focused_examples)
    replay.save(replay_out)
    return stats


def _cycle_config(args: argparse.Namespace, seed: int) -> TrainConfig:
    return TrainConfig(
        iterations=1,
        games_per_iteration=args.games_per_cycle,
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
        seed=seed,
        resume=False,
        progress=False,
    )


def _build_recommendation(cycles: List[Dict[str, object]]) -> str:
    if not cycles:
        return "No cycles completed; rerun with a larger time budget."

    wr_vs_base = [float(c["vs_base_win_rate"]) for c in cycles]
    wr_vs_prev = [float(c["vs_prev_win_rate"]) for c in cycles]
    last3_base = wr_vs_base[-3:]
    last3_prev = wr_vs_prev[-3:]

    if len(last3_base) >= 2 and mean(last3_base) >= 0.56 and mean(last3_prev) >= 0.53:
        return (
            "Signal suggests continued improvement. A longer run is justified, "
            "but keep promotion games >= 24 to reduce noise."
        )

    if len(last3_base) >= 2 and mean(last3_base) <= 0.52 and mean(last3_prev) <= 0.52:
        return (
            "Progress appears flat. Prioritize policy changes (curriculum/reward/tactical heuristics) "
            "before spending another long training window."
        )

    return (
        "Results are mixed/noisy. Run one more 8-12 hour trial with higher comparison games "
        "before committing to a full long run."
    )


def main() -> None:
    args = parse_args()
    verbose = not args.quiet

    args.work_dir.mkdir(parents=True, exist_ok=True)
    replay_path = args.work_dir / "focused_replay.pkl"
    report_path = args.work_dir / "report.json"
    current_ckpt_path = args.work_dir / "current_best.pkl"

    started = time.time()
    deadline = started + (max(0.1, args.hours) * 3600.0)

    base_model = _load_model(args.base_checkpoint)
    base_model.save(current_ckpt_path)
    base_hash = _sha1(current_ckpt_path)

    focus_stats = _prepare_focused_replay(args, replay_path)
    _log(verbose, f"[overnight] focused replay prepared: {focus_stats}")

    cycles: List[Dict[str, object]] = []
    cycle_idx = 1
    while time.time() < deadline:
        remaining_sec = max(0.0, deadline - time.time())
        _log(verbose, f"[overnight] cycle {cycle_idx} start, remaining {remaining_sec/3600.0:.2f}h")

        cycle_dir = args.work_dir / f"cycle_{cycle_idx:03d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        out_ckpt = cycle_dir / "best.pkl"

        cycle_seed = args.seed + cycle_idx * 10000
        cfg = _cycle_config(args, seed=cycle_seed)
        trainer = Trainer(cfg)
        trainer.best_model = WhiskPolicyValueModel.load(current_ckpt_path)

        c_start = time.time()
        train_summary = trainer.train(out_ckpt, replay_path=replay_path)
        cycle_elapsed = time.time() - c_start

        next_model = WhiskPolicyValueModel.load(out_ckpt)
        prev_model = WhiskPolicyValueModel.load(current_ckpt_path)

        eval_cfg = TrainConfig(
            selfplay_simulations=max(16, args.selfplay_simulations),
            eval_games=max(8, args.eval_games),
            eval_max_turns=args.eval_max_turns,
        )
        evaluator = Trainer(eval_cfg)

        vs_base = evaluator.evaluate_candidate_vs_best(
            candidate=next_model,
            best=base_model,
            games=args.comparison_games,
            seed=cycle_seed + 101,
        )
        vs_prev = evaluator.evaluate_candidate_vs_best(
            candidate=next_model,
            best=prev_model,
            games=args.comparison_games,
            seed=cycle_seed + 202,
        )

        next_vs_random = evaluator.evaluate_vs_random(next_model, seed=cycle_seed + 303)
        prev_vs_random = evaluator.evaluate_vs_random(prev_model, seed=cycle_seed + 404)

        next_model.save(current_ckpt_path)
        new_hash = _sha1(current_ckpt_path)

        cycle_record = {
            "cycle": cycle_idx,
            "elapsed_sec": cycle_elapsed,
            "train_summary": train_summary,
            "checkpoint_changed": new_hash != base_hash,
            "checkpoint_sha1": new_hash,
            "vs_base_win_rate": float(vs_base["candidate_win_rate"]),
            "vs_prev_win_rate": float(vs_prev["candidate_win_rate"]),
            "next_vs_random": float(next_vs_random),
            "prev_vs_random": float(prev_vs_random),
            "vs_base": vs_base,
            "vs_prev": vs_prev,
            "remaining_hours": max(0.0, deadline - time.time()) / 3600.0,
        }
        cycles.append(cycle_record)

        interim = {
            "started_at_epoch": started,
            "deadline_epoch": deadline,
            "hours_budget": args.hours,
            "base_checkpoint": str(args.base_checkpoint),
            "work_dir": str(args.work_dir),
            "focus_stats": focus_stats,
            "cycles": cycles,
            "recommendation": _build_recommendation(cycles),
        }
        report_path.write_text(json.dumps(interim, indent=2), encoding="utf-8")

        _log(
            verbose,
            "[overnight] cycle "
            f"{cycle_idx} done in {cycle_elapsed/60.0:.1f}m; "
            f"vs_base={cycle_record['vs_base_win_rate']:.3f}; "
            f"vs_prev={cycle_record['vs_prev_win_rate']:.3f}",
        )

        cycle_idx += 1

    final_report = {
        "started_at_epoch": started,
        "ended_at_epoch": time.time(),
        "hours_budget": args.hours,
        "base_checkpoint": str(args.base_checkpoint),
        "work_dir": str(args.work_dir),
        "focused_replay": str(replay_path),
        "focus_stats": focus_stats,
        "cycles": cycles,
        "recommendation": _build_recommendation(cycles),
        "final_checkpoint": str(current_ckpt_path),
        "final_checkpoint_sha1": _sha1(current_ckpt_path),
    }
    report_path.write_text(json.dumps(final_report, indent=2), encoding="utf-8")
    print(json.dumps(final_report, indent=2))


if __name__ == "__main__":
    main()
