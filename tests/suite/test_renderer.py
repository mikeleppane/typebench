from collections.abc import Callable
from typing import cast

import pytest

from typebench.contracts.models import (
    CalibrationStats,
    EnvFingerprint,
    MemoryStats,
    ResultsEnvelope,
    RunResult,
    TimingStats,
)
from typebench.contracts.runconfig import RunConfig
from typebench.contracts.taxonomy import FailurePhase, LocDenominator, ResultClass, ThreadMode
from typebench.suite.renderer import (
    _FAILURE_DETAIL_MAX_CHARS,
    _ab_display,
    _code_loc_or_withheld,
    _files_degraded,
    _provenance,
    build_report_html,
    build_trends,
    cpu_model_anchors,
    render_ab,
    render_compare,
    render_readme,
    render_terminal,
)

type EnvFactory = Callable[..., EnvFingerprint]


def _record(
    tool: str, wall: float, peak: int, make_env: EnvFactory, over: bool = False
) -> RunResult:
    return RunResult(
        tool=tool,
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=3,
            min_s=wall,
            median_s=wall,
            mean_s=wall,
            stddev_s=0.0,
            max_s=wall,
            times_s=[wall],
        ),
        memory=MemoryStats(
            runs=3,
            peak_bytes_min=peak,
            peak_bytes_median=peak,
            peak_bytes_max=peak,
        ),
        cpu_time_s=wall,
        parallel_efficiency=1.0,
        canonical_files=23,
        canonical_loc=4000,
        canonical_code_loc=3200,
        loc_denominator=LocDenominator.CODE,
        over_reports=over,
        env=make_env(),
    )


def test_provenance_names_machine_and_warns(make_env: EnvFactory) -> None:
    rec = _record("mypy", 1.0, 100, make_env)
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[rec])
    out = render_readme(env)
    assert "Test CPU" in out
    assert "8 cores" in out
    assert "machine-specific" in out


def test_provenance_empty_when_no_runs() -> None:
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[])
    assert _provenance(env) == ""
    assert "machine-specific" not in render_readme(env)


def test_provenance_without_run_config_omits_run_counts(make_env: EnvFactory) -> None:
    rec = _record("mypy", 1.0, 100, make_env)
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[rec])
    out = render_readme(env)
    assert "Test CPU" in out
    assert "timed runs" not in out


def test_provenance_includes_run_counts_from_config(make_env: EnvFactory) -> None:
    rec = _record("mypy", 1.0, 100, make_env)
    cfg = RunConfig(checkers=(), runs=5, warmup=2)
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[rec], run_config=cfg)
    assert "5 timed runs, 2 warmup" in render_readme(env)


def test_provenance_in_terminal(make_env: EnvFactory) -> None:
    rec = _record("mypy", 1.0, 100, make_env)
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[rec])
    assert "machine-specific" in render_terminal(env)


def test_render_readme_table_is_fastest_first_and_excludes_diagnostics(
    make_env: EnvFactory,
) -> None:
    env = ResultsEnvelope(
        suite_version="2026-06-08",
        generated_at="2026-06-08T00:00:00Z",
        runs=[
            _record("mypy", 2.0, 200_000_000, make_env),
            _record("ty", 0.5, 400_000_000, make_env),
        ],
    )
    md = render_readme(env)
    # fastest-first: ty (0.5s) before mypy (2.0s)
    assert md.index("| ty@") < md.index("| mypy@")
    # diagnostics is NOT a column (spec §8)
    assert "diagnostics" not in md.lower()
    # code-LOC throughput present (3200 LOC / 0.5 s = 6.4 kLOC/s)
    assert "6.4" in md


def test_render_readme_subtracts_harness_baselines_without_mutating_raw(
    make_env: EnvFactory,
) -> None:
    record = _record("ty", 0.050, 54_000_000, make_env)
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[record],
        harness_mem_baseline_bytes=14_000_000,
        harness_wall_overhead_s=0.010,
    )

    md = render_readme(env)

    assert "| ty@1.0 | 0.040 |" in md  # corrected all-cores wall
    assert "| 40.0 | 80.0 |" in md  # corrected mem 40.0 + 3200 LOC / 0.040 s
    assert "80.0" in md
    assert record.timing is not None and record.timing.median_s == 0.050
    assert record.memory is not None and record.memory.peak_bytes_median == 54_000_000


def test_render_readme_without_baselines_keeps_legacy_raw_display(
    make_env: EnvFactory,
) -> None:
    env = ResultsEnvelope(
        suite_version="v", generated_at="t", runs=[_record("ty", 0.050, 54_000_000, make_env)]
    )

    md = render_readme(env)

    assert "| ty@1.0 | 0.050 |" in md  # raw all-cores wall (no baselines)
    assert "| 54.0 | 64.0 |" in md  # raw mem 54.0 + 3200 LOC / 0.050 s
    assert "64.0" in md


