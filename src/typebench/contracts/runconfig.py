"""The resolved run configuration: the layered substrate (defaults < file < CLI).

A `RunConfig` is the fully merged, validated description of a run: which
checkers, which policy, which projects/buckets, which thread tracks plus cores
sweep, and the timing knobs. It is pydantic (`extra="forbid"`) so a typo in
`typebench.toml` fails loudly, and it is serialized into the ResultsEnvelope so
a run is reproducible by record. Off the measured path; pydantic is fine here.
"""

from __future__ import annotations

import pathlib  # noqa: TC003 - pydantic resolves postponed annotations at runtime.

from pydantic import BaseModel, ConfigDict, model_validator

from typebench.contracts.identity import CheckerSpec, Source
from typebench.contracts.policy import Policy
from typebench.contracts.taxonomy import SizeBucket, ThreadMode


class RunConfig(BaseModel):
    """The merged, validated run description."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checkers: tuple[CheckerSpec, ...]
    policy: Policy = Policy.STANDARD
    # None -> resolve at load, repo-root anchored. The loader fills it in.
    corpus: pathlib.Path | None = None
    projects: tuple[str, ...] = ()
    buckets: tuple[SizeBucket, ...] = ()
    thread_modes: tuple[ThreadMode, ...] = (ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED)
    cores: tuple[int, ...] = (1,)
    runs: int = 10
    warmup: int = 3
    mem_runs: int = 3

    @model_validator(mode="after")
    def _validate_ranges(self) -> RunConfig:
        """Reject invalid knobs at the substrate so a config file cannot smuggle in
        what the CLI's own clamps reject (e.g. `tracks.cores = [0]` -> `--threads 0`)."""
        if not self.cores or any(c < 1 for c in self.cores):
            msg = f"cores must be a non-empty list of values >= 1, got {list(self.cores)}"
            raise ValueError(msg)
        if self.runs < 1:
            raise ValueError(f"runs must be >= 1, got {self.runs}")
        if self.warmup < 0:
            raise ValueError(f"warmup must be >= 0, got {self.warmup}")
        if self.mem_runs < 1:
            raise ValueError(f"mem_runs must be >= 1, got {self.mem_runs}")
        return self


def _parse_spec(token: str) -> CheckerSpec:
    """Parse a `--tool` token: `mypy`, `mypy@1.19.0`, or `mypy@1.19.0+rc`."""
    tool, _, rest = token.partition("@")
    if not rest:
        return CheckerSpec(tool=tool, version=None, source=Source.PYPI)
    version, _, label = rest.partition("+")
    return CheckerSpec(
        tool=tool,
        version=version or None,
        label=label or None,
        source=Source.PYPI,
    )


def merge_tool_override(
    configured: tuple[CheckerSpec, ...], cli_tools: list[str]
) -> tuple[CheckerSpec, ...]:
    """Resolve CLI `--tool` tokens against the configured checker set.

    Bare names keep every configured spec for that tool, including pinned
    versions. Explicit `tool@version` tokens replace the configured version.
    The CLI tool set replaces the configured checker set as one selection group.
    """
    by_tool: dict[str, list[CheckerSpec]] = {}
    for spec in configured:
        by_tool.setdefault(spec.tool, []).append(spec)

    out: list[CheckerSpec] = []
    for token in cli_tools:
        spec = _parse_spec(token)
        if spec.version is None and spec.label is None and spec.tool in by_tool:
            out.extend(by_tool[spec.tool])
        else:
            out.append(spec)
    return tuple(out)
