"""Suite orchestration (spec §10/§11). Loops the (project x tool x thread-mode)
matrix behind the §12 preflight gate and writes a ResultsEnvelope. Off the measured
path; pydantic via `models` is fine here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typebench.models import ThreadMode


@dataclass(frozen=True)
class SuiteCell:
    """One unit of the benchmark matrix."""

    project: str
    tool: str
    thread_mode: ThreadMode


def build_matrix(
    projects: list[str], tools: list[str], thread_modes: list[ThreadMode]
) -> list[SuiteCell]:
    """Project-major matrix so a project's clone/venv is reused across its cells."""
    return [
        SuiteCell(project, tool, mode)
        for project in projects
        for tool in tools
        for mode in thread_modes
    ]


def shard(cells: list[SuiteCell], index: int, total: int) -> list[SuiteCell]:
    """Deterministic round-robin partition (spec §10 sharding). `total=1` is the
    identity. Round-robin (not contiguous slices) spreads heavy/light cells evenly
    across shards so no single CI job inherits all the giant-bucket work.
    """
    if total < 1:
        raise ValueError(f"shard total must be >= 1, got {total}")
    if not 0 <= index < total:
        raise ValueError(f"shard index {index} out of range for total {total}")
    return [cell for position, cell in enumerate(cells) if position % total == index]
