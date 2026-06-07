import shutil

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, ThreadMode


def test_run_single_failure_skips_timing() -> None:
    adapter = StubAdapter(exit_code=2)  # -> FAILED_CRASH
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.real_exit_code == 2
    assert result.timing is None
    assert result.tool == "stub"
    assert result.env.core_count >= 1
    assert result.thread_mode_enforced is False  # no affinity applied (§5.3)


def test_run_single_env_failure_is_recorded() -> None:
    # Missing binary -> failed{env}, captured (not raised), with an audit trail.
    adapter = StubAdapter(missing_binary=True)
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.timing is None
    assert result.error_detail  # carries the OSError text


def test_run_single_diagnostics_records_counts() -> None:
    adapter = StubAdapter(exit_code=1, diagnostics=3, files=7)
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.DIAGNOSTICS
    assert result.diagnostics == 3
    assert result.files == 7


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_single_success_includes_timing() -> None:
    adapter = StubAdapter(exit_code=0, files=4, sleep=0.02)
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=3,
        timeout=30,
    )
    assert result.result_class == ResultClass.CLEAN
    assert result.timing is not None
    assert result.timing.runs == 3
    assert result.timing.min_s > 0
