from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from typebench._internal.test_fakes import fake_raw
from typebench.contracts.identity import CheckerRuntime, CheckerSpec, Source
from typebench.contracts.proc import RawRun
from typebench.corpus.checkerenv import (
    PrepareError,
    _checker_dir,
    _fingerprint,
    cache_status,
    prepare_checker,
)


class _FakeHost:
    """Records calls and materializes fake venv artifacts."""

    def __init__(self, outs: dict[tuple[str, ...], RawRun] | None = None) -> None:
        self.calls: list[tuple[list[str], Path | None, Mapping[str, str] | None]] = []
        self._outs = outs or {}

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> RawRun:
        argv_list = list(argv)
        self.calls.append((argv_list, cwd, env))
        out = self._outs.get(tuple(argv_list[:2]), fake_raw())
        if out.exit_code != 0:
            return out
        if argv_list[:2] == ["uv", "venv"]:
            venv_bin = Path(argv_list[-1]) / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        if argv_list[:3] == ["uv", "pip", "install"]:
            venv_python = Path(argv_list[argv_list.index("--python") + 1])
            venv_bin = venv_python.parent
            install_spec = argv_list[-1]
            tool = install_spec.split("==", 1)[0]
            (venv_bin / tool).write_text("#!/bin/sh\n", encoding="utf-8")
        return out

    def which(self, name: str) -> str | None:
        return f"/usr/bin/{name}"


_FREEZE = fake_raw(stdout="mypy==1.18.2\nmypy-extensions==1.0.0\ntyping-extensions==4.9.0\n")


def test_prepare_checker_pinned_version_builds_runtime(tmp_path: Path) -> None:
    run = _FakeHost({("uv", "pip"): _FREEZE})
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    rt = prepare_checker(spec, tmp_path, install_source="PyPI wheel (mypyc)", host=run)

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
    run = _FakeHost({("uv", "pip"): _FREEZE})
    rt = prepare_checker(
        CheckerSpec(tool="mypy"), tmp_path, install_source="PyPI wheel (mypyc)", host=run
    )

    assert rt.version == "1.18.2"
    assert rt.checker_id == "mypy@1.18.2"
    install = next(c for c in run.calls if c[0][:3] == ["uv", "pip", "install"])
    assert "mypy" in install[0]
    assert "mypy==1.18.2" not in install[0]


def test_prepare_checker_unpinned_latest_never_cache_hits(tmp_path: Path) -> None:
    run1 = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(CheckerSpec(tool="mypy"), tmp_path, install_source="s", host=run1)
    assert run1.calls

    run2 = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(CheckerSpec(tool="mypy"), tmp_path, install_source="s", host=run2)
    assert run2.calls


def test_prepare_checker_lock_hash_is_over_transitive_set(tmp_path: Path) -> None:
    run_a = _FakeHost({("uv", "pip"): _FREEZE})
    run_b = _FakeHost({("uv", "pip"): fake_raw(stdout="mypy==1.18.2\nmypy-extensions==1.1.0\n")})

    a = prepare_checker(
        CheckerSpec(tool="mypy", version="1.18.2"), tmp_path / "a", install_source="s", host=run_a
    )
    b = prepare_checker(
        CheckerSpec(tool="mypy", version="1.18.2"), tmp_path / "b", install_source="s", host=run_b
    )
    assert a.version == b.version
    assert a.lock_hash != b.lock_hash


def test_prepare_checker_is_idempotent_via_sidecar(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    run1 = _FakeHost({("uv", "pip"): _FREEZE})
    first = prepare_checker(spec, tmp_path, install_source="s", host=run1)
    assert run1.calls

    run2 = _FakeHost({("uv", "pip"): _FREEZE})
    second = prepare_checker(spec, tmp_path, install_source="s", host=run2)
    assert run2.calls == []
    assert second == first


def test_cache_status_reports_hit_for_pinned_and_miss_for_unpinned_sidecar(
    tmp_path: Path,
) -> None:
    pinned = CheckerSpec(tool="mypy", version="1.18.2")
    pinned_run = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(pinned, tmp_path, install_source="s", host=pinned_run)
    assert cache_status(pinned, tmp_path) == ("cache-hit", "1.18.2")

    unpinned = CheckerSpec(tool="mypy")
    unpinned_run = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(unpinned, tmp_path, install_source="s", host=unpinned_run)
    assert cache_status(unpinned, tmp_path) == ("will-build", None)


def test_prepare_checker_rebuilds_on_python_version_change(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    run1 = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(spec, tmp_path, install_source="s", python_version="3.12", host=run1)
    assert run1.calls

    run2 = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(spec, tmp_path, install_source="s", python_version="3.13", host=run2)
    assert run2.calls


def test_prepare_checker_rebuilds_when_sidecar_marker_absent(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    run1 = _FakeHost({("uv", "pip"): _FREEZE})
    prepare_checker(spec, tmp_path, install_source="s", host=run1)
    sidecar = _checker_dir(tmp_path, spec.checker_id(), _fingerprint(spec, "3.12", "linux"))
    (sidecar / "checker.json").unlink()

    run2 = _FakeHost({("uv", "pip"): _FREEZE})
    rt = prepare_checker(spec, tmp_path, install_source="s", host=run2)
    assert run2.calls
    assert rt.checker_id == "mypy@1.18.2"


def test_prepare_checker_cleans_partial_dir_on_failure(tmp_path: Path) -> None:
    spec = CheckerSpec(tool="mypy", version="1.18.2")
    bad = _FakeHost({("uv", "pip"): fake_raw(exit_code=1, stderr="install boom")})
    with pytest.raises(PrepareError, match="boom"):
        prepare_checker(spec, tmp_path, install_source="s", host=bad)
    assert not _checker_dir(
        tmp_path, spec.checker_id(), _fingerprint(spec, "3.12", "linux")
    ).exists()


def test_prepare_checker_path_source_is_rejected(tmp_path: Path) -> None:
    run = _FakeHost({("uv", "pip"): _FREEZE})
    spec = CheckerSpec(tool="mypy", version="1.18.2", source=Source.PATH)
    with pytest.raises(PrepareError, match="only the 'pypi' source"):
        prepare_checker(spec, tmp_path, install_source="s", host=run)
