import os
from pathlib import Path

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig

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
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == expected
    # ALL_CORES is unconstrained by design, so the 1-core affinity floor is never
    # applied -> thread_mode_enforced stays False on every host (§5.3, Decision D).
    # ONE_CORE enforcement (taskset-dependent) is covered in test_collector.py.
    assert result.thread_mode_enforced is False
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
        config=NormalizedConfig(),
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
    # SIGKILL (9) is the OOM-killer's signal; mapped to failed{oom} via the
    # heuristic. Plan 4 added authoritative cgroup OOM detection (measure.py,
    # memory.events.oom_kill), but this self-SIGKILL stub is NOT a cgroup OOM, so
    # it still exercises the signal-9 fallback heuristic that the non-cgroup path
    # (and any scoped run with oom_kill=0) relies on.
    adapter = StubAdapter(signal=9)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_OOM
    assert result.timing is None
    _round_trip(result, tmp_path)
