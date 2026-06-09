"""Suite orchestration (spec §10/§11). Loops the (project x tool x thread-mode)
matrix behind the §12 preflight gate and writes a ResultsEnvelope. Off the measured
path; pydantic via `models` is fine here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typebench.contracts.config import NormalizedConfig, config_hash
from typebench.contracts.models import (
    FailurePhase,
    PreflightReport,
    ResultClass,
    ResultsEnvelope,
    RunResult,
)
from typebench.corpus.catalog import load_suite, load_suite_version
from typebench.corpus.envman import prepare_project
from typebench.engine.collector import RunManifest, run_single
from typebench.engine.env import detect_env
from typebench.preflight import preflight_project

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from typebench.adapters.base import Adapter
    from typebench.contracts.models import PreparedProject, ThreadMode
    from typebench.corpus.catalog import CorpusProject
    from typebench.engine.calibration import CalibrationStats


@dataclass(frozen=True)
class SuiteCell:
    """One unit of the benchmark matrix."""

    project: str
    tool: str
    thread_mode: ThreadMode


def build_matrix(
    projects: list[str], tools: list[str], thread_modes: list[ThreadMode]
) -> list[SuiteCell]:
    """Project-major matrix so a project's clone/venv is reused across its cells."""
    return [
        SuiteCell(project, tool, mode)
        for project in projects
        for tool in tools
        for mode in thread_modes
    ]


def shard(cells: list[SuiteCell], index: int, total: int) -> list[SuiteCell]:
    """Deterministic round-robin partition (spec §10 sharding). `total=1` is the
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
    prepared: PreparedProject | None,
    entry: CorpusProject | None,
    install_source: str,
    detail: str,
    calibration: CalibrationStats | None,
) -> RunResult:
    """A FAILED_ENV record for a cell whose project was excluded by preflight or a
    prepare failure. The bar must read 'didn't compete', never be silently absent
    (spec §7/§12). Carries whatever repro scalars are known."""
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
        tool=cell.tool,
        tool_version="unknown",
        project=cell.project,
        thread_mode=cell.thread_mode,
        result_class=ResultClass.FAILED_ENV,
        failure_phase=FailurePhase.PROBE,
        real_exit_code=-1,
        error_detail=detail.strip()[-500:] or None,
        project_sha=prepared.sha if prepared else (entry.sha if entry else None),
        lock_hash=prepared.lock_hash if prepared else None,
        config_hash=ch,
        tool_install_source=install_source,
        canonical_files=prepared.canonical_files if prepared else None,
        canonical_loc=prepared.canonical_loc if prepared else None,
        canonical_code_loc=prepared.canonical_code_loc if prepared else None,
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


def run_suite(  # noqa: PLR0913 — distinct orchestration knobs + injectable seams, mirrors run_single's noqa precedent
    *,
    suite_path: Path,
    cache_root: Path,
    tools: list[str],
    thread_modes: list[ThreadMode],
    generated_at: str,
    runs: int,
    warmup: int,
    timeout: float,
    mem_runs: int,
    measure_enabled: bool,
    calib_runs: int,
    cores: int = 1,
    shard_index: int = 0,
    shard_total: int = 1,
    projects: list[str] | None = None,
    load_projects: Callable[[Path], list[str]] = lambda p: [e.name for e in load_suite(p)],
    load_version: Callable[[Path], str] = load_suite_version,
    lookup_entry: Callable[[Path, str], CorpusProject] | None = None,
    adapter_factory: Callable[[str], Adapter] | None = None,
    prepare: Callable[..., PreparedProject] = prepare_project,
    preflight: Callable[..., PreflightReport] = preflight_project,
    run_one: Callable[..., RunResult] = run_single,
    calibrate_fn: Callable[[int], CalibrationStats] | None = None,
) -> ResultsEnvelope:
    """Run the sharded matrix behind the §12 preflight gate -> ResultsEnvelope.

    Per project (project-major, so the clone/venv is reused): prepare -> preflight;
    if the project is not ready (or prepare fails), emit one FAILED_ENV record per
    cell (visible 'didn't compete', §12) and skip running. Otherwise run each ready
    cell via run_one with a stamped RunManifest. One calibration per invocation
    (Decision H) attached to every record."""
    if adapter_factory is None:
        raise ValueError("adapter_factory is required")
    if lookup_entry is None:
        raise ValueError("lookup_entry is required")

    all_projects = projects if projects is not None else load_projects(suite_path)
    suite_version = load_version(suite_path)
    cells = shard(build_matrix(all_projects, tools, thread_modes), shard_index, shard_total)

    calibration: CalibrationStats | None = None
    if calibrate_fn is not None:
        calibration = calibrate_fn(calib_runs)

    # Group sharded cells by project, preserving matrix order.
    by_project: dict[str, list[SuiteCell]] = {}
    for cell in cells:
        by_project.setdefault(cell.project, []).append(cell)

    results: list[RunResult] = []
    for project, project_cells in by_project.items():
        entry = lookup_entry(suite_path, project)
        project_tools = sorted({c.tool for c in project_cells})
        adapters = [adapter_factory(name) for name in project_tools]
        adapter_by_name = {a.name: a for a in adapters}

        try:
            prepared = prepare(entry, cache_root)
        # Broad by intent: prepare_project raises PrepareError, but a failure of ANY
        # kind must still emit records for the project's cells, never abort the suite.
        except Exception as exc:
            for cell in project_cells:
                src = getattr(adapter_by_name.get(cell.tool), "install_source", "unknown")
                results.append(
                    _excluded_record(
                        cell,
                        None,
                        entry,
                        src,
                        f"prepare failed: {exc}",
                        calibration,
                    )
                )
            continue

        report = preflight(prepared, adapters, timeout=timeout)
        if not report.ready:
            detail = "; ".join(
                f"{t.tool}: {t.result_class.value} {t.error_detail or ''}".strip()
                for t in report.tools
                if not (t.result_class.is_measured_success and t.scope_ok)
            )
            for cell in project_cells:
                src = adapter_by_name[cell.tool].install_source
                results.append(
                    _excluded_record(
                        cell,
                        prepared,
                        entry,
                        src,
                        detail or "preflight not ready",
                        calibration,
                    )
                )
            continue

        over_by_tool = {t.tool: t.over_reports for t in report.tools}
        ch = config_hash(
            entry.src_roots,
            entry.effective_excludes(),
            entry.python_version,
            entry.python_platform,
        )
        config = _suite_config(prepared, cores)
        for cell in project_cells:
            adapter = adapter_by_name[cell.tool]
            manifest = RunManifest(
                project_sha=prepared.sha,
                lock_hash=prepared.lock_hash,
                config_hash=ch,
                canonical_files=prepared.canonical_files,
                canonical_loc=prepared.canonical_loc,
                canonical_code_loc=prepared.canonical_code_loc,
                tool_install_source=adapter.install_source,
                over_reports=over_by_tool.get(cell.tool, False),
            )
            results.append(
                run_one(
                    adapter,
                    project=project,
                    config=config,
                    thread_mode=cell.thread_mode,
                    warmup=warmup,
                    runs=runs,
                    timeout=timeout,
                    mem_runs=mem_runs,
                    measure_enabled=measure_enabled,
                    calibration=calibration,
                    manifest=manifest,
                )
            )

    return ResultsEnvelope(suite_version=suite_version, generated_at=generated_at, runs=results)
