from typebench.env import EnvFingerprint
from typebench.models import (
    MemoryStats,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    TimingStats,
)
from typebench.renderer import render_readme


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
