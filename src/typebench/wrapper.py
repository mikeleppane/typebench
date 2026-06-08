"""Exit-code wrapper (spec §5.1). Type checkers exit nonzero when they find
diagnostics — that is success, not failure. This module captures the real
outcome and maps it to the §7 taxonomy. It also exposes a CLI (Task 5) used
as hyperfine's command so hyperfine does not abort on diagnostics."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass

from typebench.taxonomy import ResultClass

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


def run_command(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> RawRun:
    """Run argv to completion, capturing the real outcome. Never raises: a
    nonzero exit, a timeout, a signal death, AND an environment error (missing
    binary / not executable) are all captured as a RawRun so the caller can
    record the right §7 class. On POSIX the command runs in a new session so a
    timeout kills its whole process tree, not just the direct child (benchmark
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
            start_new_session=os.name == "posix",
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
    with proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_tree(proc)
            stdout, stderr = proc.communicate()
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
# else = crash. Real per-tool exit maps arrive in Plan 2 (§7).
_EXIT_CODE_CLASSES: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
}


def universal_failure_prefix(raw: RawRun) -> ResultClass | None:
    """The env/oom/timeout/signal classification shared by EVERY tool, in §7
    precedence order. Returns the failure class when a universal condition
    applies, else None — the caller then applies its own exit-code logic. This
    is the single source of the prefix for both `classify_with_map` (tools with a
    clean exit map) and the overloaded-exit adapters (mypy 2, pyrefly 1) that
    need custom per-code logic afterwards."""
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
    """Universal §7 prefix then the tool's exit-code map; unknown codes fall to
    FAILED_CRASH. Tools with overloaded codes (mypy 2, pyrefly 1) instead call
    `universal_failure_prefix` directly and run their own exit-code logic."""
    prefix = universal_failure_prefix(raw)
    if prefix is not None:
        return prefix
    return exit_map.get(raw.exit_code, ResultClass.FAILED_CRASH)


def classify_default(raw: RawRun) -> ResultClass:
    """Generic classifier (stub + spine). Real per-tool maps arrive via
    `classify_with_map` in Plan 2; this is the {0: clean, 1: diagnostics} default."""
    return classify_with_map(raw, _EXIT_CODE_CLASSES)


def main(raw_args: list[str] | None = None) -> int:
    """CLI entrypoint used as hyperfine's command. Usage:

        python -m typebench.wrapper --timeout SECONDS -- <argv...>

    Exits 0 for measured-success (clean/diagnostics), 1 for any failure class.
    The real command's stdout/stderr are forwarded so output stays visible."""
    parser = argparse.ArgumentParser(prog="typebench.wrapper")
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
    # PLAN 2 TRAP — partially defused, residual blind spot documented.
    # SUCCESS PATH: all four tools (mypy/pyright/ty/pyrefly) use measured-success
    # codes ⊆ {0,1}, so this generic gate agrees with each adapter's probe
    # `classify` and no tool-specific code threading is needed.
    #
    # RESIDUAL BLIND SPOT (pyrefly): exit 1 is overloaded (diagnostics OR fatal
    # config/env). The probe phase uses the real Adapter.classify, disambiguates
    # it, and gates whether timing runs — so a *deterministically* broken run is
    # caught at probe and never timed. But a FLAKY fatal pyrefly exit-1 that
    # occurs only during a timed run is read by classify_default as DIAGNOSTICS →
    # measured-success → hyperfine silently times a broken run. Threading {0,1}
    # success codes would NOT fix this (exit 1 is in the success set yet still
    # ambiguous). The only real fix is adapter-aware timing classification, which
    # the wrapper CANNOT do without importing pydantic (violates
    # test_wrapper_import_does_not_pull_pydantic). Accepted residual risk for 2B.
    #
    # mypy (NO blind spot): overloaded exit 2 is OUTSIDE {0,1}, so a flaky
    # timed-run exit 2 → wrapper returns nonzero → hyperfine aborts → collector
    # records a failure (mislabeled crash-vs-env, but never silently timed).
    return 0 if classify_default(raw).is_measured_success else 1


if __name__ == "__main__":
    sys.exit(main())
