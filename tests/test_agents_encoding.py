from backend.app.agents.encoding import ActionCodec, StateEncoder
from backend.app.game import GameState, Mark, apply_move, commit_turn


def test_action_codec_round_trip_and_bounds():
    for row, col in ((0, 0), (3, 4), (7, 7)):
        aid = ActionCodec.coord_to_action(row, col)
        rr, cc = ActionCodec.action_to_coord(aid)
        assert (rr, cc) == (row, col)

    for bad in (-1, 64):
        try:
            ActionCodec.action_to_coord(bad)
            assert False, "Expected ValueError for out-of-range action"
        except ValueError:
            pass


def test_legal_action_mask_respects_occupied_and_reserved_and_pending():
    state = GameState()
    apply_move(state, Mark.O, 0, 0)  # reserved O

    mask_x = StateEncoder.legal_action_mask(state, Mark.X)
    assert mask_x[ActionCodec.coord_to_action(0, 0)] == 0
    assert sum(mask_x) == 63

    mask_o = StateEncoder.legal_action_mask(state, Mark.O)
    assert sum(mask_o) == 0  # O already has pending move this turn


def test_encode_observation_perspective_and_age_channels():
    state = GameState()

    # Turn 1
    apply_move(state, Mark.O, 0, 0)
    apply_move(state, Mark.X, 7, 7)
    commit_turn(state)

    # Turn 2
    apply_move(state, Mark.O, 0, 1)
    apply_move(state, Mark.X, 7, 6)
    commit_turn(state)

    obs = StateEncoder.encode_observation(state, Mark.O)

    idx_new = ActionCodec.coord_to_action(0, 1)
    idx_old = ActionCodec.coord_to_action(0, 0)
    idx_opp = ActionCodec.coord_to_action(7, 7)

    assert obs["board_self"][idx_new] == 1
    assert obs["board_self"][idx_old] == 1
    assert obs["board_opponent"][idx_opp] == 1
    assert obs["score_self"] == state.scores[Mark.O]
    assert obs["score_opponent"] == state.scores[Mark.X]

    # Newest own piece should have higher age intensity than older own piece.
    assert obs["age_self"][idx_new] > obs["age_self"][idx_old]
