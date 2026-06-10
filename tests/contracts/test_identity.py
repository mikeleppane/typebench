import dataclasses

import pytest

from typebench.contracts.identity import CheckerRuntime, CheckerSpec, Source


def test_source_members_pypi_implemented_path_git_model_only() -> None:
    assert [s.value for s in Source] == ["pypi", "path", "git"]
    assert Source.PYPI == "pypi"


def test_checker_id_bare_tool_resolves_to_latest_label() -> None:
    assert CheckerSpec(tool="mypy").checker_id() == "mypy@latest"


def test_checker_id_includes_pinned_version() -> None:
    assert CheckerSpec(tool="mypy", version="1.18.2").checker_id() == "mypy@1.18.2"


def test_checker_id_appends_label_when_present() -> None:
    spec = CheckerSpec(tool="mypy", version="1.19.0", label="rc")
    assert spec.checker_id() == "mypy@1.19.0+rc"


def test_checker_id_is_unique_across_same_tool_different_version() -> None:
    a = CheckerSpec(tool="mypy", version="1.18.2").checker_id()
    b = CheckerSpec(tool="mypy", version="1.19.0").checker_id()
    assert a != b


def test_checker_spec_defaults_to_pypi_source() -> None:
    assert CheckerSpec(tool="ty").source is Source.PYPI


def test_checker_spec_rejects_unsafe_label() -> None:
    with pytest.raises(ValueError):
        CheckerSpec(tool="mypy", label="feat/x")


def test_checker_spec_is_frozen() -> None:
    spec = CheckerSpec(tool="mypy")
    attr = "tool"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, attr, "ty")


def test_checker_runtime_carries_resolved_identity() -> None:
    rt = CheckerRuntime(
        checker_id="mypy@1.18.2",
        tool="mypy",
        binary="/cache/checkers/mypy@1.18.2/bin/mypy",
        version="1.18.2",
        lock_hash="abc",
        install_source="PyPI wheel (mypyc-compiled)",
    )
    assert rt.checker_id == "mypy@1.18.2"
    assert rt.version == "1.18.2"
    attr = "version"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(rt, attr, "9")
