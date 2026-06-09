from typing import cast

from typebench.contracts.models import (
    CalibrationStats,
    MemoryStats,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    TimingStats,
)
from typebench.env import EnvFingerprint
from typebench.renderer import build_trends, cpu_model_anchors, render_readme


def _env(cpu: str = "Test CPU") -> EnvFingerprint:
    return EnvFingerprint(
        os="Linux",
        kernel="6.6",
        cpu_model=cpu,
        core_count=8,
        python_version="3.12.0",
    )


def _record(tool: str, wall: float, peak: int, over: bool = False) -> RunResult:
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
        loc_denominator="code",
        over_reports=over,
        env=_env(),
    )


def test_render_readme_table_is_fastest_first_and_excludes_diagnostics() -> None:
    env = ResultsEnvelope(
        suite_version="2026-06-08",
        generated_at="2026-06-08T00:00:00Z",
        runs=[_record("mypy", 2.0, 200_000_000), _record("ty", 0.5, 400_000_000)],
    )
    md = render_readme(env)
    # fastest-first: ty (0.5s) before mypy (2.0s)
    assert md.index("| ty ") < md.index("| mypy ")
    # diagnostics is NOT a column (spec §8)
    assert "diagnostics" not in md.lower()
    # code-LOC throughput present (3200 LOC / 0.5 s = 6.4 kLOC/s)
    assert "6.4" in md
    # cross-pass label on parallel efficiency
    assert "cross-pass" in md.lower()


def test_render_readme_withholds_throughput_for_over_reporters() -> None:
    env = ResultsEnvelope(
        suite_version="v",
        generated_at="t",
        runs=[_record("ty", 0.5, 1, over=True)],
    )
    md = render_readme(env)
    # over_reports -> kLOC/s withheld with the asterisk caveat, not a number
    assert "—*" in md or "n/a*" in md


def test_render_readme_shows_failed_cells_as_didnt_compete() -> None:
    failed = RunResult(
        tool="pyright",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_ENV,
        real_exit_code=3,
        env=_env(),
    )
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[failed])
    md = render_readme(env)
    assert "failed{env}" in md


def _record_for_trends(tool: str, wall: float, calib_med: float, cpu: str) -> RunResult:
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
        loc_denominator="code",
        over_reports=False,
        calibration=CalibrationStats(
            workload_id="calib-pyloop-v1",
            iterations=1,
            runs=1,
            raw_min_s=calib_med,
            raw_median_s=calib_med,
            raw_max_s=calib_med,
        ),
        env=_env(cpu),
    )


def _envelope(gen: str, *records: RunResult) -> ResultsEnvelope:
    return ResultsEnvelope(suite_version="v", generated_at=gen, runs=list(records))


def _trend_points(trends: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", trends["points"])


def _corpus_markers(trends: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", trends["corpus_markers"])


def _cpu_models(trends: dict[str, object]) -> list[str]:
    return cast("list[str]", trends["cpu_models"])


def test_cpu_model_anchors_take_earliest_per_model() -> None:
    history = [
        _envelope("2026-02-01", _record_for_trends("mypy", 1.0, 0.40, "CPU-A")),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.20, "CPU-A")),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.50, "CPU-B")),
    ]
    anchors = cpu_model_anchors(history)
    assert anchors["CPU-A"] == 0.40
    assert anchors["CPU-B"] == 0.50


def test_build_trends_normalizes_against_anchor() -> None:
    history = [
        _envelope("2026-02-01", _record_for_trends("mypy", 1.0, 0.40, "CPU-A")),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.20, "CPU-A")),
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


def test_build_trends_includes_kloc_and_corpus_markers() -> None:
    history = [_envelope("2026-02-01", _record_for_trends("mypy", 2.0, 0.40, "CPU-A"))]
    trends = build_trends(history)
    p = _trend_points(trends)[0]
    kloc_s = p["kloc_s"]
    assert isinstance(kloc_s, float)
    assert abs(kloc_s - 1.6) < 1e-9
    assert _corpus_markers(trends)[0]["suite_version"] == "v"
    assert "CPU-A" in _cpu_models(trends)
