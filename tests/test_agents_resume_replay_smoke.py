import json
from pathlib import Path

from backend.app.agents.replay import ReplayBuffer
from backend.app.agents.train import TrainConfig, Trainer


def test_resume_training_and_replay_persistence(tmp_path: Path):
    out = tmp_path / "best.pkl"
    replay_path = tmp_path / "replay.pkl"

    first = Trainer(
        TrainConfig(
            iterations=1,
            games_per_iteration=1,
            selfplay_max_turns=8,
            selfplay_simulations=2,
            selfplay_workers=1,
            eval_games=2,
            eval_max_turns=10,
            promotion_games=2,
            promotion_threshold=0.0,
            replay_capacity=200,
            replay_sample_size=50,
            seed=5,
            resume=False,
        )
    )
    s1 = first.train(out, replay_path=replay_path)
    assert out.exists()
    assert replay_path.exists()

    manifest1 = Path(s1["lineage_manifest"])
    m1 = json.loads(manifest1.read_text(encoding="utf-8"))
    assert len(m1) == 2  # gen0 + gen1

    second = Trainer(
        TrainConfig(
            iterations=1,
            games_per_iteration=1,
            selfplay_max_turns=8,
            selfplay_simulations=2,
            selfplay_workers=1,
            eval_games=2,
            eval_max_turns=10,
            promotion_games=2,
            promotion_threshold=0.0,
            replay_capacity=200,
            replay_sample_size=50,
            seed=5,
            resume=True,
        )
    )
    s2 = second.train(out, replay_path=replay_path)
    m2 = json.loads(manifest1.read_text(encoding="utf-8"))

    assert s2["start_generation"] == 2
    assert len(m2) == 3  # gen0 + gen1 + gen2

    replay = ReplayBuffer.load(replay_path)
    assert len(replay.items) > 0
