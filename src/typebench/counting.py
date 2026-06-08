"""Canonical first-party counter (spec §8).

The neutral throughput denominator is identical across all four tools. It walks
only the declared `src_roots`, so any installed third-party dependency (which
lives in the venv's site-packages, outside the roots) is excluded by
construction. A tool's self-reported file count is a separate data point, never
this number.

LOC semantics: `loc` is a physical line count (blanks + comments included), a
coarse secondary number. The neutrality denominator that gates readiness is the
file count (scc-independent). scc-style code-LOC for headline kLOC/s is the
renderer's job in Plan 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class FileCount:
    """Canonical first-party totals for one project."""

    files: int
    loc: int


def _excluded_dir_names(globs: tuple[str, ...]) -> frozenset[str]:
    """Derive directory names excluded by the §6 dir-segment globs."""
    return frozenset(glob.strip("*/ ").split("/")[0] for glob in globs if glob.strip("*/ "))


def count_first_party(roots: list[Path], exclude_globs: tuple[str, ...]) -> FileCount:
    """Count `.py` files and physical lines under `roots`."""
    excluded = _excluded_dir_names(exclude_globs)
    files = 0
    loc = 0
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel_parts = set(path.relative_to(root).parts)
            if excluded & rel_parts:
                continue
            files += 1
            loc += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return FileCount(files=files, loc=loc)
