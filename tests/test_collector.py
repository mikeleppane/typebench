import shutil
import subprocess
from pathlib import Path

import pytest

from typebench import collector, measure
from typebench.adapters.stub import StubAdapter
from typebench.collector import RunManifest, run_single
from typebench.measure import MemorySummary, ResourceResult
from typebench.models import FailurePhase, ResultClass, ThreadMode, TimingStats
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun


@pytest.fixture(autouse=True)
def _disable_resource_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep collector unit tests hermetic + fast: force the plain (non-scoped)
    # probe path. Dedicated resource-pass behaviour is covered in Task 6's tests.
    # raising=False is the ONE justified use: in TDD order this fixture is written
    # before the `_resource_capable` seam (added in Task 6 Step 3) exists.
    monkeypatch.setattr(collector, "_resource_capable", lambda: False, raising=False)


def _stub_raw() -> RawRun:
    return RawRun(
        exit_code=0,
        signal=None,
        timed_out=False,
        oom=False,
        stdout='{"diagnostics": 0, "files": 1}',
        stderr="",
    )


def test_run_single_failure_skips_timing() -> None:
    adapter = StubAdapter(exit_code=2)  # -> FAILED_CRASH
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
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
        config=NormalizedConfig(),
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
        config=NormalizedConfig(),
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
        config=NormalizedConfig(),
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
        config=NormalizedConfig(),
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
        config=NormalizedConfig(),
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
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.failure_phase == FailurePhase.TIMING
    assert result.error_detail
    assert result.timing is None


def test_run_single_command_construction_failure_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # command() can touch disk / do path math (write a generated tool config,
    # relpath across drives). A raise there must become a recorded failed{env},
    # never propagate out of run_single and DROP the record (spec §12).
    adapter = StubAdapter(exit_code=0)

    def _boom(*_a: object, **_k: object) -> object:
        raise OSError("cannot write generated config")

    monkeypatch.setattr(adapter, "command", _boom)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.failure_phase == FailurePhase.PROBE
    assert result.real_exit_code == -1  # no process ran
    assert result.error_detail
    assert result.timing is None


def test_one_core_prepends_taskset_and_enforces(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_command(
        argv: list[str], timeout: float, env: dict[str, str] | None = None
    ) -> RawRun:
        captured["argv"] = argv
        return _stub_raw()

    monkeypatch.setattr(collector, "run_command", fake_run_command)
    monkeypatch.setattr(collector, "_taskset_available", lambda: True)

    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.CONSTRAINED,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert captured["argv"][:3] == ["taskset", "-c", "0"]
    assert result.thread_mode_enforced is True
    assert result.hard_cap is True  # stub cap is hard
    assert result.cap_mechanism == "cpu-affinity"


def test_one_core_without_taskset_is_not_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "_taskset_available", lambda: False)
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.CONSTRAINED,
        warmup=1,
        runs=2,
        timeout=10,
    )
    # taskset missing -> we did NOT pin -> must not claim enforcement OR the cap
    # (the adapter mechanism string bakes in "cpu-affinity"), §5.3 honesty.
    assert result.thread_mode_enforced is False
    assert result.hard_cap is None
    assert result.cap_mechanism is None


def test_all_cores_no_taskset_no_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "_taskset_available", lambda: True)
    captured: dict[str, list[str]] = {}

    def fake_run_command(
        argv: list[str], timeout: float, env: dict[str, str] | None = None
    ) -> RawRun:
        captured["argv"] = argv
        return _stub_raw()

    monkeypatch.setattr(collector, "run_command", fake_run_command)
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert captured["argv"][0] != "taskset"  # ALL_CORES is never pinned
    assert result.thread_mode_enforced is False
    assert result.hard_cap is None  # cap recorded only for the constrained track
    assert result.cap_mechanism is None


def test_resource_pass_populates_memory_cpu_efficiency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force capability ON but inject a fake scoped_probe (no real systemd needed).
    # raising=True: the _resource_capable/_scoped_probe seams exist by now (Step 3),
    # so a typo'd seam name fails loudly instead of silently creating a no-op.
    monkeypatch.setattr(collector, "_resource_capable", lambda: True)

    def fake_scoped_probe(
        argv: list[str],
        extra_env: dict[str, str],
        timeout: float,
        repeats: int,
        runner: object = None,
        prepare: object = None,
    ) -> ResourceResult:
        return ResourceResult(
            raw=RawRun(
                exit_code=0,
                signal=None,
                timed_out=False,
                oom=False,
                stdout='{"diagnostics": 0, "files": 5}',
                stderr="",
            ),
            memory=MemorySummary(
                runs=3,
                peak_bytes_min=10,
                peak_bytes_median=12,
                peak_bytes_max=15,
                memory_stat={"anon": 12},
            ),
            cpu_time_s=2.0,
            oom=False,
        )

    monkeypatch.setattr(collector, "_scoped_probe", fake_scoped_probe)

    # parallel_efficiency = cpu_time / wall is computed ONLY when timing ran, and
    # the collector gates timing on `shutil.which("hyperfine")`. Patch which so the
    # stubbed run_timing is actually invoked on hosts WITHOUT hyperfine (else this
    # test silently passes only where hyperfine happens to be installed).
    monkeypatch.setattr(collector.shutil, "which", lambda _name: "/usr/bin/hyperfine")
    monkeypatch.setattr(
        collector,
        "run_timing",
        lambda *_a, **_k: TimingStats(
            runs=2, min_s=1.0, median_s=4.0, mean_s=4.0, stddev_s=0.0, max_s=5.0, times_s=[4.0, 4.0]
        ),
    )

    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
        mem_runs=3,
    )
    assert result.result_class == ResultClass.CLEAN
    assert result.files == 5
    assert result.memory is not None and result.memory.peak_bytes_median == 12
    assert result.cpu_time_s == 2.0
    assert result.parallel_efficiency == 0.5  # cpu 2.0 / wall median 4.0


