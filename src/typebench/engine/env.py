"""Environment fingerprint (spec §9). Expanded with cgroup/lock data later."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from typebench.contracts.models import EnvFingerprint

_MEMINFO = Path("/proc/meminfo")
_CGROUP_CONTROLLERS = Path("/sys/fs/cgroup/cgroup.controllers")


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def _cmd_version(argv: list[str]) -> str | None:
    """First line of `<tool> --version`, or None if the tool is missing. No-raise:
    detect_env runs during RunResult assembly and must never crash a record."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip() or out.stderr.strip()
    return text.splitlines()[0] if text else None


def _mem_total_bytes() -> int | None:
    if not _MEMINFO.exists():
        return None
    for line in _MEMINFO.read_text().splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1]) * 1024  # kB -> bytes
    return None


def _cgroup_v2() -> bool:
    return _CGROUP_CONTROLLERS.exists()


def detect_env() -> EnvFingerprint:
    return EnvFingerprint(
        os=platform.system(),
        kernel=platform.release(),
        cpu_model=_cpu_model(),
        core_count=os.cpu_count() or 1,
        python_version=platform.python_version(),
        node_version=_cmd_version(["node", "--version"]),
        npm_version=_cmd_version(["npm", "--version"]),
        uv_version=_cmd_version(["uv", "--version"]),
        mem_total_bytes=_mem_total_bytes(),
        cgroup_v2=_cgroup_v2(),
    )
