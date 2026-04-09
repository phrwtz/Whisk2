"""Milestone-6 trainer: long-run hardened generation loop.

Adds:
- replay buffer persistence
- resume-from-checkpoint support
- multi-worker self-play sharding
"""

from __future__ import annotations

import random
import time
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

POST_MIN_DELAY_SEC = 0.1
POST_MAX_DELAY_SEC = 0.5
POST_SIMULTANEOUS_EPSILON_SEC = 0.02


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
    train_passes: int = 2
    seed: int = 0
    resume: bool = False
    progress: bool = False
    # Optional stronger benchmark track to avoid random-opponent saturation.
    benchmark_games: int = 0
    benchmark_simulations: int = 96
    benchmark_anchor_gap: int = 24


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

    def _log(self, message: str) -> None:
        if self.config.progress:
            print(message, flush=True)

    def _candidate_eval_simulations(self) -> int:
        return max(8, self.config.selfplay_simulations // 2)

    def _benchmark_eval_simulations(self) -> int:
        return max(self._candidate_eval_simulations(), self.config.benchmark_simulations)

    def _select_benchmark_anchor(
        self,
        *,
        manifest: List[Dict[str, object]],
        start_generation: int,
    ) -> tuple[int, WhiskPolicyValueModel]:
        if not manifest:
            return (0, self.best_model.copy())

        anchor_ceiling = max(0, start_generation - max(0, self.config.benchmark_anchor_gap))

        candidates = [
            rec
            for rec in manifest
            if rec.get("promoted") and int(rec.get("generation", 0)) <= anchor_ceiling
        ]
        if not candidates:
            candidates = [rec for rec in manifest if int(rec.get("generation", 0)) == 0]
        if not candidates:
            candidates = [rec for rec in manifest if rec.get("promoted")]
        if not candidates:
            return (0, self.best_model.copy())

        anchor_record = max(candidates, key=lambda rec: int(rec.get("generation", 0)))
        anchor_generation = int(anchor_record.get("generation", 0))
        anchor_path = Path(str(anchor_record.get("path", "")))
        if not anchor_path.exists():
            return (anchor_generation, self.best_model.copy())

        return (anchor_generation, WhiskPolicyValueModel.load(anchor_path))

    def train(self, checkpoint_path: Path, replay_path: Path | None = None) -> Dict[str, object]:
        if self.config.iterations <= 0:
            raise ValueError("iterations must be > 0")
        if self.config.promotion_games <= 0:
            raise ValueError("promotion_games must be > 0")
        if self.config.train_passes <= 0:
            raise ValueError("train_passes must be > 0")
        if self.config.benchmark_games < 0:
            raise ValueError("benchmark_games must be >= 0")
        if self.config.benchmark_simulations <= 0:
            raise ValueError("benchmark_simulations must be > 0")
        if self.config.benchmark_anchor_gap < 0:
            raise ValueError("benchmark_anchor_gap must be >= 0")

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

        manifest = manager.load_manifest()
        benchmark_anchor_generation = None
        benchmark_anchor_model = None
        best_win_rate_vs_anchor: float | None = None
        if self.config.benchmark_games > 0:
            benchmark_anchor_generation, benchmark_anchor_model = self._select_benchmark_anchor(
                manifest=manifest,
                start_generation=start_generation,
            )
            assert benchmark_anchor_model is not None
            best_win_rate_vs_anchor = self.evaluate_vs_anchor(
                self.best_model,
                anchor_model=benchmark_anchor_model,
                games=self.config.benchmark_games,
                seed=self.config.seed + 1111,
            )
            self._log(
                f"[train] benchmark anchor gen={benchmark_anchor_generation}; "
                f"games={self.config.benchmark_games}; "
                f"anchor_sims={self._benchmark_eval_simulations()}; "
                f"best_vs_anchor={best_win_rate_vs_anchor:.3f}"
            )

        end_generation = start_generation + self.config.iterations - 1
        overall_start = time.perf_counter()
        generation_durations: List[float] = []
        self._log(
            f"[train] start generations {start_generation}..{end_generation}, "
            f"games/iter={self.config.games_per_iteration}, sims={self.config.selfplay_simulations}, "
            f"workers={self.config.selfplay_workers}"
        )

        best_win_rate_vs_random = self.evaluate_vs_random(self.best_model, seed=self.config.seed)
        latest_examples = 0

        for generation in range(start_generation, end_generation + 1):
            gen_start = time.perf_counter()
            self._log(f"[train] generation {generation}/{end_generation} started")
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
            self._log(f"[train] generation {generation} self-play complete: {latest_examples} examples")

            replay.add_examples(examples)
            replay.save(replay_path)

            # Train candidate from best using replay sample.
            candidate_model = self.best_model.copy()
            sample_size = min(self.config.replay_sample_size, len(replay.items))
            train_batch = replay.sample(sample_size, seed=gen_seed)
            if train_batch:
                candidate_model.train_on_examples(train_batch)
                for pass_idx in range(1, self.config.train_passes):
                    resample_seed = gen_seed + (pass_idx * 7_919)
                    pass_batch = replay.sample(sample_size, seed=resample_seed)
                    if not pass_batch:
                        break
                    candidate_model.train_on_examples(pass_batch)

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

            candidate_vs_anchor = None
            if self.config.benchmark_games > 0 and benchmark_anchor_model is not None:
                candidate_vs_anchor = self.evaluate_vs_anchor(
                    candidate_model,
                    anchor_model=benchmark_anchor_model,
                    games=self.config.benchmark_games,
                    seed=gen_seed + 3500,
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
            if candidate_vs_anchor is not None:
                metrics["candidate_win_rate_vs_anchor"] = candidate_vs_anchor
                metrics["benchmark_anchor_generation"] = int(benchmark_anchor_generation or 0)

            if promoted:
                self.best_model = candidate_model
                if generation not in promoted_generations:
                    promoted_generations.append(generation)
                best_win_rate_vs_random = max(best_win_rate_vs_random, candidate_random)
                if candidate_vs_anchor is not None:
                    best_win_rate_vs_anchor = candidate_vs_anchor
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

            gen_elapsed = time.perf_counter() - gen_start
            generation_durations.append(gen_elapsed)
            done_count = generation - start_generation + 1
            remaining = max(0, self.config.iterations - done_count)
            avg_gen = sum(generation_durations) / len(generation_durations)
            eta_sec = avg_gen * remaining
            pct_complete = (done_count / max(1, self.config.iterations)) * 100.0
            self._log(
                f"[train] progress: {done_count}/{self.config.iterations} iterations "
                f"({pct_complete:.1f}% complete)"
            )

            anchor_fragment = ""
            if candidate_vs_anchor is not None and best_win_rate_vs_anchor is not None:
                anchor_fragment = (
                    f"; candidate_vs_anchor={candidate_vs_anchor:.3f}; "
                    f"best_vs_anchor={best_win_rate_vs_anchor:.3f}; "
                    f"anchor_gen={int(benchmark_anchor_generation or 0)}"
                )
            self._log(
                f"[train] generation {generation} done in {gen_elapsed:.1f}s; "
                f"promoted={promoted}; candidate_vs_best={head_to_head['candidate_win_rate']:.3f}; "
                f"candidate_vs_random={candidate_random:.3f}"
                f"{anchor_fragment}; replay={len(replay.items)}; ETA~{eta_sec/60:.1f}m"
            )

        best_path = manager.best_path() or checkpoint_path
        if best_path != checkpoint_path:
            loaded = WhiskPolicyValueModel.load(best_path)
            loaded.save(checkpoint_path)

        total_elapsed = time.perf_counter() - overall_start
        self._log(f"[train] completed in {total_elapsed/60:.1f} minutes")
        return {
            "iterations": self.config.iterations,
            "start_generation": start_generation,
            "end_generation": end_generation,
            "examples_last_iteration": latest_examples,
            "best_win_rate_vs_random": best_win_rate_vs_random,
            "best_win_rate_vs_anchor": best_win_rate_vs_anchor,
            "benchmark_anchor_generation": benchmark_anchor_generation,
            "benchmark_games": self.config.benchmark_games,
            "benchmark_simulations": self.config.benchmark_simulations,
            "checkpoint": str(checkpoint_path),
            "lineage_manifest": str(manager.manifest_path),
            "replay_path": str(replay_path),
            "replay_size": len(replay.items),
            "promoted_generations": promoted_generations,
        }

    def evaluate_vs_random(self, model: WhiskPolicyValueModel, seed: int = 0) -> float:
        model_agent = ModelAgent(
            model,
            simulations=self._candidate_eval_simulations(),
            rollout_max_turns=self.config.eval_max_turns,
        )
        random_agent = RandomAgent()
        arena = Arena(max_turns=self.config.eval_max_turns)

        total = self.config.eval_games
        games_as_o = total // 2
        games_as_x = total - games_as_o

        model_wins = 0
        games_played = 0

        if games_as_o > 0:
            summary_o = arena.run(model_agent, random_agent, games=games_as_o, seed=seed)
            model_wins += summary_o.wins_o
            games_played += summary_o.games

        if games_as_x > 0:
            summary_x = arena.run(random_agent, model_agent, games=games_as_x, seed=seed + 5000)
            model_wins += summary_x.wins_x
            games_played += summary_x.games

        return model_wins / max(1, games_played)

    def evaluate_vs_anchor(
        self,
        model: WhiskPolicyValueModel,
        anchor_model: WhiskPolicyValueModel,
        games: int,
        seed: int = 0,
    ) -> float:
        if games <= 0:
            raise ValueError("games must be > 0")

        arena = Arena(max_turns=self.config.eval_max_turns)
        candidate_agent = ModelAgent(
            model,
            simulations=self._candidate_eval_simulations(),
            rollout_max_turns=self.config.eval_max_turns,
        )
        anchor_agent = ModelAgent(
            anchor_model,
            simulations=self._benchmark_eval_simulations(),
            rollout_max_turns=self.config.eval_max_turns,
        )

        games_as_o = games // 2
        games_as_x = games - games_as_o

        candidate_wins = 0
        games_played = 0

        if games_as_o > 0:
            summary_o = arena.run(candidate_agent, anchor_agent, games=games_as_o, seed=seed)
            candidate_wins += summary_o.wins_o
            games_played += summary_o.games

        if games_as_x > 0:
            summary_x = arena.run(anchor_agent, candidate_agent, games=games_as_x, seed=seed + 5000)
            candidate_wins += summary_x.wins_x
            games_played += summary_x.games

        return candidate_wins / max(1, games_played)

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
            simulations=self._candidate_eval_simulations(),
            rollout_max_turns=self.config.eval_max_turns,
        )
        best_agent = ModelAgent(
            best,
            simulations=self._candidate_eval_simulations(),
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
