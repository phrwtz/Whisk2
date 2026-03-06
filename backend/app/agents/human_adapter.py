"""Human-vs-agent adapter for live server games."""

from __future__ import annotations

import os
import random
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

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
                # If the human move is already pending, use direct priors instead of
                # full MCTS (which expects both sides to have legal actions).
                if state.pending[Mark.O] is not None or state.pending[Mark.X] is not None:
                    priors, _ = self.model.predict(obs)
                    best_id = max(legal_ids, key=lambda i: priors[i])
                    top = sorted(legal_ids, key=lambda i: priors[i], reverse=True)[:3]
                    best_row, best_col = ActionCodec.action_to_coord(best_id)
                    return BotDecision(
                        row=best_row,
                        col=best_col,
                        source="model_prior",
                        candidates=[
                            BotCandidate(
                                row=ActionCodec.action_to_coord(i)[0],
                                col=ActionCodec.action_to_coord(i)[1],
                                score=float(priors[i]),
                            )
                            for i in top
                        ],
                    )

                mcts = MCTS(model=self.model, simulations=12, rollout_max_turns=80)
                pi = mcts.search(env, bot_mark, self.rng)
                best_id = max(legal_ids, key=lambda i: pi[i])
                top = sorted(legal_ids, key=lambda i: pi[i], reverse=True)[:3]
                best_row, best_col = ActionCodec.action_to_coord(best_id)
                return BotDecision(
                    row=best_row,
                    col=best_col,
                    source="mcts",
                    candidates=[
                        BotCandidate(
                            row=ActionCodec.action_to_coord(i)[0],
                            col=ActionCodec.action_to_coord(i)[1],
                            score=float(pi[i]),
                        )
                        for i in top
                    ],
                )

        row, col = self.greedy.select_action(env, bot_mark, self.rng)
        return BotDecision(
            row=row,
            col=col,
            source="greedy",
            candidates=[BotCandidate(row=row, col=col, score=1.0)],
        )
