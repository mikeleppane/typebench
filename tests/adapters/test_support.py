from __future__ import annotations

import subprocess
from typing import Never

from typebench.adapters._support import confirm_clean, probe_version
from typebench.adapters.stub import StubAdapter
from typebench.contracts.taxonomy import ResultClass
from typebench.engine.wrapper import RawRun


def _completed(stdout: str, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["tool", "--version"], 0, stdout=stdout, stderr=stderr)


def test_probe_version_stdout_wins() -> None:
    def runner(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return _completed("tool 1.2.3\n", "stderr version\n")

    assert probe_version(["tool", "--version"], runner=runner) == "tool 1.2.3"


def test_probe_version_stderr_fallback_when_stdout_empty() -> None:
    def runner(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return _completed("", "stderr version\n")

    assert probe_version(["tool", "--version"], runner=runner) == "stderr version"


def test_probe_version_unknown_when_both_streams_empty() -> None:
    def runner(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return _completed("", "")

    assert probe_version(["tool", "--version"], runner=runner) == "unknown"


def test_probe_version_unknown_on_oserror() -> None:
    def runner(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> Never:
        raise OSError("missing")

    assert probe_version(["tool", "--version"], runner=runner) == "unknown"


def test_confirm_clean_truth_table() -> None:
    assert confirm_clean(5, tolerate_unknown=False) == ResultClass.CLEAN
    assert confirm_clean(0, tolerate_unknown=False) == ResultClass.FAILED_ENV
    assert confirm_clean(None, tolerate_unknown=False) == ResultClass.FAILED_ENV
    assert confirm_clean(5, tolerate_unknown=True) == ResultClass.CLEAN
    assert confirm_clean(0, tolerate_unknown=True) == ResultClass.FAILED_ENV
    assert confirm_clean(None, tolerate_unknown=True) == ResultClass.CLEAN


def test_stub_adapter_clean_exit_does_not_require_files_count() -> None:
    raw = RawRun(0, None, False, False, "", "")

    assert StubAdapter().classify(raw) == ResultClass.CLEAN
