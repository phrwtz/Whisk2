"""State/action encoding utilities for learning agents."""

from __future__ import annotations

from typing import Dict, List, Tuple, Union

from ..game import BOARD_SIZE, GameState, Mark

Coord = Tuple[int, int]
ActionLike = Union[int, Coord]


class ActionCodec:
    """Maps between board coordinates and flat action IDs."""

    NUM_ACTIONS = BOARD_SIZE * BOARD_SIZE

    @staticmethod
    def coord_to_action(row: int, col: int) -> int:
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            raise ValueError("Coordinate out of bounds")
        return row * BOARD_SIZE + col

    @staticmethod
    def action_to_coord(action_id: int) -> Coord:
        if not (0 <= action_id < ActionCodec.NUM_ACTIONS):
            raise ValueError("Action index out of bounds")
        return divmod(action_id, BOARD_SIZE)

    @staticmethod
    def normalize(action: ActionLike) -> Coord:
        if isinstance(action, int):
            return ActionCodec.action_to_coord(action)
        row, col = action
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            raise ValueError("Coordinate out of bounds")
        return (row, col)


class StateEncoder:
    """Encodes game state from a single player's perspective."""

    @staticmethod
    def legal_action_mask(state: GameState, viewer_mark: Mark) -> List[int]:
        if state.pending[viewer_mark] is not None:
            return [0] * ActionCodec.NUM_ACTIONS

        occ = state.board_occupancy()
        reserved = state.reserved_squares()

        mask = [1] * ActionCodec.NUM_ACTIONS
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                idx = ActionCodec.coord_to_action(row, col)
                if (row, col) in occ or (row, col) in reserved:
                    mask[idx] = 0
        return mask

    @staticmethod
    def encode_observation(state: GameState, viewer_mark: Mark) -> Dict[str, object]:
        """Return a deterministic, model-ready observation dict.

        All board arrays are flattened row-major with length 64.
        """
        opponent = Mark.X if viewer_mark == Mark.O else Mark.O
        occupancy = state.board_occupancy()

        board_self = [0] * ActionCodec.NUM_ACTIONS
        board_opp = [0] * ActionCodec.NUM_ACTIONS
        board_empty = [1] * ActionCodec.NUM_ACTIONS
        age_self = [0.0] * ActionCodec.NUM_ACTIONS
        age_opp = [0.0] * ActionCodec.NUM_ACTIONS
        reserved = [0] * ActionCodec.NUM_ACTIONS

        for (row, col), mark in occupancy.items():
            idx = ActionCodec.coord_to_action(row, col)
            board_empty[idx] = 0
            if mark == viewer_mark:
                board_self[idx] = 1
            else:
                board_opp[idx] = 1

        for mark, age_arr in ((viewer_mark, age_self), (opponent, age_opp)):
            dq = state.pieces[mark]
            for idx_from_oldest, piece in enumerate(dq):
                age_rank = len(dq) - 1 - idx_from_oldest
                # 1.0 newest ... 0.2 oldest, 0 means no piece.
                normalized = (5 - age_rank) / 5.0
                flat = ActionCodec.coord_to_action(piece.row, piece.col)
                age_arr[flat] = normalized

        for row, col in state.reserved_squares():
            reserved[ActionCodec.coord_to_action(row, col)] = 1

        return {
            "turn": state.turn,
            "viewer_mark": viewer_mark.value,
            "board_self": board_self,
            "board_opponent": board_opp,
            "board_empty": board_empty,
            "age_self": age_self,
            "age_opponent": age_opp,
            "reserved": reserved,
            "pending_self": int(state.pending[viewer_mark] is not None),
            "pending_opponent": int(state.pending[opponent] is not None),
            "score_self": state.scores[viewer_mark],
            "score_opponent": state.scores[opponent],
            "legal_action_mask": StateEncoder.legal_action_mask(state, viewer_mark),
        }
