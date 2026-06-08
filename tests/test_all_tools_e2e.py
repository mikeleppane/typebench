import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench.cli import app
from typebench.models import ResultClass, RunResult

runner = CliRunner()
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_REAL_TOOLS = ["mypy", "pyright", "ty", "pyrefly"]


def _run(tool: str, fixture: str, out: Path) -> RunResult:
    res = runner.invoke(
        app,
        [
            "run",
            "--tool",
            tool,
            "--project",
            fixture,
            "--src-root",
            str(_FIXTURES / fixture),
            "--runs",
            "2",
            "--warmup",
            "1",
            "--output",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    return RunResult.model_validate_json(out.read_text())


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="needs hyperfine")
@pytest.mark.parametrize("tool", _REAL_TOOLS)
def test_tool_flags_error_fixture(tool: str, tmp_path: Path) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")
    rr = _run(tool, "error_project", tmp_path / "r.json")
    assert rr.tool == tool
    assert rr.result_class == ResultClass.DIAGNOSTICS
    assert rr.diagnostics is not None and rr.diagnostics > 0
    assert rr.timing is not None and rr.timing.runs == 2


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="needs hyperfine")
@pytest.mark.parametrize("tool", _REAL_TOOLS)
def test_tool_passes_clean_fixture(tool: str, tmp_path: Path) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")
    rr = _run(tool, "clean_project", tmp_path / "r.json")
    assert rr.tool == tool
    assert rr.result_class == ResultClass.CLEAN
    assert rr.diagnostics == 0
