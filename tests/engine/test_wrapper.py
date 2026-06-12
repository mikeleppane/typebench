import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import ClassVar

import pytest

from typebench.contracts.models import ResultClass
from typebench.engine import wrapper
from typebench.engine.wrapper import RawRun, classify_default, run_command, universal_failure_prefix


class _ClosablePipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _timeout_expired(argv: list[str], timeout: float | None) -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(argv, timeout if timeout is not None else 0.0)


class _TimeoutThenBlockedDrainPopen:
    created: ClassVar["_TimeoutThenBlockedDrainPopen | None"] = None

    def __init__(
        self,
        argv: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: Mapping[str, str] | None,
        cwd: Path | None,
        start_new_session: bool,
    ) -> None:
        self.argv = argv
        self.communicate_calls = 0
        self.kill_calls = 0
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = _ClosablePipe()
        self.stderr = _ClosablePipe()
        _TimeoutThenBlockedDrainPopen.created = self

    def __enter__(self) -> "_TimeoutThenBlockedDrainPopen":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def kill(self) -> None:
        self.kill_calls += 1

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        raise _timeout_expired(self.argv, timeout)


class _TimeoutThenCleanDrainPopen:
    created: ClassVar["_TimeoutThenCleanDrainPopen | None"] = None

    def __init__(
        self,
        argv: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: Mapping[str, str] | None,
        cwd: Path | None,
        start_new_session: bool,
    ) -> None:
        self.argv = argv
        self.communicate_calls = 0
        self.kill_calls = 0
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = _ClosablePipe()
        self.stderr = _ClosablePipe()
        _TimeoutThenCleanDrainPopen.created = self

    def __enter__(self) -> "_TimeoutThenCleanDrainPopen":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def kill(self) -> None:
        self.kill_calls += 1

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise _timeout_expired(self.argv, timeout)
        return "partial stdout", "partial stderr"


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


def test_run_command_timeout_blocked_post_kill_drain_returns_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TimeoutThenBlockedDrainPopen.created = None
    monkeypatch.setattr(wrapper.subprocess, "Popen", _TimeoutThenBlockedDrainPopen)

    raw = run_command(["fake-checker"], timeout=0.1)

    proc = _TimeoutThenBlockedDrainPopen.created
    assert proc is not None
    assert proc.communicate_calls == 2
    assert proc.kill_calls >= 1
    assert proc.stdout.closed is True
    assert proc.stderr.closed is True
    assert raw.timed_out is True
    assert raw.exit_code == -1
    assert raw.oom is False
    assert raw.stdout == ""
    assert raw.stderr == ""


def test_run_command_timeout_clean_post_kill_drain_preserves_captured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TimeoutThenCleanDrainPopen.created = None
    monkeypatch.setattr(wrapper.subprocess, "Popen", _TimeoutThenCleanDrainPopen)

    raw = run_command(["fake-checker"], timeout=0.1)

    proc = _TimeoutThenCleanDrainPopen.created
    assert proc is not None
    assert proc.communicate_calls == 2
    assert raw.timed_out is True
    assert raw.exit_code == -1
    assert raw.oom is False
    assert raw.stdout == "partial stdout"
    assert raw.stderr == "partial stderr"


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
        "import sys, typebench.engine.wrapper\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'pydantic')\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_universal_failure_prefix_returns_none_when_no_universal_condition() -> None:
    # exit code alone is NOT a universal condition -> None (caller decides).
    assert universal_failure_prefix(RawRun(0, None, False, False, "", "")) is None
    assert universal_failure_prefix(RawRun(1, None, False, False, "", "")) is None
    assert universal_failure_prefix(RawRun(2, None, False, False, "", "")) is None


def test_universal_failure_prefix_detects_each_condition() -> None:
    assert (
        universal_failure_prefix(RawRun(0, None, False, False, "", "", env_error=True))
        == ResultClass.FAILED_ENV
    )
    assert universal_failure_prefix(RawRun(0, None, False, True, "", "")) == ResultClass.FAILED_OOM
    assert (
        universal_failure_prefix(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT
    )
    assert universal_failure_prefix(RawRun(0, 9, False, False, "", "")) == ResultClass.FAILED_OOM
    assert universal_failure_prefix(RawRun(0, 11, False, False, "", "")) == ResultClass.FAILED_CRASH
