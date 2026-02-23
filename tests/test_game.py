import pytest

from backend.app.game import (
    GameState,
    Mark,
    apply_move,
    commit_turn,
    count_exact_lines,
    pieces_for_client,
)


def place_and_commit(state: GameState, o_coord, x_coord):
    apply_move(state, Mark.O, *o_coord)
    apply_move(state, Mark.X, *x_coord)
    return commit_turn(state)


def test_reject_occupied_square():
    state = GameState()
    place_and_commit(state, (0, 0), (0, 1))

    # Next turn O tries to play on an occupied square.
    with pytest.raises(ValueError):
        apply_move(state, Mark.O, 0, 0)


def test_reject_reserved_square():
    state = GameState()
    apply_move(state, Mark.O, 2, 2)

    # X tries the same square in the same turn; should be rejected.
    with pytest.raises(ValueError):
        apply_move(state, Mark.X, 2, 2)


def test_reject_double_move_same_player_in_turn():
    state = GameState()
    apply_move(state, Mark.O, 3, 3)

    with pytest.raises(ValueError):
        apply_move(state, Mark.O, 3, 4)


def test_piece_limit_5_per_player():
    state = GameState()
    # Commit 6 turns; each player places 6 pieces total.
    for i in range(6):
        place_and_commit(state, (0, i), (1, i))

    # Oldest pieces should have been removed; only 5 remain.
    assert len(state.pieces[Mark.O]) == 5
    assert len(state.pieces[Mark.X]) == 5

    occ = state.board_occupancy()
    assert (0, 0) not in occ
    assert (1, 0) not in occ


def test_age_rank_mapping_newest_is_0():
    state = GameState()
    for i in range(5):
        place_and_commit(state, (0, i), (1, i))

    client_pieces = pieces_for_client(state)
    o_pieces = [p for p in client_pieces if p['mark'] == 'O']

    # Newest O is at (0,4) and should have age_rank 0.
    newest = [p for p in o_pieces if p['row'] == 0 and p['col'] == 4][0]
    oldest = [p for p in o_pieces if p['row'] == 0 and p['col'] == 0][0]
    assert newest['age_rank'] == 0
    assert oldest['age_rank'] == 4


def test_scoring_3_in_row_horizontal_exact():
    # Construct occupancy: O has exactly three in a row at top left.
    occ = {(0, 0): Mark.O, (0, 1): Mark.O, (0, 2): Mark.O}
    assert count_exact_lines(occ, Mark.O, 3) == 1


def test_scoring_4_in_row_does_not_count_as_three():
    occ = {(0, 0): Mark.O, (0, 1): Mark.O, (0, 2): Mark.O, (0, 3): Mark.O}
    assert count_exact_lines(occ, Mark.O, 4) == 1
    assert count_exact_lines(occ, Mark.O, 3) == 0


def test_scoring_5_in_row_diagonal():
    occ = {(i, i): Mark.X for i in range(5)}
    assert count_exact_lines(occ, Mark.X, 5) == 1
    assert count_exact_lines(occ, Mark.X, 4) == 0
    assert count_exact_lines(occ, Mark.X, 3) == 0


def test_running_total_scoring_accumulates_per_commit():
    state = GameState()
    place_and_commit(state, (0, 0), (7, 7))
    place_and_commit(state, (0, 1), (7, 6))
    summary = place_and_commit(state, (0, 2), (7, 5))
    assert summary["scores"]["O"] == 1

    summary = place_and_commit(state, (2, 0), (7, 4))
    assert summary["scores"]["O"] == 2


def test_game_end_when_50_or_more():
    # We won't simulate a real game; instead, directly bump score.
    state = GameState()
    state.scores[Mark.O] = 49

    # Make a 3-in-row for O by placing pieces.
    # O places (0,0),(0,1),(0,2) across three turns.
    place_and_commit(state, (0, 0), (7, 7))
    place_and_commit(state, (0, 1), (7, 6))
    summary = place_and_commit(state, (0, 2), (7, 5))

    assert summary['done'] is True
    assert summary['winner'] == 'O'


def test_game_end_tie_when_both_reach_50():
    state = GameState()
    state.scores[Mark.O] = 49
    state.scores[Mark.X] = 49

    summary = place_and_commit(state, (0, 0), (1, 0))
    summary = place_and_commit(state, (0, 1), (1, 1))
    summary = place_and_commit(state, (0, 2), (1, 2))

    assert summary["done"] is True
    assert summary["winner"] == "TIE"
