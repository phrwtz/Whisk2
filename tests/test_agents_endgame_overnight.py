from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_endgame_defense_overnight import _build_recommendation


def test_build_recommendation_detects_improving_signal():
    cycles = [
        {"vs_base_win_rate": 0.57, "vs_prev_win_rate": 0.54},
        {"vs_base_win_rate": 0.58, "vs_prev_win_rate": 0.55},
        {"vs_base_win_rate": 0.56, "vs_prev_win_rate": 0.53},
    ]
    rec = _build_recommendation(cycles)
    assert "continued improvement" in rec


def test_build_recommendation_detects_flat_signal():
    cycles = [
        {"vs_base_win_rate": 0.50, "vs_prev_win_rate": 0.51},
        {"vs_base_win_rate": 0.51, "vs_prev_win_rate": 0.50},
        {"vs_base_win_rate": 0.52, "vs_prev_win_rate": 0.52},
    ]
    rec = _build_recommendation(cycles)
    assert "flat" in rec


def test_build_recommendation_handles_mixed_signal():
    cycles = [
        {"vs_base_win_rate": 0.60, "vs_prev_win_rate": 0.49},
        {"vs_base_win_rate": 0.50, "vs_prev_win_rate": 0.56},
    ]
    rec = _build_recommendation(cycles)
    assert "mixed/noisy" in rec
