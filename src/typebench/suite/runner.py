"""Suite orchestration for the project x checker-id x thread-mode matrix.

Runs the matrix behind the preflight gate and writes a ResultsEnvelope. Off the
measured path; pydantic via `models` is fine here.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.contracts.config import NormalizedConfig, config_hash
from typebench.contracts.models import (
    FailurePhase,
    ResolvedChecker,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
)
from typebench.contracts.policy import Policy
from typebench.corpus.counting import first_party_files
from typebench.engine import measure
from typebench.engine.collector import RunManifest
from typebench.engine.env import detect_env
from typebench.engine.proc import SYSTEM_HOST
from typebench.engine.timing import TimingCommandError, run_timing

if TYPE_CHECKING:
    from typebench.adapters.base import CheckerHandle
    from typebench.contracts.identity import CheckerSpec
    from typebench.contracts.models import PreparedProject
    from typebench.contracts.runconfig import RunConfig
    from typebench.corpus.catalog import CorpusProject
    from typebench.engine.calibration import CalibrationStats
    from typebench.suite.ports import BenchEngine, CheckerResolver, CorpusSource

_process_host = SYSTEM_HOST


@dataclass(frozen=True)
class SuiteCell:
    """One unit of the benchmark matrix, keyed by resolved checker identity."""

    project: str
    checker_id: str  # was: tool — two versions of one tool are distinct cells
    thread_mode: ThreadMode
    cores: int | None  # CONSTRAINED's pinned core count; None for ALL_CORES


def build_matrix(
    projects: list[str],
    checker_ids: list[str],
    thread_modes: list[ThreadMode],
    cores: tuple[int, ...] = (1,),
) -> list[SuiteCell]:
    """Project-major matrix so a project's clone/venv is reused across its cells.
    ALL_CORES cells carry cores=None (unconstrained); CONSTRAINED cells carry the
    selected core count. Cores multiply only CONSTRAINED cells; ALL_CORES is emitted
    exactly once per project/checker with cores=None."""
    cells: list[SuiteCell] = []
    for project in projects:
        for checker_id in checker_ids:
            for mode in thread_modes:
                if mode is ThreadMode.ALL_CORES:
                    cells.append(SuiteCell(project, checker_id, mode, None))
                else:
                    cells.extend(SuiteCell(project, checker_id, mode, n) for n in cores)
    return cells


def shard(cells: list[SuiteCell], index: int, total: int) -> list[SuiteCell]:
    """Deterministic round-robin partition. `total=1` is the
    identity. Round-robin (not contiguous slices) spreads heavy/light cells evenly
    across shards so no single CI job inherits all the giant-bucket work.
    """
    if total < 1:
        raise ValueError(f"shard total must be >= 1, got {total}")
    if not 0 <= index < total:
        raise ValueError(f"shard index {index} out of range for total {total}")
    return [cell for position, cell in enumerate(cells) if position % total == index]


def _excluded_record(
    cell: SuiteCell,
    checker: CheckerHandle,
    prepared: PreparedProject | None,
    entry: CorpusProject | None,
    detail: str,
    calibration: CalibrationStats | None,
    policy: Policy,
    headline_eligible: bool,
) -> RunResult:
    """A FAILED_ENV record for a cell whose project was excluded by preflight or a
    prepare failure. The bar must read 'didn't compete', never be silently absent.
    Carries whatever repro scalars are known."""
    ch = (
        config_hash(
            entry.src_roots,
            entry.effective_excludes(),
            entry.python_version,
            entry.python_platform,
        )
        if entry is not None
        else None
    )
    return RunResult(
        tool=checker.tool,
        tool_version="unknown",
        checker_id=cell.checker_id,
        policy=policy,
        headline_eligible=headline_eligible,
        project=cell.project,
        thread_mode=cell.thread_mode,
        result_class=ResultClass.FAILED_ENV,
        failure_phase=FailurePhase.PROBE,
        real_exit_code=-1,
        error_detail=detail.strip()[-500:] or None,
        project_sha=prepared.sha if prepared else (entry.sha if entry else None),
        lock_hash=prepared.lock_hash if prepared else None,
        config_hash=ch,
        tool_install_source=checker.install_source,
        canonical_files=prepared.canonical_files if prepared else None,
        canonical_loc=prepared.canonical_loc if prepared else None,
        canonical_code_loc=prepared.canonical_code_loc if prepared else None,
        calibration=calibration,
        env=detect_env(),
    )


def _exclude_cells(
    project_cells: list[SuiteCell],
    handle_by_id: dict[str, CheckerHandle],
    prepared: PreparedProject | None,
    entry: CorpusProject,
    detail: str,
    calibration: CalibrationStats | None,
    policy: Policy,
) -> list[RunResult]:
    headline_eligible = policy is Policy.STANDARD
    return [
        _excluded_record(
            cell,
            handle_by_id[cell.checker_id],
            prepared,
            entry,
            detail,
            calibration,
            policy,
            headline_eligible,
        )
        for cell in project_cells
    ]


def _checker_resolve_failed_record(
    cell: SuiteCell,
    spec: CheckerSpec,
    detail: str,
    calibration: CalibrationStats | None,
    policy: Policy,
) -> RunResult:
    """A visible FAILED_ENV record for a checker that could not resolve up front."""
    return RunResult(
        tool=spec.tool,
        tool_version="unknown",
        checker_id=spec.checker_id(),
        policy=policy,
        headline_eligible=False,
        project=cell.project,
        thread_mode=cell.thread_mode,
        # cores stays None: no command ran, no affinity was enforced — the same
        # honesty contract as _excluded_record (never claim a pin that did not run).
        result_class=ResultClass.FAILED_ENV,
        failure_phase=FailurePhase.PROBE,
        real_exit_code=-1,
        error_detail=detail.strip()[-500:] or None,
        calibration=calibration,
        env=detect_env(),
    )


def _suite_config(prepared: PreparedProject, cores: int) -> NormalizedConfig:
    return NormalizedConfig(
        src_roots=prepared.src_roots,
        exclude_globs=prepared.exclude_globs,
        python_version=prepared.python_version,
        python_platform=prepared.python_platform,
        venv_python=prepared.venv_python or None,
        cores=cores,
    )


def prewarm_project_sources(prepared: PreparedProject) -> None:
    """Read canonical sources outside the measured scope.

    cgroup v2 charges file-cache pages to the first scope that faults them in.
    Pre-warming makes file pages consistently host-cache-backed before the memory
    pass, so memory.peak measures anonymous/kernel working set rather than
    accidental first-touch file cache state.
    """
    roots = [Path(root) for root in prepared.src_roots]
    for path in first_party_files(roots, prepared.exclude_globs):
        try:
            path.read_bytes()
        except OSError:
            continue


def _measure_harness_baselines(
    *, timeout: float, runs: int, warmup: int, measure_enabled: bool
) -> tuple[int | None, float | None]:
    """Measure raw suite-level harness costs, returning None when unavailable."""
    mem_baseline: int | None = None
    if measure_enabled and measure.capable():
        try:
            resource = measure.scoped_probe(
                [sys.executable, "-c", "pass"],
                extra_env={},
                timeout=timeout,
                repeats=1,
            )
        except (measure.MeasureError, OSError, ValueError, KeyError):
            mem_baseline = None
        else:
            if resource.memory is not None:
                mem_baseline = resource.memory.peak_bytes_median

    wall_overhead: float | None = None
    true_bin = _process_host.which("true")
    if true_bin is not None and _process_host.which("hyperfine") is not None:
        try:
            timing = run_timing(
                [true_bin],
                prepare_cmd=None,
                warmup=warmup,
                runs=runs,
                timeout=timeout,
                extra_env={},
            )
        except (TimingCommandError, OSError, ValueError, KeyError):
            wall_overhead = None
        else:
            wall_overhead = timing.median_s

    return mem_baseline, wall_overhead


def _failed_checker_records(
    failed_specs: list[tuple[CheckerSpec, str]],
    all_projects: list[str],
    thread_modes: list[ThreadMode],
    cores: tuple[int, ...],
    shard_index: int,
    shard_total: int,
    calibration: CalibrationStats | None,
    policy: Policy,
) -> list[RunResult]:
    """FAILED_ENV records for every cell of a checker that could not resolve, so it
    reads 'didn't compete' rather than being silently absent."""
    records: list[RunResult] = []
    for spec, detail in failed_specs:
        cells = shard(
            build_matrix(all_projects, [spec.checker_id()], thread_modes, cores),
            shard_index,
            shard_total,
        )
        records.extend(
            _checker_resolve_failed_record(cell, spec, detail, calibration, policy)
            for cell in cells
        )
    return records


