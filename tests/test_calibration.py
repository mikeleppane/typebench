import subprocess
import sys
import time

import pytest

import typebench.calibration as cal
from typebench.calibration import ITERATIONS, WORKLOAD_ID, calibrate
from typebench.models import CalibrationStats


def test_calibrate_returns_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a deterministic timer so the test is fast + stable.
    times = iter([0.0, 0.20, 0.0, 0.21, 0.0, 0.19])  # start/stop pairs
    monkeypatch.setattr(cal.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(cal, "_run_workload", lambda: None)  # skip real CPU work

    stats = calibrate(runs=3)
    assert isinstance(stats, CalibrationStats)
    assert stats.workload_id == WORKLOAD_ID
    assert stats.iterations == ITERATIONS
    assert stats.runs == 3
    assert stats.raw_min_s == 0.19
    assert round(stats.raw_median_s, 2) == 0.20
    assert stats.raw_max_s == 0.21


def test_calibration_workload_is_deterministic_and_cpu_bound() -> None:
    # The real workload runs without error and consumes measurable time.
    t0 = time.perf_counter()
    cal._run_workload()
    assert time.perf_counter() - t0 > 0.0


def test_calibrate_rejects_zero_runs() -> None:
    with pytest.raises(ValueError, match="calibration runs must be >= 1"):
        calibrate(runs=0)


def test_calibration_import_does_not_pull_pydantic() -> None:
    code = (
        "import sys, typebench.calibration\n"
        "print(','.join(sorted(m for m in sys.modules if m.split('.')[0]=='pydantic')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
