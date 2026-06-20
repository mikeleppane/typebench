"""Opt-in neutrality check (spec §8 carry-over).

Clones the real pinned httpx, installs its locked deps, and proves the canonical
first-party count excludes dependency files by construction.
"""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from typebench.adapters.base import CheckerHandle
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.adapters.zuban import ZubanAdapter
from typebench.contracts.identity import CheckerSpec
from typebench.corpus.catalog import CorpusProject, load_suite
from typebench.corpus.envman import prepare_project
from typebench.suite.preflight import preflight_project

_SUITE = Path(__file__).resolve().parents[2] / "corpus" / "suite.toml"
_FIRST_PARTY_SCOPED = {"pyright", "ty", "pyrefly"}


def _online() -> bool:
    try:
        socket.create_connection(("github.com", 443), timeout=3).close()
    except OSError:
        return False
    return True


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("TYPEBENCH_RUN_NETWORK_TESTS") != "1",
        reason="opt-in: set TYPEBENCH_RUN_NETWORK_TESTS=1 to run (clones GitHub)",
    ),
    pytest.mark.skipif(not _online(), reason="offline: skips real clone"),
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv"),
]


def _httpx_entry() -> CorpusProject:
    return next(project for project in load_suite(_SUITE) if project.name == "httpx")


def test_prepare_httpx_resolves_deps_and_counts_first_party_only(tmp_path: Path) -> None:
    entry = _httpx_entry()
    prepared = prepare_project(entry, tmp_path / "cache")

    proc = subprocess.run(
        [prepared.venv_python, "-c", "import httpcore; print(httpcore.__file__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    dep_path = proc.stdout.strip()

    excluded_dirs = {glob.strip("*/ ") for glob in entry.effective_excludes()}
    root = Path(prepared.checkout) / "httpx"
    independent = [
        path
        for path in root.rglob("*.py")
        if not (set(path.relative_to(root).parts) & excluded_dirs)
    ]
    assert prepared.canonical_files == len(independent)
    assert prepared.canonical_files > 0
    for src_root in prepared.src_roots:
        assert not dep_path.startswith(src_root)


def test_httpx_preflight_records_per_tool_divergence(tmp_path: Path) -> None:
    prepared = prepare_project(_httpx_entry(), tmp_path / "cache")
    pairs = (
        (MypyAdapter(), "mypy"),
        (PyrightAdapter(), "pyright"),
        (TyAdapter(), "ty"),
        (PyreflyAdapter(), "pyrefly"),
        (ZubanAdapter(), "zuban"),
    )
    checkers = [
        CheckerHandle(spec=CheckerSpec(tool=name), adapter=adapter)
        for adapter, name in pairs
        if shutil.which(name) is not None
    ]
    if not checkers:
        pytest.skip("no real checkers installed")

    report = preflight_project(prepared, checkers, timeout=300)

    assert report.ready, [
        (tool.tool, tool.result_class.value, tool.scope_ok) for tool in report.tools
    ]
    by = {tool.tool: tool for tool in report.tools}
    for name in _FIRST_PARTY_SCOPED & set(by):
        tool = by[name]
        if tool.self_reported_files is not None:
            assert tool.self_reported_files >= report.canonical_files, (
                name,
                tool.self_reported_files,
                report.canonical_files,
            )
            assert tool.scope_ok
    # mypy and zuban share mypy's "checked K source files" semantics, which can
    # exceed canonical when followed imports are counted -> over-report → review.
    for name in ("mypy", "zuban"):
        if name in by:
            self_files = by[name].self_reported_files
            if self_files is not None:
                assert self_files >= report.canonical_files
                if self_files > report.canonical_files:
                    assert report.throughput_review_required

    print(
        "httpx file counts: canonical="
        f"{report.canonical_files} "
        + " ".join(f"{tool.tool}={tool.self_reported_files}" for tool in report.tools)
    )
