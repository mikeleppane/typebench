"""External-tool inventory for `typebench doctor`.

Owns the tool inventory as static data and resolves presence/version through
three injected seams (which / probe / resource_capable) so the full matrix,
including the systemd-run + cgroup-v2 capability row, is testable without a host.

Layering: this is engine-level and MUST NOT import `adapters` (that would invert
contracts <- engine <- adapters). The four checker rows are static strings, not
the live adapter registry. If doctor ever needs the real registry, it moves up to
the cli layer.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from typebench.engine import measure
from typebench.engine.env import cmd_version

if TYPE_CHECKING:
    from collections.abc import Callable


class Tier(StrEnum):
    REQUIRED = "required"
    PER_TOOL = "per-tool"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class ToolCheck:
    name: str
    role: str
    tier: Tier
    present: bool
    healthy: bool
    version: str | None
    if_absent: str
    install_hint: str


@dataclass(frozen=True)
class _Spec:
    name: str
    tier: Tier
    role: str
    if_absent: str
    install_hint: str
    binary: str | None
    probe_argv: tuple[str, ...]


# Static inventory. Order = display order. Checker names are literals, not adapter imports.
_INVENTORY: Final[tuple[_Spec, ...]] = (
    _Spec(
        "uv",
        Tier.REQUIRED,
        "env + corpus venvs",
        "setup/corpus fails",
        "mise use -g uv@latest  (or: curl -LsSf https://astral.sh/uv/install.sh | sh)",
        "uv",
        ("uv", "--version"),
    ),
    _Spec(
        "git",
        Tier.REQUIRED,
        "clone pinned corpus",
        "corpus prep fails",
        "system package manager (apt/brew install git)",
        "git",
        ("git", "--version"),
    ),
    _Spec(
        "mypy",
        Tier.PER_TOOL,
        "checker",
        "mypy rows absent from suite",
        "uv sync  (dev dependency)",
        "mypy",
        ("mypy", "--version"),
    ),
    _Spec(
        "pyright",
        Tier.PER_TOOL,
        "checker (needs node)",
        "pyright rows absent",
        "uv sync  (dev dependency)",
        "pyright",
        ("pyright", "--version"),
    ),
    _Spec(
        "pyrefly",
        Tier.PER_TOOL,
        "checker",
        "pyrefly rows absent",
        "uv sync  (dev dependency)",
        "pyrefly",
        ("pyrefly", "--version"),
    ),
    _Spec(
        "ty",
        Tier.PER_TOOL,
        "checker",
        "ty rows absent",
        "uv sync  (dev dependency)",
        "ty",
        ("ty", "--version"),
    ),
    _Spec(
        "node",
        Tier.PER_TOOL,
        "pyright runtime",
        "pyright cannot run; others unaffected",
        "mise use -g node@lts",
        "node",
        ("node", "--version"),
    ),
    _Spec(
        "hyperfine",
        Tier.OPTIONAL,
        "wall-time",
        "timing fields null",
        "mise use -g hyperfine@latest",
        "hyperfine",
        ("hyperfine", "--version"),
    ),
    _Spec(
        "tokei",
        Tier.OPTIONAL,
        "canonical code-LOC",
        "canonical_code_loc null; physical fallback",
        "mise use -g tokei@latest",
        "tokei",
        ("tokei", "--version"),
    ),
    _Spec(
        "taskset",
        Tier.OPTIONAL,
        "constrained affinity floor",
        "no affinity claim recorded",
        "util-linux (Linux only)",
        "taskset",
        ("taskset", "--version"),
    ),
    _Spec(
        "systemd-run",
        Tier.OPTIONAL,
        "memory/CPU/OOM",
        "resource fields null",
        "Linux + cgroup v2 + systemd --user session",
        None,
        (),
    ),
)


def run_doctor(
    *,
    which: Callable[[str], str | None] = shutil.which,
    probe: Callable[[list[str]], str | None] = cmd_version,
    resource_capable: Callable[[], bool] = measure.capable,
) -> list[ToolCheck]:
    """Resolve every inventory row through the injected seams.

    `present` is physical availability; `healthy` is usability for the role. They
    diverge only for pyright: a pyright binary with no node runtime is present but
    not healthy, so it can never render `ok`.
    """
    node_present = which("node") is not None
    node_version = probe(["node", "--version"]) if node_present else None
    checks: list[ToolCheck] = []
    for spec in _INVENTORY:
        if spec.binary is None:
            cap = resource_capable()
            checks.append(
                ToolCheck(
                    spec.name,
                    spec.role,
                    spec.tier,
                    cap,
                    cap,
                    None,
                    spec.if_absent,
                    spec.install_hint,
                )
            )
            continue
        present = which(spec.binary) is not None
        version = probe(list(spec.probe_argv)) if present and spec.probe_argv else None
        # A probe-backed tool whose `--version` fails is on PATH but not usable: it
        # must not render `ok` or satisfy `--check`. which() success alone is not
        # enough — a broken/incompatible binary still resolves on PATH.
        healthy = present and (version is not None if spec.probe_argv else True)
        if spec.name == "pyright":
            healthy = healthy and node_present
            if present and version is not None:
                version = (
                    f"{version} (node {node_version})"
                    if node_present
                    else f"{version} (node MISSING)"
                )
        checks.append(
            ToolCheck(
                spec.name,
                spec.role,
                spec.tier,
                present,
                healthy,
                version,
                spec.if_absent,
                spec.install_hint,
            )
        )
    return checks
