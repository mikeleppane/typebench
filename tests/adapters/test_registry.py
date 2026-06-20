import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.registry import (
    UnknownToolError,
    create_adapter,
    default_checker_specs,
    default_tools,
    tool_names,
    validate_specs,
)
from typebench.contracts.identity import CheckerSpec


def test_registry_default_tools_are_real_checker_specs() -> None:
    assert default_tools() == ("mypy", "pyright", "pyrefly", "ty", "zuban")
    assert tuple(spec.tool for spec in default_checker_specs()) == default_tools()


def test_registry_create_adapter_for_every_tool() -> None:
    for name in tool_names():
        assert isinstance(create_adapter(name), Adapter)


def test_registry_validate_specs_rejects_unknown_tool() -> None:
    with pytest.raises(UnknownToolError):
        validate_specs((CheckerSpec(tool="missing"),))
