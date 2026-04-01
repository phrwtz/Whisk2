from backend.app.agents.selfplay import _defense_threat_penalty, _shape_value_target


def test_defense_threat_penalty_zero_when_no_threat():
    p = _defense_threat_penalty(opponent_score=20, opponent_threat=0, opponent_scoring_moves=0)
    assert p == 0.0


def test_defense_threat_penalty_increases_with_severity():
    mild = _defense_threat_penalty(opponent_score=30, opponent_threat=1, opponent_scoring_moves=1)
    high = _defense_threat_penalty(opponent_score=30, opponent_threat=4, opponent_scoring_moves=2)
    near_goal = _defense_threat_penalty(opponent_score=44, opponent_threat=1, opponent_scoring_moves=1)
    forced_loss = _defense_threat_penalty(opponent_score=46, opponent_threat=4, opponent_scoring_moves=2)

    assert mild > 0.0
    assert high > mild
    assert near_goal > mild
    assert forced_loss > high


def test_shape_value_target_applies_hard_penalty_near_goal():
    ex = {
        "opp_score_now": 46,
        "opp_immediate_threat": 4,
        "opp_scoring_moves": 2,
    }

    shaped_win = _shape_value_target(ex, 1.0)
    shaped_tie = _shape_value_target(ex, 0.0)
    shaped_loss = _shape_value_target(ex, -1.0)

    assert shaped_win < 1.0
    assert shaped_tie < 0.0
    assert shaped_loss == -1.0
