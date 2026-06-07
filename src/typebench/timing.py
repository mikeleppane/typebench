"""Timing pass via hyperfine (spec §5.4). hyperfine handles warmup, repeated
runs, and statistics; we hand it the wrapper (Task 5) so diagnostics exits do
not abort the run, and `--prepare` clears the checker cache before each run."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from typebench.models import TimingStats


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
        "typebench.wrapper",
        "--timeout",
        str(timeout),
        "--",
        *argv,
    ]
    return shlex.join(parts)


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
    every timed run, keeping each run cold (§5.2); None means nothing to prepare
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
        subprocess.run(cmd, check=True, capture_output=True, text=True, env=run_env)
        data: dict[str, Any] = json.loads(json_path.read_text())
        return parse_hyperfine_json(data)
