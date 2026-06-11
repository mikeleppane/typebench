from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench import cli
from typebench.cli import app
from typebench.contracts.models import (
    EnvFingerprint,
    ResolvedChecker,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    TimingStats,
)
from typebench.contracts.taxonomy import LocDenominator, ResultClass

type EnvFactory = Callable[..., EnvFingerprint]

runner = CliRunner()


def _ab_record(checker_id: str, wall: float, make_env: EnvFactory) -> RunResult:
    return RunResult(
        tool="pyrefly",
        tool_version="x",
        checker_id=checker_id,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=2, min_s=wall, median_s=wall, mean_s=wall, stddev_s=0.0, max_s=wall, times_s=[wall]
        ),
        files=10,
        canonical_code_loc=3200,
        loc_denominator=LocDenominator.CODE,
        env=make_env(),
    )


def test_ab_command_prints_wall_delta_and_writes_envelope(
    tmp_path: Path, make_env: EnvFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    out = tmp_path / "env.json"
    candidate_bin = tmp_path / "bin"
    candidate_bin.write_text("x")

    def fake_run_ab(**kwargs: object) -> ResultsEnvelope:
        return ResultsEnvelope(
            suite_version="ab-2026-06-11",
            generated_at=str(kwargs["generated_at"]),
            runs=[
                _ab_record("pyrefly@path+pr", 1.5, make_env),
                _ab_record("pyrefly@0.36.2+release", 2.0, make_env),
            ],
            resolved_checkers=(
                ResolvedChecker(
                    checker_id="pyrefly@path+pr",
                    tool="pyrefly",
                    version="path",
                    lock_hash="sha256:x",
                    install_source="path",
                ),
                ResolvedChecker(
                    checker_id="pyrefly@0.36.2+release",
                    tool="pyrefly",
                    version="0.36.2",
                    lock_hash="lh",
                    install_source="pypi",
                ),
            ),
        )

    monkeypatch.setattr(cli, "run_ab", fake_run_ab)

    result = runner.invoke(
        app,
        [
            "ab",
            "--checker",
            "pyrefly",
            "--candidate-bin",
            str(candidate_bin),
            "--baseline",
            "==0.36",
            "--candidate-label",
            "pr",
            "--baseline-label",
            "release",
            "--target",
            str(target),
            "--output",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "-25.0%" in result.output
    assert out.exists()
    assert ResultsEnvelope.model_validate_json(out.read_text()).suite_version == "ab-2026-06-11"


def test_ab_rejects_missing_candidate_bin(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    result = runner.invoke(
        app,
        [
            "ab",
            "--checker",
            "mypy",
            "--candidate-bin",
            str(tmp_path / "does-not-exist"),
            "--target",
            str(target),
            "--output",
            str(tmp_path / "env.json"),
        ],
    )
    assert result.exit_code == 2
    assert "candidate-bin is not a file" in result.output


def test_ab_rejects_path_prefixed_baseline(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    cand = tmp_path / "bin"
    cand.write_text("x")
    result = runner.invoke(
        app,
        [
            "ab",
            "--checker",
            "mypy",
            "--candidate-bin",
            str(cand),
            "--baseline",
            "path:/some/binary",
            "--target",
            str(target),
            "--output",
            str(tmp_path / "env.json"),
        ],
    )
    assert result.exit_code == 2
    assert "use --baseline-bin" in result.output
