"""The normalized benchmark config (spec §6) — the equal observable inputs fed
to every checker. A pure value object; each adapter renders it into its own
config file / flags. Defaults are the neutral, stock-but-equal policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Excluded everywhere (spec §6): tests, vendored, generated, caches.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "**/tests/**",
    "**/test/**",
    "**/_vendor/**",
    "**/vendor/**",
    "**/generated/**",
    "**/_generated/**",
    "**/__pycache__/**",
    "**/node_modules/**",
)

# Bump when the LOCKED §6 policy set (flags/posture) changes, so config_hash
# distinguishes pre/post-policy runs even at identical inputs.
NORMALIZED_POLICY_VERSION = "v1"


def config_hash(
    src_roots: tuple[str, ...],
    exclude_globs: tuple[str, ...],
    python_version: str,
    python_platform: str,
) -> str:
    """Stable, machine-independent hash of the resolved normalized config (spec §6).

    Callers MUST pass REPO-RELATIVE src_roots (e.g. CorpusProject.src_roots), never
    the absolute checkout path or the venv — those are machine-specific and would
    make the hash non-comparable across runs/VMs. Inputs are sorted so ordering is
    irrelevant; NORMALIZED_POLICY_VERSION folds the locked policy revision in.
    """
    payload = "\n".join(
        [
            NORMALIZED_POLICY_VERSION,
            "\x00".join(sorted(src_roots)),
            "\x00".join(sorted(exclude_globs)),
            python_version,
            python_platform,
        ]
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class NormalizedConfig:
    """§6 inputs. `src_roots` are absolute first-party dirs to analyze (the
    throughput denominator); `venv_python` is the project venv interpreter used
    to resolve installed third-party imports (deps resolved, first-party
    diagnostics only)."""

    src_roots: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = field(default=DEFAULT_EXCLUDES)
    python_version: str = "3.12"
    python_platform: str = "linux"
    venv_python: str | None = None
