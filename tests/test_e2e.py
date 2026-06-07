import os
from pathlib import Path

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode

requires_posix = pytest.mark.skipif(
    os.name != "posix", reason="signal semantics are POSIX-specific"
)


def _round_trip(result: RunResult, tmp_path: Path) -> RunResult:
    path = tmp_path / "r.json"
    path.write_text(result.model_dump_json())
    restored = RunResult.model_validate_json(path.read_text())
    assert restored == result
    return restored


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (StubAdapter(exit_code=0), ResultClass.CLEAN),
        (StubAdapter(exit_code=1, diagnostics=2, files=5), ResultClass.DIAGNOSTICS),
        (StubAdapter(exit_code=2), ResultClass.FAILED_CRASH),
        (StubAdapter(missing_binary=True), ResultClass.FAILED_ENV),
    ],
)
def test_pipeline_classes_round_trip_to_json(
    adapter: StubAdapter, expected: ResultClass, tmp_path: Path
) -> None:
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ONE_CORE,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == expected
    assert result.thread_mode_enforced is False  # recorded mode was not enforced (§5.3)
    restored = _round_trip(result, tmp_path)
    # Failures must be visible, never silently dropped (spec §12).
    if not expected.is_measured_success:
        assert restored.timing is None
        assert restored.result_class.value.startswith("failed")


def test_pipeline_records_timeout(tmp_path: Path) -> None:
    # Probe sleeps past the timeout -> failed{timeout}, no timing recorded.
    adapter = StubAdapter(exit_code=0, sleep=5.0)
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=1,
    )
    assert result.result_class == ResultClass.FAILED_TIMEOUT
    assert result.timing is None
    _round_trip(result, tmp_path)


@requires_posix
def test_pipeline_records_oom_heuristic(tmp_path: Path) -> None:
    # SIGKILL (9) is the OOM-killer's signal; mapped to failed{oom} until cgroup
    # OOM detection lands in Plan 4.
    adapter = StubAdapter(signal=9)
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_OOM
    assert result.timing is None
    _round_trip(result, tmp_path)
