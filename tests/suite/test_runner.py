from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

import typebench.suite.runner as runner_mod
from typebench.adapters.base import CheckerHandle
from typebench.adapters.stub import StubAdapter
from typebench.contracts.config import MeasurementPlan, NormalizedConfig
from typebench.contracts.identity import CheckerRuntime, CheckerSpec
from typebench.contracts.models import (
    CalibrationStats,
    EnvFingerprint,
    FailurePhase,
    PreflightReport,
    PreparedProject,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    ToolPreflight,
)
from typebench.contracts.policy import Policy
from typebench.contracts.runconfig import RunConfig
from typebench.corpus.catalog import CorpusProject, SizeBucket
from typebench.engine.collector import RunManifest
from typebench.suite.runner import SuiteCell, build_matrix, run_suite, shard

type EnvFactory = Callable[..., EnvFingerprint]
type CellKey = tuple[str, str, ThreadMode, int | None]


def test_build_matrix_is_project_major() -> None:
    cells = build_matrix(
        ["a", "b"], ["mypy@latest", "ty@latest"], [ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED]
    )
    assert len(cells) == 8
    # checker_id is the cell key (was: tool); ALL_CORES carries cores=None.
    assert cells[0] == SuiteCell("a", "mypy@latest", ThreadMode.ALL_CORES, None)
    assert all(isinstance(c, SuiteCell) for c in cells)


def test_build_matrix_cores_list_multiplies_only_constrained() -> None:
    cells = build_matrix(
        ["demo"],
        ["mypy@latest", "pyright@latest"],
        [ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED],
        cores=(1, 4),
    )

    assert cells == [
        SuiteCell("demo", "mypy@latest", ThreadMode.ALL_CORES, None),
        SuiteCell("demo", "mypy@latest", ThreadMode.CONSTRAINED, 1),
        SuiteCell("demo", "mypy@latest", ThreadMode.CONSTRAINED, 4),
        SuiteCell("demo", "pyright@latest", ThreadMode.ALL_CORES, None),
        SuiteCell("demo", "pyright@latest", ThreadMode.CONSTRAINED, 1),
        SuiteCell("demo", "pyright@latest", ThreadMode.CONSTRAINED, 4),
    ]


def test_build_matrix_single_core_default_is_one_constrained_cell() -> None:
    cells = build_matrix(
        ["demo"],
        ["mypy@latest"],
        [ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED],
    )

    assert cells == [
        SuiteCell("demo", "mypy@latest", ThreadMode.ALL_CORES, None),
        SuiteCell("demo", "mypy@latest", ThreadMode.CONSTRAINED, 1),
    ]


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


@pytest.mark.parametrize(
    ("policy", "expected_headline_eligible"),
    [(Policy.STANDARD, True), (Policy.STRICT, False)],
)
def test_exclude_cells_records_failed_env_for_each_cell(
    policy: Policy, expected_headline_eligible: bool
) -> None:
    specs = (CheckerSpec(tool="stub", version="1.0"), CheckerSpec(tool="pyright", version="1.1.0"))
    handles = [FakeResolver().resolve(spec) for spec in specs]
    handle_by_id = {handle.checker_id: handle for handle in handles}
    cells = [
        SuiteCell("demo", "stub@1.0", ThreadMode.ALL_CORES, None),
        SuiteCell("demo", "pyright@1.1.0", ThreadMode.CONSTRAINED, 4),
    ]
    detail = f" {'0123456789' * 60} "

    records = runner_mod._exclude_cells(
        cells,
        handle_by_id,
        _prepared("demo"),
        _entry("demo"),
        detail,
        _calib(),
        policy,
    )

    assert len(records) == len(cells)
    assert [record.checker_id for record in records] == ["stub@1.0", "pyright@1.1.0"]
    assert all(record.result_class is ResultClass.FAILED_ENV for record in records)
    assert all(record.failure_phase is FailurePhase.PROBE for record in records)
    assert all(record.error_detail == detail.strip()[-500:] for record in records)
    assert all(record.headline_eligible is expected_headline_eligible for record in records)


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