def test_render_readme_marks_rows_with_swap_observed(make_env: EnvFactory) -> None:
    record = _record("mypy", 2.0, 200_000_000, make_env).model_copy(
        update={
            "memory": MemoryStats(
                runs=3,
                peak_bytes_min=190_000_000,
                peak_bytes_median=200_000_000,
                peak_bytes_max=210_000_000,
                swap_peak_bytes_min=0,
                swap_peak_bytes_median=4096,
                swap_peak_bytes_max=4096,
                mem_under_swap=True,
            )
        }
    )
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[record])

    md = render_readme(env)

    assert "200.0!" in md
    assert "`!` = swap observed" in md


def test_render_readme_withholds_throughput_for_over_reporters(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[_record("ty", 0.5, 1, make_env, over=True)],
    )
    md = render_readme(env)
    # over_reports -> kLOC/s withheld with the asterisk caveat, not a number
    assert "—*" in md or "n/a*" in md


def test_render_readme_shows_failed_cells_as_didnt_compete(make_env: EnvFactory) -> None:
    failed = RunResult(
        tool="pyright",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_ENV,
        real_exit_code=3,
        env=make_env(),
    )
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[failed])
    md = render_readme(env)
    assert "failed{env}" in md


def _record_for_trends(
    tool: str, wall: float, calib_med: float, cpu: str, make_env: EnvFactory
) -> RunResult:
    return RunResult(
        tool=tool,
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=1,
            min_s=wall,
            median_s=wall,
            mean_s=wall,
            stddev_s=0.0,
            max_s=wall,
            times_s=[wall],
        ),
        memory=MemoryStats(
            runs=1,
            peak_bytes_min=1,
            peak_bytes_median=200_000_000,
            peak_bytes_max=1,
        ),
        canonical_code_loc=3200,
        loc_denominator=LocDenominator.CODE,
        over_reports=False,
        calibration=CalibrationStats(
            workload_id="calib-pyloop-v1",
            iterations=1,
            runs=1,
            raw_min_s=calib_med,
            raw_median_s=calib_med,
            raw_max_s=calib_med,
        ),
        env=make_env(cpu_model=cpu),
    )


def _record_for_trends_spread(
    min_s: float,
    median_s: float,
    max_s: float,
    stddev_s: float,
    calib_med: float | None,
    cpu: str,
    make_env: EnvFactory,
    runs: int = 3,
) -> RunResult:
    calibration = (
        CalibrationStats(
            workload_id="calib-pyloop-v1",
            iterations=1,
            runs=1,
            raw_min_s=calib_med,
            raw_median_s=calib_med,
            raw_max_s=calib_med,
        )
        if calib_med is not None
        else None
    )
    return RunResult(
        tool="mypy",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=runs,
            min_s=min_s,
            median_s=median_s,
            mean_s=median_s,
            stddev_s=stddev_s,
            max_s=max_s,
            times_s=[median_s] if runs == 1 else [min_s, median_s, max_s],
        ),
        memory=MemoryStats(
            runs=3,
            peak_bytes_min=1,
            peak_bytes_median=200_000_000,
            peak_bytes_max=200_000_000,
        ),
        canonical_code_loc=3200,
        loc_denominator=LocDenominator.CODE,
        over_reports=False,
        calibration=calibration,
        env=make_env(cpu_model=cpu),
    )


def _record_versioned(
    checker_id: str,
    tool: str,
    wall: float,
    make_env: EnvFactory,
    *,
    thread_mode: ThreadMode = ThreadMode.ALL_CORES,
    cores: int | None = None,
) -> RunResult:
    return RunResult(
        tool=tool,
        tool_version=checker_id.split("@", 1)[1],
        checker_id=checker_id,
        project="httpx",
        thread_mode=thread_mode,
        cores=cores,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=1,
            min_s=wall,
            median_s=wall,
            mean_s=wall,
            stddev_s=0.0,
            max_s=wall,
            times_s=[wall],
        ),
        memory=MemoryStats(
            runs=1,
            peak_bytes_min=1,
            peak_bytes_median=200_000_000,
            peak_bytes_max=200_000_000,
        ),
        canonical_code_loc=3200,
        loc_denominator=LocDenominator.CODE,
        over_reports=False,
        env=make_env(),
    )


def _envelope(gen: str, *records: RunResult) -> ResultsEnvelope:
    return ResultsEnvelope(suite_version="v", generated_at=gen, runs=list(records))


