import sys
from pathlib import Path

from typebench.engine.proc import SYSTEM_HOST, SystemProcessHost


def test_system_process_host_runs_command_with_cwd(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    raw = SystemProcessHost().run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('marker.txt').write_text('ok')",
        ],
        cwd=tmp_path,
        timeout=10,
    )

    assert raw.exit_code == 0
    assert marker.read_text(encoding="utf-8") == "ok"


def test_system_process_host_records_launch_failure_as_env_error() -> None:
    raw = SystemProcessHost().run(["typebench-nonexistent-checker-xyz"], timeout=10)

    assert raw.env_error is True
    assert raw.exit_code == -1
    assert raw.stderr


def test_system_process_host_which_delegates_to_path_lookup() -> None:
    assert SYSTEM_HOST.which("python") is not None
