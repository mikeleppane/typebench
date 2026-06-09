from pathlib import Path

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.contracts.models import (
    CalibrationStats,
    PreflightReport,
    PreparedProject,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    ToolPreflight,
)
from typebench.corpus.catalog import CorpusProject, SizeBucket
from typebench.engine.env import detect_env
from typebench.suite import SuiteCell, build_matrix, run_suite, shard


def test_build_matrix_is_project_major() -> None:
    cells = build_matrix(["a", "b"], ["mypy", "ty"], [ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED])
    assert len(cells) == 8
    assert cells[0] == SuiteCell("a", "mypy", ThreadMode.ALL_CORES)
    assert all(isinstance(c, SuiteCell) for c in cells)


def test_shard_partitions_disjointly_and_covers_all() -> None:
    cells = build_matrix(["a", "b", "c"], ["mypy", "ty"], [ThreadMode.ALL_CORES])
    s0 = shard(cells, 0, 3)
    s1 = shard(cells, 1, 3)
    s2 = shard(cells, 2, 3)
    assert set(s0) | set(s1) | set(s2) == set(cells)
    assert not (set(s0) & set(s1))
    assert len(s0) + len(s1) + len(s2) == len(cells)


def test_shard_total_one_is_identity() -> None:
    cells = build_matrix(["a"], ["mypy"], [ThreadMode.ALL_CORES])
    assert shard(cells, 0, 1) == cells


def test_shard_rejects_bad_index() -> None:
    cells = build_matrix(["a"], ["mypy"], [ThreadMode.ALL_CORES])
    with pytest.raises(ValueError):
        shard(cells, 3, 3)
    with pytest.raises(ValueError):
        shard(cells, 0, 0)


def _prepared(name: str) -> PreparedProject:
    return PreparedProject(
        name=name,
        checkout="/x/repo",
        venv_python="/x/venv/bin/python",
        src_roots=("/x/repo/pkg",),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        python_platform="linux",
        sha="SHA1",
        lock_hash="LH",
        frozen=("pkg==1.0",),
        canonical_files=10,
        canonical_loc=500,
        canonical_code_loc=400,
        fingerprint="fp",
    )


def _entry(name: str) -> CorpusProject:
    return CorpusProject(
        name=name,
        repo_url="https://x",
        sha="SHA1",
        tag="v1",
        size_bucket=SizeBucket.SMALL,
        python_version="3.12",
        src_roots=("pkg",),
        install=("uv pip install .",),
        exclude_globs=("**/tests/**",),
    )


def _ready_report(name: str, tools: list[str]) -> PreflightReport:
    return PreflightReport(
        project=name,
        sha="SHA1",
        python_version="3.12",
        lock_hash="LH",
        canonical_files=10,
        canonical_loc=500,
        ready=True,
        tools=[
            ToolPreflight(
                tool=t,
                version="1",
                result_class=ResultClass.CLEAN,
                real_exit_code=0,
                self_reported_files=10,
                over_reports=False,
            )
            for t in tools
        ],
    )


def _calib() -> CalibrationStats:
    return CalibrationStats(
        workload_id="calib-pyloop-v1",
        iterations=1,
        runs=1,
        raw_min_s=0.3,
        raw_median_s=0.3,
        raw_max_s=0.3,
    )


def test_run_suite_runs_ready_cells_and_builds_envelope() -> None:
    captured: list[object] = []

    def fake_run_one(adapter: object, **kwargs: object) -> RunResult:
        captured.append(kwargs.get("manifest"))

        return RunResult(
            tool=getattr(adapter, "name", "stub"),
            tool_version="1",
            project=str(kwargs["project"]),
            thread_mode=ThreadMode(kwargs["thread_mode"])
            if not isinstance(kwargs["thread_mode"], ThreadMode)
            else kwargs["thread_mode"],
            result_class=ResultClass.CLEAN,
            real_exit_code=0,
            env=detect_env(),
        )

    envelope = run_suite(
        suite_path=Path("/x/suite.toml"),
        cache_root=Path("/x/cache"),
        tools=["stub"],
        thread_modes=[ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED],
        generated_at="2026-06-08T00:00:00Z",
        runs=1,
        warmup=1,
        timeout=10,
        mem_runs=1,
        measure_enabled=False,
        calib_runs=1,
        load_projects=lambda _p: ["demo"],
        load_version=lambda _p: "2026-06-08",
        adapter_factory=lambda _name: StubAdapter(),
        lookup_entry=lambda _p, name: _entry(name),
        prepare=lambda _entry, _cache: _prepared("demo"),
        preflight=lambda _prepared, _adapters, **_kwargs: _ready_report("demo", ["stub"]),
        run_one=fake_run_one,
        calibrate_fn=lambda _runs: _calib(),
    )
    assert isinstance(envelope, ResultsEnvelope)
    assert envelope.suite_version == "2026-06-08"
    assert envelope.generated_at == "2026-06-08T00:00:00Z"
    assert len(envelope.runs) == 2
    assert all(r.result_class == ResultClass.CLEAN for r in envelope.runs)
    assert all(m is not None for m in captured)


def test_run_suite_excluded_project_emits_failed_records() -> None:
    not_ready = PreflightReport(
        project="demo",
        sha="SHA1",
        python_version="3.12",
        lock_hash="LH",
        canonical_files=10,
        canonical_loc=500,
        ready=False,
        tools=[
            ToolPreflight(
                tool="stub",
                version="1",
                result_class=ResultClass.FAILED_ENV,
                real_exit_code=3,
                error_detail="pyrefly env error",
            )
        ],
    )

    def boom_run_one(adapter: object, **kwargs: object) -> RunResult:
        raise AssertionError("run_one must NOT be called for an excluded project")

    envelope = run_suite(
        suite_path=Path("/x/suite.toml"),
        cache_root=Path("/x/cache"),
        tools=["stub"],
        thread_modes=[ThreadMode.ALL_CORES],
        generated_at="t",
        runs=1,
        warmup=1,
        timeout=10,
        mem_runs=1,
        measure_enabled=False,
        calib_runs=1,
        load_projects=lambda _p: ["demo"],
        load_version=lambda _p: "v",
        adapter_factory=lambda _name: StubAdapter(),
        lookup_entry=lambda _p, name: _entry(name),
        prepare=lambda _entry, _cache: _prepared("demo"),
        preflight=lambda _prepared, _adapters, **_kwargs: not_ready,
        run_one=boom_run_one,
        calibrate_fn=lambda _runs: _calib(),
    )
    assert len(envelope.runs) == 1
    rec = envelope.runs[0]
    assert rec.result_class == ResultClass.FAILED_ENV
    assert rec.error_detail is not None and "pyrefly env error" in rec.error_detail
