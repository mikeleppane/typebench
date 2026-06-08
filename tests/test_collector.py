import shutil
import subprocess
from pathlib import Path

import pytest

from typebench import collector
from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import FailurePhase, ResultClass, ThreadMode
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
        thread_mode=ThreadMode.ONE_CORE,
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
        thread_mode=ThreadMode.ONE_CORE,
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
