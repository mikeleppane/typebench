"""Single source of truth for checker adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.stub import StubAdapter
from typebench.adapters.ty import TyAdapter
from typebench.adapters.zuban import ZubanAdapter
from typebench.contracts.identity import CheckerSpec
from typebench.engine.proc import SYSTEM_HOST

if TYPE_CHECKING:
    from collections.abc import Callable

    from typebench.adapters.base import Adapter
    from typebench.contracts.proc import ProcessHost


class UnknownToolError(ValueError):
    """Raised when a checker spec names a tool with no registered adapter."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"Unknown tool: {tool!r}. Known: {sorted(tool_names())}")


@dataclass(frozen=True)
class ToolEntry:
    name: str
    factory: Callable[[ProcessHost], Adapter]
    real: bool


_ENTRIES: tuple[ToolEntry, ...] = (
    ToolEntry("mypy", MypyAdapter, True),
    ToolEntry("pyright", PyrightAdapter, True),
    ToolEntry("pyrefly", PyreflyAdapter, True),
    ToolEntry("ty", TyAdapter, True),
    ToolEntry("zuban", ZubanAdapter, True),
    ToolEntry("stub", lambda _host: StubAdapter(), False),
)
_BY_NAME = {entry.name: entry for entry in _ENTRIES}


def tool_names() -> tuple[str, ...]:
    return tuple(entry.name for entry in _ENTRIES)


def default_tools() -> tuple[str, ...]:
    return tuple(entry.name for entry in _ENTRIES if entry.real)


def default_checker_specs() -> tuple[CheckerSpec, ...]:
    return tuple(CheckerSpec(tool=name) for name in default_tools())


def create_adapter(name: str, *, host: ProcessHost = SYSTEM_HOST) -> Adapter:
    entry = _BY_NAME.get(name)
    if entry is None:
        raise UnknownToolError(name)
    return entry.factory(host)


def validate_specs(specs: tuple[CheckerSpec, ...]) -> None:
    for spec in specs:
        if spec.tool not in _BY_NAME:
            raise UnknownToolError(spec.tool)
