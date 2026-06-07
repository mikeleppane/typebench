"""Collector — assembles one RunResult (spec §4, §8). Probe then time."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from typebench.env import detect_env
from typebench.models import RunResult, ThreadMode
from typebench.timing import run_timing
from typebench.wrapper import run_command

if TYPE_CHECKING:
    from typebench.adapters.base import Adapter


def run_single(
    adapter: Adapter,
    project: str,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
) -> RunResult:
    adapter.clear_cache(project)
    argv, extra_env = adapter.command(project, thread_mode)

    # Phase 1: probe — one real run to classify and parse counts.
    raw = run_command(argv, timeout=timeout, env=extra_env)
    result_class = adapter.classify(raw)
    diagnostics = files = None
    if result_class.is_measured_success:
        diagnostics, files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)

    # Phase 2: time — only for measured-success, only if hyperfine present.
    # prepare_command clears the checker cache before EVERY timed run (§5.2);
    # None for stateless tools like the stub.
    timing = None
    if result_class.is_measured_success and shutil.which("hyperfine"):
        timing = run_timing(
            argv,
            prepare_cmd=adapter.prepare_command(project),
            warmup=warmup,
            runs=runs,
            timeout=timeout,
            extra_env=extra_env,
        )

    error_detail = None
    if not result_class.is_measured_success:
        error_detail = raw.stderr.strip()[-500:] or None

    return RunResult(
        tool=adapter.name,
        tool_version=adapter.version(),
        project=project,
        thread_mode=thread_mode,
        # Plan 1 applies no CPU affinity/cap -> never claim an unenforced mode (§5.3).
        thread_mode_enforced=False,
        result_class=result_class,
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