def _prepare_stub_runtime(spec: CheckerSpec, *, install_source: str) -> CheckerRuntime:
    return CheckerRuntime(
        checker_id=spec.checker_id(),
        tool=spec.tool,
        binary="/b/stub",
        version=spec.version or "1.0",
        lock_hash="L",
        install_source=install_source,
    )


@dataclass
class FakeCorpus:
    entries_: list[CorpusProject]
    version_: str = "v"
    prepared: dict[str, PreparedProject] = field(default_factory=dict)
    prepare_failures: dict[str, Exception] = field(default_factory=dict)

    def entries(self) -> list[CorpusProject]:
        return list(self.entries_)

    def version(self) -> str:
        return self.version_

    def prepare(self, entry: CorpusProject) -> PreparedProject:
        failure = self.prepare_failures.get(entry.name)
        if failure is not None:
            raise failure
        return self.prepared.get(entry.name, _prepared(entry.name))


@dataclass
class FakeResolver:
    fail_tools: set[str] = field(default_factory=set)

    def resolve(self, spec: CheckerSpec) -> CheckerHandle:
        if spec.tool in self.fail_tools:
            raise RuntimeError("no matching wheel")
        adapter = StubAdapter()
        runtime = _prepare_stub_runtime(spec, install_source=adapter.install_source)
        return CheckerHandle(spec=spec, adapter=adapter, runtime=runtime)


@dataclass
class FakeEngine:
    make_env: EnvFactory
    report: PreflightReport | None = None
    run_should_fail: bool = False
    preflight_failures: set[str] = field(default_factory=set)
    run_cell_failures: set[CellKey] = field(default_factory=set)
    calibration_calls: list[int] = field(default_factory=list)
    preflight_timeouts: list[float] = field(default_factory=list)
    run_manifests: list[RunManifest] = field(default_factory=list)
    run_plans: list[object] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def calibrate(self, runs: int) -> CalibrationStats:
        self.calibration_calls.append(runs)
        return _calib()

    def preflight(
        self,
        prepared: PreparedProject,
        checkers: list[CheckerHandle],
        *,
        timeout: float,
        policy: Policy,
    ) -> PreflightReport:
        self.preflight_timeouts.append(timeout)
        if prepared.name in self.preflight_failures:
            raise RuntimeError(f"preflight exploded for {prepared.name}")
        if self.report is not None:
            return self.report
        return _ready_report(prepared.name, [checker.tool for checker in checkers])

    def run_cell(
        self,
        checker: CheckerHandle,
        *,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        plan: MeasurementPlan,
        manifest: RunManifest,
        calibration: CalibrationStats | None,
        policy: Policy,
    ) -> RunResult:
        if self.run_should_fail:
            raise AssertionError("run_cell must NOT be called")
        if (project, checker.checker_id, thread_mode, config.cores) in self.run_cell_failures:
            raise RuntimeError(f"run_cell exploded for {project} {checker.checker_id}")
        self.events.append("run_cell")
        self.run_manifests.append(manifest)
        self.run_plans.append(plan)
        return RunResult(
            tool=checker.tool,
            tool_version=checker.runtime.version if checker.runtime is not None else "1",
            checker_id=checker.checker_id,
            policy=policy,
            headline_eligible=policy is Policy.STANDARD,
            project=project,
            thread_mode=thread_mode,
            result_class=ResultClass.CLEAN,
            real_exit_code=0,
            calibration=calibration,
            env=self.make_env(),
        )


