"""Self-play loop driven by MCTS and policy-value model."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import random
from dataclasses import dataclass
from typing import Dict, List

from .encoding import ActionCodec, StateEncoder
from .env import WhiskEnv
from .mcts import MCTS
from .model import WhiskPolicyValueModel
from ..game import Mark

POST_MIN_DELAY_SEC = 0.1
POST_MAX_DELAY_SEC = 0.5
POST_SIMULTANEOUS_EPSILON_SEC = 0.02


@dataclass
class SelfPlayConfig:
    games: int = 20
    seed: int = 0
    max_turns: int = 200
    simulations: int = 48


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
                }
            )
            game_traces.append(
                {
                    "game": game_idx,
                    "player": "X",
                    "obs": obs_x,
                    "policy_target": pi_x,
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
            ex["value_target"] = z
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
