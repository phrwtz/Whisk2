import json
from pathlib import Path

from backend.app.agents.report import build_training_report, render_markdown, write_report_files


def test_report_build_and_render_from_manifest(tmp_path: Path):
    manifest = [
        {"generation": 0, "path": "g0.pkl", "promoted": True, "metrics": {"bootstrap": 1}},
        {
            "generation": 1,
            "path": "g1.pkl",
            "promoted": True,
            "metrics": {
                "candidate_win_rate_vs_best": 0.60,
                "candidate_win_rate_vs_random": 0.55,
                "examples": 100,
                "replay_size": 100,
            },
        },
        {
            "generation": 2,
            "path": "g2.pkl",
            "promoted": False,
            "metrics": {
                "candidate_win_rate_vs_best": 0.40,
                "candidate_win_rate_vs_random": 0.52,
                "examples": 120,
                "replay_size": 220,
            },
        },
    ]

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = build_training_report(manifest_path)
    assert report.total_generations == 2
    assert report.promoted_generations == [1]
    assert report.latest_replay_size == 220
    assert abs(report.candidate_vs_random_delta - (0.52 - 0.55)) < 1e-9

    md = render_markdown(report)
    assert "Whisk Training Report" in md
    assert "| 1 | True" in md

    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"
    written = write_report_files(manifest_path, out_json=out_json, out_md=out_md)
    assert Path(written["json"]).exists()
    assert Path(written["markdown"]).exists()
