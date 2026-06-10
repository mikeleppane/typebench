import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from typebench.contracts.models import (
    CalibrationStats,
    EnvFingerprint,
    FailurePhase,
    LocDenominator,
    MemoryStats,
    Policy,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    TimingStats,
)

type EnvFactory = Callable[..., EnvFingerprint]


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


def test_run_result_round_trips_through_json(make_env: EnvFactory) -> None:
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
        env=make_env(),
    )
    blob = result.model_dump_json()
    restored = RunResult.model_validate_json(blob)
    assert restored == result
    assert restored.schema_version == 4
    assert restored.thread_mode_enforced is False  # default; Plan 4 sets it true
    assert json.loads(blob)["result_class"] == "diagnostics"


def test_timing_is_optional_for_failures(make_env: EnvFactory) -> None:
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_CRASH,
        real_exit_code=139,
        env=make_env(),
    )
    assert result.timing is None
    assert result.diagnostics is None


def test_run_result_rejects_unknown_fields(make_env: EnvFactory) -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate(
            {
                "tool": "stub",
                "tool_version": "1.0",
                "project": "demo",
                "thread_mode": "all-cores",
                "result_class": "clean",
                "real_exit_code": 0,
                "env": make_env().model_dump(),
                "bogus": True,
            }
        )


def test_runresult_schema_version_is_4() -> None:
    rec = RunResult(
        tool="mypy",
        tool_version="1.18.2",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=EnvFingerprint(
            os="linux", kernel="x", cpu_model="x", core_count=1, python_version="3.12"
        ),
    )
    assert rec.schema_version == 4
    # New identity/policy fields default for a manual (no-corpus) run.
    assert rec.checker_id is None
    assert rec.policy is Policy.STANDARD
    assert rec.headline_eligible is True  # standard policy is headline-eligible


def test_runresult_records_checker_id_and_policy_round_trip() -> None:
    rec = RunResult(
        tool="mypy",
        tool_version="1.19.0",
        checker_id="mypy@1.19.0+rc",
        policy=Policy.STANDARD,
        headline_eligible=True,
        project="httpx",
        thread_mode=ThreadMode.CONSTRAINED,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=EnvFingerprint(
            os="linux", kernel="x", cpu_model="x", core_count=1, python_version="3.12"
        ),
    )
    back = RunResult.model_validate_json(rec.model_dump_json())
    assert back.checker_id == "mypy@1.19.0+rc"
    assert back.policy is Policy.STANDARD
    assert back.schema_version == 4


def test_thread_mode_enforced_defaults_false(make_env: EnvFactory) -> None:
    # Plan 1 records the requested thread_mode but applies no CPU affinity, so
    # the JSON must never claim a methodology that was not enforced (spec §5.3).
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.CONSTRAINED,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
    )
    assert result.thread_mode_enforced is False


def test_failure_metadata_round_trips(make_env: EnvFactory) -> None:
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
        env=make_env(),
    )
    restored = RunResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.error_detail is not None
    assert restored.timing is None


def test_failure_phase_defaults_none_and_round_trips(make_env: EnvFactory) -> None:
    # Measured-success carries no failure phase; a timing-phase failure records
    # FailurePhase.TIMING so real_exit_code (the clean probe's) is unambiguous.
    clean = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
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
        env=make_env(),
    )
    restored = RunResult.model_validate_json(crashed.model_dump_json())
    assert restored.failure_phase == FailurePhase.TIMING


def test_run_result_v2_carries_memory_cpu_calibration(make_env: EnvFactory) -> None:
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
        thread_mode=ThreadMode.CONSTRAINED,
        thread_mode_enforced=True,
        hard_cap=True,
        cap_mechanism="single-process + cpu-affinity",
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        memory=mem,
        cpu_time_s=0.42,
        parallel_efficiency=0.95,
        calibration=calib,
        env=make_env(),
    )
    assert r.schema_version == 4
    assert r.memory is not None and r.memory.peak_bytes_median == 110
    assert r.cpu_time_s == 0.42
    assert r.parallel_efficiency == 0.95
    assert r.calibration is not None and r.calibration.workload_id == "calib-pyloop-v1"
    assert r.hard_cap is True


