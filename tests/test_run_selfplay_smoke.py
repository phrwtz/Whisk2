import json
from pathlib import Path

from scripts.run_selfplay import run_selfplay


REQUIRED_TOP_LEVEL_KEYS = {
    "game",
    "turn",
    "player",
    "obs",
    "action",
    "action_coord",
    "reward",
    "done",
    "winner",
    "outcome",
    "scores",
}

REQUIRED_OBS_KEYS = {
    "turn",
    "viewer_mark",
    "board_self",
    "board_opponent",
    "board_empty",
    "age_self",
    "age_opponent",
    "reserved",
    "pending_self",
    "pending_opponent",
    "score_self",
    "score_opponent",
    "legal_action_mask",
}


def test_run_selfplay_smoke_schema_and_row_counts(tmp_path: Path):
    out_path = tmp_path / "smoke.jsonl"
    max_turns = 5

    summary = run_selfplay(num_games=1, seed=123, out_path=out_path, max_turns=max_turns)

    assert summary["games"] == 1
    assert summary["max_turns"] <= max_turns
    assert out_path.exists()

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # Two player-perspective records per committed turn.
    assert len(rows) == summary["transitions"]
    assert len(rows) % 2 == 0

    observed_players = set()
    for row in rows:
        assert REQUIRED_TOP_LEVEL_KEYS.issubset(row.keys())
        assert isinstance(row["action"], int)
        assert isinstance(row["action_coord"], list)
        assert len(row["action_coord"]) == 2

        assert isinstance(row["obs"], dict)
        assert REQUIRED_OBS_KEYS.issubset(row["obs"].keys())

        for key in (
            "board_self",
            "board_opponent",
            "board_empty",
            "age_self",
            "age_opponent",
            "reserved",
            "legal_action_mask",
        ):
            assert len(row["obs"][key]) == 64

        observed_players.add(row["player"])

    assert observed_players == {"O", "X"}
