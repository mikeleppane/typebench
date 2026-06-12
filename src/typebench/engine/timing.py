"""Timing pass via hyperfine.

hyperfine handles warmup, repeated runs, and statistics; we hand it the wrapper
so diagnostics exits do not abort the run, and `--prepare` clears the checker
cache before each run.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Final, Protocol, cast

from typebench.contracts.models import TimingStats

# `--prepare` clears caches before every hyperfine iteration; budget a few
# seconds for large cache directories so the outer cap does not false-positive.
_PREPARE_BUDGET_S: Final = 5.0
# hyperfine startup, JSON export, and process teardown sit outside the wrapped
# checker timeout, so keep this comfortably above ordinary scheduler noise.
_HYPERFINE_OVERHEAD_S: Final = 45.0


class _PopenFactory(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: dict[str, str] | None,
        start_new_session: bool,
    ) -> subprocess.Popen[str]: ...


_popen = cast("_PopenFactory", subprocess.Popen)


class TimingCommandError(RuntimeError):
    """hyperfine itself exited non-zero while running the timing pass."""

    def __init__(self, stderr: str) -> None:
        self.stderr = stderr
        super().__init__(stderr or "timing run failed under hyperfine")


def parse_hyperfine_json(data: dict[str, Any]) -> TimingStats:
    results = data.get("results") or []
    if not results:
        raise ValueError("hyperfine JSON has no results")
    r = results[0]
    times = list(r["times"])
    return TimingStats(
        runs=len(times),
        min_s=float(r["min"]),
        median_s=float(r["median"]),
        mean_s=float(r["mean"]),
        stddev_s=float(r.get("stddev") or 0.0),
        max_s=float(r["max"]),
        times_s=times,
    )


def _wrapped_command_string(argv: list[str], timeout: float) -> str:
    parts = [
        sys.executable,
        "-m",
        "typebench.engine.wrapper",
        "--timeout",
        str(timeout),
        "--",
        *argv,
    ]
    return shlex.join(parts)


def _outer_timeout(warmup: int, runs: int, per_run_timeout: float) -> float:
    """Derive a generous wall-clock cap for the hyperfine process itself."""
    iterations = warmup + runs
    return iterations * (per_run_timeout + _PREPARE_BUDGET_S) + _HYPERFINE_OVERHEAD_S


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def _run_hyperfine(cmd: list[str], env: dict[str, str] | None, timeout: float) -> None:
    proc = _popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=os.name == "posix",
    )

    with proc:
        try:
            _stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_tree(proc)
            try:
                _stdout, _stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()
            raise TimingCommandError(
                f"outer timeout: hyperfine exceeded {timeout:.1f}s wall-clock budget"
            ) from exc

        if proc.returncode != 0:
            raise TimingCommandError(stderr)


def run_timing(
    argv: list[str],
    prepare_cmd: str | None,
    warmup: int,
    runs: int,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> TimingStats:
    """Run the timing pass and return wall-time statistics.

    `argv` is the *real* checker invocation; it is wrapped so hyperfine sees a
    success exit for diagnostics. `prepare_cmd` (e.g. cache clear) runs before
    every timed run, keeping each run cold; None means nothing to prepare
    (stub has no cache). `extra_env` is set on the hyperfine process and inherited
    by the wrapped command (e.g. TY_MAX_PARALLELISM)."""
    run_env = {**os.environ, **extra_env} if extra_env else None
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "hyperfine.json"
        cmd = [
            "hyperfine",
            "--warmup",
            str(warmup),
            "--runs",
            str(runs),
            "--export-json",
            str(json_path),
        ]
        if prepare_cmd:
            cmd += ["--prepare", prepare_cmd]
        cmd.append(_wrapped_command_string(argv, timeout))
        _run_hyperfine(cmd, run_env, _outer_timeout(warmup, runs, timeout))
        data: dict[str, Any] = json.loads(json_path.read_text())
        return parse_hyperfine_json(data)
