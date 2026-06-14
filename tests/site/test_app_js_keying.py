"""Static guard for site/app.js chart identity keying.

The published site keys identity by *tool* for the time trend (one line per tool,
version is metadata), but a tool that has two versions on the SAME date (an A/B
comparison) must split into per-checker_id lines so neither version is hidden. The
single-snapshot matrix stays keyed on checker_id (each tool has one version there).
"""

from __future__ import annotations

from pathlib import Path

_APP_JS = Path(__file__).resolve().parents[2] / "site" / "app.js"


def _src() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def test_app_js_trend_groups_by_tool() -> None:
    # The trend collapses version bumps across dates into one line per tool, so the
    # legend stays bounded; a bump must not spawn a permanent new series.
    assert "byTool" in _src()


def test_app_js_trend_splits_same_day_versions() -> None:
    # Guards the regression where a bare-tool lookup drops a second same-day version:
    # a collided tool splits into per-checker_id lines instead of hiding one.
    src = _src()
    assert "split" in src
    assert "p.checker_id === cid" in src


def test_app_js_trend_lookup_scoped_to_budget() -> None:
    # Trend points are looked up inside a single (thread_mode, cores) budget, so a
    # tool match can never pull a row from a different core count.
    assert "budgetKey(p) === budget" in _src()


def test_app_js_matrix_keyed_on_checker_id() -> None:
    # Within one snapshot a tool has a single version, so matrix cells stay keyed on
    # checker_id and the column header carries the version.
    assert "p.checker_id === c" in _src()


def test_app_js_snapshot_options_scoped_to_budget() -> None:
    # The snapshot picker is rebuilt per selected budget so it can never name a date
    # the table is not showing (a budget may lack a later run's core sweep).
    assert "syncSnapshots" in _src()


def test_app_js_checker_count_counts_tools_not_versions() -> None:
    # The "checkers" stat counts distinct tools, not checker_ids — bumping ty's
    # version must not inflate the count from 4 to 5.
    src = _src()
    assert "p.checker_id)).size" not in src
    assert "map((p) => p.tool)" in src
