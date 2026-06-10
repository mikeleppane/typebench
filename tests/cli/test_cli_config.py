from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict, Unpack

import pytest
from typer.testing import CliRunner, Result

from typebench import cli
from typebench.cli import app
from typebench.contracts.identity import CheckerSpec
from typebench.contracts.models import ResultsEnvelope
from typebench.contracts.policy import Policy
from typebench.contracts.runconfig import RunConfig
from typebench.contracts.taxonomy import ThreadMode
from typebench.corpus.catalog import CorpusProject

runner = CliRunner()


@dataclass
class SuiteCapture:
    checkers: tuple[CheckerSpec, ...]
    thread_modes: list[ThreadMode]
    cores: tuple[int, ...]
    projects: list[str]
    policy: Policy
    run_config: RunConfig | None
    runs: int
    warmup: int
    mem_runs: int


class CapturedCorpus(Protocol):
    def entries(self) -> list[CorpusProject]: ...


class RunSuiteKwargs(TypedDict):
    config: RunConfig
    corpus: CapturedCorpus
    resolver: object
    engine: object
    generated_at: str
    shard_index: int
    shard_total: int


def _suite_toml(project_name: str = "httpx", bucket: str = "small") -> str:
    return f"""\
[suite]
version = "v"

[[project]]
name = "{project_name}"
repo_url = "https://example.test/{project_name}.git"
sha = "abc123"
tag = "v1"
size_bucket = "{bucket}"
python_version = "3.12"
src_roots = ["pkg"]
install = ["uv pip install ."]
"""


def _invoke(args: list[str]) -> Result:
    return runner.invoke(app, args)


def _capture_run_suite(captures: list[SuiteCapture]) -> Callable[..., ResultsEnvelope]:
    def fake_run_suite(**kwargs: Unpack[RunSuiteKwargs]) -> ResultsEnvelope:
        run_config = kwargs["config"]
        captures.append(
            SuiteCapture(
                checkers=run_config.checkers,
                thread_modes=list(run_config.thread_modes),
                cores=run_config.cores,
                projects=[entry.name for entry in kwargs["corpus"].entries()],
                policy=run_config.policy,
                run_config=run_config,
                runs=run_config.runs,
                warmup=run_config.warmup,
                mem_runs=run_config.mem_runs,
            )
        )
        return ResultsEnvelope(suite_version="v", generated_at=kwargs["generated_at"], runs=[])

    return fake_run_suite


