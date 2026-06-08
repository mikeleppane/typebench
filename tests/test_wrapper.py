import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from typebench.models import ResultClass
from typebench.wrapper import RawRun, classify_default, run_command


def test_run_command_captures_clean_exit() -> None:
    raw = run_command([sys.executable, "-c", "print('hi')"], timeout=10)
    assert raw.exit_code == 0
    assert raw.timed_out is False
    assert raw.env_error is False
    assert "hi" in raw.stdout
    assert raw.signal is None


def test_run_command_captures_nonzero_exit() -> None:
    raw = run_command([sys.executable, "-c", "import sys; sys.exit(1)"], timeout=10)
    assert raw.exit_code == 1


def test_run_command_times_out() -> None:
    raw = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    assert raw.timed_out is True


def test_run_command_reports_env_error_for_missing_binary() -> None:
    # A missing executable is an environment failure, not a crash: run_command
    # captures it (does NOT propagate) so the collector can record failed{env}.
    raw = run_command(["typebench-nonexistent-checker-xyz"], timeout=10)
    assert raw.env_error is True
    assert raw.timed_out is False
    assert raw.stderr  # carries the OSError text for the audit trail


@pytest.mark.skipif(os.name != "posix", reason="signal semantics are POSIX-specific")
def test_run_command_records_signal() -> None:
    # SIGSEGV (-11) -> Python returncode is negative.
    raw = run_command(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"],
        timeout=10,
    )
    assert raw.signal == 11


def test_classify_default_maps_classes() -> None:
    assert classify_default(RawRun(0, None, False, False, "", "")) == ResultClass.CLEAN
    assert classify_default(RawRun(1, None, False, False, "", "")) == ResultClass.DIAGNOSTICS
    assert classify_default(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert classify_default(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT
    assert classify_default(RawRun(11, 11, False, False, "", "")) == ResultClass.FAILED_CRASH
    # Explicit OOM flag (cgroup-sourced, Plan 4) wins over everything.
    assert classify_default(RawRun(137, None, False, True, "", "")) == ResultClass.FAILED_OOM
    # SIGKILL (9) with no explicit flag -> OOM heuristic until cgroup detection lands.
    assert classify_default(RawRun(-9, 9, False, False, "", "")) == ResultClass.FAILED_OOM
    # Environment error -> failed{env}.
    assert (
        classify_default(RawRun(-1, None, False, False, "", "", env_error=True))
        == ResultClass.FAILED_ENV
    )


def test_classify_default_timeout_precedes_sigkill_oom() -> None:
    # Documented precedence: timeout (#3) is checked BEFORE the SIGKILL->OOM
    # heuristic (#4). A run that is both timed_out and signal==9 is failed{timeout}.
    assert classify_default(RawRun(0, 9, True, False, "", "")) == ResultClass.FAILED_TIMEOUT


@pytest.mark.skipif(os.name != "posix", reason="process-group kill is POSIX-specific")
def test_run_command_timeout_kills_process_tree(tmp_path: Path) -> None:
    # A timed-out parent must not leave a grandchild running, or it steals CPU
    # from later benchmark runs. The grandchild writes a marker AFTER the timeout
    # fires; if the whole process group was killed, the marker never appears.
    marker = tmp_path / "grandchild.marker"
    grandchild = tmp_path / "gc.py"
    grandchild.write_text(f"import time\ntime.sleep(1.0)\nopen({str(marker)!r}, 'w').close()\n")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n"
        "time.sleep(1.0)\n"
    )
    raw = run_command([sys.executable, str(parent)], timeout=0.3)
    assert raw.timed_out is True
    time.sleep(1.2)  # past when the orphaned grandchild would have written its marker
    assert not marker.exists(), "grandchild survived timeout: process tree not killed"


def test_wrapper_import_does_not_pull_pydantic() -> None:
    # The wrapper is hyperfine's per-run command; importing pydantic here adds
    # ~50ms of startup to EVERY timed run and biases the benchmark. Lock it out.
    # Run in a fresh interpreter — pytest itself already imported pydantic.
    code = (
        "import sys, typebench.wrapper\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'pydantic')\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
