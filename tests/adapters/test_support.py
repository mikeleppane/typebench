from __future__ import annotations

from typebench._internal.test_fakes import FakeHost, fake_raw
from typebench.adapters._support import confirm_clean, probe_version
from typebench.adapters.stub import StubAdapter
from typebench.contracts.proc import RawRun
from typebench.contracts.taxonomy import ResultClass


def test_probe_version_stdout_wins() -> None:
    host = FakeHost({("tool", "--version"): fake_raw(stdout="tool 1.2.3\n", stderr="stderr\n")})

    assert probe_version(["tool", "--version"], host=host) == "tool 1.2.3"


def test_probe_version_stderr_fallback_when_stdout_empty() -> None:
    host = FakeHost({("tool", "--version"): fake_raw(stderr="stderr version\n")})

    assert probe_version(["tool", "--version"], host=host) == "stderr version"


def test_probe_version_unknown_when_both_streams_empty() -> None:
    host = FakeHost({("tool", "--version"): fake_raw()})

    assert probe_version(["tool", "--version"], host=host) == "unknown"


def test_probe_version_unknown_on_env_error() -> None:
    host = FakeHost({("tool", "--version"): fake_raw(stderr="missing", env_error=True)})

    assert probe_version(["tool", "--version"], host=host) == "unknown"


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
