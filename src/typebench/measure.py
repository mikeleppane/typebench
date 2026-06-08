"""Resource pass (spec §5.5) — peak cgroup memory + CPU-time under a transient
cgroup v2 scope, plus real cgroup OOM detection. Deliberately pydantic-free: the
in-scope wrapper runs as a child process and reuses the (also pydantic-free)
exit-code wrapper; importing pydantic here would add startup cost to every scoped
run. Stays stdlib-only + `typebench.wrapper`."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
