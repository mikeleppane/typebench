"""Resource pass (spec §5.5) — peak cgroup memory + CPU-time under a transient
cgroup v2 scope, plus real cgroup OOM detection. Deliberately pydantic-free: the
in-scope wrapper runs as a child process and reuses the (also pydantic-free)
exit-code wrapper; importing pydantic here would add startup cost to every scoped
run. Stays stdlib-only + `typebench.wrapper`."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.wrapper import RawRun, run_command, universal_failure_prefix

if TYPE_CHECKING:
    from collections.abc import Callable

_CGROUP_ROOT = Path("/sys/fs/cgroup")
# core 0, uniform single-core floor (spec §5.3, Decision B). Applied by the
# collector as an argv prefix; referenced here only for documentation parity.
_AFFINITY_CORE = 0


@dataclass(frozen=True)
class CgroupSample:
    """One read of a scope's cgroup v2 accounting files (read before teardown)."""

    peak_bytes: int
    cpu_usage_usec: int
    cpu_user_usec: int
    cpu_system_usec: int
    oom_kill: int
    mem_stat: dict[str, int]


def _read_kv(path: Path) -> dict[str, int]:
    """Parse a `key value` cgroup file into a dict; missing file -> empty."""
    out: dict[str, int] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.partition(" ")
        value = value.strip()
        if sep and value.lstrip("-").isdigit():
            out[key] = int(value)
    return out


def _read_int(path: Path) -> int:
    return int(path.read_text().strip())


def read_cgroup_stats(cgroup_dir: Path) -> CgroupSample:
    """Read memory.peak / cpu.stat / memory.stat / memory.events from a cgroup v2
    directory. Pure (no process spawned) so it is unit-testable against fixture
    files. Callers MUST invoke this while the scope still exists (§5.5)."""
    cpu = _read_kv(cgroup_dir / "cpu.stat")
    events = _read_kv(cgroup_dir / "memory.events")
    return CgroupSample(
        peak_bytes=_read_int(cgroup_dir / "memory.peak"),
        cpu_usage_usec=cpu.get("usage_usec", 0),
        cpu_user_usec=cpu.get("user_usec", 0),
        cpu_system_usec=cpu.get("system_usec", 0),
        oom_kill=events.get("oom_kill", 0),
        mem_stat=_read_kv(cgroup_dir / "memory.stat"),
    )


def _self_cgroup_dir() -> Path:
    """The cgroup v2 directory of the current process (the `0::/path` line of
    /proc/self/cgroup, mounted under /sys/fs/cgroup)."""
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            rel = line.split("::", 1)[1].strip().lstrip("/")
            return _CGROUP_ROOT / rel
    raise OSError("no cgroup v2 (0::) entry in /proc/self/cgroup")


