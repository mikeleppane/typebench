"""Corpus as data.

`suite.toml` declares each pinned real-world project: its release-tag SHA,
first-party source roots, size bucket, target Python, and an explicit install
recipe. The corpus is the only project-specific data the engine consumes;
everything else is generic.
"""

from __future__ import annotations

import tomllib
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

from typebench.contracts.config import DEFAULT_EXCLUDES

if TYPE_CHECKING:
    from pathlib import Path


class SizeBucket(StrEnum):
    """LOC bands that reveal scaling curves."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    GIANT = "giant"


class CorpusProject(BaseModel):
    """One pinned corpus entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    repo_url: str
    sha: str
    tag: str
    size_bucket: SizeBucket
    python_version: str
    # Normalized config locks BOTH version and platform; defaulted so existing minimal entries
    # stay valid, but the suite pins it explicitly.
    python_platform: str = "linux"
    src_roots: tuple[str, ...]
    install: tuple[str, ...]
    # Repo-relative path to a checked-in constraints lock. When set, install pins
    # to it via UV_CONSTRAINT and prepare verifies the frozen resolution still
    # matches. None => resolve-and-freeze is a lock seed only.
    constraints: str | None = None
    # Extends (never replaces) the default excludes so tests/vendored/generated are
    # always excluded; restricted to dir-segment globs by the validator.
    exclude_globs: tuple[str, ...] = ()

    @field_validator("exclude_globs")
    @classmethod
    def _only_dir_segment_globs(cls, globs: tuple[str, ...]) -> tuple[str, ...]:
        """Reject globs that cannot be reduced to excluded directory names."""
        for glob in globs:
            segment = glob.strip("*/ ")
            if not segment or "/" in segment or "." in segment or glob != f"**/{segment}/**":
                msg = f"exclude_globs must be dir-segment globs (**/<dir>/**), got {glob!r}"
                raise ValueError(msg)
        return globs

    def effective_excludes(self) -> tuple[str, ...]:
        """Return the default excludes followed by this entry's extensions."""
        return DEFAULT_EXCLUDES + self.exclude_globs


def load_suite(path: Path) -> list[CorpusProject]:
    """Parse and validate `suite.toml`, failing loudly on malformed corpus data."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("project", [])
    projects = [CorpusProject.model_validate(entry) for entry in entries]
    # Resolve each entry's repo-relative `constraints` to an absolute path anchored
    # at the repo root (the suite lives at <root>/corpus/suite.toml, so the root is
    # the suite file's grandparent). Without this, `typebench preflight --corpus
    # <path>` run from any CWD other than the repo root would fail to find the lock.
    # Entries built directly (tests) without constraints are untouched.
    repo_root = path.resolve().parent.parent
    projects = [
        project.model_copy(update={"constraints": str(repo_root / project.constraints)})
        if project.constraints is not None
        else project
        for project in projects
    ]
    names = [project.name for project in projects]
    dupes = sorted({name for name in names if names.count(name) > 1})
    if dupes:
        msg = f"duplicate corpus project name(s): {dupes}"
        raise ValueError(msg)
    return projects


def load_suite_version(path: Path) -> str:
    """Read `[suite] version` from suite.toml; 'unversioned' when absent."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    suite = raw.get("suite", {})
    version = suite.get("version") if isinstance(suite, dict) else None
    return str(version) if version else "unversioned"
