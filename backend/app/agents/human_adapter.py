"""Human-vs-agent adapter for live server games."""

from __future__ import annotations

import math
import os
import random
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .baselines import GreedyScoreAgent
from .encoding import ActionCodec, StateEncoder
from .env import WhiskEnv
from .mcts import MCTS
from .model import WhiskPolicyValueModel
from ..game import GameState, Mark

Coord = Tuple[int, int]


@dataclass
class BotCandidate:
    row: int
    col: int
    score: float


@dataclass
class BotDecision:
    row: int
    col: int
    source: str
    candidates: List[BotCandidate]


class HumanVsAgentSession:
    """Selects bot actions from a promoted checkpoint when available."""

    def __init__(self, checkpoint_path: Optional[Path] = None, seed: int = 0) -> None:
        if checkpoint_path is None:
            checkpoint_path = Path(
                os.getenv("WHISK_BOT_CHECKPOINT", "artifacts/releases/whiskbot_latest.pkl")
            )
        self.checkpoint_path = checkpoint_path
        self.rng = random.Random(seed)
        self.model: Optional[WhiskPolicyValueModel] = None
        self.greedy = GreedyScoreAgent()
        self.stochastic_temperature = self._env_float("WHISK_BOT_TEMPERATURE", 0.18, min_value=0.01, max_value=2.0)
        self.stochastic_epsilon = self._env_float("WHISK_BOT_EPSILON", 0.05, min_value=0.0, max_value=0.5)
        self.stochastic_top_k = self._env_int("WHISK_BOT_TOP_K", 5, min_value=1, max_value=16)
        if checkpoint_path.exists():
            try:
                self.model = WhiskPolicyValueModel.load(checkpoint_path)
            except Exception:
                self.model = None

    def choose_action(self, state: GameState, bot_mark: Mark) -> Coord:
        decision = self.choose_decision(state, bot_mark)
        return (decision.row, decision.col)

    def choose_decision(self, state: GameState, bot_mark: Mark) -> BotDecision:
        """Choose a legal move for `bot_mark` from the current shared state."""
        env = WhiskEnv(mode="remote")
        env.state = deepcopy(state)

        legal = env.legal_actions(bot_mark)
        if not legal:
            raise RuntimeError("Bot has no legal actions")

        if self.model is not None:
            obs = StateEncoder.encode_observation(env.state, bot_mark)
            legal_ids = [i for i, bit in enumerate(obs["legal_action_mask"]) if bit]
            if legal_ids:
                opponent = Mark.X if bot_mark == Mark.O else Mark.O
                # In Human-vs-Bot mode, the human's pending move is known at this point.
                # Evaluate each legal bot response by committing the pending turn.
                if state.pending[opponent] is not None:
                    return self._choose_pending_lookahead(env, bot_mark, legal_ids, obs)

                mcts = MCTS(model=self.model, simulations=12, rollout_max_turns=80)
                pi = mcts.search(env, bot_mark, self.rng)
                scores = {i: float(pi[i]) for i in legal_ids}
                chosen_id = self._sample_action_from_scores(scores)
                return self._build_decision(chosen_id, "mcts", scores)

        row, col = self.greedy.select_action(env, bot_mark, self.rng)
        return BotDecision(
            row=row,
            col=col,
            source="greedy",
            candidates=[BotCandidate(row=row, col=col, score=1.0)],
        )

    def _choose_pending_lookahead(
        self,
        env: WhiskEnv,
        bot_mark: Mark,
        legal_ids: List[int],
        obs: Dict[str, object],
    ) -> BotDecision:
        assert self.model is not None
        opponent = Mark.X if bot_mark == Mark.O else Mark.O
        priors, _ = self.model.predict(obs)
        scores: Dict[int, float] = {}

        for action_id in legal_ids:
            action = ActionCodec.action_to_coord(action_id)
            sim_env = env.clone()
            try:
                sim_env.reserve_move(bot_mark, action)
                if not sim_env.ready_to_commit():
                    continue
                committed = sim_env.commit_pending_turn()
            except Exception:
                continue

            next_obs = StateEncoder.encode_observation(sim_env.state, bot_mark)
            _, next_value = self.model.predict(next_obs)

            added_self = int(committed.added.get(bot_mark.value, 0))
            added_opp = int(committed.added.get(opponent.value, 0))
            prior = float(priors[action_id])

            terminal_bonus = 0.0
            if committed.done:
                if committed.winner == bot_mark.value:
                    terminal_bonus = 1.0
                elif committed.winner == opponent.value:
                    terminal_bonus = -1.0

            # Blend immediate scoring swing with post-commit model value.
            utility = (
                (1.5 * float(next_value))
                + (0.35 * float(added_self - added_opp))
                + (0.20 * prior)
                + (0.60 * terminal_bonus)
            )
            scores[action_id] = utility

        if not scores:
            scores = {i: float(priors[i]) for i in legal_ids}

        chosen_id = self._sample_action_from_scores(scores)
        return self._build_decision(chosen_id, "model_lookahead", scores)

    def _build_decision(self, chosen_id: int, source: str, scores: Dict[int, float]) -> BotDecision:
        chosen_row, chosen_col = ActionCodec.action_to_coord(chosen_id)
        top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return BotDecision(
            row=chosen_row,
            col=chosen_col,
            source=source,
            candidates=[
                BotCandidate(
                    row=ActionCodec.action_to_coord(i)[0],
                    col=ActionCodec.action_to_coord(i)[1],
                    score=float(score),
                )
                for i, score in top
            ],
        )

    def _sample_action_from_scores(self, scores: Dict[int, float]) -> int:
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked:
            raise RuntimeError("No candidate scores available for bot decision")

        shortlist = [action_id for action_id, _ in ranked[: min(len(ranked), self.stochastic_top_k)]]
        if len(shortlist) == 1:
            return shortlist[0]

        if self.stochastic_epsilon > 0 and self.rng.random() < self.stochastic_epsilon:
            return self.rng.choice(shortlist)

        temperature = max(0.01, self.stochastic_temperature)
        raw_scores = [scores[action_id] for action_id in shortlist]
        max_score = max(raw_scores)
        weights = [math.exp((score - max_score) / temperature) for score in raw_scores]
        return self._sample_weighted(shortlist, weights)

    def _sample_weighted(self, choices: List[int], weights: List[float]) -> int:
        total = sum(weights)
        if total <= 0:
            return self.rng.choice(choices)
        target = self.rng.random() * total
        accum = 0.0
        for action_id, weight in zip(choices, weights):
            accum += weight
            if accum >= target:
                return action_id
        return choices[-1]

    @staticmethod
    def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return max(min_value, min(max_value, value))

    @staticmethod
    def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(min_value, min(max_value, value))
