"""Adapter protocol: the only checker-specific surface.

The protocol is pinned to a broad shape so real adapters add behavior without
breaking signatures: `command` already returns (argv, env) for vars like
TY_MAX_PARALLELISM, and `install` / `parallelism_cap` / `prepare_command` exist
as the stable surface. The spine calls
command/parse/classify/clear_cache/prepare_command/version; install and
parallelism_cap are no-ops on the stub but implemented by real adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from typebench.engine.wrapper import classify_default

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.contracts.config import NormalizedConfig
    from typebench.contracts.identity import CheckerRuntime, CheckerSpec
    from typebench.contracts.models import ResultClass, ThreadMode
    from typebench.contracts.proc import RawRun


@dataclass(frozen=True)
class ParallelismCap:
    """How a tool is constrained in the constrained track. `hard_cap` is
    the honesty flag: True = a real worker cap, False = best-effort only."""

    mechanism: str
    hard_cap: bool


@dataclass(frozen=True)
class CheckerHandle:
    """A declared checker spec bundled with its adapter and optional runtime."""

    spec: CheckerSpec
    adapter: Adapter
    runtime: CheckerRuntime | None = None

    @property
    def tool(self) -> str:
        return self.spec.tool

    @property
    def checker_id(self) -> str:
        return self.runtime.checker_id if self.runtime is not None else self.spec.checker_id()

    @property
    def binary(self) -> str | None:
        return self.runtime.binary if self.runtime is not None else None

    @property
    def install_source(self) -> str:
        return (
            self.runtime.install_source if self.runtime is not None else self.adapter.install_source
        )


@runtime_checkable
class Adapter(Protocol):
    name: str
    install_source: str  # manifest: "PyPI wheel (mypyc)", "npm + Node", ...

    def version(self, binary: str | None = None) -> str:
        """Resolved checker version string. `binary` is the absolute path to the
        per-version venv binary (corpus.checkerenv); None probes the bare tool on
        PATH (manual/back-compat)."""
        ...

    def install(self) -> str:
        """Resolve + verify the expected distribution; return the resolved
        version. The stub no-ops because there is no distribution to verify."""
        ...

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
        binary: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """(argv, extra_env) running the checker on `project` under the
        normalized `config` and `thread_mode`. `binary`, when set, is the absolute
        path to the resolved per-version venv binary and becomes argv[0] (the timed
        command invokes it directly — no launcher overhead); None keeps the bare
        tool name for manual/back-compat runs. `workdir` is a run-scoped dir the
        adapter may write a generated tool config into. `extra_env` carries vars
        like TY_MAX_PARALLELISM."""
        ...

    def parallelism_cap(
        self, thread_mode: ThreadMode, cores: int, binary: str | None = None
    ) -> ParallelismCap:
        """Declare how this tool is constrained in the `constrained` track.

        `cores` is the configured core count so a tool whose cap MECHANISM depends
        on it (e.g. mypy: `--num-workers` only when cores > 1, else single-process)
        can report honestly — never claiming a cap it did not apply. `binary`, when
        set, is the resolved per-version venv binary the cap is probed against (so
        the recorded mechanism matches the binary that actually ran)."""
        ...

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        """Return (diagnostics, files) from the checker's output."""
        ...

    def classify(self, raw: RawRun) -> ResultClass:
        """Map a RawRun to the result taxonomy."""
        ...

    def clear_cache(self, project: str) -> None:
        """Remove any checker cache so every run is cold."""
        ...

    def prepare_command(self, project: str) -> str | None:
        """A hyperfine-safe shell command that clears the checker cache before
        EVERY timed run, or None when the tool is stateless. Wired into
        `hyperfine --prepare` by the collector so warmups/runs stay cold."""
        ...


def default_classify(raw: RawRun) -> ResultClass:
    """Shared fallback so adapters can delegate to the generic map."""
    return classify_default(raw)


def coerce_count(value: object) -> int | None:
    """Coerce a parsed-JSON field to a count, or None. Rejects bools (JSON
    true/false are ints in Python) and non-ints so a malformed summary line
    cannot inject a garbage count. Real adapters reuse this in parse()."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
