import json

import pytest
from pydantic import ValidationError

from typebench.models import (
    CalibrationStats,
    EnvFingerprint,
    FailurePhase,
    MemoryStats,
    ResultClass,
    RunResult,
    ThreadMode,
    TimingStats,
)


def test_result_class_measured_success() -> None:
    assert ResultClass.CLEAN.is_measured_success
    assert ResultClass.DIAGNOSTICS.is_measured_success
    assert not ResultClass.FAILED_ENV.is_measured_success
    assert not ResultClass.FAILED_CRASH.is_measured_success
    assert not ResultClass.FAILED_TIMEOUT.is_measured_success
    assert not ResultClass.FAILED_OOM.is_measured_success


def test_result_class_values_match_taxonomy() -> None:
    # Spec §7 taxonomy strings, stable on disk.
    assert ResultClass.FAILED_ENV.value == "failed{env}"
    assert ResultClass.FAILED_TIMEOUT.value == "failed{timeout}"


def _env() -> EnvFingerprint:
    return EnvFingerprint(
        os="Linux",
        kernel="6.6.0",
        cpu_model="Test CPU",
        core_count=8,
        python_version="3.12.0",
    )


def test_run_result_round_trips_through_json() -> None:
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.DIAGNOSTICS,
        real_exit_code=1,
        diagnostics=3,
        files=10,
        timing=TimingStats(
            runs=3,
            min_s=0.10,
            median_s=0.11,
            mean_s=0.12,
            stddev_s=0.01,
            max_s=0.14,
            times_s=[0.10, 0.11, 0.14],
        ),
        env=_env(),
    )
    blob = result.model_dump_json()
    restored = RunResult.model_validate_json(blob)
    assert restored == result
    assert restored.schema_version == 2
    assert restored.thread_mode_enforced is False  # default; Plan 4 sets it true
    assert json.loads(blob)["result_class"] == "diagnostics"


def test_timing_is_optional_for_failures() -> None:
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_CRASH,
        real_exit_code=139,
        env=_env(),
    )
    assert result.timing is None
    assert result.diagnostics is None


def test_run_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate(
            {
                "tool": "stub",
                "tool_version": "1.0",
                "project": "demo",
                "thread_mode": "all-cores",
                "result_class": "clean",
                "real_exit_code": 0,
                "env": _env().model_dump(),
                "bogus": True,
            }
        )


def test_thread_mode_enforced_defaults_false() -> None:
    # Plan 1 records the requested thread_mode but applies no CPU affinity, so
    # the JSON must never claim a methodology that was not enforced (spec §5.3).
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ONE_CORE,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=_env(),
    )
    assert result.thread_mode_enforced is False


def test_failure_metadata_round_trips() -> None:
    # Enough detail to audit failed{env} vs failed{crash} after the fact (spec §5.1).
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_ENV,
        real_exit_code=-1,
        signal=None,
        timed_out=False,
        oom=False,
        error_detail="No such file or directory: 'typebench-nonexistent-checker'",
        env=_env(),
    )
    restored = RunResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.error_detail is not None
    assert restored.timing is None


def test_failure_phase_defaults_none_and_round_trips() -> None:
    # Measured-success carries no failure phase; a timing-phase failure records
    # FailurePhase.TIMING so real_exit_code (the clean probe's) is unambiguous.
    clean = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=_env(),
    )
    assert clean.failure_phase is None
    crashed = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_CRASH,
        real_exit_code=0,
        failure_phase=FailurePhase.TIMING,
        env=_env(),
    )
    restored = RunResult.model_validate_json(crashed.model_dump_json())
    assert restored.failure_phase == FailurePhase.TIMING


def test_run_result_v2_carries_memory_cpu_calibration() -> None:
    mem = MemoryStats(
        runs=3,
        peak_bytes_min=100,
        peak_bytes_median=110,
        peak_bytes_max=120,
        memory_stat={"anon": 90, "file": 10},
    )
    calib = CalibrationStats(
        workload_id="calib-pyloop-v1",
        iterations=5_000_000,
        runs=5,
        raw_min_s=0.30,
        raw_median_s=0.31,
        raw_max_s=0.33,
    )
    r = RunResult(
        tool="mypy",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ONE_CORE,
        thread_mode_enforced=True,
        hard_cap=True,
        cap_mechanism="single-process + cpu-affinity",
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        memory=mem,
        cpu_time_s=0.42,
        parallel_efficiency=0.95,
        calibration=calib,
        env=_env(),
    )
    assert r.schema_version == 2
    assert r.memory is not None and r.memory.peak_bytes_median == 110
    assert r.cpu_time_s == 0.42
    assert r.parallel_efficiency == 0.95
    assert r.calibration is not None and r.calibration.workload_id == "calib-pyloop-v1"
    assert r.hard_cap is True


def test_run_result_v2_defaults_are_none() -> None:
    # A capability-gated engine on a non-cgroup host produces a record with the new
    # fields absent — they must default to None, not break the schema.
    r = RunResult(
        tool="stub",
        tool_version="0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=_env(),
    )
    assert r.schema_version == 2
    assert r.memory is None
    assert r.cpu_time_s is None
    assert r.parallel_efficiency is None
    assert r.calibration is None
    assert r.hard_cap is None
    assert r.cap_mechanism is None
    assert r.thread_mode_enforced is False
