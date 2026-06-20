from pathlib import Path

import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.adapters.zuban import ZubanAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.taxonomy import ThreadMode

_BIN = "/cache/checkers/mypy@1.18.2/bin"
_ADAPTERS: tuple[tuple[Adapter, str], ...] = (
    (MypyAdapter(), "mypy"),
    (PyrightAdapter(), "pyright"),
    (PyreflyAdapter(), "pyrefly"),
    (TyAdapter(), "ty"),
    (ZubanAdapter(), "zuban"),
)


def _config(root: Path) -> NormalizedConfig:
    return NormalizedConfig(
        src_roots=(str(root / "src"),),
        python_version="3.12",
        python_platform="linux",
        venv_python=str(root / "venv" / "bin" / "python"),
        cores=1,
    )


def _fake_mypy_version(_self: object, binary: str | None = None) -> str:
    return "mypy 1.18.2"


@pytest.mark.parametrize(("adapter", "tool"), _ADAPTERS)
def test_command_uses_resolved_binary_when_given(
    adapter: Adapter, tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # mypy probes --version to size workers; pin it offline so capture is hermetic.
    monkeypatch.setattr(MypyAdapter, "version", _fake_mypy_version)
    workdir = tmp_path / "work"
    workdir.mkdir()
    argv, _env = adapter.command(
        "proj", _config(tmp_path), ThreadMode.CONSTRAINED, workdir, binary=f"{_BIN}/{tool}"
    )
    assert argv[0] == f"{_BIN}/{tool}"


@pytest.mark.parametrize(("adapter", "tool"), _ADAPTERS)
def test_command_falls_back_to_bare_tool_name(
    adapter: Adapter, tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MypyAdapter, "version", _fake_mypy_version)
    workdir = tmp_path / "work"
    workdir.mkdir()
    argv, _env = adapter.command("proj", _config(tmp_path), ThreadMode.CONSTRAINED, workdir)
    assert argv[0] == tool  # bare name -> goldens stay byte-identical
