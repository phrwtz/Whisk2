"""PUCT-style Monte Carlo Tree Search for Whisk.

Search is player-conditional: for the selected player, opponent moves are
sampled from the model priors on each simulation branch.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .encoding import ActionCodec, StateEncoder
from .env import WhiskEnv
from .model import WhiskPolicyValueModel
from ..game import Mark

Coord = Tuple[int, int]


@dataclass
class ChildStats:
    prior: float
    visits: int = 0
    value_sum: float = 0.0

    @property
    def q(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits


@dataclass
class RootNode:
    children: Dict[int, ChildStats] = field(default_factory=dict)
    visits: int = 0


def _sample_from_probs(rng: random.Random, probs: List[float]) -> int:
    r = rng.random()
    acc = 0.0
    for i, p in enumerate(probs):
        acc += p
        if r <= acc:
            return i
    # Handle tiny floating rounding mismatch.
    return max(0, len(probs) - 1)


def _normalize_probs(probs: List[float]) -> List[float]:
    s = sum(probs)
    if s <= 0:
        n = len(probs)
        return [1.0 / n] * n if n else []
    return [p / s for p in probs]


class MCTS:
    def __init__(
        self,
        model: WhiskPolicyValueModel,
        simulations: int = 48,
        c_puct: float = 1.25,
        rollout_max_turns: int = 120,
    ) -> None:
        self.model = model
        self.simulations = simulations
        self.c_puct = c_puct
        self.rollout_max_turns = rollout_max_turns

    def search(self, env: WhiskEnv, player: Mark, rng: random.Random) -> List[float]:
        """Return improved policy distribution over 64 actions."""
        legal = env.legal_actions(player)
        policy = [0.0] * ActionCodec.NUM_ACTIONS
        if not legal:
            return policy

        root = RootNode()
        root_obs = StateEncoder.encode_observation(env.state, player)
        root_priors, _ = self.model.predict(root_obs)

        for coord in legal:
            a = ActionCodec.coord_to_action(*coord)
            root.children[a] = ChildStats(prior=root_priors[a])

        # If priors collapsed on illegal space, fallback uniform over legal.
        total_prior = sum(ch.prior for ch in root.children.values())
        if total_prior <= 0:
            uniform = 1.0 / len(root.children)
            for ch in root.children.values():
                ch.prior = uniform
        else:
            for ch in root.children.values():
                ch.prior /= total_prior

        for _ in range(self.simulations):
            action = self._select(root)
            leaf_value = self._simulate_once(env, player, action, rng)
            child = root.children[action]
            child.visits += 1
            child.value_sum += leaf_value
            root.visits += 1

        if root.visits == 0:
            # shouldn't happen, but keep API safe.
            p = 1.0 / len(legal)
            for coord in legal:
                policy[ActionCodec.coord_to_action(*coord)] = p
            return policy

        for action, ch in root.children.items():
            policy[action] = ch.visits / root.visits

        return policy

    def _select(self, root: RootNode) -> int:
        sqrt_n = math.sqrt(max(1, root.visits))
        best_action = -1
        best_score = -1e18

        for action, child in root.children.items():
            u = self.c_puct * child.prior * (sqrt_n / (1 + child.visits))
            score = child.q + u
            if score > best_score:
                best_score = score
                best_action = action

        return best_action

    def _simulate_once(self, env: WhiskEnv, player: Mark, action: int, rng: random.Random) -> float:
        sim_env = env.clone()
        opponent = Mark.X if player == Mark.O else Mark.O

        my_coord = ActionCodec.action_to_coord(action)
        opp_coord = self._sample_opponent_action(sim_env, opponent, rng, forbid=my_coord)
        if player == Mark.O:
            sim_env.step_joint(my_coord, opp_coord)
        else:
            sim_env.step_joint(opp_coord, my_coord)

        # Short stochastic rollout guided by model priors.
        while not sim_env.is_terminal() and sim_env.state.turn < self.rollout_max_turns:
            a_o = self._sample_model_action(sim_env, Mark.O, rng)
            a_x = self._sample_model_action(sim_env, Mark.X, rng, forbid=a_o)
            if a_o is None or a_x is None:
                break
            sim_env.step_joint(a_o, a_x)

        return self._value_from_state(sim_env, player)

    def _sample_model_action(
        self,
        env: WhiskEnv,
        mark: Mark,
        rng: random.Random,
        forbid: Coord | None = None,
    ) -> Coord | None:
        legal = env.legal_actions(mark)
        if forbid is not None:
            legal = [c for c in legal if c != forbid]
        if not legal:
            return None

        obs = StateEncoder.encode_observation(env.state, mark)
        priors, _ = self.model.predict(obs)

        legal_ids = [ActionCodec.coord_to_action(*c) for c in legal]
        probs = [max(0.0, priors[i]) for i in legal_ids]
        probs = _normalize_probs(probs)

        idx = _sample_from_probs(rng, probs)
        return legal[idx]

    def _sample_opponent_action(self, env: WhiskEnv, opponent: Mark, rng: random.Random, forbid: Coord) -> Coord:
        sampled = self._sample_model_action(env, opponent, rng, forbid=forbid)
        if sampled is not None:
            return sampled

        # Fallback: if model offered no option after forbidding, any legal move.
        legal = env.legal_actions(opponent)
        if not legal:
            raise RuntimeError("Opponent has no legal actions")
        return rng.choice(legal)

    @staticmethod
    def _value_from_state(env: WhiskEnv, player: Mark) -> float:
        score_o = env.state.scores[Mark.O]
        score_x = env.state.scores[Mark.X]
        diff = score_o - score_x
        if player == Mark.X:
            diff = -diff

        # Scale to approximately [-1, 1] around game target scores.
        value = diff / 50.0
        if value > 1.0:
            return 1.0
        if value < -1.0:
            return -1.0
        return value
