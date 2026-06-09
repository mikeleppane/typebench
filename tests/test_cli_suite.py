from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench import cli
from typebench.cli import app
from typebench.contracts.models import ResultClass, ResultsEnvelope, RunResult, ThreadMode
from typebench.env import detect_env

runner = CliRunner()


def test_suite_writes_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Stub run_suite so the CLI is tested for wiring + file write, not orchestration.
    def fake_run_suite(**kwargs: object) -> ResultsEnvelope:
        rec = RunResult(
            tool="stub",
            tool_version="0",
            project="demo",
            thread_mode=ThreadMode.ALL_CORES,
            result_class=ResultClass.CLEAN,
            real_exit_code=0,
            env=detect_env(),
        )
        return ResultsEnvelope(
            suite_version="v", generated_at=str(kwargs["generated_at"]), runs=[rec]
        )

    monkeypatch.setattr(cli, "run_suite", fake_run_suite)
    suite = tmp_path / "suite.toml"
    suite.write_text('[suite]\nversion="v"\n')
    out = tmp_path / "results" / "2026-06-08.json"
    out.parent.mkdir()
    result = runner.invoke(
        app,
        [
            "suite",
            "--corpus",
            str(suite),
            "--output",
            str(out),
            "--tool",
            "stub",
            "--shard",
            "0/1",
            "--no-calibrate",
            "--no-measure",
            "--runs",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = ResultsEnvelope.model_validate_json(out.read_text())
    assert len(envelope.runs) == 1


def test_suite_rejects_bad_shard(tmp_path: Path) -> None:
    suite = tmp_path / "suite.toml"
    suite.write_text("[[project]]\nname='x'\n")
    out = tmp_path / "r.json"
    result = runner.invoke(
        app, ["suite", "--corpus", str(suite), "--output", str(out), "--shard", "3/2"]
    )
    assert result.exit_code == 2
    assert "shard" in result.output.lower()
