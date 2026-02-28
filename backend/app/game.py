"""Game rules and state for the 8x8 'Whisk' game.

This module is intentionally UI-agnostic: it doesn't know about browsers,
websockets, or HTML. That makes it easy to unit-test.

Rules recap:
- Board is 8x8.
- Players are O (blue) and X (red).
- Each player may have at most 5 pieces on the board at once.
  When a 6th would be added, the oldest piece of that player disappears.
- Scoring (evaluated after both players' moves are revealed each turn):
  * +1 for each 3-in-a-row
  * +4 for each 4-in-a-row
  * +9 for each 5-in-a-row
  Lines count horizontally, vertically, and diagonally.
- Game ends when one or both players reach 50+ points.

Turn lifecycle:
1) each player reserves a move via apply_move
2) when both moves are present, commit_turn reveals and applies both
3) scores are added to running totals from the resulting board position
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from enum import Enum
from typing import Deque, Dict, List, Optional, Set, Tuple


BOARD_SIZE = 8
MAX_PIECES_PER_PLAYER = 5


class Mark(str, Enum):
    O = "O"
    X = "X"


Coord = Tuple[int, int]  # (row, col)


@dataclass
class Piece:
    """A single piece on the board."""

    mark: Mark
    row: int
    col: int
    # Monotonic turn index when this piece was placed.
    turn_placed: int


@dataclass
class GameState:
    """Mutable game state."""

    turn: int = 0
    # Each player's pieces are stored oldest -> newest.
    pieces: Dict[Mark, Deque[Piece]] = field(
        default_factory=lambda: {Mark.O: deque(), Mark.X: deque()}
    )
    scores: Dict[Mark, int] = field(default_factory=lambda: {Mark.O: 0, Mark.X: 0})
    
    # Pending (not-yet-revealed) moves for the current turn.
    # The server layer uses this to enforce simultaneous placement.
    pending: Dict[Mark, Optional[Coord]] = field(
        default_factory=lambda: {Mark.O: None, Mark.X: None}
    )
    last_highlighted_cells: Set[Coord] = field(default_factory=set)
    highlight_visible_for: Set[Mark] = field(default_factory=lambda: {Mark.O, Mark.X})

    def board_occupancy(self) -> Dict[Coord, Mark]:
        """Return a mapping of occupied squares to marks."""
        occ: Dict[Coord, Mark] = {}
        for mark, dq in self.pieces.items():
            for p in dq:
                occ[(p.row, p.col)] = mark
        return occ

    def reserved_squares(self) -> Set[Coord]:
        """Squares currently reserved by pending moves."""
        return {c for c in self.pending.values() if c is not None}


DIRECTIONS: List[Coord] = [
    (0, 1),   # horizontal
    (1, 0),   # vertical
    (1, 1),   # diag down-right
    (1, -1),  # diag down-left
]


def in_bounds(r: int, c: int) -> bool:
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


def _window_cells(start: Coord, dr: int, dc: int, length: int) -> List[Coord]:
    sr, sc = start
    return [(sr + i * dr, sc + i * dc) for i in range(length)]


def _scoring_line_windows(
    occ: Dict[Coord, Mark], mark: Mark, length: int
) -> List[List[Coord]]:
    """Return all "exact" lines of `length` for `mark`."""
    windows: List[List[Coord]] = []
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            for dr, dc in DIRECTIONS:
                cells = _window_cells((r, c), dr, dc, length)
                if not all(in_bounds(rr, cc) for rr, cc in cells):
                    continue
                if not all(occ.get((rr, cc)) == mark for rr, cc in cells):
                    continue

                br, bc = r - dr, c - dc
                ar, ac = r + length * dr, c + length * dc
                before_ok = (not in_bounds(br, bc)) or (occ.get((br, bc)) != mark)
                after_ok = (not in_bounds(ar, ac)) or (occ.get((ar, ac)) != mark)
                if before_ok and after_ok:
                    windows.append(cells)
    return windows


def scoring_coords_for_mark(occ: Dict[Coord, Mark], mark: Mark) -> Set[Coord]:
    """Return the set of coords that contribute to scoring lines for `mark`."""
    coords: Set[Coord] = set()
    for length in (3, 4, 5):
        for line in _scoring_line_windows(occ, mark, length):
            coords.update(line)
    return coords


def scoring_coords_for_board(occ: Dict[Coord, Mark]) -> Set[Coord]:
    """Return the set of scoring coords for both players combined."""
    coords: Set[Coord] = set()
    for mark in (Mark.O, Mark.X):
        coords.update(scoring_coords_for_mark(occ, mark))
    return coords


def scoring_coords_for_pending_move(
    occ: Dict[Coord, Mark], mark: Mark, pending_coord: Coord
) -> Set[Coord]:
    """Return scoring coords for lines that include the pending move."""
    coords: Set[Coord] = set()
    for length in (3, 4, 5):
        for line in _scoring_line_windows(occ, mark, length):
            if pending_coord in line:
                coords.update(line)
    return coords


def score_for_move(
    occ: Dict[Coord, Mark], mark: Mark, move_coord: Coord
) -> int:
    """Return points created by `mark`'s most recent move at `move_coord`."""
    score = 0
    for length, points in ((3, 1), (4, 4), (5, 9)):
        count = 0
        for line in _scoring_line_windows(occ, mark, length):
            if move_coord in line:
                count += 1
        score += count * points
    return score


