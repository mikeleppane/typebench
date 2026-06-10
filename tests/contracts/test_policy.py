import dataclasses
from types import MappingProxyType

import pytest

from typebench.contracts.policy import PRESETS, CheckerPosture, Policy


def test_standard_preset_is_the_locked_headline_posture() -> None:
    p = PRESETS[Policy.STANDARD]
    assert p == CheckerPosture(
        strict=False,
        analyze_untyped_defs=True,
        resolve_deps_report_first_party=True,
        suppress_project_config=True,
    )


def test_strict_is_not_shipped_yet() -> None:
    # strict is deferred until per-tool translations are verified + goldened.
    assert Policy.STRICT not in PRESETS


def test_posture_is_frozen() -> None:
    assert dataclasses.is_dataclass(CheckerPosture)
    p = PRESETS[Policy.STANDARD]
    field_name = "strict"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(p, field_name, True)  # dynamic -> no static assign-to-frozen error


def test_presets_table_is_immutable() -> None:
    # The table itself is read-only, not just each CheckerPosture value.
    assert isinstance(PRESETS, MappingProxyType)
