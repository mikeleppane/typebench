"""Production host for un-timed helper subprocesses."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from typebench.engine.wrapper import run_command

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from typebench.contracts.proc import RawRun


class SystemProcessHost:
    """ProcessHost backed by the local OS."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> RawRun:
        return run_command(list(argv), timeout=timeout, env=dict(env) if env else None, cwd=cwd)

    def which(self, name: str) -> str | None:
        return shutil.which(name)


SYSTEM_HOST = SystemProcessHost()
