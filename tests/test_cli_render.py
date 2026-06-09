import json
from pathlib import Path

from typer.testing import CliRunner

from typebench.cli import app
from typebench.contracts.models import (
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    TimingStats,
)
from typebench.env import EnvFingerprint

runner = CliRunner()


def _envelope_file(path: Path, gen: str) -> None:
    rec = RunResult(
        tool="mypy",
        tool_version="1.0",
        project="httpx",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        timing=TimingStats(
            runs=1,
            min_s=1.0,
            median_s=1.0,
            mean_s=1.0,
            stddev_s=0.0,
            max_s=1.0,
            times_s=[1.0],
        ),
        canonical_code_loc=3200,
        loc_denominator="code",
        env=EnvFingerprint(
            os="Linux",
            kernel="6.6",
            cpu_model="CPU-A",
            core_count=8,
            python_version="3.12.0",
        ),
    )
    env = ResultsEnvelope(suite_version="2026-06-08", generated_at=gen, runs=[rec])
    path.write_text(env.model_dump_json())


def test_render_updates_readme_markers_and_writes_trends(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    _envelope_file(results / "2026-06-08.json", "2026-06-08T00:00:00Z")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# typebench\n\nIntro prose.\n\n<!-- TYPEBENCH:BEGIN -->\n"
        "OLD\n<!-- TYPEBENCH:END -->\n\nFooter prose.\n"
    )
    trends = tmp_path / "site" / "data" / "trends.json"
    trends.parent.mkdir(parents=True)
    result = runner.invoke(
        app,
        [
            "render",
            "--results-dir",
            str(results),
            "--readme",
            str(readme),
            "--trends",
            str(trends),
        ],
    )
    assert result.exit_code == 0, result.output
    text = readme.read_text()
    assert "Intro prose." in text and "Footer prose." in text
    assert "OLD" not in text
    assert "| mypy " in text
    data = json.loads(trends.read_text())
    assert data["points"][0]["tool"] == "mypy"
