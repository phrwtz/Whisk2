from backend.app.agents.focus_replay import (
    build_endgame_focused_examples,
    example_scores,
    example_threat_profile,
    is_critical_threat_example,
    is_endgame_defense_example,
    is_endgame_focus_example,
)


def _example(
    score_self: int,
    score_opp: int,
    *,
    opp_threat: int = 0,
    opp_score_now: int | None = None,
    opp_scoring_moves: int = 0,
):
    return {
        "obs": {
            "score_self": score_self,
            "score_opponent": score_opp,
        },
        "policy_target": [0.0],
        "value_target": 0.0,
        "opp_immediate_threat": opp_threat,
        "opp_score_now": score_opp if opp_score_now is None else opp_score_now,
        "opp_scoring_moves": opp_scoring_moves,
    }


def test_example_scores_defaults_for_missing_obs():
    assert example_scores({}) == (0, 0)
    assert example_scores({"obs": "bad"}) == (0, 0)


def test_threat_profile_uses_explicit_metadata_with_score_fallback():
    ex = _example(10, 22, opp_threat=4, opp_score_now=0, opp_scoring_moves=3)
    assert example_threat_profile(ex) == (4, 22, 3)


def test_focus_and_defense_predicates_include_threat_signals():
    ex_focus_by_score = _example(40, 10)
    ex_focus_by_threat = _example(20, 20, opp_threat=4)
    ex_defense_by_near_goal = _example(10, 10, opp_threat=1, opp_score_now=44)
    ex_background = _example(35, 35, opp_threat=0)

    assert is_endgame_focus_example(ex_focus_by_score, 40)
    assert is_endgame_focus_example(ex_focus_by_threat, 40)
    assert is_endgame_defense_example(ex_defense_by_near_goal, 40)
    assert not is_endgame_focus_example(ex_background, 40)


def test_critical_threat_predicate():
    ex_critical_total = _example(10, 45, opp_threat=5)
    ex_critical_near_goal = _example(10, 44, opp_threat=1)
    ex_not_critical = _example(10, 30, opp_threat=1)

    assert is_critical_threat_example(ex_critical_total)
    assert is_critical_threat_example(ex_critical_near_goal)
    assert not is_critical_threat_example(ex_not_critical)


def test_build_endgame_focused_examples_biases_output_to_threats():
    src = [
        _example(10, 10),
        _example(12, 12),
        _example(20, 44, opp_threat=1, opp_scoring_moves=2),
        _example(43, 20, opp_threat=0),
        _example(18, 18, opp_threat=4, opp_scoring_moves=2),
        _example(15, 46, opp_threat=5, opp_scoring_moves=3),
    ]
    out, stats = build_endgame_focused_examples(
        src,
        score_floor=40,
        focus_multiplier=2,
        defense_multiplier=3,
        threat_multiplier=3,
        critical_multiplier=4,
        background_cap=1,
        max_examples=80,
        seed=123,
    )

    assert stats["input_examples"] == 6
    assert stats["focus_examples"] >= 3
    assert stats["defense_examples"] >= 2
    assert stats["threat_examples"] >= 2
    assert stats["critical_examples"] >= 2
    assert 0 < stats["output_examples"] <= 80

    threat_heavy = sum(1 for ex in out if int(ex.get("opp_immediate_threat", 0)) >= 4)
    assert threat_heavy >= 6
