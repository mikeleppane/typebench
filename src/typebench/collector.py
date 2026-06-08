"""Collector — assembles one RunResult (spec §4, §8). Probe then time."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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


# Module seams (overridable in tests + by capability):
_resource_capable = measure.capable
_scoped_probe = measure.scoped_probe

_AFFINITY_PREFIX = ["taskset", "-c", "0"]  # uniform single-core floor (spec §5.3)


def _taskset_available() -> bool:
    """taskset present AND core 0 is in this process's CPU affinity mask. The mask
    check is load-bearing: under a restrictive cpuset (containers, some CI runners)
    core 0 may be disallowed, so `taskset -c 0 <checker>` would EXIT 1 *before the
    checker runs* — and exit 1 reads as diagnostics/measured-success, recording a
    bogus fast timing AND a false thread_mode_enforced for a command that never ran.
    Only claim the pin we can actually apply (§5.3, Decision D)."""
    if shutil.which("taskset") is None:
        return False
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is None:  # non-Linux without the affinity API (taskset is Linux-only anyway)
        return False
    return 0 in getaffinity(0)


def _apply_affinity(argv: list[str], thread_mode: ThreadMode) -> tuple[list[str], bool]:
    """Prepend the uniform single-core affinity prefix for the ONE_CORE track.
    Returns (argv, enforced). ALL_CORES is unconstrained by design (not pinned).
    enforced is True ONLY when ONE_CORE AND taskset is actually available — the
    honesty flag must never claim a pin we could not apply (§5.3, Decision D)."""
    if thread_mode is ThreadMode.ONE_CORE and _taskset_available():
        return ([*_AFFINITY_PREFIX, *argv], True)
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
) -> RunResult:
    if mem_runs < 1:
        raise ValueError(f"mem_runs must be >= 1, got {mem_runs}")
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
            )

        # Apply the uniform 1-core affinity prefix (ONE_CORE only) BEFORE any run,
        # so probe + resource + timing all share the same pinned command (§5.3).
        argv, thread_enforced = _apply_affinity(argv, thread_mode)
        cap = adapter.parallelism_cap(thread_mode)
        # The adapter mechanism strings bake in "cpu-affinity" (Plan 4's floor), so
        # record the cap ONLY when affinity actually ran. On ONE_CORE without
        # taskset (mac/dev), or on ALL_CORES, record neither — never claim a pin we
        # did not apply (§5.3 honesty, Decision A).
        record_cap = thread_mode is ThreadMode.ONE_CORE and thread_enforced
        hard_cap = cap.hard_cap if record_cap else None
        cap_mechanism = cap.mechanism if record_cap else None

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
        )
