"""Exit-code wrapper (spec §5.1). Type checkers exit nonzero when they find
diagnostics — that is success, not failure. This module captures the real
outcome and maps it to the §7 taxonomy. It also exposes a CLI (Task 5) used
as hyperfine's command so hyperfine does not abort on diagnostics."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from typebench.models import ResultClass

# OOM-killer signal. A bare SIGKILL with no cgroup OOM flag is treated as an
# OOM heuristic until cgroup OOM detection lands (Plan 4 sets RawRun.oom).
_SIGKILL = 9


@dataclass(frozen=True)
class RawRun:
    exit_code: int
    signal: int | None
    timed_out: bool
    oom: bool
    stdout: str
    stderr: str
    env_error: bool = False


def run_command(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> RawRun:
    """Run argv to completion, capturing the real outcome. Never raises: a
    nonzero exit, a timeout, a signal death, AND an environment error (missing
    binary / not executable) are all captured as a RawRun so the caller can
    record the right §7 class. `env` is merged over the inherited environment
    (adapters inject e.g. TY_MAX_PARALLELISM)."""
    run_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
            check=False,  # nonzero exit is data here (diagnostics), not an error.
        )
    except subprocess.TimeoutExpired as exc:
        return RawRun(
            exit_code=-1,
            signal=None,
            timed_out=True,
            oom=False,
            stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
        )
    except OSError as exc:
        # Missing binary, not executable, etc. -> environment failure (§7).
        return RawRun(
            exit_code=-1,
            signal=None,
            timed_out=False,
            oom=False,
            stdout="",
            stderr=str(exc),
            env_error=True,
        )
    returncode = proc.returncode
    signal = -returncode if returncode < 0 else None
    return RawRun(
        exit_code=returncode,
        signal=signal,
        timed_out=False,
        oom=False,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


# Generic exit-code convention: 0 = clean, 1 = diagnostics found, anything
# else = crash. Real per-tool exit maps arrive in Plan 2 (§7).
_EXIT_CODE_CLASSES: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
}


def classify_default(raw: RawRun) -> ResultClass:
    """Generic classifier. Real per-tool exit maps arrive in Plan 2 (§7).

    Convention shared by the stub and most checkers: 0 = clean, 1 = diagnostics
    found, anything else / signal / timeout / oom / env-error = failure.
    Order matters: env-error and explicit OOM are checked before the generic
    signal/exit-code fallbacks."""
    if raw.env_error:
        return ResultClass.FAILED_ENV
    if raw.oom or raw.signal == _SIGKILL:
        # Explicit cgroup OOM flag (Plan 4), or a bare SIGKILL treated as the
        # OOM heuristic until cgroup OOM detection lands.
        return ResultClass.FAILED_OOM
    if raw.timed_out:
        return ResultClass.FAILED_TIMEOUT
    if raw.signal is not None:
        return ResultClass.FAILED_CRASH
    return _EXIT_CODE_CLASSES.get(raw.exit_code, ResultClass.FAILED_CRASH)
