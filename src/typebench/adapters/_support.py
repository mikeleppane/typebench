from __future__ import annotations

import subprocess
from collections.abc import Callable

from typebench.contracts.taxonomy import ResultClass

type VersionRunner = Callable[..., subprocess.CompletedProcess[str]]


def probe_version(argv: list[str], *, runner: VersionRunner) -> str:
    """No-raise `<tool> --version`: return stdout.strip() or stderr.strip() or
    "unknown". OSError returns "unknown" so version probes never drop a record."""
    try:
        out = runner(argv, capture_output=True, text=True, check=False)
    except OSError:
        return "unknown"
    return out.stdout.strip() or out.stderr.strip() or "unknown"


def confirm_clean(files: int | None, *, tolerate_unknown: bool) -> ResultClass:
    """Exit-0 CLEAN honesty gate. Positive files are CLEAN; zero files are
    FAILED_ENV; unknown files are FAILED_ENV unless best-effort counts are tolerated."""
    if files:
        return ResultClass.CLEAN
    if files is None and tolerate_unknown:
        return ResultClass.CLEAN
    return ResultClass.FAILED_ENV