def test_run_suite_runs_ready_cells_and_builds_envelope(make_env: EnvFactory) -> None:
    run_config = RunConfig(
        checkers=(CheckerSpec(tool="stub", version="1.0"),),
        runs=1,
        warmup=1,
        timeout=10,
        mem_runs=1,
        calib_runs=1,
    )
    engine = FakeEngine(make_env)
    envelope = run_suite(
        config=run_config,
        corpus=FakeCorpus([_entry("demo")], version_="2026-06-08"),
        resolver=FakeResolver(),
        engine=engine,
        generated_at="2026-06-08T00:00:00Z",
    )
    assert isinstance(envelope, ResultsEnvelope)
    assert envelope.suite_version == "2026-06-08"
    assert envelope.generated_at == "2026-06-08T00:00:00Z"
    assert len(envelope.runs) == 2
    assert all(r.result_class == ResultClass.CLEAN for r in envelope.runs)
    assert envelope.run_config == run_config
    assert envelope.resolved_checkers[0].lock_hash == "L"
    assert all(r.checker_id == "stub@1.0" for r in envelope.runs)
    assert all(m is not None for m in engine.run_manifests)
    assert engine.calibration_calls == [1]


def test_run_suite_records_harness_baselines_once(
    monkeypatch: pytest.MonkeyPatch, make_env: EnvFactory
) -> None:
    calls = {"baselines": 0}

    def fake_baselines(**kwargs: object) -> tuple[int | None, float | None]:
        calls["baselines"] += 1
        return (14_000_000, 0.029)

    monkeypatch.setattr(runner_mod, "_measure_harness_baselines", fake_baselines)

    envelope = run_suite(
        config=RunConfig(
            checkers=(CheckerSpec(tool="stub", version="1.0"),),
            thread_modes=(ThreadMode.ALL_CORES,),
            runs=1,
            warmup=1,
            timeout=10,
            mem_runs=1,
            measure=True,
            calibrate=False,
        ),
        corpus=FakeCorpus([_entry("demo")]),
        resolver=FakeResolver(),
        engine=FakeEngine(make_env),
        generated_at="t",
    )

    assert calls["baselines"] == 1
    assert envelope.harness_mem_baseline_bytes == 14_000_000
    assert envelope.harness_wall_overhead_s == 0.029


def test_run_suite_prewarms_project_sources_before_run_one(
    monkeypatch: pytest.MonkeyPatch, make_env: EnvFactory
) -> None:
    events: list[str] = []
    engine = FakeEngine(make_env, events=events)

    def fake_prewarm(prepared: PreparedProject) -> None:
        assert prepared.name == "demo"
        events.append("prewarm")

    monkeypatch.setattr(runner_mod, "prewarm_project_sources", fake_prewarm)

    run_suite(
        config=RunConfig(
            checkers=(CheckerSpec(tool="stub", version="1.0"),),
            thread_modes=(ThreadMode.ALL_CORES,),
            runs=1,
            warmup=1,
            timeout=10,
            mem_runs=1,
            measure=False,
            calibrate=False,
        ),
        corpus=FakeCorpus([_entry("demo")]),
        resolver=FakeResolver(),
        engine=engine,
        generated_at="t",
    )

    assert events == ["prewarm", "run_cell"]


def test_run_suite_excluded_project_emits_failed_records(make_env: EnvFactory) -> None:
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

    envelope = run_suite(
        config=RunConfig(
            checkers=(CheckerSpec(tool="stub", version="1.0"),),
            thread_modes=(ThreadMode.ALL_CORES,),
            runs=1,
            warmup=1,
            timeout=10,
            mem_runs=1,
            measure=False,
            calib_runs=1,
        ),
        corpus=FakeCorpus([_entry("demo")]),
        resolver=FakeResolver(),
        engine=FakeEngine(make_env, report=not_ready, run_should_fail=True),
        generated_at="t",
    )
    assert len(envelope.runs) == 1
    rec = envelope.runs[0]
    assert rec.result_class == ResultClass.FAILED_ENV
    assert rec.error_detail is not None and "pyrefly env error" in rec.error_detail
    assert rec.checker_id == "stub@1.0"


