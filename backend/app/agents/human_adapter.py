"""Human-vs-agent adapter for live server games."""

from __future__ import annotations

from collections import deque
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
from ..game import GameState, Mark, MAX_PIECES_PER_PLAYER, Piece, score_for_move

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
        self.live_mcts_simulations = self._env_int("WHISK_BOT_MCTS_SIMS", 24, min_value=8, max_value=256)
        self.stochastic_temperature = self._env_float("WHISK_BOT_TEMPERATURE", 0.18, min_value=0.01, max_value=2.0)
        self.stochastic_epsilon = self._env_float("WHISK_BOT_EPSILON", 0.05, min_value=0.0, max_value=0.5)
        self.stochastic_top_k = self._env_int("WHISK_BOT_TOP_K", 5, min_value=1, max_value=16)
        self.pending_eval_top_k = self._env_int("WHISK_BOT_PENDING_EVAL_TOP_K", 10, min_value=2, max_value=24)
        self.pending_rollouts = self._env_int("WHISK_BOT_PENDING_ROLLOUTS", 3, min_value=0, max_value=16)
        self.pending_rollout_turns = self._env_int("WHISK_BOT_PENDING_ROLLOUT_TURNS", 4, min_value=1, max_value=32)
        self.opening_tactical_enabled = bool(
            self._env_int("WHISK_BOT_OPENING_TACTICAL", 1, min_value=0, max_value=1)
        )
        self.opening_two_ply_enabled = bool(
            self._env_int("WHISK_BOT_OPENING_2PLY", 1, min_value=0, max_value=1)
        )
        self.opening_two_ply_candidates = self._env_int(
            "WHISK_BOT_OPENING_2PLY_CANDIDATES", 8, min_value=2, max_value=24
        )
        self.opening_two_ply_responses = self._env_int(
            "WHISK_BOT_OPENING_2PLY_RESPONSES", 6, min_value=2, max_value=24
        )
        self.opening_two_ply_weight = self._env_float(
            "WHISK_BOT_OPENING_2PLY_WEIGHT", 0.45, min_value=0.0, max_value=2.0
        )
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

        opponent = Mark.X if bot_mark == Mark.O else Mark.O
        legal_ids = [ActionCodec.coord_to_action(*coord) for coord in legal]
        must_block_ids = self._must_block_imminent_five_ids(env, bot_mark, opponent, legal_ids)
        if must_block_ids:
            scores = {action_id: 0.0 for action_id in must_block_ids}
            if self.model is not None:
                obs = StateEncoder.encode_observation(env.state, bot_mark)
                priors, _ = self.model.predict(obs)
                for action_id in must_block_ids:
                    scores[action_id] = float(priors[action_id])
            chosen_id = max(scores, key=scores.get)
            return self._build_decision(chosen_id, "must_block", scores)

        if self.opening_tactical_enabled and state.pending[opponent] is None and self._is_opening_phase(state):
            opening_decision = self._choose_opening_tactical(env, bot_mark, opponent, legal_ids)
            if opening_decision is not None:
                return opening_decision

        if state.pending[opponent] is not None and self.model is None:
            pending_tactical = self._choose_pending_tactical(env, bot_mark, legal_ids)
            if pending_tactical is not None:
                return pending_tactical

        if self.model is not None:
            obs = StateEncoder.encode_observation(env.state, bot_mark)
            legal_ids = [i for i, bit in enumerate(obs["legal_action_mask"]) if bit]
            if legal_ids:
                # In Human-vs-Bot mode, the human's pending move is known at this point.
                # Evaluate each legal bot response by committing the pending turn.
                if state.pending[opponent] is not None:
                    return self._choose_pending_lookahead(env, bot_mark, legal_ids, obs)

                mcts = MCTS(model=self.model, simulations=self.live_mcts_simulations, rollout_max_turns=80)
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

    def _choose_opening_tactical(
        self,
        env: WhiskEnv,
        bot_mark: Mark,
        opponent: Mark,
        legal_ids: List[int],
    ) -> BotDecision | None:
        scores: Dict[int, float] = {}
        priors: List[float] | None = None
        if self.model is not None:
            obs = StateEncoder.encode_observation(env.state, bot_mark)
            priors, _ = self.model.predict(obs)

        for action_id in legal_ids:
            action = ActionCodec.action_to_coord(action_id)
            sim_env = env.clone()
            try:
                sim_env.reserve_move(bot_mark, action)
            except Exception:
                continue

            opp_best = self._max_immediate_score(sim_env, opponent)
            bot_best = self._max_immediate_score(sim_env, bot_mark)
            own_now = self._immediate_move_score(env, bot_mark, action)
            center = self._center_bias(action)
            prior = float(priors[action_id]) if priors is not None else 0.0

            opp_norm = min(9, opp_best) / 9.0
            bot_norm = min(9, bot_best) / 9.0
            own_norm = min(9, own_now) / 9.0

            score = (
                (0.55 * bot_norm)
                + (0.30 * own_norm)
                + (0.15 * prior)
                + (0.05 * center)
                - (0.95 * opp_norm)
            )
            if opp_best >= 9:
                score -= 1.5
            elif opp_best >= 4:
                score -= 0.6
            elif opp_best >= 1:
                score -= 0.2
            if bot_best >= 9:
                score += 0.9
            elif bot_best >= 4:
                score += 0.35
            scores[action_id] = score

        if self.opening_two_ply_enabled and self.opening_two_ply_weight > 0 and len(scores) > 1:
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            candidate_ids = [
                action_id
                for action_id, _ in ranked[: min(len(ranked), self.opening_two_ply_candidates)]
            ]
            for action_id in candidate_ids:
                scores[action_id] += self.opening_two_ply_weight * self._opening_two_ply_worst_case(
                    env=env,
                    bot_mark=bot_mark,
                    opponent=opponent,
                    action_id=action_id,
                )

        if not scores:
            return None
        chosen_id = max(scores, key=scores.get)
        return self._build_decision(chosen_id, "opening_tactical", scores)

    def _opening_two_ply_worst_case(
        self,
        env: WhiskEnv,
        bot_mark: Mark,
        opponent: Mark,
        action_id: int,
    ) -> float:
        action = ActionCodec.action_to_coord(action_id)
        sim_env = env.clone()
        try:
            sim_env.reserve_move(bot_mark, action)
        except Exception:
            return -1.0

        opp_legal = sim_env.legal_actions(opponent)
        if not opp_legal:
            return 0.0

        ranked_replies = sorted(
            opp_legal,
            key=lambda coord: (
                self._immediate_move_score(sim_env, opponent, coord),
                self._center_bias(coord),
            ),
            reverse=True,
        )

        worst_case = float("inf")
        for opp_action in ranked_replies[: min(len(ranked_replies), self.opening_two_ply_responses)]:
            utility = self._opening_two_ply_reply_utility(
                sim_env=sim_env,
                bot_mark=bot_mark,
                opponent=opponent,
                opp_action=opp_action,
            )
            worst_case = min(worst_case, utility)

        return 0.0 if not math.isfinite(worst_case) else worst_case

    def _opening_two_ply_reply_utility(
        self,
        sim_env: WhiskEnv,
        bot_mark: Mark,
        opponent: Mark,
        opp_action: Coord,
    ) -> float:
        reply_env = sim_env.clone()
        try:
            reply_env.reserve_move(opponent, opp_action)
            if not reply_env.ready_to_commit():
                return -1.0
            committed = reply_env.commit_pending_turn()
        except Exception:
            return -1.0

        added_self = int(committed.added.get(bot_mark.value, 0))
        added_opp = int(committed.added.get(opponent.value, 0))
        immediate_delta = max(-1.0, min(1.0, (added_self - added_opp) / 9.0))

        bot_threat = self._max_immediate_score(reply_env, bot_mark)
        opp_threat = self._max_immediate_score(reply_env, opponent)
        threat_margin = (min(9, bot_threat) - min(9, opp_threat)) / 9.0
        score_value = self._value_from_scores(reply_env.state, bot_mark)

        terminal_bonus = 0.0
        if committed.done:
            if committed.winner == bot_mark.value:
                terminal_bonus = 1.0
            elif committed.winner == opponent.value:
                terminal_bonus = -1.0

        return (
            (0.55 * threat_margin)
            + (0.30 * immediate_delta)
            + (0.15 * score_value)
            + (0.75 * terminal_bonus)
        )

    def _choose_pending_tactical(
        self,
        env: WhiskEnv,
        bot_mark: Mark,
        legal_ids: List[int],
    ) -> BotDecision | None:
        opponent = Mark.X if bot_mark == Mark.O else Mark.O
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

            added_self = int(committed.added.get(bot_mark.value, 0))
            added_opp = int(committed.added.get(opponent.value, 0))
            immediate_delta = max(-1.0, min(1.0, (added_self - added_opp) / 9.0))

            bot_threat = self._max_immediate_score(sim_env, bot_mark)
            opp_threat = self._max_immediate_score(sim_env, opponent)
            threat_margin = (min(9, bot_threat) - min(9, opp_threat)) / 9.0
            center = self._center_bias(action)
            score = (0.70 * immediate_delta) + (0.62 * threat_margin) + (0.10 * center)

            if opp_threat >= 9:
                score -= 1.30
            elif opp_threat >= 4:
                score -= 0.55
            if bot_threat >= 9:
                score += 0.65
            elif bot_threat >= 4:
                score += 0.25

            if committed.done:
                if committed.winner == bot_mark.value:
                    score += 1.0
                elif committed.winner == opponent.value:
                    score -= 1.0

            scores[action_id] = score

        if not scores:
            return None
        chosen_id = max(scores, key=scores.get)
        # Keep source stable for frontend handling.
        return self._build_decision(chosen_id, "opening_tactical", scores)

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
        ordered = sorted(legal_ids, key=lambda i: priors[i], reverse=True)
        candidate_ids = ordered[: min(len(ordered), self.pending_eval_top_k)]

        for action_id in candidate_ids:
            scores[action_id] = self._pending_action_utility(
                env=env,
                bot_mark=bot_mark,
                opponent=opponent,
                action_id=action_id,
                prior=float(priors[action_id]),
            )

        if not scores:
            scores = {i: float(priors[i]) for i in legal_ids}
        elif len(scores) == 1:
            # Keep at least two actions in consideration to avoid fully rigid replies.
            for action_id in ordered:
                if action_id not in scores:
                    scores[action_id] = float(priors[action_id])
                    break

        chosen_id = self._sample_action_from_scores(scores)
        return self._build_decision(chosen_id, "model_lookahead", scores)

    def _pending_action_utility(
        self,
        env: WhiskEnv,
        bot_mark: Mark,
        opponent: Mark,
        action_id: int,
        prior: float,
    ) -> float:
        assert self.model is not None
        action = ActionCodec.action_to_coord(action_id)
        sim_env = env.clone()
        try:
            sim_env.reserve_move(bot_mark, action)
            if not sim_env.ready_to_commit():
                return -1e9
            committed = sim_env.commit_pending_turn()
        except Exception:
            return -1e9

        next_obs = StateEncoder.encode_observation(sim_env.state, bot_mark)
        _, next_value = self.model.predict(next_obs)

        added_self = int(committed.added.get(bot_mark.value, 0))
        added_opp = int(committed.added.get(opponent.value, 0))
        immediate_delta = added_self - added_opp

        terminal_bonus = 0.0
        if committed.done:
            if committed.winner == bot_mark.value:
                terminal_bonus = 1.0
            elif committed.winner == opponent.value:
                terminal_bonus = -1.0

        # Tactical terms: can we threaten points next turn, and can opponent?
        bot_threat = self._max_immediate_score(sim_env, bot_mark)
        opp_threat = self._max_immediate_score(sim_env, opponent)
        bot_threat_norm = min(9, bot_threat) / 9.0
        opp_threat_norm = min(9, opp_threat) / 9.0
        minimax_margin = self._one_turn_minimax_margin(sim_env, bot_mark, opponent)

        rollout_value = self._rollout_value(sim_env, bot_mark)

        return (
            (1.25 * float(next_value))
            + (0.45 * float(immediate_delta))
            + (0.35 * rollout_value)
            + (0.30 * minimax_margin)
            + (0.20 * prior)
            + (0.12 * bot_threat_norm)
            - (0.20 * opp_threat_norm)
            + (0.75 * terminal_bonus)
        )

    def _rollout_value(self, env: WhiskEnv, bot_mark: Mark) -> float:
        if self.pending_rollouts <= 0:
            return self._value_from_scores(env.state, bot_mark)

        values: List[float] = []
        for _ in range(self.pending_rollouts):
            sim_env = env.clone()
            for _ in range(self.pending_rollout_turns):
                if sim_env.is_terminal():
                    break
                a_o = self._sample_model_action(sim_env, Mark.O)
                a_x = self._sample_model_action(sim_env, Mark.X)
                if a_o is None or a_x is None:
                    break
                if a_o == a_x:
                    # Only second mover reroutes on collision.
                    first_mark = self.rng.choice([Mark.O, Mark.X])
                    if first_mark == Mark.O:
                        a_x = self._sample_model_action(sim_env, Mark.X, forbid=a_o)
                    else:
                        a_o = self._sample_model_action(sim_env, Mark.O, forbid=a_x)
                    if a_o is None or a_x is None:
                        break
                sim_env.step_joint(a_o, a_x)

            values.append(self._value_from_scores(sim_env.state, bot_mark))

        if not values:
            return self._value_from_scores(env.state, bot_mark)
        return sum(values) / len(values)

    def _sample_model_action(
        self,
        env: WhiskEnv,
        mark: Mark,
        forbid: Coord | None = None,
    ) -> Coord | None:
        legal = env.legal_actions(mark)
        if forbid is not None:
            legal = [coord for coord in legal if coord != forbid]
        if not legal:
            return None

        if self.model is None:
            return self.rng.choice(legal)

        obs = StateEncoder.encode_observation(env.state, mark)
        priors, _ = self.model.predict(obs)
        legal_ids = [ActionCodec.coord_to_action(*coord) for coord in legal]
        weights = [max(0.0, float(priors[action_id])) for action_id in legal_ids]
        total = sum(weights)
        if total <= 0:
            return self.rng.choice(legal)

        r = self.rng.random() * total
        acc = 0.0
        for coord, weight in zip(legal, weights):
            acc += weight
            if acc >= r:
                return coord
        return legal[-1]

    def _max_immediate_score(self, env: WhiskEnv, mark: Mark) -> int:
        legal = env.legal_actions(mark)
        if not legal:
            return 0
        return max(self._immediate_move_score(env, mark, coord) for coord in legal)

    def _one_turn_minimax_margin(self, env: WhiskEnv, bot_mark: Mark, opponent: Mark) -> float:
        bot_best = self._max_immediate_score(env, bot_mark)
        opp_best = self._max_immediate_score(env, opponent)
        return (min(9, bot_best) - min(9, opp_best)) / 9.0

    @staticmethod
    def _immediate_move_score(env: WhiskEnv, mark: Mark, action: Coord) -> int:
        state = deepcopy(env.state)
        row, col = action

        pieces = {
            Mark.O: deque(state.pieces[Mark.O]),
            Mark.X: deque(state.pieces[Mark.X]),
        }

        pieces[mark].append(Piece(mark=mark, row=row, col=col, turn_placed=state.turn + 1))
        while len(pieces[mark]) > MAX_PIECES_PER_PLAYER:
            pieces[mark].popleft()

        occ = {}
        for piece_mark, dq in pieces.items():
            for piece in dq:
                occ[(piece.row, piece.col)] = piece_mark

        return score_for_move(occ, mark, action)

    @staticmethod
    def _value_from_scores(state: GameState, mark: Mark) -> float:
        diff = state.scores[Mark.O] - state.scores[Mark.X]
        if mark == Mark.X:
            diff = -diff
        value = diff / 50.0
        return max(-1.0, min(1.0, value))

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

    def _imminent_five_blockers(
        self,
        env: WhiskEnv,
        opponent: Mark,
        legal_ids: List[int],
    ) -> List[int]:
        threats = {
            coord
            for coord in env.legal_actions(opponent)
            if self._immediate_move_score(env, opponent, coord) >= 9
        }
        if not threats:
            return []
        return [action_id for action_id in legal_ids if ActionCodec.action_to_coord(action_id) in threats]

    @staticmethod
    def _is_opening_phase(state: GameState) -> bool:
        return (
            len(state.pieces[Mark.O]) < MAX_PIECES_PER_PLAYER
            and len(state.pieces[Mark.X]) < MAX_PIECES_PER_PLAYER
        )

    @staticmethod
    def _center_bias(action: Coord) -> float:
        row, col = action
        # Normalize to roughly [-1, 0], higher is better (near center).
        return -(((row - 3.5) ** 2 + (col - 3.5) ** 2) / 24.5)

    def _must_block_imminent_five_ids(
        self,
        env: WhiskEnv,
        bot_mark: Mark,
        opponent: Mark,
        legal_ids: List[int],
    ) -> List[int]:
        if env.state.pending[opponent] is not None:
            # Pending opponent move is known in human-vs-bot mode. Evaluate our legal replies
            # after commit and force a block only when some replies avoid an immediate 5 threat
            # and others do not.
            opp_best_by_action: Dict[int, int] = {}
            for action_id in legal_ids:
                action = ActionCodec.action_to_coord(action_id)
                sim_env = env.clone()
                try:
                    sim_env.reserve_move(bot_mark, action)
                    if not sim_env.ready_to_commit():
                        continue
                    sim_env.commit_pending_turn()
                except Exception:
                    continue
                opp_best_by_action[action_id] = self._max_immediate_score(sim_env, opponent)

            if not opp_best_by_action:
                return []
            min_opp_best = min(opp_best_by_action.values())
            has_risky = any(score >= 9 for score in opp_best_by_action.values())
            if not has_risky or min_opp_best >= 9:
                return []
            return [action_id for action_id, score in opp_best_by_action.items() if score == min_opp_best]

        opponent_threat_blocks = self._imminent_five_blockers(env, opponent, legal_ids)
        if not opponent_threat_blocks:
            return []

        # "Truly forced" only when there is exactly one lethal threat square.
        threat_coords = {
            ActionCodec.action_to_coord(action_id)
            for action_id in opponent_threat_blocks
        }
        if len(threat_coords) != 1:
            return []

        # If we have our own immediate 5-point threat, don't force a defensive block.
        own_winning_actions = self._imminent_five_blockers(env, bot_mark, legal_ids)
        if own_winning_actions:
            return []

        return opponent_threat_blocks

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
