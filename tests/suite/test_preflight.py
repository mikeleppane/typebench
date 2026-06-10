import shutil
from pathlib import Path
from typing import override

import pytest
from pydantic import ValidationError

from typebench.adapters.base import Adapter, ParallelismCap
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.contracts.config import DEFAULT_EXCLUDES, NormalizedConfig
from typebench.contracts.models import (
    PreflightReport,
    PreparedProject,
    ResultClass,
    ThreadMode,
    ToolPreflight,
)
from typebench.corpus.counting import count_first_party
from typebench.engine.wrapper import RawRun
from typebench.suite.preflight import preflight_project


def _prepared() -> PreparedProject:
    return PreparedProject(
        name="httpx",
        checkout="/cache/httpx@sha/repo",
        venv_python="/cache/httpx@sha/venv/bin/python",
        src_roots=("/cache/httpx@sha/repo/httpx",),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        python_platform="linux",
        sha="0" * 40,
        lock_hash="deadbeef",
        frozen=("httpcore==1.0.0", "idna==3.0"),
        canonical_files=42,
        canonical_loc=9000,
        fingerprint="fp-abc123",
    )


def test_prepared_project_round_trips() -> None:
    prepared = _prepared()
    again = PreparedProject.model_validate_json(prepared.model_dump_json())
    assert again == prepared
    assert again.src_roots == ("/cache/httpx@sha/repo/httpx",)  # tuple preserved


def test_prepared_project_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        PreparedProject.model_validate({**_prepared().model_dump(), "bogus": 1})


def test_preflight_report_round_trips() -> None:
    report = PreflightReport(
        project="httpx",
        sha="0" * 40,
        python_version="3.12",
        lock_hash="deadbeef",
        canonical_files=42,
        canonical_loc=9000,
        ready=True,
        throughput_review_required=True,  # mypy over-reports (analyzed-work divergence)
        tools=[
            ToolPreflight(
                tool="mypy",
                version="mypy 1.0",
                result_class=ResultClass.DIAGNOSTICS,
                real_exit_code=1,
                self_reported_files=500,
                files_divergence=458,
                scope_ok=True,
                over_reports=True,  # 500 > 42 canonical: follows dep imports (§191)
            ),
            ToolPreflight(
                tool="pyright",
                version="pyright 1.1",
                result_class=ResultClass.CLEAN,
                real_exit_code=0,
                self_reported_files=42,
                files_divergence=0,
            ),
        ],
    )
    again = PreflightReport.model_validate_json(report.model_dump_json())
    assert again == report
    assert again.tools[0].files_divergence == 458
    assert again.tools[0].over_reports is True
    assert again.throughput_review_required is True


def test_tool_preflight_allows_none_counts() -> None:
    tool_preflight = ToolPreflight(
        tool="ty",
        version="ty 0.0.44",
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        self_reported_files=None,
        files_divergence=None,
    )
    assert tool_preflight.self_reported_files is None
    assert tool_preflight.scope_ok is True  # unverifiable count is allowed, not a mis-scope


def test_tool_preflight_captures_failure_diagnostics() -> None:
    # A failed{env} tool must carry enough to audit it (mirrors RunResult / §5.1).
    tool_preflight = ToolPreflight(
        tool="ty",
        version="ty 0.0.44",
        result_class=ResultClass.FAILED_ENV,
        real_exit_code=-1,
        signal=None,
        timed_out=False,
        oom=False,
        error_detail="ModuleNotFoundError: httpcore",
    )
    assert tool_preflight.result_class is ResultClass.FAILED_ENV
    assert tool_preflight.error_detail == "ModuleNotFoundError: httpcore"


class _CannedAdapter:
    """Fully annotated Adapter double for report assembly tests."""

    install_source = "fake"

    def __init__(self, name: str, result_class: ResultClass, files: int | None) -> None:
        self.name = name
        self._rc = result_class
        self._files = files

    def version(self, binary: str | None = None) -> str:
        return f"{self.name} 1.0"

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
        binary: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        return (["true"], {})

    def parallelism_cap(
        self, thread_mode: ThreadMode, cores: int, binary: str | None = None
    ) -> ParallelismCap:
        return ParallelismCap(mechanism="x", hard_cap=False)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        return (0, self._files)

    def classify(self, raw: RawRun) -> ResultClass:
        return self._rc

    def clear_cache(self, project: str) -> None:
        return None

    def prepare_command(self, project: str) -> str | None:
        return None


class _BrokenCommandAdapter(_CannedAdapter):
    """Adapter whose command construction raises."""

    @override
    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
        binary: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        msg = "cannot write config"
        raise OSError(msg)


