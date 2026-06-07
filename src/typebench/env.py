"""Environment fingerprint (spec §9). Expanded with cgroup/lock data later."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from typebench.models import EnvFingerprint


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def detect_env() -> EnvFingerprint:
    return EnvFingerprint(
        os=platform.system(),
        kernel=platform.release(),
        cpu_model=_cpu_model(),
        core_count=os.cpu_count() or 1,
        python_version=platform.python_version(),
    )
