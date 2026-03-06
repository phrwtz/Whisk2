#!/usr/bin/env python3
"""Run baseline-vs-baseline evaluation matches."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.agents.baselines import CenterBiasAgent, GreedyScoreAgent, RandomAgent
from backend.app.agents.eval import Arena


def build_agent(name: str):
    name = name.lower()
    if name == "random":
        return RandomAgent()
    if name == "greedy":
        return GreedyScoreAgent()
    if name == "center":
        return CenterBiasAgent()
    raise ValueError(f"Unknown agent: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate baseline Whisk agents")
    parser.add_argument("--agent-o", default="random", choices=["random", "greedy", "center"])
    parser.add_argument("--agent-x", default="greedy", choices=["random", "greedy", "center"])
    parser.add_argument("--games", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=300)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    arena = Arena(max_turns=args.max_turns)
    summary = arena.run(
        agent_o=build_agent(args.agent_o),
        agent_x=build_agent(args.agent_x),
        games=args.games,
        seed=args.seed,
    )

    output = {
        "agent_o": args.agent_o,
        "agent_x": args.agent_x,
        **summary.as_dict(),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