def test_config_init_scaffolds_commented_toml(tmp_path: Path) -> None:
    out = tmp_path / "typebench.toml"
    result = _invoke(["config", "init", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "[[checker]]" in text
    assert "tool = " in text
    assert "version = " in text
    assert 'tool = "mypy"' in text
    assert 'tool = "pyright"' in text
    assert 'tool = "pyrefly"' in text
    assert 'tool = "ty"' in text


def test_config_show_prints_effective_config(tmp_path: Path) -> None:
    config = tmp_path / "typebench.toml"
    config.write_text(
        'policy = "standard"\nprojects = ["httpx"]\n[[checker]]\ntool = "mypy"\n'
        'version = "1.18.2"\n',
        encoding="utf-8",
    )
    result = _invoke(["config", "show", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "mypy@1.18.2" in result.stdout
    assert "standard" in result.stdout
    assert "httpx" in result.stdout


def test_suite_dry_run_prints_matrix_without_executing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom() -> ResultsEnvelope:
        raise AssertionError("run_suite must not be called on --dry-run")

    monkeypatch.setattr(cli, "run_suite", boom)
    suite = tmp_path / "suite.toml"
    suite.write_text(_suite_toml(), encoding="utf-8")
    config = tmp_path / "typebench.toml"
    config.write_text('[[checker]]\ntool = "mypy"\nversion = "1.18.2"\n', encoding="utf-8")
    out = tmp_path / "r.json"
    result = _invoke(
        ["suite", "--corpus", str(suite), "--output", str(out), "-c", str(config), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "mypy@1.18.2" in result.stdout
    assert "httpx" in result.stdout
    assert "matrix" in result.stdout.lower()
    assert "headline" in result.stdout.lower()
    assert not out.exists()


def test_suite_thread_mode_constrained_replaces_config_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suite = tmp_path / "suite.toml"
    suite.write_text(_suite_toml(), encoding="utf-8")
    out = tmp_path / "r.json"
    captures: list[SuiteCapture] = []
    monkeypatch.setattr(cli, "run_suite", _capture_run_suite(captures))

    result = _invoke(
        [
            "suite",
            "--corpus",
            str(suite),
            "--output",
            str(out),
            "--tool",
            "stub",
            "--thread-mode",
            "constrained",
            "--no-calibrate",
            "--no-measure",
        ]
    )

    assert result.exit_code == 0, result.output
    assert len(captures) == 1
    assert captures[0].thread_modes == [ThreadMode.CONSTRAINED]


def test_suite_without_config_preserves_bare_back_compat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suite = tmp_path / "suite.toml"
    suite.write_text(_suite_toml(), encoding="utf-8")
    out = tmp_path / "r.json"
    captures: list[SuiteCapture] = []
    monkeypatch.setattr(cli, "run_suite", _capture_run_suite(captures))

    result = _invoke(
        [
            "suite",
            "--corpus",
            str(suite),
            "--output",
            str(out),
            "--no-calibrate",
            "--no-measure",
        ]
    )

    assert result.exit_code == 0, result.output
    assert len(captures) == 1
    assert captures[0].checkers == (
        CheckerSpec(tool="mypy"),
        CheckerSpec(tool="pyright"),
        CheckerSpec(tool="pyrefly"),
        CheckerSpec(tool="ty"),
    )
    assert captures[0].thread_modes == [ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED]
    assert captures[0].cores == (1,)


def test_run_config_without_dry_run_is_preview_only(tmp_path: Path) -> None:
    config = tmp_path / "typebench.toml"
    config.write_text('[[checker]]\ntool = "stub"\nversion = "0"\n', encoding="utf-8")
    out = tmp_path / "r.json"

    result = _invoke(["run", "-c", str(config), "--tool", "stub", "--output", str(out)])

    assert result.exit_code == 2
    assert "run -c is preview-only" in result.output


def test_suite_rejects_strict_policy_before_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom() -> ResultsEnvelope:
        raise AssertionError("run_suite must not run under an unsupported policy")

    monkeypatch.setattr(cli, "run_suite", boom)
    suite = tmp_path / "suite.toml"
    suite.write_text(_suite_toml(), encoding="utf-8")
    config = tmp_path / "typebench.toml"
    config.write_text(
        'policy = "strict"\n[[checker]]\ntool = "stub"\nversion = "0"\n', encoding="utf-8"
    )
    out = tmp_path / "r.json"

    result = _invoke(["suite", "--corpus", str(suite), "--output", str(out), "-c", str(config)])

    assert result.exit_code == 2
    assert "strict" in result.output
    assert not out.exists()


def test_suite_rejects_unknown_tool_up_front(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom() -> ResultsEnvelope:
        raise AssertionError("run_suite must not run for an unknown tool")

    monkeypatch.setattr(cli, "run_suite", boom)
    suite = tmp_path / "suite.toml"
    suite.write_text(_suite_toml(), encoding="utf-8")
    out = tmp_path / "r.json"

    result = _invoke(["suite", "--corpus", str(suite), "--output", str(out), "--tool", "bogus"])

    assert result.exit_code == 2
    assert "bogus" in result.output
    assert not out.exists()


def test_suite_reads_run_knobs_from_config_when_cli_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suite = tmp_path / "suite.toml"
    suite.write_text(_suite_toml(), encoding="utf-8")
    config = tmp_path / "typebench.toml"
    config.write_text(
        '[[checker]]\ntool = "stub"\nversion = "0"\n[run]\nruns = 5\nwarmup = 2\nmem_runs = 1\n',
        encoding="utf-8",
    )
    out = tmp_path / "r.json"
    captures: list[SuiteCapture] = []
    monkeypatch.setattr(cli, "run_suite", _capture_run_suite(captures))

    result = _invoke(
        [
            "suite",
            "--corpus",
            str(suite),
            "--output",
            str(out),
            "-c",
            str(config),
            "--no-calibrate",
            "--no-measure",
        ]
    )

    assert result.exit_code == 0, result.output
    # The file's [run] layer wins when the CLI flag is absent (defaults < file < CLI).
    assert (captures[0].runs, captures[0].warmup, captures[0].mem_runs) == (5, 2, 1)