def _loc_record(
    make_env: EnvFactory, denominator: LocDenominator | None, *, code_loc: int, phys_loc: int
) -> RunResult:
    return RunResult(
        tool="mypy",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=make_env(),
        canonical_code_loc=code_loc,
        canonical_loc=phys_loc,
        loc_denominator=denominator,
    )


def _trend_points(trends: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", trends["points"])


def _trend_failures(trends: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", trends["failures"])


def _corpus_markers(trends: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", trends["corpus_markers"])


def _cpu_models(trends: dict[str, object]) -> list[str]:
    return cast("list[str]", trends["cpu_models"])


def _failed_record(
    tool: str,
    make_env: EnvFactory,
    result_class: ResultClass,
    *,
    updates: dict[str, object] | None = None,
) -> RunResult:
    payload: dict[str, object] = {
        "tool": tool,
        "tool_version": "1.0",
        "project": "httpx",
        "thread_mode": ThreadMode.ALL_CORES,
        "result_class": result_class,
        "real_exit_code": 2,
        "canonical_code_loc": 3200,
        "loc_denominator": LocDenominator.CODE,
        "env": make_env(),
    }
    if updates is not None:
        payload.update(updates)
    return RunResult.model_validate(payload)


@pytest.mark.parametrize(
    ("denominator", "expected"),
    [
        (LocDenominator.CODE, 4000),  # only code-LOC feeds the headline column
        (LocDenominator.PHYSICAL, None),  # physical fallback is withheld, not mislabeled
        (None, None),  # unknown denominator is withheld
    ],
)
def test_code_loc_withholds_non_code_denominators(
    make_env: EnvFactory, denominator: LocDenominator | None, expected: int | None
) -> None:
    record = _loc_record(make_env, denominator, code_loc=4000, phys_loc=9000)
    assert _code_loc_or_withheld(record) == expected


def test_cpu_model_anchors_take_earliest_per_model(make_env: EnvFactory) -> None:
    history = [
        _envelope("2026-02-01", _record_for_trends("mypy", 1.0, 0.40, "CPU-A", make_env)),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.20, "CPU-A", make_env)),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.50, "CPU-B", make_env)),
    ]
    anchors = cpu_model_anchors(history)
    assert anchors["CPU-A"] == 0.40
    assert anchors["CPU-B"] == 0.50


def test_build_trends_normalizes_against_anchor(make_env: EnvFactory) -> None:
    history = [
        _envelope("2026-02-01", _record_for_trends("mypy", 1.0, 0.40, "CPU-A", make_env)),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.20, "CPU-A", make_env)),
    ]
    trends = build_trends(history)
    points = [p for p in _trend_points(trends) if p["date"] == "2026-03-01"]
    assert len(points) == 1
    p = points[0]
    assert p["wall_median_s"] == 1.0
    wall_norm = p["wall_median_s_norm"]
    assert isinstance(wall_norm, float)
    assert abs(wall_norm - 2.0) < 1e-9
    anchor_point = next(p for p in _trend_points(trends) if p["date"] == "2026-02-01")
    anchor_wall_norm = anchor_point["wall_median_s_norm"]
    assert isinstance(anchor_wall_norm, float)
    assert abs(anchor_wall_norm - 1.0) < 1e-9


def test_build_trends_includes_kloc_and_corpus_markers(make_env: EnvFactory) -> None:
    history = [_envelope("2026-02-01", _record_for_trends("mypy", 2.0, 0.40, "CPU-A", make_env))]
    trends = build_trends(history)
    p = _trend_points(trends)[0]
    kloc_s = p["kloc_s"]
    assert isinstance(kloc_s, float)
    assert abs(kloc_s - 1.6) < 1e-9
    assert _corpus_markers(trends)[0]["suite_version"] == "v"
    assert "CPU-A" in _cpu_models(trends)
    # code_loc + size_tier drive the site's projects-by-checkers matrix tiering.
    assert p["code_loc"] == 3200
    assert p["size_tier"] == "Small"


def test_build_trends_uses_harness_corrected_values(make_env: EnvFactory) -> None:
    history = [
        ResultsEnvelope(
            suite_version="v",
            generated_at="2026-02-01",
            runs=[_record_for_trends("mypy", 0.050, 0.40, "CPU-A", make_env)],
            harness_mem_baseline_bytes=14_000_000,
            harness_wall_overhead_s=0.010,
        )
    ]

    point = _trend_points(build_trends(history))[0]

    assert point["wall_median_s"] == 0.04
    assert point["peak_mem_mb"] == 186.0
    assert point["kloc_s"] == 80.0