def _resolve_checkers(
    *,
    specs: tuple[CheckerSpec, ...],
    resolver: CheckerResolver,
) -> tuple[list[CheckerHandle], list[tuple[CheckerSpec, str]]]:
    """Resolve each spec up front; failures become visible records, never aborts."""
    handles: list[CheckerHandle] = []
    failed: list[tuple[CheckerSpec, str]] = []
    for spec in specs:
        try:
            handles.append(resolver.resolve(spec))
        except Exception as exc:
            failed.append((spec, f"checker resolve failed: {exc}"))
    return handles, failed


def run_suite(
    *,
    config: RunConfig,
    corpus: CorpusSource,
    resolver: CheckerResolver,
    engine: BenchEngine,
    generated_at: str,
    shard_index: int = 0,
    shard_total: int = 1,
) -> ResultsEnvelope:
    """Run the sharded matrix behind the preflight gate -> ResultsEnvelope.

    Per project (project-major, so the clone/venv is reused): prepare -> preflight;
    if the project is not ready (or prepare fails), emit one FAILED_ENV record per
    cell (visible 'didn't compete') and skip running. Otherwise run each ready
    cell via run_one with a stamped RunManifest. One calibration per invocation
    is attached to every record."""
    entries = corpus.entries()
    all_projects = [entry.name for entry in entries]
    entry_by_name = {entry.name: entry for entry in entries}
    suite_version = corpus.version()
    plan = config.measurement_plan()
    handles, failed_specs = _resolve_checkers(specs=config.checkers, resolver=resolver)
    checker_ids = [handle.checker_id for handle in handles]
    handle_by_id = {handle.checker_id: handle for handle in handles}
    cells = shard(
        build_matrix(
            all_projects,
            checker_ids,
            list(config.thread_modes),
            config.cores,
        ),
        shard_index,
        shard_total,
    )

    calibration: CalibrationStats | None = None
    if config.calibrate:
        calibration = engine.calibrate(config.calib_runs)
    harness_mem_baseline_bytes, harness_wall_overhead_s = _measure_harness_baselines(
        timeout=plan.timeout_s,
        runs=plan.runs,
        warmup=plan.warmup,
        measure_enabled=plan.measure,
    )

    # Group sharded cells by project, preserving matrix order.
    by_project: dict[str, list[SuiteCell]] = {}
    for cell in cells:
        by_project.setdefault(cell.project, []).append(cell)

    results: list[RunResult] = _failed_checker_records(
        failed_specs,
        all_projects,
        list(config.thread_modes),
        config.cores,
        shard_index,
        shard_total,
        calibration,
        config.policy,
    )
    for project, project_cells in by_project.items():
        entry = entry_by_name[project]
        project_handles = list(
            {cell.checker_id: handle_by_id[cell.checker_id] for cell in project_cells}.values()
        )

        try:
            prepared = corpus.prepare(entry)
        # Broad by intent: prepare_project raises PrepareError, but a failure of ANY
        # kind must still emit records for the project's cells, never abort the suite.
        except Exception as exc:
            results.extend(
                _exclude_cells(
                    project_cells,
                    handle_by_id,
                    None,
                    entry,
                    f"prepare failed: {exc}",
                    calibration,
                    config.policy,
                )
            )
            continue

        try:
            prewarm_project_sources(prepared)
        except Exception as exc:
            results.extend(
                _exclude_cells(
                    project_cells,
                    handle_by_id,
                    prepared,
                    entry,
                    f"source pre-warm failed: {exc}",
                    calibration,
                    config.policy,
                )
            )
            continue

        report = engine.preflight(
            prepared,
            project_handles,
            timeout=plan.timeout_s,
            policy=config.policy,
        )
        if not report.ready:
            detail = "; ".join(
                f"{t.tool}: {t.result_class.value} {t.error_detail or ''}".strip()
                for t in report.tools
                if not (t.result_class.is_measured_success and t.scope_ok)
            )
            results.extend(
                _exclude_cells(
                    project_cells,
                    handle_by_id,
                    prepared,
                    entry,
                    detail or "preflight not ready",
                    calibration,
                    config.policy,
                )
            )
            continue

        over_by_checker = {t.checker_id: t.over_reports for t in report.tools}
        ch = config_hash(
            entry.src_roots,
            entry.effective_excludes(),
            entry.python_version,
            entry.python_platform,
        )
        for cell in project_cells:
            handle = handle_by_id[cell.checker_id]
            cell_cores = cell.cores if cell.cores is not None else 1
            cell_config = _suite_config(prepared, cell_cores)
            manifest = RunManifest(
                project_sha=prepared.sha,
                lock_hash=prepared.lock_hash,
                config_hash=ch,
                canonical_files=prepared.canonical_files,
                canonical_loc=prepared.canonical_loc,
                canonical_code_loc=prepared.canonical_code_loc,
                tool_install_source=handle.install_source,
                over_reports=over_by_checker.get(handle.checker_id, False),
            )
            results.append(
                engine.run_cell(
                    handle,
                    project=project,
                    config=cell_config,
                    thread_mode=cell.thread_mode,
                    plan=plan,
                    calibration=calibration,
                    manifest=manifest,
                    policy=config.policy,
                )
            )

    resolved_checkers = tuple(
        ResolvedChecker(
            checker_id=handle.checker_id,
            tool=handle.tool,
            version=handle.runtime.version if handle.runtime is not None else "unknown",
            lock_hash=handle.runtime.lock_hash if handle.runtime is not None else "",
            install_source=handle.install_source,
        )
        for handle in handles
    )
    return ResultsEnvelope(
        suite_version=suite_version,
        generated_at=generated_at,
        runs=results,
        run_config=config,
        resolved_checkers=resolved_checkers,
        harness_mem_baseline_bytes=harness_mem_baseline_bytes,
        harness_wall_overhead_s=harness_wall_overhead_s,
    )
