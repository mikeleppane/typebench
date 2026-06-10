"""Checker identity: declared specs and resolved runtimes.

A CheckerSpec is the declared intent: tool, optional version/label, and source.
Its checker_id is the stable matrix identity, not the bare tool name, so two
versions of one checker can coexist as distinct cells.

A CheckerRuntime is the resolved identity produced by checker environment setup:
the exact version, the absolute binary path, and the lock hash for the frozen
transitive dependency set.

This module is pydantic-free by design. It is base-layer vocabulary shared by
adapters and corpus setup, and must stay cheap to import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Source(StrEnum):
    """Where a checker distribution comes from."""

    PYPI = "pypi"
    PATH = "path"
    GIT = "git"


@dataclass(frozen=True)
class CheckerSpec:
    """Declared checker identity before environment resolution."""

    tool: str
    version: str | None = None
    label: str | None = None
    source: Source = Source.PYPI

    def __post_init__(self) -> None:
        if self.label is not None and not re.fullmatch(r"[A-Za-z0-9._-]+", self.label):
            msg = f"label must be [A-Za-z0-9._-]+ (path-safe), got {self.label!r}"
            raise ValueError(msg)

    def checker_id(self) -> str:
        """Return the stable declared checker identity."""
        base = f"{self.tool}@{self.version or 'latest'}"
        return f"{base}+{self.label}" if self.label else base


@dataclass(frozen=True)
class CheckerRuntime:
    """Resolved checker identity used as the matrix key and record stamp."""

    checker_id: str
    tool: str
    binary: str
    version: str
    lock_hash: str
    install_source: str
