"""Canonical first-party counter.

The neutral throughput denominator is identical across all four tools. It walks
only the declared `src_roots`, so any installed third-party dependency (which
lives in the venv's site-packages, outside the roots) is excluded by
construction. A tool's self-reported file count is a separate data point, never
this number.

LOC semantics: `loc` is a physical line count (blanks + comments included), a
coarse secondary number. The neutrality denominator that gates readiness is the
file count. tokei code-LOC is computed here and consumed by the renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.engine.proc import SYSTEM_HOST

if TYPE_CHECKING:
    from typebench.contracts.proc import ProcessHost

# argv chunk size so a giant project's file list stays under OS argv limits.
_TOKEI_CHUNK = 500


@dataclass(frozen=True)
class FileCount:
    """Canonical first-party totals for one project."""

    files: int
    loc: int


def _excluded_dir_names(globs: tuple[str, ...]) -> frozenset[str]:
    """Derive directory names excluded by the normalized dir-segment globs."""
    return frozenset(glob.strip("*/ ").split("/")[0] for glob in globs if glob.strip("*/ "))


def first_party_files(roots: list[Path], exclude_globs: tuple[str, ...]) -> list[Path]:
    """Return the canonical first-party `.py` file set."""
    excluded = _excluded_dir_names(exclude_globs)
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel_parts = set(path.relative_to(root).parts)
            if excluded & rel_parts:
                continue
            out.append(path)
    return out


def count_first_party(roots: list[Path], exclude_globs: tuple[str, ...]) -> FileCount:
    """Count `.py` files and physical lines under `roots`."""
    files = first_party_files(roots, exclude_globs)
    loc = sum(
        len(path.read_text(encoding="utf-8", errors="replace").splitlines()) for path in files
    )
    return FileCount(files=len(files), loc=loc)


def _count_tokei_chunk(chunk: list[Path], host: ProcessHost) -> tuple[int, int] | None:
    out = host.run(
        [
            "tokei",
            "--output",
            "json",
            "--no-ignore",
            "--types",
            "Python",
            *[str(path) for path in chunk],
        ],
        timeout=300,
    )
    if out.exit_code != 0:
        return None

    try:
        data: object = json.loads(out.stdout)
    except ValueError:
        return None
    if isinstance(data, dict):
        python = data.get("Python")
        if isinstance(python, dict):
            code = python.get("code")
            reports = python.get("reports")
            if isinstance(code, int) and isinstance(reports, list):
                return code, len(reports)
    return None


def count_code_loc(files: list[Path], *, host: ProcessHost = SYSTEM_HOST) -> int | None:
    """tokei Python code-LOC over exactly `files`, or None for physical-LOC fallback."""
    if not files or host.which("tokei") is None:
        return None

    normalized_files = [Path(path) for path in files]
    total_code = 0
    total_reports = 0
    for start in range(0, len(normalized_files), _TOKEI_CHUNK):
        chunk = normalized_files[start : start + _TOKEI_CHUNK]
        chunk_totals = _count_tokei_chunk(chunk, host)
        if chunk_totals is None:
            return None
        code, reports = chunk_totals
        total_code += code
        total_reports += reports

    if total_reports != len(normalized_files):
        return None
    return total_code