def count_exact_lines(occ: Dict[Coord, Mark], mark: Mark, length: int) -> int:
    """Count lines of exactly `length` for `mark`.

    "Exactly" means:
    - all `length` cells in the window are the player's mark, AND
    - the cell immediately before the window (in the same direction)
      is either out of bounds or not the player's mark, AND
    - the cell immediately after the window is either out of bounds
      or not the player's mark.

    This prevents counting a 4-in-a-row as two 3-in-a-rows, etc.
    """
    return len(_scoring_line_windows(occ, mark, length))


def score_for_board(occ: Dict[Coord, Mark], mark: Mark) -> int:
    """Compute the score contribution for `mark` given an occupancy map."""
    threes = count_exact_lines(occ, mark, 3)
    fours = count_exact_lines(occ, mark, 4)
    fives = count_exact_lines(occ, mark, 5)
    return threes * 1 + fours * 4 + fives * 9


def _view_piece_queues(
    state: GameState, viewer_mark: Optional[Mark] = None
) -> Dict[Mark, Deque[Piece]]:
    """Return per-player piece queues from a single viewer's perspective.

    Committed pieces are always included. If `viewer_mark` has a pending move,
    include that pending move only for that viewer, applying the same 5-piece
    cap behavior the viewer would see after the move.
    """
    view: Dict[Mark, Deque[Piece]] = {
        Mark.O: deque(state.pieces[Mark.O]),
        Mark.X: deque(state.pieces[Mark.X]),
    }

    if viewer_mark is None:
        return view

    pending = state.pending[viewer_mark]
    if pending is None:
        return view

    row, col = pending
    view_piece = Piece(mark=viewer_mark, row=row, col=col, turn_placed=state.turn)
    view[viewer_mark].append(view_piece)
    while len(view[viewer_mark]) > MAX_PIECES_PER_PLAYER:
        view[viewer_mark].popleft()
    return view


def _occupancy_from_queues(pieces: Dict[Mark, Deque[Piece]]) -> Dict[Coord, Mark]:
    occ: Dict[Coord, Mark] = {}
    for mark, dq in pieces.items():
        for p in dq:
            occ[(p.row, p.col)] = mark
    return occ


