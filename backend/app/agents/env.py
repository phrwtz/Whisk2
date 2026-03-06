"""Training-friendly environment wrapper around Whisk game rules."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

from ..game import (
    BOARD_SIZE,
    GameState,
    Mark,
    apply_move,
    commit_single_move,
    commit_turn,
    ready_to_commit,
)

Coord = Tuple[int, int]
ActionLike = Union[int, Coord]


@dataclass
class StepResult:
    """Summary for a committed environment step."""

    turn: int
    added: Dict[str, int]
    scores: Dict[str, int]
    done: bool
    winner: Optional[str]


class WhiskEnv:
    """Environment API used by agents and self-play.

    Modes:
    - remote: simultaneous hidden actions, committed together
    - local: alternating marks, committed one action at a time
    """

    def __init__(self, mode: str = "remote") -> None:
        if mode not in ("remote", "local"):
            raise ValueError("mode must be 'remote' or 'local'")
        self.mode = mode
        self.state = GameState()
        self.local_next_mark = Mark.O

    def reset(self, seed: Optional[int] = None) -> GameState:
        # Whisk is deterministic given actions; seed retained for API compatibility.
        _ = seed
        self.state = GameState()
        self.local_next_mark = Mark.O
        return self.state

    def clone(self) -> "WhiskEnv":
        env = WhiskEnv(mode=self.mode)
        env.state = deepcopy(self.state)
        env.local_next_mark = self.local_next_mark
        return env

    def to_game_state(self) -> GameState:
        return deepcopy(self.state)

    def current_player(self) -> Optional[Mark]:
        if self.mode == "local":
            return self.local_next_mark
        return None

    def is_terminal(self) -> bool:
        return self.state.scores[Mark.O] >= 50 or self.state.scores[Mark.X] >= 50

    def winner(self) -> Optional[str]:
        if not self.is_terminal():
            return None

        if self.state.scores[Mark.O] > self.state.scores[Mark.X]:
            return "O"
        if self.state.scores[Mark.X] > self.state.scores[Mark.O]:
            return "X"
        return "TIE"

    def legal_actions(self, player: Mark) -> list[Coord]:
        if self.is_terminal():
            return []

        if self.mode == "remote" and self.state.pending[player] is not None:
            return []

        occupied = set(self.state.board_occupancy().keys())
        reserved = self.state.reserved_squares()
        legal: list[Coord] = []
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                coord = (row, col)
                if coord in occupied or coord in reserved:
                    continue
                legal.append(coord)
        return legal

    def reserve_move(self, player: Mark, action: ActionLike) -> None:
        row, col = self._normalize_action(action)
        apply_move(self.state, player, row, col)

    def commit_pending_turn(self) -> StepResult:
        if self.mode != "remote":
            raise RuntimeError("commit_pending_turn is only available in remote mode")
        summary = commit_turn(self.state)
        return StepResult(
            turn=int(summary["turn"]),
            added=dict(summary["added"]),
            scores=dict(summary["scores"]),
            done=bool(summary["done"]),
            winner=summary["winner"],
        )

    def step_joint(self, o_action: ActionLike, x_action: ActionLike) -> StepResult:
        """Commit one simultaneous remote turn.

        This operation is transactional: if either reservation fails,
        no pending moves are left behind.
        """
        if self.mode != "remote":
            raise RuntimeError("step_joint is only available in remote mode")

        snapshot = deepcopy(self.state)
        try:
            self.reserve_move(Mark.O, o_action)
            self.reserve_move(Mark.X, x_action)
            return self.commit_pending_turn()
        except Exception:
            self.state = snapshot
            raise

    def step_local(self, mark: Mark, action: ActionLike) -> StepResult:
        if self.mode != "local":
            raise RuntimeError("step_local is only available in local mode")

        if mark != self.local_next_mark:
            raise ValueError(f"Expected {self.local_next_mark.value}'s move")

        row, col = self._normalize_action(action)
        apply_move(self.state, mark, row, col)
        summary = commit_single_move(self.state, mark)
        self.local_next_mark = Mark.X if mark == Mark.O else Mark.O

        return StepResult(
            turn=int(summary["turn"]),
            added=dict(summary["added"]),
            scores=dict(summary["scores"]),
            done=bool(summary["done"]),
            winner=summary["winner"],
        )

    def ready_to_commit(self) -> bool:
        return ready_to_commit(self.state)

    @staticmethod
    def _normalize_action(action: ActionLike) -> Coord:
        if isinstance(action, int):
            if action < 0 or action >= BOARD_SIZE * BOARD_SIZE:
                raise ValueError("Action index out of range")
            return divmod(action, BOARD_SIZE)

        row, col = action
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            raise ValueError("Move out of bounds")
        return (row, col)
