"""CLI input-hardening: invalid configuration must fail fast and gracefully
(clean stderr + exit 2), never hang or surface a raw traceback."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench.cli import app

runner = CliRunner()


def _out(tmp_path: Path) -> str:
    return str(tmp_path / "o.json")


# --- F1: timing args that would hang or corrupt measurement are rejected early ---
# `hyperfine --runs 0` spins forever; validation must fire before any work starts.


@pytest.mark.parametrize(
    "bad",
    [
        ["--runs", "0"],
        ["--runs", "-3"],
        ["--warmup", "-1"],
        ["--timeout", "0"],
        ["--timeout", "-1"],
    ],
)
def test_run_rejects_invalid_timing_args(bad: list[str], tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["run", "--tool", "stub", "--output", _out(tmp_path), "--project", "x", *bad]
    )
    assert result.exit_code == 2  # rejected, not run (no hang)


def test_suite_rejects_invalid_timing_args(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["suite", "--corpus", "corpus/suite.toml", "--output", _out(tmp_path), "--runs", "0"]
    )
    assert result.exit_code == 2


# --- F2: a missing/unreadable corpus path is a clean error, not a traceback ---


def test_preflight_bad_corpus_path_is_graceful(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "preflight",
            "--corpus",
            "/no/such/suite.toml",
            "--project",
            "x",
            "--output",
            _out(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert not isinstance(result.exception, FileNotFoundError)


def test_run_corpus_project_bad_corpus_path_is_graceful(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--tool",
            "stub",
            "--output",
            _out(tmp_path),
            "--corpus-project",
            "x",
            "--corpus",
            "/no/such/suite.toml",
        ],
    )
    assert result.exit_code == 2
    assert not isinstance(result.exception, FileNotFoundError)


def test_suite_bad_corpus_path_is_graceful(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["suite", "--corpus", "/no/such/suite.toml", "--output", _out(tmp_path)]
    )
    assert result.exit_code == 2
    assert not isinstance(result.exception, FileNotFoundError)
