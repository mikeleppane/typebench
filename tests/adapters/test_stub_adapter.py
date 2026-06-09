from pathlib import Path

import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.stub import StubAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.models import ResultClass, ThreadMode
from typebench.engine.wrapper import RawRun, classify_with_map, run_command


def _cfg() -> NormalizedConfig:
    return NormalizedConfig()


def test_stub_command_runs_and_reports_diagnostics(tmp_path: Path) -> None:
    adapter = StubAdapter(exit_code=1, diagnostics=4, files=9)
    argv, env = adapter.command("demo", _cfg(), ThreadMode.ALL_CORES, tmp_path)
    raw = run_command(argv, timeout=10, env=env)
    assert raw.exit_code == 1
    assert adapter.parse(raw.stdout, raw.stderr, raw.exit_code) == (4, 9)
    assert adapter.classify(raw) == ResultClass.DIAGNOSTICS


def test_stub_command_clean(tmp_path: Path) -> None:
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    argv, env = adapter.command("demo", _cfg(), ThreadMode.ALL_CORES, tmp_path)
    raw = run_command(argv, timeout=10, env=env)
    assert adapter.classify(raw) == ResultClass.CLEAN
    assert adapter.parse(raw.stdout, raw.stderr, raw.exit_code) == (0, 5)


def test_stub_missing_binary_is_env_failure(tmp_path: Path) -> None:
    adapter = StubAdapter(missing_binary=True)
    argv, env = adapter.command("demo", _cfg(), ThreadMode.ALL_CORES, tmp_path)
    raw = run_command(argv, timeout=10, env=env)
    assert raw.env_error is True
    assert adapter.classify(raw) == ResultClass.FAILED_ENV


def test_stub_satisfies_adapter_protocol() -> None:
    # runtime_checkable: the stub is a structural Adapter (catches drift early).
    assert isinstance(StubAdapter(), Adapter)


def test_stub_version_is_stable() -> None:
    assert StubAdapter().version() == "stub-1.0"


def test_stub_clear_cache_and_prepare_are_noops() -> None:
    adapter = StubAdapter()
    adapter.clear_cache("demo")  # must not raise
    assert adapter.prepare_command("demo") is None  # stateless: nothing to clear


@pytest.mark.parametrize("bad_output", ["5", "[1, 2, 3]", "null", "not json at all", ""])
def test_stub_parse_is_graceful_on_non_object_output(bad_output: str) -> None:
    # Valid-but-non-object JSON, unparsable text, and empty output must all
    # degrade to (None, None) rather than raising (real checkers print arbitrary
    # trailing lines). This is the template real adapters copy.
    assert StubAdapter().parse(bad_output, "", 0) == (None, None)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('{"diagnostics": 3, "files": 7}', (3, 7)),
        ('{"diagnostics": true, "files": 7}', (None, 7)),  # JSON bool is not a count
        ('{"diagnostics": "3", "files": 7}', (None, 7)),  # string is not a count
        ('{"diagnostics": 3.5, "files": 7}', (None, 7)),  # float is not a count
        ('{"files": 7}', (None, 7)),  # missing key -> None
    ],
)
def test_stub_parse_coerces_counts_to_int_or_none(
    line: str, expected: tuple[int | None, int | None]
) -> None:
    # An object with non-int field values must not leak garbage counts into the
    # record; each field coerces to int or None (template for real parsers).
    assert StubAdapter().parse(line, "", 0) == expected


def test_classify_with_map_honors_universal_prefix_then_exit_map() -> None:
    m = {0: ResultClass.CLEAN, 1: ResultClass.DIAGNOSTICS, 2: ResultClass.FAILED_ENV}
    assert classify_with_map(RawRun(0, None, False, False, "", ""), m) == ResultClass.CLEAN
    assert classify_with_map(RawRun(2, None, False, False, "", ""), m) == ResultClass.FAILED_ENV
    # unknown code -> crash floor
    assert classify_with_map(RawRun(9, None, False, False, "", ""), m) == ResultClass.FAILED_CRASH
    # universal prefix wins over the map
    assert classify_with_map(RawRun(0, None, True, False, "", ""), m) == ResultClass.FAILED_TIMEOUT
    assert (
        classify_with_map(RawRun(0, None, False, False, "", "", env_error=True), m)
        == ResultClass.FAILED_ENV
    )
    assert classify_with_map(RawRun(0, 9, False, False, "", ""), m) == ResultClass.FAILED_OOM