def _fake_probe(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> RawRun:
    return RawRun(exit_code=0, signal=None, timed_out=False, oom=False, stdout="", stderr="")


def _failed_probe(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> RawRun:
    return RawRun(
        exit_code=-1,
        signal=None,
        timed_out=False,
        oom=False,
        stdout="",
        stderr="ModuleNotFoundError: httpcore",
        env_error=True,
    )


def _prepared_at(src: Path) -> PreparedProject:
    return PreparedProject(
        name="demo",
        checkout=str(src.parent),
        venv_python="/nonexistent/bin/python",
        src_roots=(str(src),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        python_platform="linux",
        sha="0" * 40,
        lock_hash="h",
        frozen=(),
        canonical_files=10,
        canonical_loc=100,
        fingerprint="fp",
    )


def test_preflight_records_divergence_and_ready(tmp_path: Path) -> None:
    prepared = _prepared_at(tmp_path)
    adapters: list[Adapter] = [
        _CannedAdapter("mypy", ResultClass.DIAGNOSTICS, files=500),
        _CannedAdapter("pyright", ResultClass.CLEAN, files=10),
        _CannedAdapter("ty", ResultClass.CLEAN, files=None),
    ]
    report = preflight_project(prepared, adapters, timeout=30, probe=_fake_probe)
    assert report.ready is True
    assert report.throughput_review_required is True
    by = {tool.tool: tool for tool in report.tools}
    assert by["mypy"].files_divergence == 490
    assert by["mypy"].over_reports is True
    assert by["mypy"].scope_ok is True
    assert by["pyright"].files_divergence == 0
    assert by["pyright"].over_reports is False
    assert by["ty"].self_reported_files is None
    assert by["ty"].files_divergence is None
    assert by["ty"].scope_ok is True


def test_preflight_mis_scope_blocks_ready(tmp_path: Path) -> None:
    prepared = _prepared_at(tmp_path)
    adapters: list[Adapter] = [
        _CannedAdapter("pyright", ResultClass.CLEAN, files=10),
        _CannedAdapter("ty", ResultClass.CLEAN, files=5),
    ]
    report = preflight_project(prepared, adapters, timeout=30, probe=_fake_probe)
    assert report.ready is False
    by = {tool.tool: tool for tool in report.tools}
    assert by["ty"].scope_ok is False
    assert by["ty"].files_divergence == -5
    assert by["pyright"].scope_ok is True


def test_preflight_not_ready_when_a_tool_fails(tmp_path: Path) -> None:
    prepared = _prepared_at(tmp_path)
    adapters: list[Adapter] = [
        _CannedAdapter("pyright", ResultClass.CLEAN, files=10),
        _CannedAdapter("ty", ResultClass.FAILED_ENV, files=None),
    ]
    report = preflight_project(prepared, adapters, timeout=30, probe=_failed_probe)
    assert report.ready is False
    by = {tool.tool: tool for tool in report.tools}
    assert by["ty"].result_class is ResultClass.FAILED_ENV
    assert by["ty"].real_exit_code == -1
    assert "ModuleNotFoundError" in (by["ty"].error_detail or "")


def test_preflight_records_command_construction_failure(tmp_path: Path) -> None:
    prepared = _prepared_at(tmp_path)
    adapters: list[Adapter] = [_BrokenCommandAdapter("pyright", ResultClass.CLEAN, files=10)]
    report = preflight_project(prepared, adapters, timeout=30, probe=_fake_probe)
    assert report.ready is False
    tool_preflight = report.tools[0]
    assert tool_preflight.result_class is ResultClass.FAILED_ENV
    assert tool_preflight.real_exit_code == -1
    assert "cannot write config" in (tool_preflight.error_detail or "")


def test_preflight_real_tools_on_clean_fixture(tmp_path: Path, fixtures_dir: Path) -> None:
    src = fixtures_dir / "pkg_project" / "pkg"
    fc = count_first_party([src], DEFAULT_EXCLUDES)
    prepared = PreparedProject(
        name="pkg",
        checkout=str(fixtures_dir / "pkg_project"),
        venv_python="",
        src_roots=(str(src),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        python_platform="linux",
        sha="0" * 40,
        lock_hash="h",
        frozen=(),
        canonical_files=fc.files,
        canonical_loc=fc.loc,
        fingerprint="fp",
    )
    pairs = (
        (MypyAdapter(), "mypy"),
        (PyrightAdapter(), "pyright"),
        (TyAdapter(), "ty"),
        (PyreflyAdapter(), "pyrefly"),
    )
    adapters: list[Adapter] = [adapter for adapter, name in pairs if shutil.which(name) is not None]
    if not adapters:
        pytest.skip("no real checkers installed")
    report = preflight_project(prepared, adapters, timeout=120)
    assert report.ready is True
    assert all(tool.result_class.is_measured_success for tool in report.tools)
    assert all(tool.scope_ok for tool in report.tools)
