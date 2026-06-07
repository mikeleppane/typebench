from pathlib import Path

from typer.testing import CliRunner

from typebench.cli import app
from typebench.models import RunResult

runner = CliRunner()


def test_cli_run_stub_writes_results_json(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    result = runner.invoke(
        app,
        [
            "run",
            "--tool",
            "stub",
            "--project",
            "demo",
            "--thread-mode",
            "all-cores",
            "--runs",
            "2",
            "--warmup",
            "1",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = RunResult.model_validate_json(out.read_text())
    assert parsed.tool == "stub"
    assert parsed.project == "demo"


def test_cli_run_rejects_unknown_tool(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["run", "--tool", "nope", "--project", "demo", "--output", str(tmp_path / "r.json")],
    )
    assert result.exit_code != 0
    assert "Unknown tool" in result.output
