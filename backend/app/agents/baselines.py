"""Simple baseline agents for Whisk evaluation and smoke tests."""

from __future__ import annotations

import random
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import List, Tuple

from .env import WhiskEnv
from ..game import MAX_PIECES_PER_PLAYER, Mark, Piece, score_for_move

Coord = Tuple[int, int]


def _immediate_move_score(env: WhiskEnv, mark: Mark, action: Coord) -> int:
    """Heuristic: points `mark` would score if action were committed now."""
    state = deepcopy(env.state)
    row, col = action

    pieces = {
        Mark.O: deque(state.pieces[Mark.O]),
        Mark.X: deque(state.pieces[Mark.X]),
    }

    pieces[mark].append(Piece(mark=mark, row=row, col=col, turn_placed=state.turn + 1))
    while len(pieces[mark]) > MAX_PIECES_PER_PLAYER:
        pieces[mark].popleft()

    occ = {}
    for piece_mark, dq in pieces.items():
        for p in dq:
            occ[(p.row, p.col)] = piece_mark

    return score_for_move(occ, mark, action)


def _center_bias(action: Coord) -> float:
    row, col = action
    # Board center lies between 3 and 4 on both axes.
    return -((row - 3.5) ** 2 + (col - 3.5) ** 2)


@dataclass
class RandomAgent:
    name: str = "random"

    def select_action(self, env: WhiskEnv, mark: Mark, rng: random.Random) -> Coord:
        legal = env.legal_actions(mark)
        if not legal:
            raise RuntimeError(f"No legal actions available for {mark.value}")
        return rng.choice(legal)


@dataclass
class GreedyScoreAgent:
    name: str = "greedy"

    def select_action(self, env: WhiskEnv, mark: Mark, rng: random.Random) -> Coord:
        legal = env.legal_actions(mark)
        if not legal:
            raise RuntimeError(f"No legal actions available for {mark.value}")

        best_score = None
        best_actions: List[Coord] = []
        for action in legal:
            score = _immediate_move_score(env, mark, action)
            if best_score is None or score > best_score:
                best_score = score
                best_actions = [action]
            elif score == best_score:
                best_actions.append(action)

        return rng.choice(best_actions)


@dataclass
class CenterBiasAgent:
    name: str = "center"

    def select_action(self, env: WhiskEnv, mark: Mark, rng: random.Random) -> Coord:
        legal = env.legal_actions(mark)
        if not legal:
            raise RuntimeError(f"No legal actions available for {mark.value}")

        best_bias = None
        best_actions: List[Coord] = []
        for action in legal:
            bias = _center_bias(action)
            if best_bias is None or bias > best_bias:
                best_bias = bias
                best_actions = [action]
            elif bias == best_bias:
                best_actions.append(action)

        return rng.choice(best_actions)