def test_run_suite_preflight_crash_emits_failed_env_for_all_cells(
    make_env: EnvFactory,
) -> None:
    envelope = run_suite(
        config=RunConfig(
            checkers=(CheckerSpec(tool="stub", version="1.0"),),
            thread_modes=(ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED),
            runs=1,
            warmup=1,
            timeout=10,
            mem_runs=1,
            measure=False,
            calibrate=False,
        ),
        corpus=FakeCorpus([_entry("demo"), _entry("second")]),
        resolver=FakeResolver(),
        engine=FakeEngine(make_env, preflight_failures={"demo"}),
        generated_at="t",
    )

    demo_records = [record for record in envelope.runs if record.project == "demo"]
    second_records = [record for record in envelope.runs if record.project == "second"]

    assert len(demo_records) == 2
    assert all(record.result_class is ResultClass.FAILED_ENV for record in demo_records)
    assert all(record.failure_phase is FailurePhase.PROBE for record in demo_records)
    assert all("preflight crashed" in (record.error_detail or "") for record in demo_records)
    assert len(second_records) == 2
    assert all(record.result_class is ResultClass.CLEAN for record in second_records)


def test_run_suite_run_cell_crash_emits_failed_env_for_that_cell(
    make_env: EnvFactory,
) -> None:
    envelope = run_suite(
        config=RunConfig(
            checkers=(CheckerSpec(tool="stub", version="1.0"),),
            thread_modes=(ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED),
            runs=1,
            warmup=1,
            timeout=10,
            mem_runs=1,
            measure=False,
            calibrate=False,
        ),
        corpus=FakeCorpus([_entry("demo")]),
        resolver=FakeResolver(),
        engine=FakeEngine(
            make_env,
            run_cell_failures={("demo", "stub@1.0", ThreadMode.ALL_CORES, 1)},
        ),
        generated_at="t",
    )

    crashed = [record for record in envelope.runs if record.thread_mode is ThreadMode.ALL_CORES]
    siblings = [record for record in envelope.runs if record.thread_mode is ThreadMode.CONSTRAINED]

    assert len(crashed) == 1
    assert crashed[0].result_class is ResultClass.FAILED_ENV
    assert crashed[0].failure_phase is FailurePhase.PROBE
    assert "run_cell crashed" in (crashed[0].error_detail or "")
    assert len(siblings) == 1
    assert siblings[0].result_class is ResultClass.CLEAN


def test_run_suite_checker_resolve_failure_emits_failed_records(
    make_env: EnvFactory,
) -> None:
    envelope = run_suite(
        config=RunConfig(
            checkers=(
                CheckerSpec(tool="stub", version="1.0"),
                CheckerSpec(tool="pyright", version="1.1.0"),
            ),
            thread_modes=(ThreadMode.ALL_CORES,),
            runs=1,
            warmup=1,
            timeout=10,
            mem_runs=1,
            measure=False,
            calib_runs=1,
        ),
        corpus=FakeCorpus([_entry("demo")]),
        resolver=FakeResolver(fail_tools={"pyright"}),
        engine=FakeEngine(make_env),
        generated_at="t",
    )

    clean = [r for r in envelope.runs if r.result_class is ResultClass.CLEAN]
    failed = [r for r in envelope.runs if r.result_class is ResultClass.FAILED_ENV]
    assert [r.checker_id for r in clean] == ["stub@1.0"]
    assert len(failed) == 1
    assert failed[0].tool == "pyright"
    assert failed[0].tool_version == "unknown"
    assert failed[0].checker_id == "pyright@1.1.0"
    assert failed[0].failure_phase is FailurePhase.PROBE
    assert failed[0].headline_eligible is False
    assert failed[0].cores is None  # no command ran -> never claim a core pin
    assert "checker resolve failed: no matching wheel" in (failed[0].error_detail or "")
