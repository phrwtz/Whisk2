import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_train.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def test_managed_session_persists_params_and_resumes(tmp_path: Path):
    out = tmp_path / "checkpoints" / "best.pkl"
    replay = tmp_path / "checkpoints" / "replay.pkl"
    session = tmp_path / "checkpoints" / "session.json"
    manifest = out.parent / "manifest.json"

    first = _run(
        [
            "--managed-session",
            "--reset-managed-session",
            "--session-file",
            str(session),
            "--target-total-iterations",
            "2",
            "--max-iterations-per-run",
            "1",
            "--iterations",
            "1",
            "--games-per-iteration",
            "1",
            "--selfplay-max-turns",
            "8",
            "--selfplay-simulations",
            "2",
            "--selfplay-workers",
            "1",
            "--eval-games",
            "2",
            "--eval-max-turns",
            "10",
            "--promotion-games",
            "2",
            "--promotion-threshold",
            "0.0",
            "--replay-capacity",
            "200",
            "--replay-sample-size",
            "50",
            "--train-passes",
            "1",
            "--seed",
            "5",
            "--out",
            str(out),
            "--replay",
            str(replay),
            "--quiet",
        ]
    )
    assert first.returncode == 0, first.stderr
    assert "[train-runner] boot" in first.stdout
    assert "created session" in first.stdout
    assert session.exists()
    assert manifest.exists()

    m1 = json.loads(manifest.read_text(encoding="utf-8"))
    assert [int(row["generation"]) for row in m1] == [0, 1]

    second = _run(["--managed-session", "--session-file", str(session), "--quiet"])
    assert second.returncode == 0, second.stderr
    assert "[train-runner] boot" in second.stdout
    assert "loaded session" in second.stdout

    m2 = json.loads(manifest.read_text(encoding="utf-8"))
    assert [int(row["generation"]) for row in m2] == [0, 1, 2]

    third = _run(["--managed-session", "--session-file", str(session), "--quiet"])
    assert third.returncode == 0, third.stderr
    assert '"status": "already_complete"' in third.stdout

    m3 = json.loads(manifest.read_text(encoding="utf-8"))
    assert [int(row["generation"]) for row in m3] == [0, 1, 2]
