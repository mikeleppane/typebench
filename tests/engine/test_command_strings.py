import subprocess
from pathlib import Path

from typebench.adapters.stub import StubAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.models import ThreadMode
from typebench.engine import timing
from typebench.engine.measure import scoped_probe


def test_wrapped_command_string_targets_engine_wrapper() -> None:
    command = timing._wrapped_command_string(["somechecker", "arg"], 10.0)
    assert "-m typebench.engine.wrapper" in command


def test_scoped_probe_scope_command_targets_engine_measure() -> None:
    captured: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.write_text(
            """
            {
              "exit_code": 0,
              "signal": null,
              "timed_out": false,
              "oom": false,
              "env_error": false,
              "stdout": "",
              "stderr": "",
              "cgroup": {
                "peak_bytes": 1,
                "cpu_usage_usec": 1,
                "cpu_user_usec": 1,
                "cpu_system_usec": 0,
                "oom_kill": 0,
                "mem_stat": {}
              }
            }
            """
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    scoped_probe(["somechecker", "arg"], extra_env={}, timeout=10.0, repeats=1, runner=runner)

    assert captured
    argv = captured[0]
    assert "-m" in argv
    assert "typebench.engine.measure" in argv


def test_stub_command_targets_internal_fake_checker(tmp_path: Path) -> None:
    argv, _env = StubAdapter().command("demo", NormalizedConfig(), ThreadMode.ALL_CORES, tmp_path)
    assert "-m" in argv
    assert "typebench._internal.fake_checker" in argv
