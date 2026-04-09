#!/usr/bin/env python3
"""Run milestone-6 self-play training and emit long-run summary."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.checkpoints import CheckpointManager
from backend.app.agents.train import TrainConfig, Trainer

SESSION_SCHEMA_VERSION = 1
BENCHMARK_GAMES_DEFAULT = 0
BENCHMARK_SIMULATIONS_DEFAULT = 96
BENCHMARK_ANCHOR_GAP_DEFAULT = 24


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
    parser.add_argument("--train-passes", type=int, default=2)
    parser.add_argument("--benchmark-games", type=int, default=BENCHMARK_GAMES_DEFAULT)
    parser.add_argument("--benchmark-simulations", type=int, default=BENCHMARK_SIMULATIONS_DEFAULT)
    parser.add_argument("--benchmark-anchor-gap", type=int, default=BENCHMARK_ANCHOR_GAP_DEFAULT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Disable periodic progress logs")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("artifacts/checkpoints/m6_best.pkl"))
    parser.add_argument("--replay", type=Path, default=Path("artifacts/checkpoints/replay_buffer.pkl"))
    parser.add_argument(
        "--managed-session",
        action="store_true",
        help="Persist training params/progress for easy interruption-safe restarts",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=Path("artifacts/checkpoints/train_session.json"),
        help="Session metadata JSON path used by --managed-session",
    )
    parser.add_argument(
        "--target-total-iterations",
        type=int,
        default=None,
        help="Total generations to reach across restarts in managed-session mode",
    )
    parser.add_argument(
        "--max-iterations-per-run",
        type=int,
        default=None,
        help="Optional cap on generations performed per invocation in managed-session mode",
    )
    parser.add_argument(
        "--reset-managed-session",
        action="store_true",
        help="Discard stored managed-session config and create a new one from current flags",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _boot_log(managed: bool, session_file: Path) -> None:
    mode = "managed" if managed else "standard"
    print(
        f"[train-runner] boot ts={_utc_now_iso()} pid={os.getpid()} host={socket.gethostname()} "
        f"cwd={Path.cwd()} mode={mode} session={session_file}",
        flush=True,
    )


def _train_args_from_cli(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "iterations": int(args.iterations),
        "games_per_iteration": int(args.games_per_iteration),
        "selfplay_max_turns": int(args.selfplay_max_turns),
        "selfplay_simulations": int(args.selfplay_simulations),
        "selfplay_workers": int(args.selfplay_workers),
        "eval_games": int(args.eval_games),
        "eval_max_turns": int(args.eval_max_turns),
        "promotion_games": int(args.promotion_games),
        "promotion_threshold": float(args.promotion_threshold),
        "replay_capacity": int(args.replay_capacity),
        "replay_sample_size": int(args.replay_sample_size),
        "train_passes": int(args.train_passes),
        "benchmark_games": int(args.benchmark_games),
        "benchmark_simulations": int(args.benchmark_simulations),
        "benchmark_anchor_gap": int(args.benchmark_anchor_gap),
        "seed": int(args.seed),
        "out": str(args.out),
        "replay": str(args.replay),
    }


def _load_session(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("schema_version", -1)) != SESSION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported session schema in {path}: {data.get('schema_version')} "
            f"(expected {SESSION_SCHEMA_VERSION})"
        )
    if not isinstance(data.get("train_args"), dict):
        raise ValueError(f"Session is missing train_args: {path}")
    return data


def _save_session(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _effective_iterations(
    *,
    train_args: Dict[str, Any],
    completed_generations: int,
    target_total_iterations: int | None,
    max_iterations_per_run: int | None,
) -> int:
    if target_total_iterations is not None:
        remaining = max(0, int(target_total_iterations) - completed_generations)
        if remaining <= 0:
            return 0
        if max_iterations_per_run is None:
            return remaining
        return min(remaining, max(1, int(max_iterations_per_run)))

    planned = max(1, int(train_args["iterations"]))
    if max_iterations_per_run is None:
        return planned
    return min(planned, max(1, int(max_iterations_per_run)))


def main() -> None:
    args = parse_args()
    _boot_log(args.managed_session, args.session_file)

    cli_train_args = _train_args_from_cli(args)
    train_args = dict(cli_train_args)
    target_total_iterations: int | None = args.target_total_iterations
    max_iterations_per_run: int | None = args.max_iterations_per_run
    resume = bool(args.resume)

    if args.managed_session:
        if args.reset_managed_session and args.session_file.exists():
            args.session_file.unlink()
            print(f"[train-runner] removed prior session: {args.session_file}", flush=True)

        if args.session_file.exists():
            session = _load_session(args.session_file)
            loaded_train_args = dict(session["train_args"])
            # Backward-compatible merge: keep stored values, but supply defaults for newly added keys.
            train_args = dict(cli_train_args)
            train_args.update(loaded_train_args)

            target_total_iterations = (
                int(session["target_total_iterations"])
                if session.get("target_total_iterations") is not None
                else None
            )
            max_iterations_per_run = (
                int(session["max_iterations_per_run"])
                if session.get("max_iterations_per_run") is not None
                else None
            )

            # Allow explicit one-off updates without manual file edits.
            if args.target_total_iterations is not None:
                target_total_iterations = int(args.target_total_iterations)
            if args.max_iterations_per_run is not None:
                max_iterations_per_run = int(args.max_iterations_per_run)

            # Allow explicit benchmark overrides without resetting the session.
            if (
                "benchmark_games" not in loaded_train_args
                or args.benchmark_games != BENCHMARK_GAMES_DEFAULT
            ):
                train_args["benchmark_games"] = int(args.benchmark_games)
            if (
                "benchmark_simulations" not in loaded_train_args
                or args.benchmark_simulations != BENCHMARK_SIMULATIONS_DEFAULT
            ):
                train_args["benchmark_simulations"] = int(args.benchmark_simulations)
            if (
                "benchmark_anchor_gap" not in loaded_train_args
                or args.benchmark_anchor_gap != BENCHMARK_ANCHOR_GAP_DEFAULT
            ):
                train_args["benchmark_anchor_gap"] = int(args.benchmark_anchor_gap)

            print(
                f"[train-runner] loaded session: {args.session_file} "
                f"(target_total={target_total_iterations}, max_per_run={max_iterations_per_run})",
                flush=True,
            )
        else:
            if target_total_iterations is None:
                target_total_iterations = int(train_args["iterations"])
            session = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "created_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
                "target_total_iterations": int(target_total_iterations)
                if target_total_iterations is not None
                else None,
                "max_iterations_per_run": int(max_iterations_per_run)
                if max_iterations_per_run is not None
                else None,
                "train_args": train_args,
            }
            _save_session(args.session_file, session)
            print(
                f"[train-runner] created session: {args.session_file} "
                f"(target_total={target_total_iterations}, max_per_run={max_iterations_per_run})",
                flush=True,
            )

        # Persist any explicit updates applied to an existing session.
        session["updated_at"] = _utc_now_iso()
        session["target_total_iterations"] = (
            int(target_total_iterations) if target_total_iterations is not None else None
        )
        session["max_iterations_per_run"] = (
            int(max_iterations_per_run) if max_iterations_per_run is not None else None
        )
        session["train_args"] = train_args
        _save_session(args.session_file, session)

        manager = CheckpointManager(Path(train_args["out"]).parent)
        completed_generations = max(0, manager.last_generation())
        run_iterations = _effective_iterations(
            train_args=train_args,
            completed_generations=completed_generations,
            target_total_iterations=target_total_iterations,
            max_iterations_per_run=max_iterations_per_run,
        )
        resume = completed_generations > 0

        print(
            f"[train-runner] status completed={completed_generations} "
            f"target_total={target_total_iterations} run_iterations={run_iterations} "
            f"resume={resume}",
            flush=True,
        )

        if run_iterations <= 0:
            payload = {
                "status": "already_complete",
                "completed_generations": completed_generations,
                "target_total_iterations": target_total_iterations,
                "session_file": str(args.session_file),
            }
            print(json.dumps(payload, indent=2))
            return
    else:
        run_iterations = int(train_args["iterations"])

    cfg = TrainConfig(
        iterations=run_iterations,
        games_per_iteration=int(train_args["games_per_iteration"]),
        selfplay_max_turns=int(train_args["selfplay_max_turns"]),
        selfplay_simulations=int(train_args["selfplay_simulations"]),
        selfplay_workers=int(train_args["selfplay_workers"]),
        eval_games=int(train_args["eval_games"]),
        eval_max_turns=int(train_args["eval_max_turns"]),
        promotion_games=int(train_args["promotion_games"]),
        promotion_threshold=float(train_args["promotion_threshold"]),
        replay_capacity=int(train_args["replay_capacity"]),
        replay_sample_size=int(train_args["replay_sample_size"]),
        train_passes=int(train_args["train_passes"]),
        benchmark_games=int(train_args.get("benchmark_games", 0)),
        benchmark_simulations=int(train_args.get("benchmark_simulations", 96)),
        benchmark_anchor_gap=int(train_args.get("benchmark_anchor_gap", 24)),
        seed=int(train_args["seed"]),
        resume=resume,
        progress=not args.quiet,
    )
    trainer = Trainer(cfg)
    summary = trainer.train(Path(train_args["out"]), replay_path=Path(train_args["replay"]))
    if args.managed_session:
        summary["managed_session"] = str(args.session_file)
        summary["target_total_iterations"] = target_total_iterations
        summary["max_iterations_per_run"] = max_iterations_per_run
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
