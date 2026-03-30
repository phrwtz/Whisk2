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


def is_endgame_focus_example(example: Example, score_floor: int) -> bool:
    """Whether this example comes from a near-goal board state for either side."""
    score_self, score_opp = example_scores(example)
    return score_self >= score_floor or score_opp >= score_floor


def is_endgame_defense_example(example: Example, score_floor: int) -> bool:
    """Whether this example reflects a defense-critical near-goal opponent state."""
    _, score_opp = example_scores(example)
    return score_opp >= score_floor


def build_endgame_focused_examples(
    examples: Sequence[Example],
    *,
    score_floor: int = 40,
    focus_multiplier: int = 4,
    defense_multiplier: int = 6,
    background_cap: int = 6000,
    max_examples: int = 24000,
    seed: int = 0,
) -> Tuple[List[Example], Dict[str, int]]:
    """Upsample late-game examples to create a defense-focused bootstrap set."""
    rng = random.Random(seed)

    focus_multiplier = max(1, int(focus_multiplier))
    defense_multiplier = max(1, int(defense_multiplier))
    background_cap = max(0, int(background_cap))
    max_examples = max(1, int(max_examples))

    focus_examples: List[Example] = []
    defense_examples: List[Example] = []
    background_examples: List[Example] = []

    for ex in examples:
        if is_endgame_focus_example(ex, score_floor):
            focus_examples.append(ex)
            if is_endgame_defense_example(ex, score_floor):
                defense_examples.append(ex)
        else:
            background_examples.append(ex)

    focused: List[Example] = []
    for _ in range(focus_multiplier):
        focused.extend(focus_examples)
    for _ in range(max(0, defense_multiplier - 1)):
        focused.extend(defense_examples)

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
        "background_examples": len(background_examples),
        "output_examples": len(focused),
        "score_floor": int(score_floor),
        "focus_multiplier": focus_multiplier,
        "defense_multiplier": defense_multiplier,
        "background_cap": background_cap,
        "max_examples": max_examples,
    }
    return focused, stats
