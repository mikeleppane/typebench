import shutil
import subprocess
from pathlib import Path

import pytest

from typebench import collector
from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import FailurePhase, ResultClass, ThreadMode


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


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_single_records_timing_phase_failure(tmp_path: Path) -> None:
    # Probe succeeds (clean) but a timed run fails under hyperfine -> the record
    # must be a recorded failure, NOT an uncaught crash with no result (§5.1/§12).
    state = tmp_path / "count"
    adapter = StubAdapter(state_file=str(state), fail_after_runs=1)
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.failure_phase == FailurePhase.TIMING
    assert result.timing is None
    assert result.error_detail  # carries hyperfine's stderr (audit trail)
    assert result.diagnostics is None


def test_run_single_timing_crash_marks_timing_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    # Probe is clean (exit 0) but the timed run crashes under hyperfine. The
    # record must be failed{crash} AND failure_phase=timing so real_exit_code (the
    # clean probe's 0) cannot be misread as "clean command, failed result".
    monkeypatch.setattr(collector.shutil, "which", lambda _name: "/usr/bin/hyperfine")

    def _boom(*_a: object, **_k: object) -> object:
        raise subprocess.CalledProcessError(1, "hyperfine", stderr="timed run died")

    monkeypatch.setattr(collector, "run_timing", _boom)
    result = run_single(
        StubAdapter(exit_code=0),
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.failure_phase == FailurePhase.TIMING
    assert result.real_exit_code == 0  # the clean probe's exit, now disambiguated
    assert result.error_detail == "timed run died"
    assert result.timing is None
    assert result.diagnostics is None


def test_run_single_timing_harness_error_is_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A garbled/empty hyperfine JSON (ValueError) or vanished export file (OSError)
    # is a HARNESS failure, not a checker crash. Record failed{env}, never drop it.
    monkeypatch.setattr(collector.shutil, "which", lambda _name: "/usr/bin/hyperfine")

    def _boom(*_a: object, **_k: object) -> object:
        raise ValueError("hyperfine JSON has no results")

    monkeypatch.setattr(collector, "run_timing", _boom)
    result = run_single(
        StubAdapter(exit_code=0),
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.failure_phase == FailurePhase.TIMING
    assert result.error_detail
    assert result.timing is None
