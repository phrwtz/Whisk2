"""Evaluation harness for running baseline agent tournaments."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from .env import WhiskEnv
from .policy import Agent
from ..game import Mark

POST_MIN_DELAY_SEC = 0.1
POST_MAX_DELAY_SEC = 0.5
POST_SIMULTANEOUS_EPSILON_SEC = 0.02


@dataclass
class GameResult:
    winner: Optional[str]
    turns: int
    score_o: int
    score_x: int


@dataclass
class EvalSummary:
    games: int
    wins_o: int
    wins_x: int
    ties: int
    avg_turns: float
    avg_score_o: float
    avg_score_x: float

    def as_dict(self) -> Dict[str, float | int]:
        return {
            "games": self.games,
            "wins_o": self.wins_o,
            "wins_x": self.wins_x,
            "ties": self.ties,
            "avg_turns": self.avg_turns,
            "avg_score_o": self.avg_score_o,
            "avg_score_x": self.avg_score_x,
        }


class Arena:
    """Runs agent-vs-agent matches on WhiskEnv remote mode."""

    def __init__(self, max_turns: int = 300) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be > 0")
        self.max_turns = max_turns

    def play_game(self, agent_o: Agent, agent_x: Agent, seed: int) -> GameResult:
        rng = random.Random(seed)
        env = WhiskEnv(mode="remote")
        env.reset(seed=seed)

        while not env.is_terminal() and env.state.turn < self.max_turns:
            legal_o = env.legal_actions(Mark.O)
            legal_x = env.legal_actions(Mark.X)
            if not legal_o or not legal_x:
                break

            action_o = agent_o.select_action(env, Mark.O, rng)
            action_x = agent_x.select_action(env, Mark.X, rng)

            # If both agents target the same cell, only the second submitter reroutes.
            if action_x == action_o:
                o_delay = rng.uniform(POST_MIN_DELAY_SEC, POST_MAX_DELAY_SEC)
                x_delay = rng.uniform(POST_MIN_DELAY_SEC, POST_MAX_DELAY_SEC)
                if abs(o_delay - x_delay) <= POST_SIMULTANEOUS_EPSILON_SEC:
                    first_mark = rng.choice([Mark.O, Mark.X])
                elif o_delay < x_delay:
                    first_mark = Mark.O
                else:
                    first_mark = Mark.X
                if first_mark == Mark.O:
                    alternatives_x = [coord for coord in legal_x if coord != action_o]
                    if not alternatives_x:
                        break
                    action_x = rng.choice(alternatives_x)
                else:
                    alternatives_o = [coord for coord in legal_o if coord != action_x]
                    if not alternatives_o:
                        break
                    action_o = rng.choice(alternatives_o)

            env.step_joint(action_o, action_x)

        winner = env.winner()
        if winner is None:
            score_o = env.state.scores[Mark.O]
            score_x = env.state.scores[Mark.X]
            if score_o > score_x:
                winner = "O"
            elif score_x > score_o:
                winner = "X"
            else:
                winner = "TIE"

        return GameResult(
            winner=winner,
            turns=env.state.turn,
            score_o=env.state.scores[Mark.O],
            score_x=env.state.scores[Mark.X],
        )

    def run(self, agent_o: Agent, agent_x: Agent, games: int, seed: int = 0) -> EvalSummary:
        if games <= 0:
            raise ValueError("games must be > 0")

        results: List[GameResult] = []
        for i in range(games):
            results.append(self.play_game(agent_o, agent_x, seed=seed + i))

        wins_o = sum(1 for r in results if r.winner == "O")
        wins_x = sum(1 for r in results if r.winner == "X")
        ties = sum(1 for r in results if r.winner == "TIE")

        return EvalSummary(
            games=games,
            wins_o=wins_o,
            wins_x=wins_x,
            ties=ties,
            avg_turns=sum(r.turns for r in results) / games,
            avg_score_o=sum(r.score_o for r in results) / games,
            avg_score_x=sum(r.score_x for r in results) / games,
        )
