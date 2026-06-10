"""Static guard for site/app.js chart identity keying."""

from __future__ import annotations

from pathlib import Path

_APP_JS = Path(__file__).resolve().parents[2] / "site" / "app.js"


def test_app_js_keys_series_on_checker_id() -> None:
    src = _APP_JS.read_text(encoding="utf-8")

    assert "p.checker_id" in src
    assert "p.checker_id ===" in src


def test_app_js_no_longer_keys_lookup_on_bare_tool_only() -> None:
    src = _APP_JS.read_text(encoding="utf-8")

    assert "p.tool === tool" not in src


def test_app_js_lookup_includes_cores() -> None:
    src = _APP_JS.read_text(encoding="utf-8")

    assert "p.cores" in src
