"""The locked checker posture, as one auditable table.

Posture is the equalized *intent* (strictness, untyped-def analysis, import
handling, project-config suppression). Each adapter translates a CheckerPosture
into its own native flags — the translation stays in the adapter, the posture
intent lives here. This module is the single source of posture *intent*; the
faithful per-tool *rendering* is locked separately by the adapter goldens (so
the neutrality audit surface is this table PLUS the four translators PLUS the
goldens, not this table alone).

Pydantic-free on purpose: `contracts` is the no-internal-deps base layer
(`contracts <- engine <- {adapters, corpus}`), and the measured wrapper path
must stay pydantic-free (AGENTS.md, "Measurement fidelity"). Keeping posture
stdlib-only means any layer — including a future engine module on the timed
path that records which posture ran — can import it without dragging pydantic's
import cost in. (It is NOT "adapters are the hot path": `command()` runs once
during orchestration, and adapters already import the pydantic `contracts.models`
at runtime — the real hot path is `engine.wrapper`, which imports the
pydantic-free `contracts.taxonomy`.)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class Policy(StrEnum):
    """Named, equalized posture profiles."""

    STANDARD = "standard"  # locked headline posture (published)
    STRICT = "strict"  # deferred — needs verified per-tool translations + goldens


@dataclass(frozen=True)
class CheckerPosture:
    """The equalized knobs every checker must express the same way.

    Fields are *intent*, not literal flag-equivalence. Example:
    `resolve_deps_report_first_party` renders as mypy `--follow-imports=silent`
    (diagnostic scoping) AND pyright `useLibraryCodeForTypes` (stub fallback) —
    related, not identical, mechanisms that co-activate under standard. The
    goldens, not the field name, are what verify each tool's rendering is
    neutrality-faithful.
    """

    strict: bool
    analyze_untyped_defs: bool
    resolve_deps_report_first_party: bool
    suppress_project_config: bool


# MappingProxyType so the table is read-only at runtime (the CheckerPosture
# values are already frozen). Annotated as Mapping (no __setitem__) so a stray
# PRESETS[...] = ... also fails the type check.
PRESETS: Mapping[Policy, CheckerPosture] = MappingProxyType(
    {
        Policy.STANDARD: CheckerPosture(
            strict=False,
            analyze_untyped_defs=True,
            resolve_deps_report_first_party=True,
            suppress_project_config=True,
        ),
        # Policy.STRICT: add ONLY with verified per-tool strict translations +
        # goldens. Until then every adapter's posture helper raises on strict.
    }
)