def test_resource_pass_oom_reclassifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "_resource_capable", lambda: True)

    def fake_scoped_probe(
        argv: list[str],
        extra_env: dict[str, str],
        timeout: float,
        repeats: int,
        runner: object = None,
        prepare: object = None,
    ) -> ResourceResult:
        return ResourceResult(
            raw=RawRun(
                exit_code=-1, signal=9, timed_out=False, oom=True, stdout="", stderr="killed"
            ),
            memory=None,
            cpu_time_s=None,
            oom=True,
        )

    monkeypatch.setattr(collector, "_scoped_probe", fake_scoped_probe)
    adapter = StubAdapter(exit_code=0)
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
    assert result.oom is True
    assert result.timing is None  # OOM -> not a measured success -> no timing


def test_resource_pass_falls_back_to_plain_probe_on_measure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A TOTAL resource-pass failure (MeasureError) must NOT drop the record: the
    # collector falls back to a plain run_command probe (Decision J).
    monkeypatch.setattr(collector, "_resource_capable", lambda: True)

    def boom_scoped_probe(
        argv: list[str],
        extra_env: dict[str, str],
        timeout: float,
        repeats: int,
        runner: object = None,
        prepare: object = None,
    ) -> ResourceResult:
        raise measure.MeasureError("no usable payload")

    monkeypatch.setattr(collector, "_scoped_probe", boom_scoped_probe)
    monkeypatch.setattr(collector.shutil, "which", lambda _name: None)  # skip timing

    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    result = run_single(
        adapter,
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    # Record still produced via the plain probe; just no memory.
    assert result.result_class == ResultClass.CLEAN
    assert result.files == 5
    assert result.memory is None
    assert result.cpu_time_s is None


def test_taskset_unavailable_when_core0_not_in_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    # Review finding: taskset installed but core 0 outside the cpuset (containers /
    # restricted CI) -> `taskset -c 0` would exit 1 BEFORE the checker runs, which
    # reads as diagnostics and fakes thread_mode_enforced. Guard on the affinity mask.
    monkeypatch.setattr(collector.shutil, "which", lambda _n: "/usr/bin/taskset")
    monkeypatch.setattr(collector.os, "sched_getaffinity", lambda _pid: {1, 2, 3})
    assert collector._taskset_available() is False


def test_taskset_available_when_core0_in_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector.shutil, "which", lambda _n: "/usr/bin/taskset")
    monkeypatch.setattr(collector.os, "sched_getaffinity", lambda _pid: {0, 1, 2})
    assert collector._taskset_available() is True


def test_run_single_stamps_manifest_fields() -> None:
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    manifest = RunManifest(
        project_sha="80960fa",
        lock_hash="lh",
        config_hash="ch",
        canonical_files=23,
        canonical_loc=4000,
        canonical_code_loc=3200,
        tool_install_source="builtin",
        over_reports=False,
    )
    result = run_single(
        adapter,
        project="httpx",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=1,
        timeout=10,
        manifest=manifest,
    )
    assert result.project_sha == "80960fa"
    assert result.lock_hash == "lh"
    assert result.config_hash == "ch"
    assert result.tool_install_source == "builtin"
    assert result.canonical_files == 23
    assert result.canonical_code_loc == 3200
    assert result.loc_denominator == "code"
    assert result.over_reports is False


def test_run_single_loc_denominator_physical_when_no_code_loc() -> None:
    manifest = RunManifest(canonical_files=10, canonical_loc=500, canonical_code_loc=None)
    result = run_single(
        StubAdapter(exit_code=0, files=1),
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=1,
        timeout=10,
        manifest=manifest,
    )
    assert result.loc_denominator == "physical"
    assert result.canonical_code_loc is None


def test_run_single_no_manifest_leaves_scalars_none() -> None:
    result = run_single(
        StubAdapter(exit_code=0, files=1),
        project="demo",
        config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=1,
        timeout=10,
    )
    assert result.project_sha is None
    assert result.loc_denominator is None
