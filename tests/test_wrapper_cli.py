import subprocess
import sys


def _run_wrapper(inner_argv: list[str], timeout: str = "10") -> int:
    return subprocess.run(
        [sys.executable, "-m", "typebench.wrapper", "--timeout", timeout, "--", *inner_argv],
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def test_wrapper_cli_exits_zero_on_clean() -> None:
    assert _run_wrapper([sys.executable, "-c", "import sys; sys.exit(0)"]) == 0


def test_wrapper_cli_exits_zero_on_diagnostics() -> None:
    # exit 1 == diagnostics == measured success -> wrapper reports 0 to hyperfine.
    assert _run_wrapper([sys.executable, "-c", "import sys; sys.exit(1)"]) == 0


def test_wrapper_cli_exits_nonzero_on_crash() -> None:
    assert _run_wrapper([sys.executable, "-c", "import sys; sys.exit(2)"]) != 0
