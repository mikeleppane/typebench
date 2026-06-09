import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench.cli import app
from typebench.contracts.models import ResultClass, RunResult

runner = CliRunner()
_HAS = shutil.which("pyright") is not None and shutil.which("hyperfine") is not None
# Repo-root fixtures (see Task 4) — NOT tests/fixtures, which the **/tests/** exclude would hide.
_FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.mark.skipif(not _HAS, reason="needs pyright + hyperfine")
def test_cli_pyright_on_error_fixture(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    res = runner.invoke(
        app,
        [
            "run",
            "--tool",
            "pyright",
            "--project",
            "error_project",
            "--src-root",
            str(_FIXTURES / "error_project"),
            "--runs",
            "2",
            "--warmup",
            "1",
            "--output",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    rr = RunResult.model_validate_json(out.read_text())
    assert rr.tool == "pyright"
    assert rr.result_class == ResultClass.DIAGNOSTICS
    assert rr.diagnostics is not None and rr.diagnostics > 0
    assert rr.timing is not None and rr.timing.runs == 2
    assert rr.files is not None and rr.files > 0  # parse-sanity: files checked
