"""Collector — assembles one RunResult (spec §4, §8). Probe then time."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from typebench import measure
from typebench.env import detect_env
from typebench.models import FailurePhase, MemoryStats, ResultClass, RunResult, ThreadMode
from typebench.timing import run_timing
from typebench.wrapper import run_command

if TYPE_CHECKING:
    from typebench.adapters.base import Adapter
    from typebench.models import CalibrationStats
    from typebench.normalized_config import NormalizedConfig


@dataclass(frozen=True)
class RunManifest:
    """Per-cell reproducibility data the collector stamps onto a RunResult (spec §9).

    Built by the suite orchestrator and corpus-mode `typebench run`; None in manual
    mode. Carries scalars only; frozen dep contents stay in the committed lockfile.
    """

    project_sha: str | None = None
    lock_hash: str | None = None
    config_hash: str | None = None
    canonical_files: int | None = None
    canonical_loc: int | None = None
    canonical_code_loc: int | None = None
    tool_install_source: str | None = None
    over_reports: bool | None = None


# Module seams (overridable in tests + by capability):
_resource_capable = measure.capable
_scoped_probe = measure.scoped_probe


def _affinity_spec(cores: int) -> str:
    """taskset -c cpu-list for the CONSTRAINED track: '0' for a single core, else
    '0-(N-1)' to pin to the first N cores (spec §5.3)."""
    return "0" if cores <= 1 else f"0-{cores - 1}"


def _taskset_available(cores: int) -> bool:
    """taskset present AND cores 0..N-1 are ALL in this process's CPU affinity mask.
    The mask check is load-bearing: under a restrictive cpuset (containers, some CI
    runners) or on a box with fewer than N usable cores, `taskset -c 0-(N-1) <checker>`
    would EXIT 1 *before the checker runs* — and exit 1 reads as diagnostics/measured-
    success, recording a bogus fast timing AND a false thread_mode_enforced for a
    command that never ran. Only claim the pin we can actually apply (§5.3, Decision D)."""
    if cores < 1 or shutil.which("taskset") is None:
        return False
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is None:  # non-Linux without the affinity API (taskset is Linux-only anyway)
        return False
    mask = getaffinity(0)
    return all(core in mask for core in range(cores))


def _apply_affinity(argv: list[str], thread_mode: ThreadMode, cores: int) -> tuple[list[str], bool]:
    """Prepend the taskset affinity prefix for the CONSTRAINED track, pinning to the
    first `cores` cores. Returns (argv, enforced). ALL_CORES is unconstrained by design
    (not pinned). enforced is True ONLY when CONSTRAINED AND all N cores are actually
    pinnable — the honesty flag must never claim a pin we could not apply (§5.3,
    Decision D)."""
    if thread_mode is ThreadMode.CONSTRAINED and _taskset_available(cores):
        return (["taskset", "-c", _affinity_spec(cores), *argv], True)
    return (argv, False)


def run_single(  # noqa: PLR0913, PLR0915 — distinct orchestration knobs threaded from the CLI; the probe→time→assemble phases intentionally share ONE workdir scope (§6: workdir must outlive every timed run), so they stay in one function by design
    adapter: Adapter,
    project: str,
    config: NormalizedConfig,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
    mem_runs: int = 3,
    measure_enabled: bool = True,
    calibration: CalibrationStats | None = None,
    manifest: RunManifest | None = None,
) -> RunResult:
    if mem_runs < 1:
        raise ValueError(f"mem_runs must be >= 1, got {mem_runs}")
    # Lock-manifest stamp (spec §9). loc_denominator records which throughput
    # denominator the headline kLOC/s should use: "code" when tokei produced a
    # reconciled code-LOC, else "physical". None when no canonical denominator
    # is known at all (manual run without a corpus project).
    man = manifest or RunManifest()
    loc_denominator = (
        None
        if man.canonical_files is None
        else "code"
        if man.canonical_code_loc is not None
        else "physical"
    )
    adapter.clear_cache(project)
    # Run-scoped workdir for any adapter-generated tool config; it must outlive
    # both the probe and every timed run, so it wraps the whole body (§6). The
    # RunResult is built INSIDE the `with` so the dir survives every timed run.
    with tempfile.TemporaryDirectory(prefix="typebench-") as tmp:
        workdir = Path(tmp)
        try:
            argv, extra_env = adapter.command(project, config, thread_mode, workdir)
        except (OSError, ValueError) as exc:
            # Building the command can touch disk / do path math (e.g. writing a
            # generated tool config, relpath across drives). A failure here is a
            # setup/env problem, NOT a checker result — record failed{env} so the
            # record is never dropped (spec §12; "never drop a record"). No process
            # ran, so real_exit_code is the -1 sentinel.
            return RunResult(
                tool=adapter.name,
                tool_version=adapter.version(),
                project=project,
                thread_mode=thread_mode,
                thread_mode_enforced=False,
                result_class=ResultClass.FAILED_ENV,
                failure_phase=FailurePhase.PROBE,
                real_exit_code=-1,
                error_detail=f"command construction failed: {exc}".strip()[-500:],
                env=detect_env(),
                project_sha=man.project_sha,
                lock_hash=man.lock_hash,
                config_hash=man.config_hash,
                tool_install_source=man.tool_install_source,
                canonical_files=man.canonical_files,
                canonical_loc=man.canonical_loc,
                canonical_code_loc=man.canonical_code_loc,
                loc_denominator=loc_denominator,
                over_reports=man.over_reports,
            )

        # Apply the N-core affinity prefix (CONSTRAINED only) BEFORE any run, so
        # probe + resource + timing all share the same pinned command (§5.3).
        argv, thread_enforced = _apply_affinity(argv, thread_mode, config.cores)
        cap = adapter.parallelism_cap(thread_mode, config.cores)
        # The adapter mechanism strings bake in "cpu-affinity" (Plan 4's floor), so
        # record the cap + pinned core count ONLY when affinity actually ran. On
        # CONSTRAINED without taskset (mac/dev) or on ALL_CORES, record neither —
        # never claim a pin we did not apply (§5.3 honesty, Decision A).
        record_cap = thread_mode is ThreadMode.CONSTRAINED and thread_enforced
        hard_cap = cap.hard_cap if record_cap else None
        cap_mechanism = cap.mechanism if record_cap else None
        cores = config.cores if record_cap else None

        # Phase 1: probe. When measurement is available, the probe runs UNDER a
        # transient cgroup scope (M COLD repeats) so it yields peak memory +
        # cpu-time + real OOM in one pass (§5.5); the authoritative repeat drives
        # classify+parse and a generic failure on any repeat is surfaced
        # (Decision I). A TOTAL harness failure (MeasureError / OSError / timeout /
        # bad JSON) falls back to a plain run_command so the record is NEVER dropped
        # (Decision J). The prepare callback clears the checker cache before each
        # cold repeat (§5.2).
        resource: measure.ResourceResult | None = None
        if measure_enabled and _resource_capable():
            try:
                resource = _scoped_probe(
                    argv,
                    extra_env=extra_env,
                    timeout=timeout,
                    repeats=mem_runs,
                    prepare=lambda: adapter.clear_cache(project),
                )
            except (
                measure.MeasureError,
                OSError,
                ValueError,
                KeyError,
                subprocess.SubprocessError,
            ):
                # Defense-in-depth: scoped_probe is built not to escape a malformed
                # payload, but ANY resource-pass failure must fall back to a plain
                # probe so a record is never dropped (§12, Decision J).
                resource = None
        if resource is not None:
            raw = resource.raw
            memory_summary = resource.memory
            cpu_time_s = resource.cpu_time_s
        else:
            raw = run_command(argv, timeout=timeout, env=extra_env)
            memory_summary = None
            cpu_time_s = None
        result_class = adapter.classify(raw)
        failure_phase = None if result_class.is_measured_success else FailurePhase.PROBE
        diagnostics = files = None
        if result_class.is_measured_success:
            diagnostics, files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)

        # Phase 2: time — only for measured-success, only if hyperfine present.
        # prepare_command clears the checker cache before EVERY timed run (§5.2);
        # None for stateless tools like the stub.
        timing = None
        timing_error: str | None = None
        if result_class.is_measured_success and shutil.which("hyperfine"):
            try:
                timing = run_timing(
                    argv,
                    prepare_cmd=adapter.prepare_command(project),
                    warmup=warmup,
                    runs=runs,
                    timeout=timeout,
                    extra_env=extra_env,
                )
            except subprocess.CalledProcessError as exc:
                # The probe was measured-success but a TIMED run failed under
                # hyperfine (flaky crash/oom/timeout). Spec §5.1/§12: record a
                # failure, never crash or drop the record. Precise reclassification
                # of the timing-phase failure is deferred (Plan 2/4); FAILED_CRASH
                # is the honest floor. failure_phase=TIMING marks that real_exit_code
                # is the *successful probe's*, so the record cannot be misread as a
                # clean command with a failed result.
                result_class = ResultClass.FAILED_CRASH
                failure_phase = FailurePhase.TIMING
                timing = None
                diagnostics = files = None
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                timing_error = stderr.strip()[-500:] or "timing run failed under hyperfine"
            except (OSError, ValueError, KeyError) as exc:
                # hyperfine emitted no/garbled JSON, or its export file vanished
                # (a shutil.which TOCTOU): a HARNESS failure, not a checker result.
                # Record failed{env} so the record is never dropped (spec §12).
                result_class = ResultClass.FAILED_ENV
                failure_phase = FailurePhase.TIMING
                timing = None
                diagnostics = files = None
                timing_error = f"timing harness error: {exc}".strip()[-500:]

        memory = (
            MemoryStats(
                runs=memory_summary.runs,
                peak_bytes_min=memory_summary.peak_bytes_min,
                peak_bytes_median=memory_summary.peak_bytes_median,
                peak_bytes_max=memory_summary.peak_bytes_max,
                memory_stat=memory_summary.memory_stat,
            )
            if memory_summary is not None
            else None
        )
        # Parallel efficiency = CPU-time / wall (§8). NOTE the two numbers come from
        # DIFFERENT passes: cpu_time_s is the median over the COLD scoped resource
        # repeats (under the cgroup), timing.median_s is the WARM hyperfine wall
        # median (not scoped). The ratio is a robust ~parallelism indicator (≈1 for
        # single-threaded, >1 for parallel tools) but is not a within-run figure;
        # the Plan 5 renderer must interpret it as cross-pass (cold-cpu / warm-wall).
        parallel_efficiency = (
            cpu_time_s / timing.median_s
            if cpu_time_s is not None and timing is not None and timing.median_s > 0
            else None
        )

        error_detail = None
        if not result_class.is_measured_success:
            error_detail = timing_error or (raw.stderr.strip()[-500:] or None)

        return RunResult(
            tool=adapter.name,
            tool_version=adapter.version(),
            project=project,
            thread_mode=thread_mode,
            thread_mode_enforced=thread_enforced,
            cores=cores,
            hard_cap=hard_cap,
            cap_mechanism=cap_mechanism,
            result_class=result_class,
            failure_phase=failure_phase,
            real_exit_code=raw.exit_code,
            signal=raw.signal,
            timed_out=raw.timed_out,
            oom=raw.oom,
            error_detail=error_detail,
            diagnostics=diagnostics,
            files=files,
            timing=timing,
            memory=memory,
            cpu_time_s=cpu_time_s,
            parallel_efficiency=parallel_efficiency,
            calibration=calibration,
            env=detect_env(),
            project_sha=man.project_sha,
            lock_hash=man.lock_hash,
            config_hash=man.config_hash,
            tool_install_source=man.tool_install_source,
            canonical_files=man.canonical_files,
            canonical_loc=man.canonical_loc,
            canonical_code_loc=man.canonical_code_loc,
            loc_denominator=loc_denominator,
            over_reports=man.over_reports,
        )
