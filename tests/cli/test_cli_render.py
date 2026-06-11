import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from typebench.cli import app
from typebench.contracts.models import (
    EnvFingerprint,
    ResultClass,
    ResultsEnvelope,
    RunResult,
    ThreadMode,
    TimingStats,
)
from typebench.contracts.taxonomy import LocDenominator

type EnvFactory = Callable[..., EnvFingerprint]

runner = CliRunner()


def _envelope_file(path: Path, gen: str, make_env: EnvFactory) -> None:
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
        loc_denominator=LocDenominator.CODE,
        env=make_env(cpu_model="CPU-A"),
    )
    env = ResultsEnvelope(suite_version="2026-06-08", generated_at=gen, runs=[rec])
    path.write_text(env.model_dump_json())


def test_render_updates_readme_markers_and_writes_trends(
    tmp_path: Path, make_env: EnvFactory
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    _envelope_file(results / "2026-06-08.json", "2026-06-08T00:00:00Z", make_env)
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
    assert "| mypy@" in text  # rows are checker_id-labelled (legacy record -> mypy@1.0)
    data = json.loads(trends.read_text())
    assert data["points"][0]["tool"] == "mypy"
    assert data["points"][0]["checker_id"] == "mypy@1.0"


def test_render_empty_store_writes_placeholder_not_error(tmp_path: Path) -> None:
    # The publish workflow runs before the first official envelope exists; an
    # empty store must render a placeholder + empty trends, not exit non-zero.
    results = tmp_path / "data" / "official"
    results.mkdir(parents=True)
    readme = tmp_path / "README.md"
    readme.write_text(
        "# typebench\n\nIntro prose.\n\n<!-- TYPEBENCH:BEGIN -->\n"
        "OLD\n<!-- TYPEBENCH:END -->\n\nFooter prose.\n"
    )
    trends = tmp_path / "site" / "data" / "trends.json"
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
    assert "No official results published yet" in text
    assert json.loads(trends.read_text()) == {
        "cpu_models": [],
        "points": [],
        "corpus_markers": [],
    }


def _fake_site(root: Path) -> Path:
    site = root / "site"
    (site / "vendor").mkdir(parents=True)
    (site / "index.html").write_text(
        '<script src="./vendor/chart.umd.min.js"></script>\n<script src="./app.js"></script>\n'
    )
    (site / "app.js").write_text("console.log('app')")
    (site / "vendor" / "chart.umd.min.js").write_text("/*chart*/")
    return site


def test_report_builds_self_contained_html(tmp_path: Path, make_env: EnvFactory) -> None:
    results = tmp_path / "results"
    results.mkdir()
    _envelope_file(results / "2026-06-08.json", "2026-06-08T00:00:00Z", make_env)
    site = _fake_site(tmp_path)
    out = tmp_path / "report.html"
    result = runner.invoke(
        app,
        [
            "report",
            "--results-dir",
            str(results),
            "--site-dir",
            str(site),
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    html = out.read_text()
    assert "/*chart*/" in html  # chart.js vendored inline
    assert 'src="./vendor' not in html  # no external asset refs
    assert "console.log('app')" in html  # app.js inline
    assert "mypy@1.0" in html  # the envelope's data is embedded