def test_run_result_v2_defaults_are_none(make_env: EnvFactory) -> None:
    # A capability-gated engine on a non-cgroup host produces a record with the new
    # fields absent — they must default to None, not break the schema.
    r = RunResult(
        tool="stub",
        tool_version="0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
    )
    assert r.schema_version == 4
    assert r.memory is None
    assert r.cpu_time_s is None
    assert r.parallel_efficiency is None
    assert r.calibration is None
    assert r.hard_cap is None
    assert r.cap_mechanism is None
    assert r.thread_mode_enforced is False


def test_env_fingerprint_expands_with_optional_runtime_fields() -> None:
    # New §9 fields default so existing hand-built fingerprints stay valid.
    base = EnvFingerprint(
        os="Linux", kernel="6.6", cpu_model="x", core_count=8, python_version="3.12.0"
    )
    assert base.node_version is None
    assert base.npm_version is None
    assert base.uv_version is None
    assert base.mem_total_bytes is None
    assert base.cgroup_v2 is False
    full = EnvFingerprint(
        os="Linux",
        kernel="6.6",
        cpu_model="x",
        core_count=8,
        python_version="3.12.0",
        node_version="v20.1.0",
        npm_version="10.2.0",
        uv_version="uv 0.4.0",
        mem_total_bytes=16_000_000_000,
        cgroup_v2=True,
    )
    assert EnvFingerprint.model_validate_json(full.model_dump_json()) == full


def test_run_result_enrichment_scalars_default_none_and_round_trip(make_env: EnvFactory) -> None:
    base = RunResult(
        tool="mypy",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
    )
    assert base.schema_version == 4
    for field in (
        base.project_sha,
        base.lock_hash,
        base.config_hash,
        base.tool_install_source,
        base.canonical_files,
        base.canonical_loc,
        base.canonical_code_loc,
        base.loc_denominator,
        base.over_reports,
    ):
        assert field is None
    rich = RunResult(
        tool="mypy",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
        project_sha="80960fa",
        lock_hash="abc123",
        config_hash="def456",
        tool_install_source="PyPI wheel (mypyc-compiled)",
        canonical_files=23,
        canonical_loc=4000,
        canonical_code_loc=3200,
        loc_denominator=LocDenominator.CODE,
        over_reports=False,
    )
    assert RunResult.model_validate_json(rich.model_dump_json()) == rich


@pytest.mark.parametrize(
    ("denominator", "expected_json"),
    [
        (LocDenominator.CODE, "code"),
        (LocDenominator.PHYSICAL, "physical"),
        (None, None),
    ],
)
def test_loc_denominator_serializes_to_stable_strings(
    make_env: EnvFactory,
    denominator: LocDenominator | None,
    expected_json: str | None,
) -> None:
    rec = RunResult(
        tool="mypy",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
        loc_denominator=denominator,
    )
    dumped = json.loads(rec.model_dump_json())
    assert dumped["loc_denominator"] == expected_json
    assert RunResult.model_validate_json(rec.model_dump_json()) == rec


def test_loc_denominator_coerces_on_disk_strings(make_env: EnvFactory) -> None:
    # The on-disk string coerces back to the enum: byte-identical contract.
    payload = {
        "tool": "mypy",
        "tool_version": "1.0",
        "project": "httpx",
        "thread_mode": "all-cores",
        "result_class": "clean",
        "real_exit_code": 0,
        "env": make_env().model_dump(),
        "loc_denominator": "code",
    }
    assert RunResult.model_validate(payload).loc_denominator is LocDenominator.CODE


def test_loc_denominator_rejects_out_of_domain(make_env: EnvFactory) -> None:
    payload = {
        "tool": "mypy",
        "tool_version": "1.0",
        "project": "httpx",
        "thread_mode": "all-cores",
        "result_class": "clean",
        "real_exit_code": 0,
        "env": make_env().model_dump(),
        "loc_denominator": "lines",  # not code|physical
    }
    with pytest.raises(ValidationError):
        RunResult.model_validate(payload)


def test_results_envelope_wraps_records(make_env: EnvFactory) -> None:
    rec = RunResult(
        tool="stub",
        tool_version="0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
    )
    env = ResultsEnvelope(
        suite_version="2026-06-08",
        generated_at="2026-06-08T00:00:00Z",
        runs=[rec],
    )
    assert env.schema_version == 1
    restored = ResultsEnvelope.model_validate_json(env.model_dump_json())
    assert restored == env
    assert len(restored.runs) == 1


def test_results_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ResultsEnvelope.model_validate(
            {"suite_version": "v", "generated_at": "t", "runs": [], "bogus": 1}
        )
