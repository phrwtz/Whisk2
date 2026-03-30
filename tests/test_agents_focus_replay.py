from backend.app.agents.focus_replay import (
    build_endgame_focused_examples,
    example_scores,
    is_endgame_defense_example,
    is_endgame_focus_example,
)


def _example(score_self: int, score_opp: int):
    return {
        "obs": {
            "score_self": score_self,
            "score_opponent": score_opp,
        },
        "policy_target": [0.0],
        "value_target": 0.0,
    }


def test_example_scores_defaults_for_missing_obs():
    assert example_scores({}) == (0, 0)
    assert example_scores({"obs": "bad"}) == (0, 0)


def test_focus_and_defense_predicates():
    ex_a = _example(39, 40)
    ex_b = _example(40, 10)
    ex_c = _example(35, 35)

    assert is_endgame_focus_example(ex_a, 40)
    assert is_endgame_focus_example(ex_b, 40)
    assert not is_endgame_focus_example(ex_c, 40)

    assert is_endgame_defense_example(ex_a, 40)
    assert not is_endgame_defense_example(ex_b, 40)


def test_build_endgame_focused_examples_biases_output():
    src = [
        _example(10, 10),
        _example(12, 12),
        _example(20, 44),
        _example(43, 20),
    ]
    out, stats = build_endgame_focused_examples(
        src,
        score_floor=40,
        focus_multiplier=2,
        defense_multiplier=3,
        background_cap=1,
        max_examples=50,
        seed=123,
    )

    assert stats["input_examples"] == 4
    assert stats["focus_examples"] == 2
    assert stats["defense_examples"] == 1
    assert 0 < stats["output_examples"] <= 50

    # Focus examples should be duplicated enough to dominate output.
    focus_count = sum(1 for ex in out if is_endgame_focus_example(ex, 40))
    assert focus_count >= 5
