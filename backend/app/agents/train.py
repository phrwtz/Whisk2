"""Milestone-6 trainer: long-run hardened generation loop.

Adds:
- replay buffer persistence
- resume-from-checkpoint support
- multi-worker self-play sharding
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .baselines import RandomAgent
from .checkpoints import CheckpointManager
from .encoding import ActionCodec, StateEncoder
from .env import WhiskEnv
from .eval import Arena
from .mcts import MCTS
from .model import WhiskPolicyValueModel
from .replay import ReplayBuffer
from .selfplay import SelfPlayConfig, SelfPlayRunner
from ..game import Mark


@dataclass
class TrainConfig:
    iterations: int = 3
    games_per_iteration: int = 16
    selfplay_max_turns: int = 160
    selfplay_simulations: int = 32
    selfplay_workers: int = 1
    eval_games: int = 24
    eval_max_turns: int = 120
    promotion_games: int = 16
    promotion_threshold: float = 0.55
    replay_capacity: int = 20000
    replay_sample_size: int = 4000
    seed: int = 0
    resume: bool = False


class ModelAgent:
    """Agent wrapper that uses MCTS + model to choose actions."""

    name = "mcts_model"

    def __init__(self, model: WhiskPolicyValueModel, simulations: int = 48, rollout_max_turns: int = 80) -> None:
        self.model = model
        self.simulations = simulations
        self.rollout_max_turns = rollout_max_turns

    def select_action(self, env: WhiskEnv, mark: Mark, rng: random.Random) -> tuple[int, int]:
        mcts = MCTS(
            model=self.model,
            simulations=self.simulations,
            rollout_max_turns=self.rollout_max_turns,
        )
        obs = StateEncoder.encode_observation(env.state, mark)
        pi = mcts.search(env, mark, rng)

        legal_ids = [i for i, bit in enumerate(obs["legal_action_mask"]) if bit]
        if not legal_ids:
            raise RuntimeError("No legal actions")

        best_id = max(legal_ids, key=lambda i: pi[i])
        return ActionCodec.action_to_coord(best_id)


class Trainer:
    def __init__(self, config: TrainConfig) -> None:
        self.config = config
        self.best_model = WhiskPolicyValueModel()

    def train(self, checkpoint_path: Path, replay_path: Path | None = None) -> Dict[str, object]:
        if self.config.iterations <= 0:
            raise ValueError("iterations must be > 0")
        if self.config.promotion_games <= 0:
            raise ValueError("promotion_games must be > 0")

        manager = CheckpointManager(checkpoint_path.parent)
        replay_path = replay_path or (checkpoint_path.parent / "replay_buffer.pkl")

        if replay_path.exists():
            replay = ReplayBuffer.load(replay_path)
            replay.capacity = self.config.replay_capacity
        else:
            replay = ReplayBuffer(capacity=self.config.replay_capacity)

        promoted_generations: List[int] = []

        if self.config.resume and manager.best_path() is not None:
            best_path = manager.best_path()
            assert best_path is not None
            self.best_model = WhiskPolicyValueModel.load(best_path)
            start_generation = manager.last_generation() + 1
            for rec in manager.load_manifest():
                if rec.get("promoted") and int(rec.get("generation", 0)) > 0:
                    promoted_generations.append(int(rec["generation"]))
        else:
            # Fresh run writes generation 0 bootstrap checkpoint.
            manager.save_generation(
                model=self.best_model,
                generation=0,
                promoted=True,
                metrics={"bootstrap": 1},
            )
            start_generation = 1

        end_generation = start_generation + self.config.iterations - 1

        best_win_rate_vs_random = self.evaluate_vs_random(self.best_model, seed=self.config.seed)
        latest_examples = 0

        for generation in range(start_generation, end_generation + 1):
            # Deterministic per-generation seed spacing.
            gen_seed = self.config.seed + generation * 10_000

            sp = SelfPlayRunner(
                model=self.best_model,
                config=SelfPlayConfig(
                    games=self.config.games_per_iteration,
                    seed=gen_seed,
                    max_turns=self.config.selfplay_max_turns,
                    simulations=self.config.selfplay_simulations,
                ),
            )
            try:
                examples = sp.generate_examples(workers=self.config.selfplay_workers)
            except Exception:
                # Safe fallback if multiprocessing is unavailable in runtime.
                examples = sp.generate_examples(workers=1)
            latest_examples = len(examples)

            replay.add_examples(examples)
            replay.save(replay_path)

            # Train candidate from best using replay sample.
            candidate_model = self.best_model.copy()
            train_batch = replay.sample(min(self.config.replay_sample_size, len(replay.items)), seed=gen_seed)
            if train_batch:
                candidate_model.train_on_examples(train_batch)

            head_to_head = self.evaluate_candidate_vs_best(
                candidate_model,
                self.best_model,
                games=self.config.promotion_games,
                seed=gen_seed + 2000,
            )
            promoted = head_to_head["candidate_win_rate"] >= self.config.promotion_threshold

            candidate_random = self.evaluate_vs_random(
                candidate_model,
                seed=gen_seed + 3000,
            )

            metrics = {
                "candidate_win_rate_vs_best": head_to_head["candidate_win_rate"],
                "candidate_wins": head_to_head["candidate_wins"],
                "best_wins": head_to_head["best_wins"],
                "ties": head_to_head["ties"],
                "candidate_win_rate_vs_random": candidate_random,
                "examples": latest_examples,
                "replay_size": len(replay.items),
            }

            if promoted:
                self.best_model = candidate_model
                if generation not in promoted_generations:
                    promoted_generations.append(generation)
                best_win_rate_vs_random = max(best_win_rate_vs_random, candidate_random)
                manager.save_generation(
                    model=self.best_model,
                    generation=generation,
                    promoted=True,
                    metrics=metrics,
                )
            else:
                manager.save_generation(
                    model=candidate_model,
                    generation=generation,
                    promoted=False,
                    metrics=metrics,
                )

        best_path = manager.best_path() or checkpoint_path
        if best_path != checkpoint_path:
            loaded = WhiskPolicyValueModel.load(best_path)
            loaded.save(checkpoint_path)

        return {
            "iterations": self.config.iterations,
            "start_generation": start_generation,
            "end_generation": end_generation,
            "examples_last_iteration": latest_examples,
            "best_win_rate_vs_random": best_win_rate_vs_random,
            "checkpoint": str(checkpoint_path),
            "lineage_manifest": str(manager.manifest_path),
            "replay_path": str(replay_path),
            "replay_size": len(replay.items),
            "promoted_generations": promoted_generations,
        }

    def evaluate_vs_random(self, model: WhiskPolicyValueModel, seed: int = 0) -> float:
        rng = random.Random(seed)
        model_agent = ModelAgent(
            model,
            simulations=max(8, self.config.selfplay_simulations // 2),
            rollout_max_turns=self.config.eval_max_turns,
        )
        random_agent = RandomAgent()

        wins = 0
        total = self.config.eval_games
        for game_idx in range(total):
            env = WhiskEnv(mode="remote")
            env.reset(seed=seed + game_idx)

            while not env.is_terminal() and env.state.turn < self.config.eval_max_turns:
                a_o = model_agent.select_action(env, Mark.O, rng)
                a_x = random_agent.select_action(env, Mark.X, rng)
                if a_x == a_o:
                    legal_x = [c for c in env.legal_actions(Mark.X) if c != a_o]
                    if not legal_x:
                        break
                    a_x = rng.choice(legal_x)

                env.step_joint(a_o, a_x)

            winner = env.winner()
            if winner is None:
                if env.state.scores[Mark.O] > env.state.scores[Mark.X]:
                    winner = "O"
                elif env.state.scores[Mark.X] > env.state.scores[Mark.O]:
                    winner = "X"
                else:
                    winner = "TIE"

            if winner == "O":
                wins += 1

        return wins / max(1, total)

    def evaluate_candidate_vs_best(
        self,
        candidate: WhiskPolicyValueModel,
        best: WhiskPolicyValueModel,
        games: int,
        seed: int = 0,
    ) -> Dict[str, float | int]:
        arena = Arena(max_turns=self.config.eval_max_turns)

        games_as_o = games // 2
        games_as_x = games - games_as_o

        candidate_agent = ModelAgent(
            candidate,
            simulations=max(8, self.config.selfplay_simulations // 2),
            rollout_max_turns=self.config.eval_max_turns,
        )
        best_agent = ModelAgent(
            best,
            simulations=max(8, self.config.selfplay_simulations // 2),
            rollout_max_turns=self.config.eval_max_turns,
        )

        summary_o = arena.run(candidate_agent, best_agent, games=max(1, games_as_o), seed=seed)
        summary_x = arena.run(best_agent, candidate_agent, games=max(1, games_as_x), seed=seed + 5000)

        candidate_wins = summary_o.wins_o + summary_x.wins_x
        best_wins = summary_o.wins_x + summary_x.wins_o
        ties = summary_o.ties + summary_x.ties
        total = summary_o.games + summary_x.games

        return {
            "candidate_wins": candidate_wins,
            "best_wins": best_wins,
            "ties": ties,
            "candidate_win_rate": candidate_wins / max(1, total),
        }
