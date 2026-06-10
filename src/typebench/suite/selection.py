"""Resolve a RunConfig's selection to an ordered list of corpus project names.

Selection = union(explicit projects, expand(buckets)). Both empty = the whole
corpus. An unknown explicit project name is a loud error (never a silent skip).
Corpus declaration order is preserved so the matrix is deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typebench.contracts.runconfig import RunConfig
    from typebench.corpus.catalog import CorpusProject


class SelectionError(ValueError):
    """A selection referenced a project not in the corpus."""


def resolve_selection(config: RunConfig, corpus: list[CorpusProject]) -> list[str]:
    """Ordered project names selected by `config` against `corpus`."""
    by_name = {entry.name: entry for entry in corpus}
    if not config.projects and not config.buckets:
        return [entry.name for entry in corpus]

    unknown = sorted(name for name in config.projects if name not in by_name)
    if unknown:
        msg = f"unknown project name(s) in selection: {unknown}; known: {sorted(by_name)}"
        raise SelectionError(msg)

    chosen = set(config.projects)
    if config.buckets:
        wanted = set(config.buckets)
        chosen |= {entry.name for entry in corpus if entry.size_bucket in wanted}
    # Preserve corpus declaration order; union deduplicates.
    return [entry.name for entry in corpus if entry.name in chosen]
