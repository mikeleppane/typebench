"""Collector — assembles one RunResult (spec §4, §8). Probe then time."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.env import detect_env
from typebench.models import FailurePhase, ResultClass, RunResult, ThreadMode
from typebench.timing import run_timing
from typebench.wrapper import run_command

if TYPE_CHECKING:
    from typebench.adapters.base import Adapter
    from typebench.normalized_config import NormalizedConfig


_AFFINITY_PREFIX = ["taskset", "-c", "0"]  # uniform single-core floor (spec §5.3)


def _taskset_available() -> bool:
    return shutil.which("taskset") is not None


def _apply_affinity(argv: list[str], thread_mode: ThreadMode) -> tuple[list[str], bool]:
    """Prepend the uniform single-core affinity prefix for the ONE_CORE track.
    Returns (argv, enforced). ALL_CORES is unconstrained by design (not pinned).
    enforced is True ONLY when ONE_CORE AND taskset is actually available — the
    honesty flag must never claim a pin we could not apply (§5.3, Decision D)."""
    if thread_mode is ThreadMode.ONE_CORE and _taskset_available():
        return ([*_AFFINITY_PREFIX, *argv], True)
    return (argv, False)


def run_single(
    adapter: Adapter,
    project: str,
    config: NormalizedConfig,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
) -> RunResult:
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

        # Phase 1: probe — one real run to classify and parse counts.
        raw = run_command(argv, timeout=timeout, env=extra_env)
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
            env=detect_env(),
        )
