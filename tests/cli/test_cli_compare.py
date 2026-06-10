from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench import cli
from typebench.cli import app
from typebench.contracts.models import (
    EnvFingerprint,
    MemoryStats,
    ResolvedChecker,
    ResultsEnvelope,
    RunResult,
    TimingStats,
)
from typebench.contracts.taxonomy import LocDenominator, ResultClass, ThreadMode

type EnvFactory = Callable[..., EnvFingerprint]

runner = CliRunner()

_SUITE = """\
[suite]
version = "v"

[[project]]
name = "sqlalchemy"
repo_url = "x"
sha = "s"
tag = "v1"
size_bucket = "large"
python_version = "3.12"
src_roots = ["pkg"]
install = ["uv pip install ."]
"""


def _cmp_record(checker_id: str, wall: float, make_env: EnvFactory) -> RunResult:
    return RunResult(
        tool=checker_id.split("@", 1)[0],
        tool_version=checker_id.split("@", 1)[1],
        checker_id=checker_id,
        project="sqlalchemy",
        thread_mode=ThreadMode.CONSTRAINED,
        cores=4,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=1,
            min_s=wall,
            median_s=wall,
            mean_s=wall,
            stddev_s=0.0,
            max_s=wall,
            times_s=[wall],
        ),
        memory=MemoryStats(
            runs=1,
            peak_bytes_min=1,
            peak_bytes_median=1,
            peak_bytes_max=1,
        ),
        canonical_code_loc=200_000,
        loc_denominator=LocDenominator.CODE,
        over_reports=False,
        env=make_env(),
    )


def test_compare_two_versions_writes_envelope_and_prints_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_env: EnvFactory,
) -> None:
    def fake_run_suite(**kwargs: object) -> ResultsEnvelope:
        return ResultsEnvelope(
            suite_version="v",
            generated_at=str(kwargs["generated_at"]),
            runs=[
                _cmp_record("mypy@1.14.1", 6.27, make_env),
                _cmp_record("mypy@1.15.0", 5.81, make_env),
            ],
        )

    monkeypatch.setattr(cli, "run_suite", fake_run_suite)
    suite = tmp_path / "suite.toml"
    suite.write_text(_SUITE, encoding="utf-8")
    out = tmp_path / "cmp.json"

    result = runner.invoke(
        app,
        [
            "compare",
            "--corpus",
            str(suite),
            "--checker",
            "mypy@1.14.1",
            "--checker",
            "mypy@1.15.0",
            "--project",
            "sqlalchemy",
            "--cores",
            "4",
            "--output",
            str(out),
            "--no-measure",
            "--no-calibrate",
            "--runs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    envelope = ResultsEnvelope.model_validate_json(out.read_text(encoding="utf-8"))
    assert {run.checker_id for run in envelope.runs} == {"mypy@1.14.1", "mypy@1.15.0"}
    assert "mypy@1.14.1" in result.stdout
    assert "mypy@1.15.0" in result.stdout
    assert "-7.3%" in result.stdout


def test_compare_dry_run_executes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def boom(**_kwargs: object) -> object:
        raise AssertionError("run_suite must NOT run on --dry-run")

    monkeypatch.setattr(cli, "run_suite", boom)
    suite = tmp_path / "suite.toml"
    suite.write_text(_SUITE, encoding="utf-8")
    out = tmp_path / "cmp.json"

    result = runner.invoke(
        app,
        [
            "compare",
            "--corpus",
            str(suite),
            "--checker",
            "mypy@1.14.1",
            "--checker",
            "mypy@1.15.0",
            "--project",
            "sqlalchemy",
            "--output",
            str(out),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "matrix" in result.stdout.lower()
    assert not out.exists()


def test_compare_rejects_unknown_checker_tool(tmp_path: Path) -> None:
    out = tmp_path / "cmp.json"
    result = runner.invoke(
        app,
        [
            "compare",
            "--corpus",
            str(tmp_path / "suite.toml"),
            "--checker",
            "bogus@1.0",
            "--checker",
            "mypy@1.0",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 2
    assert "bogus" in result.output
    assert not out.exists()


def test_compare_baseline_uses_resolved_checker_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_env: EnvFactory
) -> None:
    # The first --checker is UNPINNED (declared id `mypy@latest`); records are keyed
    # on the RESOLVED id. Baseline must come from resolved_checkers, or no row matches
    # and every delta zeroes to "—".
    def fake_run_suite(**kwargs: object) -> ResultsEnvelope:
        return ResultsEnvelope(
            suite_version="v",
            generated_at=str(kwargs["generated_at"]),
            runs=[
                _cmp_record("mypy@1.18.2", 6.0, make_env),
                _cmp_record("mypy@1.19.0", 5.4, make_env),
            ],
            resolved_checkers=(
                ResolvedChecker(
                    checker_id="mypy@1.18.2",
                    tool="mypy",
                    version="1.18.2",
                    lock_hash="L0",
                    install_source="pypi",
                ),
                ResolvedChecker(
                    checker_id="mypy@1.19.0",
                    tool="mypy",
                    version="1.19.0",
                    lock_hash="L1",
                    install_source="pypi",
                ),
            ),
        )

    monkeypatch.setattr(cli, "run_suite", fake_run_suite)
    suite = tmp_path / "suite.toml"
    suite.write_text(_SUITE, encoding="utf-8")
    out = tmp_path / "cmp.json"

    result = runner.invoke(
        app,
        [
            "compare",
            "--corpus",
            str(suite),
            "--checker",
            "mypy",  # unpinned -> declared mypy@latest, resolved mypy@1.18.2
            "--checker",
            "mypy@1.19.0",
            "--project",
            "sqlalchemy",
            "--cores",
            "4",
            "--output",
            str(out),
            "--no-measure",
            "--no-calibrate",
            "--runs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    # The resolved baseline matched a row -> a real signed delta, not "—".
    assert "baseline" in result.stdout
    assert "-10.0%" in result.stdout  # (5.4 - 6.0) / 6.0 = -10.0%
