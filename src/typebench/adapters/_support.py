from __future__ import annotations

from typing import TYPE_CHECKING

from typebench.contracts.taxonomy import ResultClass

if TYPE_CHECKING:
    from typebench.contracts.proc import ProcessHost


def probe_version(argv: list[str], *, host: ProcessHost) -> str:
    """No-raise `<tool> --version`: return stdout.strip() or stderr.strip() or
    "unknown". Launch failures return "unknown" so version probes never drop a record."""
    out = host.run(argv, timeout=10)
    if out.env_error or out.timed_out:
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