def test_build_trends_success_point_carries_harness_corrected_wall_spread(
    make_env: EnvFactory,
) -> None:
    record = _record_for_trends_spread(
        min_s=0.015,
        median_s=0.050,
        max_s=0.090,
        stddev_s=0.007,
        calib_med=0.40,
        cpu="CPU-A",
        make_env=make_env,
    )
    history = [
        ResultsEnvelope(
            suite_version="v",
            generated_at="2026-02-01",
            runs=[record],
            harness_wall_overhead_s=0.020,
        )
    ]

    point = _trend_points(build_trends(history))[0]

    assert point["wall_min_s"] == 0.0
    assert point["wall_median_s"] == pytest.approx(0.030)
    assert point["wall_max_s"] == pytest.approx(0.070)
    assert point["wall_stddev_s"] == 0.007


def test_build_trends_normalizes_wall_spread_against_anchor(make_env: EnvFactory) -> None:
    history = [
        _envelope(
            "2026-02-01",
            _record_for_trends_spread(
                min_s=1.0,
                median_s=2.0,
                max_s=3.0,
                stddev_s=0.2,
                calib_med=0.50,
                cpu="CPU-A",
                make_env=make_env,
            ),
        ),
        _envelope(
            "2026-03-01",
            _record_for_trends_spread(
                min_s=1.0,
                median_s=2.0,
                max_s=3.0,
                stddev_s=0.2,
                calib_med=0.25,
                cpu="CPU-A",
                make_env=make_env,
            ),
        ),
    ]

    point = next(p for p in _trend_points(build_trends(history)) if p["date"] == "2026-03-01")

    assert point["wall_min_s_norm"] == pytest.approx(2.0)
    assert point["wall_max_s_norm"] == pytest.approx(6.0)
    assert point["wall_stddev_s_norm"] == pytest.approx(0.4)


def test_build_trends_wall_spread_norm_is_null_without_calibration(
    make_env: EnvFactory,
) -> None:
    history = [
        _envelope(
            "2026-02-01",
            _record_for_trends_spread(
                min_s=1.0,
                median_s=2.0,
                max_s=3.0,
                stddev_s=0.2,
                calib_med=None,
                cpu="CPU-A",
                make_env=make_env,
            ),
        )
    ]

    point = _trend_points(build_trends(history))[0]

    assert point["wall_min_s_norm"] is None
    assert point["wall_max_s_norm"] is None
    assert point["wall_stddev_s_norm"] is None


def test_build_trends_single_run_wall_spread_remains_degenerate(
    make_env: EnvFactory,
) -> None:
    history = [
        _envelope(
            "2026-02-01",
            _record_for_trends_spread(
                min_s=1.25,
                median_s=1.25,
                max_s=1.25,
                stddev_s=0.0,
                calib_med=0.40,
                cpu="CPU-A",
                make_env=make_env,
                runs=1,
            ),
        )
    ]

    point = _trend_points(build_trends(history))[0]

    assert point["wall_stddev_s"] == 0.0
    assert point["wall_min_s"] == point["wall_max_s"] == 1.25


def test_build_trends_failed_crash_lands_in_failures_not_points(
    make_env: EnvFactory,
) -> None:
    success = _record_versioned("mypy@1.0", "mypy", 1.0, make_env)
    failure = _failed_record(
        "pyright",
        make_env,
        ResultClass.FAILED_CRASH,
        updates={"checker_id": "pyright@1.0", "project": "httpx", "real_exit_code": 2},
    )

    trends = build_trends([_envelope("2026-02-01", success, failure)])

    assert [p["checker_id"] for p in _trend_points(trends)] == ["mypy@1.0"]
    assert [f["checker_id"] for f in _trend_failures(trends)] == ["pyright@1.0"]


def test_build_trends_failure_carries_identity_fields(make_env: EnvFactory) -> None:
    failure = _failed_record(
        "ty",
        make_env,
        ResultClass.FAILED_CRASH,
        updates={
            "project": "django",
            "checker_id": "ty@1.0+new",
            "thread_mode": ThreadMode.CONSTRAINED,
            "cores": 4,
            "canonical_code_loc": 30_000,
            "real_exit_code": 9,
        },
    )

    trends = build_trends([_envelope("2026-02-01", failure)])
    failed = _trend_failures(trends)[0]

    assert failed["date"] == "2026-02-01"
    assert failed["suite_version"] == "v"
    assert failed["project"] == "django"
    assert failed["checker_id"] == "ty@1.0+new"
    assert failed["tool"] == "ty"
    assert failed["version"] == "1.0"
    assert failed["label"] == "ty@1.0+new"
    assert failed["thread_mode"] == "constrained"
    assert failed["cores"] == 4
    assert failed["code_loc"] == 30_000
    assert failed["size_tier"] == "Medium"
    assert failed["result_class"] == "failed{crash}"
    assert failed["real_exit_code"] == 9


