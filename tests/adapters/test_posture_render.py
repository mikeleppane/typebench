from collections.abc import Callable

import pytest

from typebench.adapters.mypy import _posture_args as mypy_posture
from typebench.adapters.pyrefly import _posture_lines as pyrefly_posture
from typebench.adapters.pyright import _posture_config as pyright_posture
from typebench.adapters.ty import _posture_args as ty_posture
from typebench.adapters.zuban import _posture_args as zuban_posture
from typebench.contracts.policy import PRESETS, CheckerPosture, Policy

_STANDARD = PRESETS[Policy.STANDARD]
_STRICT = CheckerPosture(
    strict=True,
    analyze_untyped_defs=True,
    resolve_deps_report_first_party=True,
    suppress_project_config=True,
)


def test_mypy_standard_renders_expected_flags() -> None:
    assert mypy_posture(_STANDARD) == [
        "--check-untyped-defs",
        "--follow-imports=silent",
        "--config-file=",
    ]


def test_pyright_standard_renders_expected_config() -> None:
    assert pyright_posture(_STANDARD) == {
        "typeCheckingMode": "standard",
        "useLibraryCodeForTypes": True,
    }


def test_pyrefly_standard_renders_expected_lines() -> None:
    assert pyrefly_posture(_STANDARD) == [
        'preset = "default"',
        "check-unannotated-defs = true",
    ]


def test_ty_standard_renders_no_flags() -> None:
    assert ty_posture(_STANDARD) == []


def test_zuban_standard_renders_no_flags() -> None:
    # default mode satisfies analyze_untyped_defs + resolve_deps_report_first_party
    # natively, so STANDARD emits no posture flags (like ty).
    assert zuban_posture(_STANDARD) == []


@pytest.mark.parametrize(
    "render",
    [mypy_posture, pyright_posture, pyrefly_posture, ty_posture, zuban_posture],
)
def test_strict_posture_is_guarded_until_verified(
    render: Callable[[CheckerPosture], object],
) -> None:
    with pytest.raises(NotImplementedError):
        render(_STRICT)
