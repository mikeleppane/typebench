"""Exit-code wrapper for checker subprocesses.

Type checkers exit nonzero when they find diagnostics; that is success, not
failure. This module captures the real outcome and maps it to the failure
taxonomy. It also exposes the CLI used as hyperfine's command so hyperfine does
not abort on diagnostics.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from contextlib import suppress
from typing import TYPE_CHECKING, Final

from typebench.contracts.proc import RawRun
from typebench.contracts.taxonomy import ResultClass

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# OOM-killer signal. A bare SIGKILL is the OOM heuristic on the fallback
# non-cgroup probe path. The cgroup-scoped resource pass sets RawRun.oom
# authoritatively from memory.events.oom_kill when available.
_SIGKILL = 9
# Bounds the post-kill pipe drain when a detached grandchild survives and keeps
# stdout/stderr open after the benchmarked process group was killed.
_DRAIN_TIMEOUT_S: Final = 10.0


def _close_pipes(proc: subprocess.Popen[str]) -> None:
    for pipe in (proc.stdout, proc.stderr):
        if pipe is None:
            continue
        with suppress(OSError):
            pipe.close()


def _kill_again(proc: subprocess.Popen[str]) -> None:
    with suppress(OSError):
        proc.kill()


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the child and any grandchildren. The child leads its own session
    (start_new_session), so killing its process group reaps the whole tree — a
    plain proc.kill() on timeout would orphan grandchildren that then steal CPU
    from later benchmark runs. Non-POSIX has no process groups, so kill the child."""
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), _SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def run_command(
    argv: list[str],
    timeout: float | None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> RawRun:
    """Run argv to completion, capturing the real outcome. Never raises: a
    nonzero exit, a timeout, a signal death, AND an environment error (missing
    binary / not executable) are all captured as a RawRun so the caller can
    record the right taxonomy class. On POSIX the command runs in a new session
    so a timeout kills its whole process tree, not just the direct child (benchmark
    isolation). `env` is merged over the inherited environment (adapters inject
    e.g. TY_MAX_PARALLELISM)."""
    run_env = {**os.environ, **env} if env else None
    try:
        # nonzero exit is data here (diagnostics), not an error -> no check.
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=run_env,
            cwd=cwd,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        # Missing binary, not executable, etc. -> environment failure.
        return RawRun(
            exit_code=-1,
            signal=None,
            timed_out=False,
            oom=False,
            stdout="",
            stderr=str(exc),
            env_error=True,
        )
    with proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=_DRAIN_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                _close_pipes(proc)
                _kill_again(proc)
                stdout, stderr = "", ""
            return RawRun(
                exit_code=-1,
                signal=None,
                timed_out=True,
                oom=False,
                stdout=stdout or "",
                stderr=stderr or "",
            )
        returncode = proc.returncode
        sig = -returncode if returncode < 0 else None
        return RawRun(
            exit_code=returncode,
            signal=sig,
            timed_out=False,
            oom=False,
            stdout=stdout,
            stderr=stderr,
        )


# Generic exit-code convention: 0 = clean, 1 = diagnostics found, anything
# else = crash. Per-tool exit maps override this default when a checker needs a
# different mapping.
_EXIT_CODE_CLASSES: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
}


def universal_failure_prefix(raw: RawRun) -> ResultClass | None:
    """Classify env/oom/timeout/signal failures before tool-specific exit logic.

    Returns the failure class when a universal condition applies, else None; the
    caller then applies its own exit-code logic. This is the single source of the
    prefix for both `classify_with_map` (tools with a clean exit map) and the
    overloaded-exit adapters (mypy 2, pyrefly 1) that need custom per-code logic
    afterwards.
    """
    if raw.env_error:
        return ResultClass.FAILED_ENV
    if raw.oom:
        return ResultClass.FAILED_OOM
    if raw.timed_out:
        return ResultClass.FAILED_TIMEOUT
    if raw.signal == _SIGKILL:
        return ResultClass.FAILED_OOM
    if raw.signal is not None:
        return ResultClass.FAILED_CRASH
    return None


def classify_with_map(raw: RawRun, exit_map: dict[int, ResultClass]) -> ResultClass:
    """Apply universal failures, then the tool's exit-code map.

    Unknown codes fall to FAILED_CRASH. Tools with overloaded codes (mypy 2,
    pyrefly 1) instead call `universal_failure_prefix` directly and run their
    own exit-code logic.
    """
    prefix = universal_failure_prefix(raw)
    if prefix is not None:
        return prefix
    return exit_map.get(raw.exit_code, ResultClass.FAILED_CRASH)


def classify_default(raw: RawRun) -> ResultClass:
    """Generic classifier for tools that use 0=clean and 1=diagnostics."""
    return classify_with_map(raw, _EXIT_CODE_CLASSES)


def main(raw_args: list[str] | None = None) -> int:
    """CLI entrypoint used as hyperfine's command. Usage:

        python -m typebench.engine.wrapper --timeout SECONDS -- <argv...>

    Exits 0 for measured-success (clean/diagnostics), 1 for any failure class.
    The real command's stdout/stderr are forwarded so output stays visible."""
    parser = argparse.ArgumentParser(prog="typebench.engine.wrapper")
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    ns = parser.parse_args(raw_args)

    argv = ns.argv
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        parser.error("no command given after --")

    raw = run_command(argv, timeout=ns.timeout)
    sys.stdout.write(raw.stdout)
    sys.stderr.write(raw.stderr)
    # Known limitation: partially defused, with one blind spot documented.
    # SUCCESS PATH: all four tools (mypy/pyright/ty/pyrefly) use measured-success
    # codes ⊆ {0,1}, so this generic gate agrees with each adapter's probe
    # `classify` and no tool-specific code threading is needed.
    #
    # BLIND SPOT (pyrefly): exit 1 is overloaded (diagnostics OR fatal
    # config/env). The probe phase uses the real Adapter.classify, disambiguates
    # it, and gates whether timing runs — so a *deterministically* broken run is
    # caught at probe and never timed. But a FLAKY fatal pyrefly exit-1 that
    # occurs only during a timed run is read by classify_default as DIAGNOSTICS →
    # measured-success → hyperfine silently times a broken run. Threading {0,1}
    # success codes would NOT fix this (exit 1 is in the success set yet still
    # ambiguous). The only real fix is adapter-aware timing classification, which
    # the wrapper CANNOT do without importing pydantic. Keeping pydantic off the
    # measured path is required because this wrapper runs inside every hyperfine
    # measurement.
    #
    # mypy (NO blind spot): overloaded exit 2 is OUTSIDE {0,1}, so a flaky
    # timed-run exit 2 → wrapper returns nonzero → hyperfine aborts → collector
    # records a failure (mislabeled crash-vs-env, but never silently timed).
    return 0 if classify_default(raw).is_measured_success else 1


if __name__ == "__main__":
    sys.exit(main())
