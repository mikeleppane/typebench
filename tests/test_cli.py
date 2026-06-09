from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from typebench import cli
from typebench.cli import app
from typebench.collector import RunManifest
from typebench.corpus import CorpusProject
from typebench.models import (
    CalibrationStats,
    EnvFingerprint,
    PreparedProject,
    ResultClass,
    RunResult,
    ThreadMode,
)

runner = CliRunner()


def _invoke_run(args: list[str]) -> Result:
    return runner.invoke(app, ["run", *args])


def _fake_result() -> RunResult:
    # Fixed values keep the fake type-safe under pyrefly strict (kwargs are typed
    # `object`); the tests assert against `captured`, not this record's fields.
    return RunResult(
        tool="stub",
        tool_version="0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=EnvFingerprint(
            os="Linux",
            kernel="x",
            cpu_model="x",
            core_count=1,
            python_version="3.12.0",
        ),
    )


def test_cli_run_stub_writes_results_json(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    result = runner.invoke(
        app,
        [
            "run",
            "--tool",
            "stub",
            "--project",
            "demo",
            "--thread-mode",
            "all-cores",
            "--runs",
            "2",
            "--warmup",
            "1",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = RunResult.model_validate_json(out.read_text())
    assert parsed.tool == "stub"
    assert parsed.project == "demo"


def test_cli_run_rejects_unknown_tool(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["run", "--tool", "nope", "--project", "demo", "--output", str(tmp_path / "r.json")],
    )
    assert result.exit_code == 2
    assert "Unknown tool" in result.output


def test_cli_run_rejects_unwritable_output_dir(tmp_path: Path) -> None:
    # A bad --output must fail fast (exit 2) BEFORE the run, not discard the work
    # at the very end when the write fails.
    missing = tmp_path / "does-not-exist" / "results.json"
    result = runner.invoke(
        app,
        ["run", "--tool", "stub", "--project", "demo", "--output", str(missing)],
    )
    assert result.exit_code == 2
    assert "writable" in result.output.lower()


def test_run_passes_measure_and_calibration_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Capture what run_single receives so we assert the flags + calibration wire
    # through, without invoking real systemd / hyperfine.
    captured: dict[str, object] = {}

    def fake_run_single(adapter: object, **kwargs: object) -> RunResult:
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "run_single", fake_run_single)

    sentinel = CalibrationStats(
        workload_id="calib-pyloop-v1",
        iterations=1,
        runs=1,
        raw_min_s=0.1,
        raw_median_s=0.1,
        raw_max_s=0.1,
    )

    def fake_calibrate(runs: int) -> CalibrationStats:
        return sentinel

    monkeypatch.setattr(cli, "calibrate", fake_calibrate, raising=False)

    out = tmp_path / "r.json"
    result = _invoke_run(
        [
            "--tool",
            "stub",
            "--project",
            "demo",
            "--output",
            str(out),
            "--mem-runs",
            "4",
            "--no-calibrate",
        ]
    )
    assert result.exit_code == 0
    assert captured["mem_runs"] == 4
    assert captured["calibration"] is None


def test_run_calibrates_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_single(adapter: object, **kwargs: object) -> RunResult:
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "run_single", fake_run_single)
    sentinel = CalibrationStats(
        workload_id="calib-pyloop-v1",
        iterations=1,
        runs=1,
        raw_min_s=0.1,
        raw_median_s=0.1,
        raw_max_s=0.1,
    )

    def fake_calibrate(runs: int) -> CalibrationStats:
        return sentinel

    monkeypatch.setattr(cli, "calibrate", fake_calibrate, raising=False)

    out = tmp_path / "r.json"
    result = _invoke_run(["--tool", "stub", "--project", "demo", "--output", str(out)])
    assert result.exit_code == 0
    assert captured["calibration"] is sentinel


def test_run_rejects_zero_mem_runs(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    result = _invoke_run(
        ["--tool", "stub", "--project", "demo", "--output", str(out), "--mem-runs", "0"]
    )
    assert result.exit_code == 2


def test_run_corpus_mode_builds_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    entry = CorpusProject(
        name="httpx",
        repo_url="https://x",
        sha="80960fa",
        tag="0.28.0",
        size_bucket="small",
        python_version="3.12",
        src_roots=("httpx",),
        install=("uv pip install .",),
    )
    prepared = PreparedProject(
        name="httpx",
        checkout=str(tmp_path / "repo"),
        venv_python=str(tmp_path / "venv/bin/python"),
        src_roots=(str(tmp_path / "repo/httpx"),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        python_platform="linux",
        sha="80960fa",
        lock_hash="LH",
        frozen=("httpx==0.28.0",),
        canonical_files=23,
        canonical_loc=4000,
        canonical_code_loc=3200,
        fingerprint="fp",
    )

    def fake_lookup_project(_corpus: Path, _name: str) -> CorpusProject:
        return entry

    def fake_prepare_project(_entry: CorpusProject, _cache_root: Path) -> PreparedProject:
        return prepared

    monkeypatch.setattr(cli, "_lookup_project", fake_lookup_project, raising=True)
    monkeypatch.setattr(cli, "prepare_project", fake_prepare_project, raising=True)

    captured: dict[str, object] = {}

    def fake_run_single(adapter: object, **kwargs: object) -> RunResult:
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "run_single", fake_run_single)
    out = tmp_path / "r.json"
    suite = tmp_path / "suite.toml"
    suite.write_text("")
    result = _invoke_run(
        [
            "--tool",
            "mypy",
            "--corpus",
            str(suite),
            "--corpus-project",
            "httpx",
            "--output",
            str(out),
            "--no-calibrate",
            "--no-measure",
        ]
    )
    assert result.exit_code == 0, result.output
    man = captured["manifest"]
    assert isinstance(man, RunManifest)
    assert man.project_sha == "80960fa"
    assert man.lock_hash == "LH"
    assert man.canonical_code_loc == 3200
    assert man.tool_install_source == "PyPI wheel (mypyc-compiled)"
    assert man.config_hash is not None and len(man.config_hash) == 64