def test_build_trends_failure_carries_metadata_and_truncates_detail(
    make_env: EnvFactory,
) -> None:
    long_detail = "x" * (_FAILURE_DETAIL_MAX_CHARS + 10)
    oom = _failed_record(
        "mypy",
        make_env,
        ResultClass.FAILED_OOM,
        updates={
            "oom": True,
            "failure_phase": FailurePhase.TIMING,
            "error_detail": long_detail,
        },
    )
    timeout = _failed_record(
        "pyright",
        make_env,
        ResultClass.FAILED_TIMEOUT,
        updates={
            "signal": 15,
            "timed_out": True,
            "failure_phase": FailurePhase.PROBE,
        },
    )

    trends = build_trends([_envelope("2026-02-01", oom, timeout)])
    by_class = {f["result_class"]: f for f in _trend_failures(trends)}

    failed_oom = by_class["failed{oom}"]
    assert failed_oom["oom"] is True
    assert failed_oom["timed_out"] is False
    assert failed_oom["failure_phase"] == "timing"
    assert failed_oom["error_detail"] == ("x" * _FAILURE_DETAIL_MAX_CHARS) + "…"

    failed_timeout = by_class["failed{timeout}"]
    assert failed_timeout["timed_out"] is True
    assert failed_timeout["oom"] is False
    assert failed_timeout["signal"] == 15
    assert failed_timeout["failure_phase"] == "probe"


def test_build_trends_all_success_has_empty_failures(make_env: EnvFactory) -> None:
    trends = build_trends(
        [_envelope("2026-02-01", _record_versioned("mypy@1.0", "mypy", 1.0, make_env))]
    )

    assert _trend_failures(trends) == []


def test_build_trends_success_points_carry_result_class(make_env: EnvFactory) -> None:
    trends = build_trends(
        [_envelope("2026-02-01", _record_versioned("mypy@1.0", "mypy", 1.0, make_env))]
    )

    assert _trend_points(trends)[0]["result_class"] == "clean"


def test_build_trends_distinguishes_same_day_versions(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="2026-06-10T00:00:00Z",
        runs=[
            _record_versioned("mypy@1.18.2", "mypy", 2.0, make_env),
            _record_versioned("mypy@1.19.0", "mypy", 1.5, make_env),
        ],
    )
    trends = build_trends([env])
    points = _trend_points(trends)
    ids = {p["checker_id"] for p in points}
    assert ids == {"mypy@1.18.2", "mypy@1.19.0"}
    by_id = {p["checker_id"]: p for p in points}
    assert by_id["mypy@1.18.2"]["version"] == "1.18.2"
    assert by_id["mypy@1.19.0"]["tool"] == "mypy"


def test_render_readme_labels_rows_by_checker_id(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[
            _record_versioned("mypy@1.18.2", "mypy", 2.0, make_env),
            _record_versioned("mypy@1.19.0", "mypy", 1.5, make_env),
        ],
    )
    md = render_readme(env)
    assert "| Checker | All-cores |" in md
    assert "mypy@1.18.2" in md
    assert "mypy@1.19.0" in md


def test_render_readme_bolds_best_cell_per_column(make_env: EnvFactory) -> None:
    # Two checkers on one project: ty is faster (better wall + kLOC/s), mypy uses less
    # memory. Each column's winner is bold; the loser is not.
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[
            _record("ty", 0.5, 100_000_000, make_env),  # faster, more memory
            _record("mypy", 2.0, 50_000_000, make_env),  # slower, less memory
        ],
    )
    md = render_readme(env)
    assert "**0.500**" in md  # ty wins all-cores wall
    assert "**50.0**" in md  # mypy wins peak mem
    assert "**6.4**" in md  # ty wins kLOC/s (3200 / 0.5)
    assert "**2.000**" not in md  # mypy's slower wall is not bold


def test_render_readme_does_not_bold_a_lone_value(make_env: EnvFactory) -> None:
    # A single checker has no competitor, so none of its cells are bold (the footnote
    # legitimately contains its own ** markers, so check the cell values, not the page).
    env = ResultsEnvelope(
        suite_version="v", generated_at="t", runs=[_record("ty", 0.5, 100_000_000, make_env)]
    )
    md = render_readme(env)
    assert "**0.500**" not in md  # lone wall
    assert "**100.0**" not in md  # lone mem
    assert "**6.4**" not in md  # lone kLOC/s


def test_render_readme_formats_generated_timestamp(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="2026-06-10",
        generated_at="2026-06-11T09:10:55.399560+00:00",
        runs=[_record("ty", 0.5, 100_000_000, make_env)],
    )
    md = render_readme(env)
    assert "Corpus snapshot 2026-06-10 · measured 2026-06-11 09:10 UTC" in md
    assert "09:10:55.399560" not in md  # the raw microsecond stamp is gone


