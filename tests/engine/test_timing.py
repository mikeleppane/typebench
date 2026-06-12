import shutil
import subprocess
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.models import ThreadMode, TimingStats
from typebench.engine import timing
from typebench.engine.timing import (
    TimingCommandError,
    _outer_timeout,
    parse_hyperfine_json,
    run_timing,
)


def test_parse_hyperfine_json_builds_timing_stats() -> None:
    data = {
        "results": [
            {
                "command": "x",
                "mean": 0.12,
                "stddev": 0.01,
                "median": 0.11,
                "min": 0.10,
                "max": 0.14,
                "times": [0.10, 0.11, 0.14],
            }
        ]
    }
    stats = parse_hyperfine_json(data)
    assert isinstance(stats, TimingStats)
    assert stats.runs == 3
    assert stats.min_s == 0.10
    assert stats.median_s == 0.11
    assert stats.times_s == [0.10, 0.11, 0.14]


def test_parse_hyperfine_json_rejects_empty_results() -> None:
    with pytest.raises(ValueError):
        parse_hyperfine_json({"results": []})


def test_outer_timeout_scales_with_iterations_and_per_run_timeout() -> None:
    short = _outer_timeout(warmup=1, runs=2, per_run_timeout=10.0)
    long = _outer_timeout(warmup=2, runs=4, per_run_timeout=20.0)

    assert short > (1 + 2) * 10.0
    assert long > (2 + 4) * 20.0
    assert long > short


def test_run_timing_outer_timeout_raises_timing_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Pipe:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _TimeoutProc:
        def __init__(self, cmd: list[str]) -> None:
            self.cmd = cmd
            self.pid = 999_999_999
            self.returncode: int | None = None
            self.stdout: _Pipe | None = _Pipe()
            self.stderr: _Pipe | None = _Pipe()
            self.killed = False
            self.communicate_timeouts: list[float] = []

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            return False

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            if timeout is None:
                raise AssertionError("communicate() must not be called without a timeout")
            self.communicate_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout)

        def kill(self) -> None:
            self.killed = True

    proc_holder: dict[str, _TimeoutProc] = {}

    def _timeout_proc(
        cmd: list[str],
        *,
        stdout: int,
        stderr: int,
        text: bool,
        env: dict[str, str] | None,
        start_new_session: bool,
    ) -> _TimeoutProc:
        proc = _TimeoutProc(cmd)
        proc_holder["proc"] = proc
        return proc

    monkeypatch.setattr(timing, "_popen", _timeout_proc)

    with pytest.raises(TimingCommandError) as exc_info:
        run_timing(["python", "--version"], prepare_cmd=None, warmup=0, runs=1, timeout=12.5)

    assert "outer timeout" in str(exc_info.value)
    assert "hyperfine exceeded" in str(exc_info.value)
    proc = proc_holder["proc"]
    assert proc.communicate_timeouts == [_outer_timeout(warmup=0, runs=1, per_run_timeout=12.5), 5]
    assert proc.killed
    assert proc.stdout is not None
    assert proc.stdout.closed
    assert proc.stderr is not None
    assert proc.stderr.closed


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_timing_against_stub(tmp_path: Path) -> None:
    adapter = StubAdapter(exit_code=0, sleep=0.02)
    argv, env = adapter.command("demo", NormalizedConfig(), ThreadMode.ALL_CORES, tmp_path)
    stats = run_timing(argv, prepare_cmd=None, extra_env=env, warmup=1, runs=3, timeout=30)
    assert stats.runs == 3
    assert stats.min_s > 0
    assert stats.max_s >= stats.min_s
