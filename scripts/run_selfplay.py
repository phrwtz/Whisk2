#!/usr/bin/env python3
"""Smoke self-play runner: random-vs-random on WhiskEnv.

Writes one JSON line per player decision point with encoded observations.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, List

# Allow running as a script from repo root without installing a package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.encoding import ActionCodec, StateEncoder
from backend.app.agents.env import WhiskEnv
from backend.app.game import Mark


def _to_row_col(action_id: int) -> List[int]:
    row, col = ActionCodec.action_to_coord(action_id)
    return [row, col]


def run_selfplay(num_games: int, seed: int, out_path: Path, max_turns: int) -> Dict[str, int]:
    rng = random.Random(seed)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_transitions = 0
    observed_max_turns = 0

    with out_path.open("w", encoding="utf-8") as out:
        for game_idx in range(num_games):
            env = WhiskEnv(mode="remote")
            env.reset(seed=seed + game_idx)

            while not env.is_terminal() and env.state.turn < max_turns:
                state_before = env.to_game_state()

                legal_o = env.legal_actions(Mark.O)
                legal_x = env.legal_actions(Mark.X)
                if not legal_o or not legal_x:
                    break

                action_o_coord = rng.choice(legal_o)
                legal_x_after_o = [coord for coord in legal_x if coord != action_o_coord]
                if not legal_x_after_o:
                    break
                action_x_coord = rng.choice(legal_x_after_o)
                action_o = ActionCodec.coord_to_action(*action_o_coord)
                action_x = ActionCodec.coord_to_action(*action_x_coord)

                obs_o = StateEncoder.encode_observation(state_before, Mark.O)
                obs_x = StateEncoder.encode_observation(state_before, Mark.X)

                result = env.step_joint(action_o, action_x)

                winner = result.winner
                # Perspective outcome target in {-1, 0, +1}.
                outcome_o = 0 if winner in (None, "TIE") else (1 if winner == "O" else -1)
                outcome_x = 0 if winner in (None, "TIE") else (1 if winner == "X" else -1)

                record_o = {
                    "game": game_idx,
                    "turn": result.turn,
                    "player": "O",
                    "obs": obs_o,
                    "action": action_o,
                    "action_coord": _to_row_col(action_o),
                    "reward": result.added["O"],
                    "done": result.done,
                    "winner": winner,
                    "outcome": outcome_o,
                    "scores": result.scores,
                }
                out.write(json.dumps(record_o) + "\n")

                record_x = {
                    "game": game_idx,
                    "turn": result.turn,
                    "player": "X",
                    "obs": obs_x,
                    "action": action_x,
                    "action_coord": _to_row_col(action_x),
                    "reward": result.added["X"],
                    "done": result.done,
                    "winner": winner,
                    "outcome": outcome_x,
                    "scores": result.scores,
                }
                out.write(json.dumps(record_x) + "\n")

                total_transitions += 2

            if env.state.turn > observed_max_turns:
                observed_max_turns = env.state.turn

    return {
        "games": num_games,
        "seed": seed,
        "transitions": total_transitions,
        "max_turns": observed_max_turns,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Random-vs-random Whisk self-play smoke runner")
    parser.add_argument("--games", type=int, default=5, help="Number of games to simulate")
    parser.add_argument("--seed", type=int, default=0, help="Random seed")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/smoke_transitions.jsonl"),
        help="Output JSONL path",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=300,
        help="Maximum turns per game for smoke runs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.games <= 0:
        raise ValueError("--games must be > 0")
    if args.max_turns <= 0:
        raise ValueError("--max-turns must be > 0")

    summary = run_selfplay(
        num_games=args.games,
        seed=args.seed,
        out_path=args.out,
        max_turns=args.max_turns,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
