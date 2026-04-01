"""Self-play loop driven by MCTS and policy-value model."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from collections import deque
import random
from dataclasses import dataclass
from typing import Dict, List

from .encoding import ActionCodec, StateEncoder
from .env import WhiskEnv
from .mcts import MCTS
from .model import WhiskPolicyValueModel
from ..game import Mark, MAX_PIECES_PER_PLAYER, Piece, score_for_move

POST_MIN_DELAY_SEC = 0.1
POST_MAX_DELAY_SEC = 0.5
POST_SIMULTANEOUS_EPSILON_SEC = 0.02
DEFENSE_NEAR_GOAL_SCORE = 44
DEFENSE_THREAT_FLOOR = 1
DEFENSE_HIGH_THREAT_FLOOR = 4
DEFENSE_PENALTY_BASE = 0.18
DEFENSE_PENALTY_HIGH_THREAT = 0.22
DEFENSE_PENALTY_NEAR_GOAL = 0.30
DEFENSE_PENALTY_FORCED_LOSS = 0.35
DEFENSE_PENALTY_MULTI_THREAT = 0.08
DEFENSE_PENALTY_MAX = 0.85


@dataclass
class SelfPlayConfig:
    games: int = 20
    seed: int = 0
    max_turns: int = 200
    simulations: int = 48


def _immediate_move_score(env: WhiskEnv, mark: Mark, action: tuple[int, int]) -> int:
    """Points `mark` would gain by committing `action` now."""
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
        for p in dq:
            occ[(p.row, p.col)] = piece_mark

    return score_for_move(occ, mark, action)


def _opponent_immediate_threat_profile(env: WhiskEnv, mark: Mark) -> tuple[int, int]:
    """Return (max immediate score, number of immediate scoring moves) for `mark`."""
    legal = env.legal_actions(mark)
    if not legal:
        return (0, 0)

    best = 0
    scoring_moves = 0
    for action in legal:
        score = _immediate_move_score(env, mark, action)
        if score > 0:
            scoring_moves += 1
        if score > best:
            best = score
    return (best, scoring_moves)


def _defense_threat_penalty(
    *,
    opponent_score: int,
    opponent_threat: int,
    opponent_scoring_moves: int,
) -> float:
    if opponent_threat < DEFENSE_THREAT_FLOOR and opponent_scoring_moves <= 0:
        return 0.0

    penalty = DEFENSE_PENALTY_BASE
    if opponent_scoring_moves >= 2:
        penalty += DEFENSE_PENALTY_MULTI_THREAT
    if opponent_threat >= DEFENSE_HIGH_THREAT_FLOOR:
        penalty += DEFENSE_PENALTY_HIGH_THREAT
    if opponent_score >= DEFENSE_NEAR_GOAL_SCORE and opponent_threat >= DEFENSE_THREAT_FLOOR:
        penalty += DEFENSE_PENALTY_NEAR_GOAL
    if opponent_score + opponent_threat >= 50:
        penalty += DEFENSE_PENALTY_FORCED_LOSS
    return min(DEFENSE_PENALTY_MAX, max(0.0, penalty))


def _shape_value_target(example: Dict[str, object], terminal_value: float) -> float:
    """Apply explicit penalty when opponent has immediate scoring threats."""
    opp_score = int(example.get("opp_score_now", 0))
    opp_threat = int(example.get("opp_immediate_threat", 0))
    opp_scoring_moves = int(example.get("opp_scoring_moves", 0))
    penalty = _defense_threat_penalty(
        opponent_score=opp_score,
        opponent_threat=opp_threat,
        opponent_scoring_moves=opp_scoring_moves,
    )
    return max(-1.0, min(1.0, float(terminal_value) - penalty))


def _generate_examples_shard(
    model_table: Dict[object, object],
    games: int,
    seed: int,
    max_turns: int,
    simulations: int,
    shard_offset: int,
    progress_interval: int = 0,
) -> List[Dict[str, object]]:
    model = WhiskPolicyValueModel()
    model.table = model_table

    examples: List[Dict[str, object]] = []
    for local_game_idx in range(games):
        game_idx = shard_offset + local_game_idx
        rng = random.Random(seed + local_game_idx)
        env = WhiskEnv(mode="remote")
        env.reset(seed=seed + local_game_idx)
        mcts = MCTS(
            model=model,
            simulations=simulations,
            rollout_max_turns=max(12, max_turns),
        )

        game_traces: List[Dict[str, object]] = []

        while not env.is_terminal() and env.state.turn < max_turns:
            obs_o = StateEncoder.encode_observation(env.state, Mark.O)
            obs_x = StateEncoder.encode_observation(env.state, Mark.X)
            opp_threat_for_o, opp_scoring_for_o = _opponent_immediate_threat_profile(env, Mark.X)
            opp_threat_for_x, opp_scoring_for_x = _opponent_immediate_threat_profile(env, Mark.O)
            opp_score_for_o = int(env.state.scores[Mark.X])
            opp_score_for_x = int(env.state.scores[Mark.O])

            pi_o = mcts.search(env, Mark.O, rng)
            pi_x = mcts.search(env, Mark.X, rng)

            action_o = SelfPlayRunner._sample_action(pi_o, obs_o["legal_action_mask"], rng)
            action_x = SelfPlayRunner._sample_action(pi_x, obs_x["legal_action_mask"], rng)

            # Resolve collisions by post order: only the second submitter retries.
            if action_o is not None and action_x is not None and action_o == action_x:
                o_delay = rng.uniform(POST_MIN_DELAY_SEC, POST_MAX_DELAY_SEC)
                x_delay = rng.uniform(POST_MIN_DELAY_SEC, POST_MAX_DELAY_SEC)
                if abs(o_delay - x_delay) <= POST_SIMULTANEOUS_EPSILON_SEC:
                    first_mark = rng.choice([Mark.O, Mark.X])
                elif o_delay < x_delay:
                    first_mark = Mark.O
                else:
                    first_mark = Mark.X
                if first_mark == Mark.O:
                    # X is second mover; reroute X if it collided with O.
                    action_x = SelfPlayRunner._sample_action(
                        pi_x, obs_x["legal_action_mask"], rng, forbid=action_o
                    )
                else:
                    # O is second mover; reroute O if it collided with X.
                    action_o = SelfPlayRunner._sample_action(
                        pi_o, obs_o["legal_action_mask"], rng, forbid=action_x
                    )

            if action_o is None or action_x is None:
                break

            env.step_joint(action_o, action_x)

            game_traces.append(
                {
                    "game": game_idx,
                    "player": "O",
                    "obs": obs_o,
                    "policy_target": pi_o,
                    "opp_score_now": opp_score_for_o,
                    "opp_immediate_threat": opp_threat_for_o,
                    "opp_scoring_moves": opp_scoring_for_o,
                }
            )
            game_traces.append(
                {
                    "game": game_idx,
                    "player": "X",
                    "obs": obs_x,
                    "policy_target": pi_x,
                    "opp_score_now": opp_score_for_x,
                    "opp_immediate_threat": opp_threat_for_x,
                    "opp_scoring_moves": opp_scoring_for_x,
                }
            )

        winner = env.winner()
        for ex in game_traces:
            if winner in (None, "TIE"):
                z = 0.0
            elif winner == ex["player"]:
                z = 1.0
            else:
                z = -1.0
            ex["value_target"] = _shape_value_target(ex, z)
            examples.append(ex)

        if progress_interval > 0 and (
            (local_game_idx + 1) % progress_interval == 0 or (local_game_idx + 1) == games
        ):
            print(
                f"[selfplay] completed {local_game_idx + 1}/{games} games in shard "
                f"(seed={seed}, offset={shard_offset})",
                flush=True,
            )

    return examples


class SelfPlayRunner:
    def __init__(self, model: WhiskPolicyValueModel, config: SelfPlayConfig) -> None:
        self.model = model
        self.config = config

    def generate_examples(self, workers: int = 1) -> List[Dict[str, object]]:
        if self.config.games <= 0:
            raise ValueError("games must be > 0")
        progress_interval = max(1, self.config.games // 10)
        if workers <= 1:
            return _generate_examples_shard(
                model_table=self.model.table,
                games=self.config.games,
                seed=self.config.seed,
                max_turns=self.config.max_turns,
                simulations=self.config.simulations,
                shard_offset=0,
                progress_interval=progress_interval,
            )

        workers = min(max(1, workers), self.config.games)
        base = self.config.games // workers
        rem = self.config.games % workers
        shard_sizes = [base + (1 if i < rem else 0) for i in range(workers)]

        futures = []
        out: List[Dict[str, object]] = []
        offset = 0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, sz in enumerate(shard_sizes):
                if sz <= 0:
                    continue
                futures.append(
                    ex.submit(
                        _generate_examples_shard,
                        self.model.table,
                        sz,
                        self.config.seed + i * 10_000,
                        self.config.max_turns,
                        self.config.simulations,
                        offset,
                        progress_interval,
                    )
                )
                offset += sz

            for fut in as_completed(futures):
                out.extend(fut.result())

        return out

    @staticmethod
    def _sample_action(
        policy: List[float],
        legal_mask_obj: object,
        rng: random.Random,
        forbid: tuple[int, int] | None = None,
    ) -> tuple[int, int] | None:
        legal_mask = list(legal_mask_obj)
        legal_ids = [i for i, bit in enumerate(legal_mask) if bit]
        if forbid is not None:
            forbid_id = ActionCodec.coord_to_action(*forbid)
            legal_ids = [i for i in legal_ids if i != forbid_id]
        if not legal_ids:
            return None

        probs = [max(0.0, policy[i]) for i in legal_ids]
        s = sum(probs)
        if s <= 0:
            probs = [1.0 / len(legal_ids)] * len(legal_ids)
        else:
            probs = [p / s for p in probs]

        r = rng.random()
        acc = 0.0
        choice = legal_ids[-1]
        for i, p in enumerate(probs):
            acc += p
            if r <= acc:
                choice = legal_ids[i]
                break

        return ActionCodec.action_to_coord(choice)
