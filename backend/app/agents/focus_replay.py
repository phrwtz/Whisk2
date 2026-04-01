"""Replay focusing helpers for targeted training passes."""

from __future__ import annotations

import random
from typing import Dict, List, Sequence, Tuple


Example = Dict[str, object]


def _int_or_default(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def example_scores(example: Example) -> Tuple[int, int]:
    """Return (score_self, score_opponent) from a training example."""
    obs = example.get("obs", {})
    if not isinstance(obs, dict):
        return (0, 0)
    return (
        _int_or_default(obs.get("score_self", 0)),
        _int_or_default(obs.get("score_opponent", 0)),
    )


def example_threat_profile(example: Example) -> Tuple[int, int, int]:
    """Return (opponent_threat, opponent_score, opponent_scoring_moves)."""
    opp_threat = _int_or_default(example.get("opp_immediate_threat", 0))
    opp_score = _int_or_default(example.get("opp_score_now", 0))
    opp_scoring_moves = _int_or_default(example.get("opp_scoring_moves", 0))

    if opp_score <= 0:
        _, fallback_opp_score = example_scores(example)
        opp_score = fallback_opp_score

    return (opp_threat, opp_score, opp_scoring_moves)


def is_endgame_focus_example(
    example: Example,
    score_floor: int,
    *,
    threat_floor: int = 4,
    near_goal_score: int = 44,
) -> bool:
    """Whether this example comes from a near-goal or high-threat state."""
    score_self, score_opp = example_scores(example)
    opp_threat, opp_score, _ = example_threat_profile(example)
    return (
        score_self >= score_floor
        or score_opp >= score_floor
        or opp_threat >= threat_floor
        or (opp_score >= near_goal_score and opp_threat >= 1)
    )


def is_endgame_defense_example(
    example: Example,
    score_floor: int,
    *,
    threat_floor: int = 4,
    near_goal_score: int = 44,
) -> bool:
    """Whether this example is defense-critical (high opponent pressure)."""
    opp_threat, opp_score, opp_scoring_moves = example_threat_profile(example)
    return (
        opp_score >= score_floor
        or opp_threat >= threat_floor
        or (opp_score >= near_goal_score and opp_threat >= 1)
        or (opp_score >= score_floor - 4 and opp_scoring_moves >= 2)
    )


def is_critical_threat_example(example: Example, *, near_goal_score: int = 44) -> bool:
    """Whether this example could allow immediate opponent collapse/win."""
    opp_threat, opp_score, _ = example_threat_profile(example)
    return opp_score + opp_threat >= 50 or (opp_score >= near_goal_score and opp_threat >= 1)


def build_endgame_focused_examples(
    examples: Sequence[Example],
    *,
    score_floor: int = 40,
    focus_multiplier: int = 4,
    defense_multiplier: int = 6,
    threat_multiplier: int = 5,
    critical_multiplier: int = 8,
    threat_floor: int = 4,
    near_goal_score: int = 44,
    background_cap: int = 6000,
    max_examples: int = 24000,
    seed: int = 0,
) -> Tuple[List[Example], Dict[str, int]]:
    """Upsample late-game and high-threat examples for defense-focused training."""
    rng = random.Random(seed)

    focus_multiplier = max(1, int(focus_multiplier))
    defense_multiplier = max(1, int(defense_multiplier))
    threat_multiplier = max(1, int(threat_multiplier))
    critical_multiplier = max(1, int(critical_multiplier))
    background_cap = max(0, int(background_cap))
    max_examples = max(1, int(max_examples))

    focus_examples: List[Example] = []
    defense_examples: List[Example] = []
    threat_examples: List[Example] = []
    critical_examples: List[Example] = []
    background_examples: List[Example] = []

    for ex in examples:
        is_focus = is_endgame_focus_example(
            ex,
            score_floor,
            threat_floor=threat_floor,
            near_goal_score=near_goal_score,
        )
        is_defense = is_endgame_defense_example(
            ex,
            score_floor,
            threat_floor=threat_floor,
            near_goal_score=near_goal_score,
        )
        opp_threat, _, _ = example_threat_profile(ex)
        is_threat = opp_threat >= threat_floor
        is_critical = is_critical_threat_example(ex, near_goal_score=near_goal_score)

        if is_focus:
            focus_examples.append(ex)
        else:
            background_examples.append(ex)

        if is_defense:
            defense_examples.append(ex)
        if is_threat:
            threat_examples.append(ex)
        if is_critical:
            critical_examples.append(ex)

    focused: List[Example] = []
    for _ in range(focus_multiplier):
        focused.extend(focus_examples)
    for _ in range(max(0, defense_multiplier - 1)):
        focused.extend(defense_examples)
    for _ in range(max(0, threat_multiplier - 1)):
        focused.extend(threat_examples)
    for _ in range(max(0, critical_multiplier - 1)):
        focused.extend(critical_examples)

    if background_cap > 0:
        if len(background_examples) <= background_cap:
            focused.extend(background_examples)
        else:
            focused.extend(rng.sample(background_examples, k=background_cap))

    if len(focused) > max_examples:
        focused = rng.sample(focused, k=max_examples)

    rng.shuffle(focused)

    stats = {
        "input_examples": len(examples),
        "focus_examples": len(focus_examples),
        "defense_examples": len(defense_examples),
        "threat_examples": len(threat_examples),
        "critical_examples": len(critical_examples),
        "background_examples": len(background_examples),
        "output_examples": len(focused),
        "score_floor": int(score_floor),
        "focus_multiplier": focus_multiplier,
        "defense_multiplier": defense_multiplier,
        "threat_multiplier": threat_multiplier,
        "critical_multiplier": critical_multiplier,
        "threat_floor": int(threat_floor),
        "near_goal_score": int(near_goal_score),
        "background_cap": background_cap,
        "max_examples": max_examples,
    }
    return focused, stats
