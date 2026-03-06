from pathlib import Path

from backend.app.agents.encoding import StateEncoder
from backend.app.agents.env import WhiskEnv
from backend.app.agents.mcts import MCTS
from backend.app.agents.model import WhiskPolicyValueModel
from backend.app.agents.train import TrainConfig, Trainer
from backend.app.game import Mark


def test_mcts_returns_distribution_over_legal_actions():
    env = WhiskEnv(mode="remote")
    env.reset()

    model = WhiskPolicyValueModel()
    mcts = MCTS(model=model, simulations=4, rollout_max_turns=12)

    import random

    pi = mcts.search(env, Mark.O, random.Random(0))
    obs = StateEncoder.encode_observation(env.state, Mark.O)

    assert len(pi) == 64
    assert abs(sum(pi) - 1.0) < 1e-6

    for i, bit in enumerate(obs["legal_action_mask"]):
        if bit == 0:
            assert pi[i] == 0.0


def test_trainer_smoke_saves_checkpoint(tmp_path: Path):
    ckpt = tmp_path / "m3.pkl"
    trainer = Trainer(
        TrainConfig(
            iterations=1,
            games_per_iteration=1,
            selfplay_max_turns=10,
            selfplay_simulations=2,
            eval_games=2,
            eval_max_turns=12,
            seed=1,
        )
    )

    summary = trainer.train(ckpt)

    assert ckpt.exists()
    assert summary["iterations"] == 1
    assert summary["examples_last_iteration"] >= 0
    assert 0.0 <= summary["best_win_rate_vs_random"] <= 1.0
