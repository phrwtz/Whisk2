import json
from pathlib import Path

from backend.app.agents.release_gate import ReleaseGateConfig, evaluate_release_gate
from backend.app.agents.report import build_training_report


def _write_manifest(path: Path, rows):
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_release_gate_passes_when_thresholds_met(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {"generation": 0, "path": "g0.pkl", "promoted": True, "metrics": {"bootstrap": 1}},
            {
                "generation": 1,
                "path": "g1.pkl",
                "promoted": True,
                "metrics": {
                    "candidate_win_rate_vs_best": 0.60,
                    "candidate_win_rate_vs_random": 0.70,
                    "examples": 100,
                    "replay_size": 120,
                },
            },
            {
                "generation": 2,
                "path": "g2.pkl",
                "promoted": True,
                "metrics": {
                    "candidate_win_rate_vs_best": 0.55,
                    "candidate_win_rate_vs_random": 0.72,
                    "examples": 120,
                    "replay_size": 240,
                },
            },
            {
                "generation": 3,
                "path": "g3.pkl",
                "promoted": False,
                "metrics": {
                    "candidate_win_rate_vs_best": 0.48,
                    "candidate_win_rate_vs_random": 0.68,
                    "examples": 140,
                    "replay_size": 360,
                },
            },
        ],
    )

    report = build_training_report(manifest)
    result = evaluate_release_gate(report, ReleaseGateConfig(min_latest_replay_size=200))
    assert result["passed"] is True


def test_release_gate_fails_when_replay_too_small(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {"generation": 0, "path": "g0.pkl", "promoted": True, "metrics": {"bootstrap": 1}},
            {
                "generation": 1,
                "path": "g1.pkl",
                "promoted": True,
                "metrics": {
                    "candidate_win_rate_vs_best": 0.60,
                    "candidate_win_rate_vs_random": 0.80,
                    "examples": 80,
                    "replay_size": 80,
                },
            },
        ],
    )

    report = build_training_report(manifest)
    result = evaluate_release_gate(
        report,
        ReleaseGateConfig(
            min_generations=1,
            min_promotion_rate=0.0,
            min_latest_vs_random=0.5,
            min_best_vs_random=0.5,
            min_latest_replay_size=200,
        ),
    )
    assert result["passed"] is False
    replay_check = next(c for c in result["checks"] if c["name"] == "min_latest_replay_size")
    assert replay_check["passed"] is False