def test_render_readme_folds_constrained_cores_into_columns(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                2.0,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=1,
            ),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                1.5,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=4,
            ),
        ],
    )
    md = render_readme(env)
    assert "#### httpx" in md
    assert "| 1c | 4c | 8c |" in md  # the constrained sweep folded into columns
    # one folded row per checker, not one section per core count
    assert md.count("| mypy@1.19.0 |") == 1
    assert "2.000" in md and "1.500" in md  # 1-core and 4-core walls in the same row


def test_compact_table_renders_constrained_cores_from_run_config(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        run_config=RunConfig(checkers=(), cores=(1, 2, 16)),
        runs=[
            _record_versioned("mypy@1.19.0", "mypy", 0.8, make_env),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                2.0,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=1,
            ),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                1.7,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=2,
            ),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                1.2,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=16,
            ),
        ],
    )

    lines = render_readme(env).splitlines()
    header_index = lines.index("| Checker | All-cores | 1c | 2c | 16c | Peak mem (MB) | kLOC/s |")

    assert "| 4c |" not in lines[header_index]
    assert "| 8c |" not in lines[header_index]
    assert lines[header_index + 1].count("--:") == 6
    # The footnote must not name a fixed sweep either, or it goes stale for
    # non-default cores; the whole render carries no 4c/8c token.
    full = render_readme(env)
    assert "4c" not in full
    assert "8c" not in full


def test_compact_table_falls_back_to_default_cores_without_run_config(
    make_env: EnvFactory,
) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        run_config=None,
        runs=[
            _record_versioned("mypy@1.19.0", "mypy", 0.8, make_env),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                2.0,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=1,
            ),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                1.5,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=4,
            ),
            _record_versioned(
                "mypy@1.19.0",
                "mypy",
                1.3,
                make_env,
                thread_mode=ThreadMode.CONSTRAINED,
                cores=8,
            ),
        ],
    )

    assert "| Checker | All-cores | 1c | 4c | 8c | Peak mem (MB) | kLOC/s |" in render_readme(env)


def test_compact_table_sweep_only_drops_all_cores_and_sources_top_core(
    make_env: EnvFactory,
) -> None:
    """A constrained-only envelope (cores sweep, no all-cores pass) drops the
    All-cores column and anchors Peak mem + kLOC/s on the highest-core pass."""
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        run_config=RunConfig(checkers=(), cores=(1, 4, 16)),
        runs=[
            _record_versioned(
                "ty@0.0.50", "ty", w, make_env, thread_mode=ThreadMode.CONSTRAINED, cores=c
            )
            for w, c in ((2.0, 1), (1.0, 4), (0.5, 16))
        ]
        + [
            _record_versioned(
                "mypy@2.1.0", "mypy", w, make_env, thread_mode=ThreadMode.CONSTRAINED, cores=c
            )
            for w, c in ((4.0, 1), (3.0, 4), (2.5, 16))
        ],
    )
    md = render_readme(env)
    assert "| Checker | 1c | 4c | 16c | Peak mem (MB) | kLOC/s |" in md
    assert "All-cores" not in md
    # kLOC/s is anchored on the 16-core wall: ty 3200 LOC / 0.5s = 6.4 kLOC/s, and it
    # wins the column (mypy is slower), so it is bold.
    assert "**6.4**" in md
    # The footnote names the highest-core pass as the mem/kLOC source, not all-cores.
    assert "from the 16-core pass" in md
    assert "all-cores" not in md.lower()


def _cmp_record(checker_id: str, wall: float, peak: int, make_env: EnvFactory) -> RunResult:
    tool, version = checker_id.split("@", 1)
    return RunResult(
        tool=tool,
        tool_version=version,
        checker_id=checker_id,
        project="sqlalchemy",
        thread_mode=ThreadMode.CONSTRAINED,
        cores=4,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=1,
            min_s=wall,
            median_s=wall,
            mean_s=wall,
            stddev_s=0.0,
            max_s=wall,
            times_s=[wall],
        ),
        memory=MemoryStats(
            runs=1,
            peak_bytes_min=peak,
            peak_bytes_median=peak,
            peak_bytes_max=peak,
        ),
        canonical_code_loc=200_000,
        loc_denominator=LocDenominator.CODE,
        over_reports=False,
        env=make_env(),
    )


