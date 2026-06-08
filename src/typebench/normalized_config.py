"""The normalized benchmark config (spec §6) — the equal observable inputs fed
to every checker. A pure value object; each adapter renders it into its own
config file / flags. Defaults are the neutral, stock-but-equal policy."""

from __future__ import annotations

from dataclasses import dataclass, field

# Excluded everywhere (spec §6): tests, vendored, generated, caches.
_DEFAULT_EXCLUDES: tuple[str, ...] = (
    "**/tests/**",
    "**/test/**",
    "**/_vendor/**",
    "**/vendor/**",
    "**/generated/**",
    "**/_generated/**",
    "**/__pycache__/**",
    "**/node_modules/**",
)


@dataclass(frozen=True)
class NormalizedConfig:
    """§6 inputs. `src_roots` are absolute first-party dirs to analyze (the
    throughput denominator); `venv_python` is the project venv interpreter used
    to resolve installed third-party imports (deps resolved, first-party
    diagnostics only)."""

    src_roots: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = field(default=_DEFAULT_EXCLUDES)
    python_version: str = "3.12"
    python_platform: str = "linux"
    venv_python: str | None = None