def scores_for_client(state: GameState, viewer_mark: Optional[Mark] = None) -> Dict[Mark, int]:
    """Return scores as seen by a specific viewer.

    Scores are the committed running totals, plus a private preview for the
    viewer's own pending move (if any). The opponent's pending move is never
    included in this preview.
    """
    scores = {Mark.O: state.scores[Mark.O], Mark.X: state.scores[Mark.X]}
    if viewer_mark is None or state.pending[viewer_mark] is None:
        return scores

    view_queues = _view_piece_queues(state, viewer_mark)
    view_occ = _occupancy_from_queues(view_queues)
    scores[viewer_mark] += score_for_move(view_occ, viewer_mark, state.pending[viewer_mark])  # type: ignore[arg-type]
    return scores


def highlight_coords_for_viewer(
    state: GameState, viewer_mark: Optional[Mark] = None
) -> Set[Coord]:
    """Return highlight coords that should be visible to a specific viewer."""
    if viewer_mark is None:
        return set()

    pending = state.pending[viewer_mark]
    if pending is not None:
        view_queues = _view_piece_queues(state, viewer_mark)
        view_occ = _occupancy_from_queues(view_queues)
        return scoring_coords_for_pending_move(view_occ, viewer_mark, pending)

    if viewer_mark in state.highlight_visible_for:
        return set(state.last_highlighted_cells)

    return set()


def apply_move(state: GameState, mark: Mark, row: int, col: int) -> None:
    """Reserve ONE player's move for the current turn.

    Behavior:
    - Validate square is in bounds.
    - Validate player has not already moved this turn.
    - Validate square is not occupied by committed pieces.
    - Validate square is not already reserved this turn.
    - Record reservation in state.pending.

    Raises ValueError for invalid reservations.
    """
    if not in_bounds(row, col):
        raise ValueError("Move out of bounds")

    if state.pending[mark] is not None:
        raise ValueError("You already moved this turn.")

    occ = state.board_occupancy()
    if (row, col) in occ:
        raise ValueError("Square already occupied")

    if (row, col) in state.reserved_squares():
        raise ValueError("Square already occupied")

    # Reserve this move. The piece is materialized at commit time.
    state.pending[mark] = (row, col)
    state.highlight_visible_for.discard(mark)


def ready_to_commit(state: GameState) -> bool:
    return state.pending[Mark.O] is not None and state.pending[Mark.X] is not None


def commit_turn(state: GameState) -> Dict[str, object]:
    """Apply both pending moves, then add score for resulting board.

    Returns a small summary dict (useful for the server to broadcast).

    Raises RuntimeError if both pending moves are not present.
    """
    if not ready_to_commit(state):
        raise RuntimeError("Cannot commit: both players have not moved")

    state.turn += 1

    # Reveal pending moves: convert them into pieces.
    revealed: Dict[Mark, Piece] = {}
    for mark in (Mark.O, Mark.X):
        row, col = state.pending[mark]  # type: ignore[misc]
        p = Piece(mark=mark, row=row, col=col, turn_placed=state.turn)
        state.pieces[mark].append(p)
        revealed[mark] = p

        # Enforce the "only 5 pieces" rule by removing the oldest.
        while len(state.pieces[mark]) > MAX_PIECES_PER_PLAYER:
            state.pieces[mark].popleft()

    # Clear pending for next turn.
    state.pending[Mark.O] = None
    state.pending[Mark.X] = None

    # Recalculate board and add points created by each player's latest move.
    occ = state.board_occupancy()
    add_o = score_for_move(occ, Mark.O, (revealed[Mark.O].row, revealed[Mark.O].col))
    add_x = score_for_move(occ, Mark.X, (revealed[Mark.X].row, revealed[Mark.X].col))
    state.scores[Mark.O] += add_o
    state.scores[Mark.X] += add_x
    state.last_highlighted_cells = (
        scoring_coords_for_pending_move(occ, Mark.O, (revealed[Mark.O].row, revealed[Mark.O].col))
        | scoring_coords_for_pending_move(occ, Mark.X, (revealed[Mark.X].row, revealed[Mark.X].col))
    )
    state.highlight_visible_for = {Mark.O, Mark.X}

    done = state.scores[Mark.O] >= 50 or state.scores[Mark.X] >= 50
    winner: Optional[str] = None
    if done:
        # In remote mode, both moves commit simultaneously. If both reach 50+
        # on the same commit, treat that as a tie (regardless of score).
        if state.scores[Mark.O] >= 50 and state.scores[Mark.X] >= 50:
            winner = "TIE"
        elif state.scores[Mark.O] >= 50:
            winner = "O"
        else:
            winner = "X"

    return {
        "turn": state.turn,
        "revealed": {
            "O": {"row": revealed[Mark.O].row, "col": revealed[Mark.O].col},
            "X": {"row": revealed[Mark.X].row, "col": revealed[Mark.X].col},
        },
        "added": {"O": add_o, "X": add_x},
        "scores": {"O": state.scores[Mark.O], "X": state.scores[Mark.X]},
        "done": done,
        "winner": winner,
    }


