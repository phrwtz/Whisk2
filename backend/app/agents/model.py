"""Lightweight policy-value model for Whisk self-play training.

This milestone implementation is intentionally simple and dependency-free.
It uses tabular statistics keyed by encoded observations.
"""

from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .encoding import ActionCodec


StateKey = Tuple[int, ...]


def _softmax(xs: Sequence[float]) -> List[float]:
    if not xs:
        return []
    m = max(xs)
    exps = [math.exp(x - m) for x in xs]
    denom = sum(exps)
    if denom <= 0:
        return [1.0 / len(xs)] * len(xs)
    return [x / denom for x in exps]


@dataclass
class StateStats:
    policy_logits: List[float] = field(default_factory=lambda: [0.0] * ActionCodec.NUM_ACTIONS)
    value_sum: float = 0.0
    value_count: int = 0


class WhiskPolicyValueModel:
    """Tabular policy/value approximator.

    `predict` returns priors over legal actions and a scalar value in [-1, 1].
    `train_on_examples` updates state statistics from self-play targets.
    """

    def __init__(self) -> None:
        self.table: Dict[StateKey, StateStats] = {}

    @staticmethod
    def state_key(obs: Dict[str, object]) -> StateKey:
        # Compact key from board occupancy and side to move perspective.
        board_self = obs["board_self"]
        board_opp = obs["board_opponent"]
        viewer = 1 if obs["viewer_mark"] == "O" else 2
        turn_mod = int(obs["turn"]) % 256

        key: List[int] = [viewer, turn_mod]
        for s, o in zip(board_self, board_opp):
            # 0 empty, 1 self, 2 opp
            if s:
                key.append(1)
            elif o:
                key.append(2)
            else:
                key.append(0)
        return tuple(key)

    def predict(self, obs: Dict[str, object]) -> Tuple[List[float], float]:
        legal_mask = list(obs["legal_action_mask"])
        key = self.state_key(obs)
        stats = self.table.get(key)

        if stats is None:
            legal_count = sum(legal_mask)
            if legal_count == 0:
                priors = [0.0] * ActionCodec.NUM_ACTIONS
            else:
                p = 1.0 / legal_count
                priors = [p if legal_mask[i] else 0.0 for i in range(ActionCodec.NUM_ACTIONS)]
            return priors, 0.0

        masked_logits = [stats.policy_logits[i] if legal_mask[i] else -1e9 for i in range(ActionCodec.NUM_ACTIONS)]
        priors = _softmax(masked_logits)
        value = 0.0 if stats.value_count == 0 else max(-1.0, min(1.0, stats.value_sum / stats.value_count))
        return priors, value

    def train_on_examples(
        self,
        examples: Sequence[Dict[str, object]],
        policy_lr: float = 0.4,
        value_lr: float = 0.2,
    ) -> None:
        for ex in examples:
            obs = ex["obs"]
            target_policy = list(ex["policy_target"])
            target_value = float(ex["value_target"])

            key = self.state_key(obs)
            stats = self.table.setdefault(key, StateStats())

            # Exponential moving update toward visit distribution targets.
            for i in range(ActionCodec.NUM_ACTIONS):
                stats.policy_logits[i] = (1.0 - policy_lr) * stats.policy_logits[i] + policy_lr * target_policy[i]

            # Keep a running average with bounded step size.
            current = 0.0 if stats.value_count == 0 else stats.value_sum / stats.value_count
            updated = (1.0 - value_lr) * current + value_lr * target_value
            stats.value_sum = updated
            stats.value_count = 1

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self.table, f)

    @classmethod
    def load(cls, path: Path) -> "WhiskPolicyValueModel":
        model = cls()
        with path.open("rb") as f:
            model.table = pickle.load(f)
        return model

    def copy(self) -> "WhiskPolicyValueModel":
        other = WhiskPolicyValueModel()
        for key, stats in self.table.items():
            other.table[key] = StateStats(
                policy_logits=list(stats.policy_logits),
                value_sum=stats.value_sum,
                value_count=stats.value_count,
            )
        return other
