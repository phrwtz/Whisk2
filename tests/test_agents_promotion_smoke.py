import json
from pathlib import Path

from backend.app.agents.train import TrainConfig, Trainer


def test_generation_training_writes_manifest_and_best_checkpoint(tmp_path: Path):
    out = tmp_path / "best.pkl"

    trainer = Trainer(
        TrainConfig(
            iterations=2,
            games_per_iteration=1,
            selfplay_max_turns=8,
            selfplay_simulations=2,
            eval_games=2,
            eval_max_turns=10,
            promotion_games=2,
            promotion_threshold=0.0,  # force promotion path in smoke test
            seed=7,
        )
    )
    summary = trainer.train(out)

    assert out.exists()
    manifest_path = Path(summary["lineage_manifest"])
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # generation 0 bootstrap + 2 generated candidates
    assert len(manifest) == 3
    assert manifest[0]["generation"] == 0
    assert "promoted_generations" in summary
