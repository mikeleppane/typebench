"""Process boundary vocabulary for un-timed helper subprocesses.

This module is stdlib-only so it is legal in the contracts hub and cheap for
the measured wrapper path to import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class RawRun:
    """Captured outcome of a subprocess.

    Launch failures are data (`env_error=True`), not exceptions, so callers can
    record failed{env} rows instead of dropping benchmark cells.
    """

    exit_code: int
    signal: int | None
    timed_out: bool
    oom: bool
    stdout: str
    stderr: str
    env_error: bool = False


@runtime_checkable
class ProcessHost(Protocol):
    """Every un-timed subprocess plus PATH lookup.

    Implementations never raise for launch failure or deadlines: launch failure
    becomes `env_error=True`, and deadline expiry becomes `timed_out=True`.
    Timed benchmark runs stay on hyperfine, outside this abstraction.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> RawRun: ...

    def which(self, name: str) -> str | None: ...
