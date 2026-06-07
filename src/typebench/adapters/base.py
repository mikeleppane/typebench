"""Adapter protocol — the only checker-specific surface (spec §4).

The protocol is pinned to its *final-ish* shape now so real adapters (Plan 2)
add behavior, not breaking signatures: `command` already returns (argv, env)
for vars like TY_MAX_PARALLELISM, and `install` / `parallelism_cap` /
`prepare_command` exist as the stable surface. The spine only calls
command/parse/classify/clear_cache/prepare_command/version; install and
parallelism_cap are no-ops on the stub and are wired in Plans 2/4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from typebench.wrapper import classify_default

if TYPE_CHECKING:
    from typebench.models import ResultClass, ThreadMode
    from typebench.wrapper import RawRun


@dataclass(frozen=True)
class ParallelismCap:
    """How a tool is constrained in the 1-core track (spec §5.3). `hard_cap` is
    the honesty flag: True = a real worker cap, False = best-effort only."""

    mechanism: str
    hard_cap: bool


@runtime_checkable
class Adapter(Protocol):
    name: str

    def version(self) -> str:
        """Resolved checker version string."""
        ...

    def install(self) -> str:
        """Resolve + verify the expected distribution; return the resolved
        version (spec §4). Plan 2 implements real verification; stub no-ops."""
        ...

    def command(self, project: str, thread_mode: ThreadMode) -> tuple[list[str], dict[str, str]]:
        """(argv, extra_env) that runs the checker on `project` under
        `thread_mode`. `extra_env` carries vars like TY_MAX_PARALLELISM (§5.3),
        empty when none. Plan 2 adds the normalized-config argument (§6)."""
        ...

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        """Declare how this tool is constrained in the 1-core track (§5.3)."""
        ...

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        """Return (diagnostics, files) from the checker's output."""
        ...

    def classify(self, raw: RawRun) -> ResultClass:
        """Map a RawRun to the §7 taxonomy. Override per tool in Plan 2."""
        ...

    def clear_cache(self, project: str) -> None:
        """Remove any checker cache so every run is cold (§5.2)."""
        ...

    def prepare_command(self, project: str) -> str | None:
        """A hyperfine-safe shell command that clears the checker cache before
        EVERY timed run (§5.2, §5.4), or None when the tool is stateless. Wired
        into `hyperfine --prepare` by the collector so warmups/runs stay cold."""
        ...


def default_classify(raw: RawRun) -> ResultClass:
    """Shared fallback so adapters can delegate to the generic map."""
    return classify_default(raw)