def test_render_compare_baseline_is_first_spec_others_are_deltas(
    make_env: EnvFactory,
) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[
            _cmp_record("mypy@1.14.1", 6.27, 289_000_000, make_env),
            _cmp_record("mypy@1.15.0", 5.81, 301_000_000, make_env),
        ],
    )

    md = render_compare(env, baseline="mypy@1.14.1")

    assert md.startswith("_compare · baseline `mypy@1.14.1` · suite `v`_")
    assert "TYPEBENCH:BEGIN" not in md
    assert "TYPEBENCH:END" not in md
    assert "#### sqlalchemy — constrained · cores=4" in md
    assert (
        "| Checker | Wall median (s) | Δ wall | kLOC/s | Δ kLOC/s | Peak mem (MB) | Δ mem |" in md
    )
    assert "mypy@1.14.1" in md
    assert "mypy@1.15.0" in md
    assert "6.27" in md
    assert "-7.3%" in md
    assert "diagnostics" not in md.lower()
    # GFM: a blank line after the |---| separator terminates the table, so the data
    # rows must immediately follow it (no blank line between separator and first row).
    lines = md.splitlines()
    sep = next(i for i, line in enumerate(lines) if line.startswith("|---"))
    assert lines[sep + 1].lstrip().startswith("| mypy@")


def test_render_compare_defaults_baseline_to_first_record_checker_id(
    make_env: EnvFactory,
) -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[
            _cmp_record("pyright@1.1.400", 4.0, 100_000_000, make_env),
            _cmp_record("ty@0.0.1", 1.0, 50_000_000, make_env),
        ],
    )

    md = render_compare(env)

    assert md.startswith("_compare · baseline `pyright@1.1.400` · suite `v`_")
    assert "TYPEBENCH:BEGIN" not in md
    assert "pyright@1.1.400" in md
    assert "ty@0.0.1" in md
    assert "-75.0%" in md


def test_render_compare_empty_envelope_has_plain_terminal_message() -> None:
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[])

    md = render_compare(env)

    assert md == "_no records to compare_"
    assert "TYPEBENCH:BEGIN" not in md


def test_render_terminal_has_tables_without_readme_markers(make_env: EnvFactory) -> None:
    env = ResultsEnvelope(
        suite_version="2026-06-08",
        generated_at="2026-06-08T00:00:00Z",
        runs=[
            _record("mypy", 2.0, 200_000_000, make_env),
            _record("ty", 0.5, 400_000_000, make_env),
        ],
    )
    out = render_terminal(env)
    # Same grouped tables + footnote as the README, minus the HTML markers that
    # are noise in a terminal.
    assert "TYPEBENCH:BEGIN" not in out
    assert "TYPEBENCH:END" not in out
    assert "| ty@" in out and "| mypy@" in out
    assert out.index("| ty@") < out.index("| mypy@")  # fastest-first preserved
    assert "httpx" in out  # project heading
    assert "all-cores" in out.lower()  # shared footnote describes the columns


def test_build_report_html_inlines_assets_and_data() -> None:
    template = (
        "<html><body>\n"
        '<script src="./vendor/chart.umd.min.js"></script>\n'
        '<script src="./app.js"></script>\n'
        "</body></html>"
    )
    html = build_report_html(
        template,
        app_js="console.log('app');",
        chart_js="/*chart*/ var Chart = {};",
        trends={"points": [{"checker_id": "ty@1.0"}]},
    )
    # No external asset references survive — the report is one portable file.
    assert 'src="./vendor' not in html
    assert 'src="./app.js"' not in html
    assert "/*chart*/" in html
    assert "console.log('app');" in html
    # Data is embedded as a global the app reads instead of fetching.
    assert "window.__TYPEBENCH_TRENDS__" in html
    assert '"ty@1.0"' in html


def test_build_report_html_neutralizes_closing_script_tag() -> None:
    # A raw </script> inside inlined JS would terminate the <script> block early.
    template = '<script src="./vendor/chart.umd.min.js"></script><script src="./app.js"></script>'
    html = build_report_html(
        template,
        app_js="x",
        chart_js="var s='</script>';",
        trends={"points": []},
    )
    assert "var s='<\\/script>';" in html


def _files_record(tool: str, files: int | None, make_env: EnvFactory) -> RunResult:
    rec = _record(tool, 1.0, 100_000_000, make_env)
    return rec.model_copy(update={"files": files})


def test_files_degraded_true_when_either_arm_analyzed_nothing(make_env: EnvFactory) -> None:
    pr = _files_record("pyrefly", 0, make_env)
    rel = _files_record("pyrefly", 12, make_env)
    assert _files_degraded([pr, rel]) is True


def test_files_degraded_true_when_arms_disagree_on_file_count(make_env: EnvFactory) -> None:
    pr = _files_record("pyrefly", 12, make_env)
    rel = _files_record("pyrefly", 3, make_env)
    assert _files_degraded([pr, rel]) is True


