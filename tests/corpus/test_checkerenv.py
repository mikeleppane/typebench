from pathlib import Path

import pytest

from typebench.contracts.identity import CheckerRuntime, CheckerSpec, Source
from typebench.corpus.checkerenv import (
    PrepareError,
    _checker_dir,
    _fingerprint,
    prepare_checker,
)
from typebench.corpus.envman import RunOut


class _FakeRunner:
    """Records calls and materializes fake venv artifacts."""

    def __init__(self, outs: dict[tuple[str, ...], RunOut] | None = None) -> None:
        self.calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []
        self._outs = outs or {}

    def __call__(self, argv: list[str], cwd: Path | None, env: dict[str, str] | None) -> RunOut:
        self.calls.append((argv, cwd, env))
        out = self._outs.get(tuple(argv[:2]), RunOut(0, "", ""))
        if out.returncode != 0:
            return out
        if argv[:2] == ["uv", "venv"]:
            venv_bin = Path(argv[-1]) / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        if argv[:3] == ["uv", "pip", "install"]:
            venv_python = Path(argv[argv.index("--python") + 1])
            venv_bin = venv_python.parent
            install_spec = argv[-1]
            tool = install_spec.split("==", 1)[0]
            (venv_bin / tool).write_text("#!/bin/sh\n", encoding="utf-8")
        return out


_FREEZE = RunOut(
    0,
    "mypy==1.18.2\nmypy-extensions==1.0.0\ntyping-extensions==4.9.0\n",
    "",
)


def test_prepare_checker_pinned_version_builds_runtime(tmp_path: Path) -> None:
    run = _FakeRunner({("uv", "pip"): _FREEZE})
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    rt = prepare_checker(spec, tmp_path, install_source="PyPI wheel (mypyc)", run=run)

    assert isinstance(rt, CheckerRuntime)
    assert rt.checker_id == "mypy@1.18.2"
    assert rt.version == "1.18.2"
    assert rt.binary.endswith("/bin/mypy")
    assert Path(rt.binary).is_absolute()
    assert rt.lock_hash
    assert rt.install_source == "PyPI wheel (mypyc)"
    install = next(c for c in run.calls if c[0][:3] == ["uv", "pip", "install"])
    assert "mypy==1.18.2" in install[0]
    assert "--python" in install[0]
    py_arg = install[0][install[0].index("--python") + 1]
    assert py_arg.endswith("/bin/python")


def test_prepare_checker_version_none_resolves_and_records_exact(tmp_path: Path) -> None:
    run = _FakeRunner({("uv", "pip"): _FREEZE})
    rt = prepare_checker(
        CheckerSpec(tool="mypy"), tmp_path, install_source="PyPI wheel (mypyc)", run=run
    )

    assert rt.version == "1.18.2"
    assert rt.checker_id == "mypy@1.18.2"
    install = next(c for c in run.calls if c[0][:3] == ["uv", "pip", "install"])
    assert "mypy" in install[0]
    assert "mypy==1.18.2" not in install[0]


def test_prepare_checker_unpinned_latest_never_cache_hits(tmp_path: Path) -> None:
    run1 = _FakeRunner({("uv", "pip"): _FREEZE})
    prepare_checker(CheckerSpec(tool="mypy"), tmp_path, install_source="s", run=run1)
    assert run1.calls

    run2 = _FakeRunner({("uv", "pip"): _FREEZE})
    prepare_checker(CheckerSpec(tool="mypy"), tmp_path, install_source="s", run=run2)
    assert run2.calls


def test_prepare_checker_lock_hash_is_over_transitive_set(tmp_path: Path) -> None:
    run_a = _FakeRunner({("uv", "pip"): _FREEZE})
    run_b = _FakeRunner({("uv", "pip"): RunOut(0, "mypy==1.18.2\nmypy-extensions==1.1.0\n", "")})

    a = prepare_checker(
        CheckerSpec(tool="mypy", version="1.18.2"), tmp_path / "a", install_source="s", run=run_a
    )
    b = prepare_checker(
        CheckerSpec(tool="mypy", version="1.18.2"), tmp_path / "b", install_source="s", run=run_b
    )
    assert a.version == b.version
    assert a.lock_hash != b.lock_hash


def test_prepare_checker_is_idempotent_via_sidecar(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    run1 = _FakeRunner({("uv", "pip"): _FREEZE})
    first = prepare_checker(spec, tmp_path, install_source="s", run=run1)
    assert run1.calls

    run2 = _FakeRunner({("uv", "pip"): _FREEZE})
    second = prepare_checker(spec, tmp_path, install_source="s", run=run2)
    assert run2.calls == []
    assert second == first


def test_prepare_checker_rebuilds_on_python_version_change(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    run1 = _FakeRunner({("uv", "pip"): _FREEZE})
    prepare_checker(spec, tmp_path, install_source="s", python_version="3.12", run=run1)
    assert run1.calls

    run2 = _FakeRunner({("uv", "pip"): _FREEZE})
    prepare_checker(spec, tmp_path, install_source="s", python_version="3.13", run=run2)
    assert run2.calls


def test_prepare_checker_rebuilds_when_sidecar_marker_absent(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    run1 = _FakeRunner({("uv", "pip"): _FREEZE})
    prepare_checker(spec, tmp_path, install_source="s", run=run1)
    sidecar = _checker_dir(tmp_path, spec.checker_id(), _fingerprint(spec, "3.12", "linux"))
    (sidecar / "checker.json").unlink()

    run2 = _FakeRunner({("uv", "pip"): _FREEZE})
    rt = prepare_checker(spec, tmp_path, install_source="s", run=run2)
    assert run2.calls
    assert rt.checker_id == "mypy@1.18.2"


def test_prepare_checker_cleans_partial_dir_on_failure(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    bad = _FakeRunner({("uv", "pip"): RunOut(1, "", "install boom")})
    with pytest.raises(PrepareError, match="boom"):
        prepare_checker(spec, tmp_path, install_source="s", run=bad)
    assert not _checker_dir(
        tmp_path, spec.checker_id(), _fingerprint(spec, "3.12", "linux")
    ).exists()


def test_prepare_checker_path_source_is_rejected(tmp_path: Path) -> None:
    run = _FakeRunner({("uv", "pip"): _FREEZE})
    spec = CheckerSpec(tool="mypy", version="1.18.2", source=Source.PATH)
    with pytest.raises(PrepareError, match="only the 'pypi' source"):
        prepare_checker(spec, tmp_path, install_source="s", run=run)