def capable(runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> bool:
    """True iff a transient user scope with cgroup v2 memory+cpu ACCOUNTING is
    usable here. Probes the EXACT properties the real resource pass sets
    (`MemoryAccounting`/`CPUAccounting`), not a bare `true` scope — on a host where
    the cpu controller is not delegated to user scopes, `-p CPUAccounting=yes`
    fails, so we fall back to timing-only (§15) instead of silently recording
    `cpu_time_s=0`. `taskset` is deliberately NOT checked here: it gates only the
    ONE_CORE affinity floor (`collector._taskset_available`), never all-cores
    memory measurement, so a box without `taskset` still measures memory."""
    if shutil.which("systemd-run") is None:
        return False
    if not (_CGROUP_ROOT / "cgroup.controllers").exists():
        return False
    try:
        proc = runner(
            [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "-p",
                "MemoryAccounting=yes",
                "-p",
                "CPUAccounting=yes",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _sample_to_dict(sample: CgroupSample) -> dict[str, object]:
    return {
        "peak_bytes": sample.peak_bytes,
        "cpu_usage_usec": sample.cpu_usage_usec,
        "cpu_user_usec": sample.cpu_user_usec,
        "cpu_system_usec": sample.cpu_system_usec,
        "oom_kill": sample.oom_kill,
        "mem_stat": sample.mem_stat,
    }


class MeasureError(RuntimeError):
    """Raised when the scoped resource pass produced no usable payload."""


@dataclass(frozen=True)
class MemorySummary:
    """Aggregated peak memory plus the median-peak run's memory.stat snapshot."""

    runs: int
    peak_bytes_min: int
    peak_bytes_median: int
    peak_bytes_max: int
    memory_stat: dict[str, int]


@dataclass(frozen=True)
class ResourceResult:
    """Outcome of the resource pass across repeated scoped runs."""

    raw: RawRun
    memory: MemorySummary | None
    cpu_time_s: float | None
    oom: bool


def _raw_from_payload(payload: dict[str, object], *, oom_killed: bool) -> RawRun:
    return RawRun(
        exit_code=_coerce_int(payload["exit_code"]),
        signal=payload["signal"] if isinstance(payload["signal"], int) else None,
        timed_out=bool(payload["timed_out"]),
        oom=bool(payload["oom"]) or oom_killed,
        stdout=str(payload["stdout"]),
        stderr=str(payload["stderr"]),
        env_error=bool(payload["env_error"]),
    )


def _coerce_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError(f"expected integer payload value, got {type(value).__name__}")


def _median_int(values: list[int]) -> int:
    return int(statistics.median(values))


def _memory_stat_from_payload(cgroup_payload: dict[object, object]) -> dict[str, int]:
    raw_mem_stat = cgroup_payload.get("mem_stat")
    if not isinstance(raw_mem_stat, dict):
        return {}
    return {str(key): _coerce_int(value) for key, value in raw_mem_stat.items()}


def scoped_probe(
    argv: list[str],
    extra_env: dict[str, str],
    timeout: float,
    repeats: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    prepare: Callable[[], None] | None = None,
) -> ResourceResult:
    """Run argv in repeated transient scopes and aggregate resource accounting.

    Per-repeat scope failures are skipped. If every repeat fails before producing
    a payload, the caller gets MeasureError and can fall back to a plain probe.
    """
    if repeats < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats}")

    run_env = {**os.environ, **extra_env}
    setenv_args = [
        arg for key, value in extra_env.items() for arg in ("--setenv", f"{key}={value}")
    ]

    raws: list[RawRun] = []
    peaks: list[int] = []
    cpu_usages: list[int] = []
    mem_stats: list[dict[str, int]] = []
    oom = False

    for _ in range(repeats):
        try:
            if prepare is not None:
                prepare()
            with tempfile.TemporaryDirectory() as tmp:
                out_path = Path(tmp) / "payload.json"
                cmd = [
                    "systemd-run",
                    "--user",
                    "--scope",
                    "--quiet",
                    "-p",
                    "MemoryAccounting=yes",
                    "-p",
                    "CPUAccounting=yes",
                    *setenv_args,
                    "--",
                    sys.executable,
                    "-m",
                    "typebench.measure",
                    "--out",
                    str(out_path),
                    "--timeout",
                    str(timeout),
                    "--",
                    *argv,
                ]
                proc = runner(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=run_env,
                    timeout=timeout + 120,
                )
                if proc.returncode != 0:
                    continue
                payload: dict[str, object] = json.loads(out_path.read_text())
        except (OSError, ValueError, subprocess.SubprocessError):
            continue

        try:
            cgroup = payload.get("cgroup")
            oom_killed = isinstance(cgroup, dict) and _coerce_int(cgroup["oom_kill"]) > 0
            raw = _raw_from_payload(payload, oom_killed=oom_killed)
            # Parse the memory sample INSIDE the same guard. A cgroup dict missing
            # peak_bytes/cpu_usage_usec (truncated payload, schema drift, a custom
            # runner) must skip this repeat, never raise out of scoped_probe: the
            # KeyError from cgroup["peak_bytes"] is NOT in the collector's fallback
            # except set, so an escape here would drop the record (§12, Decision J).
            sample: tuple[int, int, dict[str, int]] | None = None
            if isinstance(cgroup, dict):
                sample = (
                    _coerce_int(cgroup["peak_bytes"]),
                    _coerce_int(cgroup["cpu_usage_usec"]),
                    _memory_stat_from_payload(cgroup),
                )
        except (KeyError, ValueError):
            continue
        raws.append(raw)
        # peaks/cpu_usages/mem_stats stay parallel: all three append together (or
        # none do), so representative_index can index mem_stats by a peaks position.
        if sample is not None:
            peak_bytes, cpu_usage, mem_stat = sample
            peaks.append(peak_bytes)
            cpu_usages.append(cpu_usage)
            mem_stats.append(mem_stat)
            oom = oom or oom_killed

    if not raws:
        raise MeasureError("scoped resource pass produced no usable payload")

    authoritative = raws[0]
    for raw in raws:
        if universal_failure_prefix(raw) is not None:
            authoritative = raw
            break

    if not peaks:
        return ResourceResult(raw=authoritative, memory=None, cpu_time_s=None, oom=oom)

    median_peak = _median_int(peaks)
    representative_index = min(range(len(peaks)), key=lambda index: abs(peaks[index] - median_peak))
    memory = MemorySummary(
        runs=len(peaks),
        peak_bytes_min=min(peaks),
        peak_bytes_median=median_peak,
        peak_bytes_max=max(peaks),
        memory_stat=mem_stats[representative_index],
    )
    cpu_time_s = _median_int(cpu_usages) / 1_000_000
    return ResourceResult(raw=authoritative, memory=memory, cpu_time_s=cpu_time_s, oom=oom)


def main(raw_args: list[str] | None = None) -> int:
    """In-scope wrapper. Run as `systemd-run --user --scope -- python -m
    typebench.measure --out FILE --timeout S -- <argv>`. Runs the checker to
    completion, then — WHILE STILL INSIDE THE SCOPE (§5.5 read-before-teardown) —
    reads its own cgroup and writes a JSON payload (outcome + cgroup sample) to
    --out. Always exits 0 so systemd-run sees success; the real outcome is in the
    payload. The checker output is captured by run_command (bounded diagnostics
    text), and this Python process's small footprint is a ~constant per-tool
    baseline charged to the scope (Decision F)."""
    parser = argparse.ArgumentParser(prog="typebench.measure")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    ns = parser.parse_args(raw_args)
    argv = ns.argv[1:] if ns.argv and ns.argv[0] == "--" else ns.argv

    # run_command buffers the checker's stdout/stderr in THIS process's memory,
    # which is inside the measured cgroup. For normal diagnostics (KBs) this is
    # negligible vs the checker's own analysis memory, but a diagnostics-flood run
    # would inflate memory.peak output-dependently (documented in MemoryStats; a
    # streaming variant is the fix if such a corpus entry is ever added).
    raw = run_command(argv, timeout=ns.timeout)
    try:
        sample = read_cgroup_stats(_self_cgroup_dir())
        cgroup: dict[str, object] | None = _sample_to_dict(sample)
    except (OSError, ValueError):
        # OSError: missing cgroup files / no 0:: entry. ValueError: a present-but-
        # non-integer memory.peak (_read_int). Either way the cgroup sample is
        # unreadable -> record cgroup=None but STILL write the payload, so the
        # checker's outcome for this repeat is never lost (read-before-teardown §5.5).
        cgroup = None

    payload = {
        "exit_code": raw.exit_code,
        "signal": raw.signal,
        "timed_out": raw.timed_out,
        "oom": raw.oom,
        "env_error": raw.env_error,
        "stdout": raw.stdout,
        "stderr": raw.stderr,
        "cgroup": cgroup,
    }
    Path(ns.out).write_text(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