def test_files_degraded_false_when_arms_agree_and_nonzero(make_env: EnvFactory) -> None:
    pr = _files_record("pyrefly", 12, make_env)
    rel = _files_record("pyrefly", 12, make_env)
    assert _files_degraded([pr, rel]) is False


def test_files_degraded_true_when_count_unknown(make_env: EnvFactory) -> None:
    pr = _files_record("pyrefly", None, make_env)
    rel = _files_record("pyrefly", 12, make_env)
    assert _files_degraded([pr, rel]) is True


def test_ab_display_is_tool_and_label_dropping_raw_version() -> None:
    assert _ab_display("mypy@mypy 2.1.0 (compiled: yes)+release") == "mypy (release)"
    assert _ab_display("pyrefly@path+pr") == "pyrefly (pr)"
    assert _ab_display("ty@0.0.44") == "ty@0.0.44"  # no label -> fall back to full id


def test_render_ab_rows_show_friendly_labels_not_raw_version(make_env: EnvFactory) -> None:
    base = _record("mypy", 2.0, 1, make_env).model_copy(
        update={"checker_id": "mypy@mypy 2.1.0 (compiled: yes)+release", "files": 10}
    )
    cand = _record("mypy", 1.5, 1, make_env).model_copy(
        update={"checker_id": "mypy@mypy 2.1.0 (compiled: yes)+pr", "files": 10}
    )
    env = ResultsEnvelope(suite_version="ab", generated_at="t", runs=[base, cand])
    out = render_ab(env, baseline="mypy@mypy 2.1.0 (compiled: yes)+release")
    assert "mypy (pr)" in out and "mypy (release)" in out
    assert "(compiled: yes)" not in out  # raw version string gone from display
    assert "-25.0%" in out  # delta math still correct (matching on raw id)


def test_render_ab_is_wall_only_with_delta_and_spread(make_env: EnvFactory) -> None:
    base = _record("pyrefly", 2.00, 100_000_000, make_env).model_copy(
        update={"checker_id": "pyrefly@0.36+release", "files": 10}
    )
    cand = _record("pyrefly", 1.50, 100_000_000, make_env).model_copy(
        update={"checker_id": "pyrefly@path+pr", "files": 10}
    )
    env = ResultsEnvelope(suite_version="ab-2026-06-11", generated_at="t", runs=[base, cand])

    out = render_ab(env, baseline="pyrefly@0.36+release")

    assert "Peak" not in out and "kLOC" not in out
    assert "Wall median (s)" in out and "Δ wall" in out and "runs" in out
    assert "-25.0%" in out
    assert "baseline" in out


def test_render_ab_marks_degraded_when_file_counts_disagree(make_env: EnvFactory) -> None:
    base = _record("pyrefly", 2.0, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@0.36+release", "files": 10}
    )
    cand = _record("pyrefly", 0.1, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@path+pr", "files": 0}
    )
    env = ResultsEnvelope(suite_version="ab", generated_at="t", runs=[base, cand])

    out = render_ab(env, baseline="pyrefly@0.36+release")

    assert "degraded" in out


def test_render_ab_two_targets_render_two_sections(make_env: EnvFactory) -> None:
    # Two distinct (project, mode, cores) groups -> two table sections.
    a_base = _record("pyrefly", 2.0, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@0.36+release", "files": 10, "cores": 1}
    )
    a_cand = _record("pyrefly", 1.5, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@path+pr", "files": 10, "cores": 1}
    )
    b_base = _record("pyrefly", 2.0, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@0.36+release", "files": 8, "cores": 4}
    )
    b_cand = _record("pyrefly", 1.0, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@path+pr", "files": 8, "cores": 4}
    )
    env = ResultsEnvelope(
        suite_version="ab", generated_at="t", runs=[a_base, a_cand, b_base, b_cand]
    )

    out = render_ab(env, baseline="pyrefly@0.36+release")

    assert out.count("####") == 2  # one section per group


def test_render_ab_unmatched_baseline_renders_dashes_not_crash(make_env: EnvFactory) -> None:
    # A baseline id that matches no record (e.g. the unresolved 'latest') must not
    # crash; deltas degrade to em-dashes and no row is marked baseline.
    base = _record("pyrefly", 2.0, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@0.36.2+release", "files": 10}
    )
    cand = _record("pyrefly", 1.5, 1, make_env).model_copy(
        update={"checker_id": "pyrefly@path+pr", "files": 10}
    )
    env = ResultsEnvelope(suite_version="ab", generated_at="t", runs=[base, cand])

    out = render_ab(env, baseline="pyrefly@latest+release")  # no such record

    assert "| baseline |" not in out  # no row is tagged as the baseline (caption aside)
    assert "—" in out  # deltas fell back to em-dashes