def commit_single_move(state: GameState, mark: Mark) -> Dict[str, object]:
    """Apply one pending move for `mark`, then add score for resulting board.

    Used by local mode, where a single client alternates O/X and each move
    is committed immediately (no simultaneous hidden reservations).
    """
    if state.pending[mark] is None:
        raise RuntimeError("Cannot commit: selected player has not moved")

    state.turn += 1

    row, col = state.pending[mark]  # type: ignore[misc]
    p = Piece(mark=mark, row=row, col=col, turn_placed=state.turn)
    state.pieces[mark].append(p)

    while len(state.pieces[mark]) > MAX_PIECES_PER_PLAYER:
        state.pieces[mark].popleft()

    state.pending[mark] = None

    occ = state.board_occupancy()
    # Local mode commits one mark at a time, so only the moving mark
    # should receive points for this move.
    add_mark = score_for_move(occ, mark, (p.row, p.col))
    state.scores[mark] += add_mark
    state.last_highlighted_cells = scoring_coords_for_pending_move(occ, mark, (p.row, p.col))
    state.highlight_visible_for = {Mark.O, Mark.X}

    done = state.scores[Mark.O] >= 50 or state.scores[Mark.X] >= 50
    winner: Optional[str] = None
    if done:
        if state.scores[Mark.O] > state.scores[Mark.X]:
            winner = "O"
        elif state.scores[Mark.X] > state.scores[Mark.O]:
            winner = "X"
        else:
            winner = "TIE"

    return {
        "turn": state.turn,
        "revealed": {
            mark.value: {"row": p.row, "col": p.col},
        },
        "added": {"O": add_mark if mark == Mark.O else 0, "X": add_mark if mark == Mark.X else 0},
        "scores": {"O": state.scores[Mark.O], "X": state.scores[Mark.X]},
        "done": done,
        "winner": winner,
    }


def pieces_for_client(
    state: GameState, viewer_mark: Optional[Mark] = None
) -> List[Dict[str, object]]:
    """Return pieces with an 'age_rank' 0..4 (0 = newest).

    The client uses this to set saturation:
      age_rank 0 => 100%, 1=>80%, 2=>60%, 3=>40%, 4=>20%

    If `viewer_mark` has a pending move, include that pending move only for
    that viewer.
    """
    out: List[Dict[str, object]] = []
    view = _view_piece_queues(state, viewer_mark)
    for mark in (Mark.O, Mark.X):
        dq = view[mark]
        # dq is oldest->newest, so reverse enumerate for age_rank.
        for idx_from_oldest, p in enumerate(dq):
            age_rank = len(dq) - 1 - idx_from_oldest
            out.append(
                {
                    "mark": p.mark.value,
                    "row": p.row,
                    "col": p.col,
                    "age_rank": age_rank,
                }
            )
    return out
