from pathlib import Path
from types import SimpleNamespace

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


def test_evaluate_vs_random_runs_both_seatings(monkeypatch):
    from backend.app.agents import train as train_mod

    calls = []

    def _fake_run(self, agent_o, agent_x, games, seed=0):
        calls.append((agent_o.name, agent_x.name, games, seed))
        if agent_o.name == "mcts_model":
            return SimpleNamespace(games=games, wins_o=games, wins_x=0, ties=0)
        return SimpleNamespace(games=games, wins_o=0, wins_x=games, ties=0)

    monkeypatch.setattr(train_mod.Arena, "run", _fake_run)
    trainer = Trainer(TrainConfig(eval_games=5, selfplay_simulations=8))
    score = trainer.evaluate_vs_random(WhiskPolicyValueModel(), seed=123)

    assert len(calls) == 2
    assert calls[0][0] == "mcts_model" and calls[0][1] == "random"
    assert calls[1][0] == "random" and calls[1][1] == "mcts_model"
    assert score == 1.0
