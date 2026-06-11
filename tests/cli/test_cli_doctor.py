from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench.cli import app
from typebench.engine.doctor import Tier, ToolCheck

runner = CliRunner()


def _check(name: str, tier: Tier, *, present: bool, healthy: bool | None = None) -> ToolCheck:
    return ToolCheck(
        name=name,
        role="role",
        tier=tier,
        present=present,
        healthy=present if healthy is None else healthy,
        version="1.0" if present else None,
        if_absent="consequence",
        install_hint="hint",
    )


def test_doctor_lists_every_row_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = [
        _check("uv", Tier.REQUIRED, present=True),
        _check("pyright", Tier.PER_TOOL, present=True),
        _check("hyperfine", Tier.OPTIONAL, present=False),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    for name in ("uv", "pyright", "hyperfine"):
        assert name in result.stdout


def test_doctor_check_fails_when_required_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = [
        _check("uv", Tier.REQUIRED, present=False),
        _check("git", Tier.REQUIRED, present=True),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    assert runner.invoke(app, ["doctor", "--check"]).exit_code == 1


def test_doctor_check_passes_when_only_optional_or_per_tool_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canned = [
        _check("uv", Tier.REQUIRED, present=True),
        _check("git", Tier.REQUIRED, present=True),
        _check("node", Tier.PER_TOOL, present=False),
        _check("tokei", Tier.OPTIONAL, present=False),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    assert runner.invoke(app, ["doctor", "--check"]).exit_code == 0


def test_doctor_default_exits_zero_even_with_required_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canned = [_check("uv", Tier.REQUIRED, present=False)]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    assert runner.invoke(app, ["doctor"]).exit_code == 0


def test_doctor_renders_degraded_not_ok_for_present_but_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canned = [_check("pyright", Tier.PER_TOOL, present=True, healthy=False)]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    result = runner.invoke(app, ["doctor"])
    assert "DEGRADED" in result.stdout
    assert "ok " not in result.stdout


def test_doctor_prints_install_hint_for_unhealthy_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    # A MISSING/DEGRADED row must surface how to fix it; healthy rows must not.
    canned = [
        _check("uv", Tier.REQUIRED, present=True),
        _check("tokei", Tier.OPTIONAL, present=False),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    result = runner.invoke(app, ["doctor"])
    assert "to fix:" in result.stdout
    fix_section = result.stdout.split("to fix:", 1)[1]
    assert "tokei" in fix_section  # missing tool's remediation listed
    assert "uv" not in fix_section  # healthy tool not listed in remediation


def test_doctor_groups_rows_by_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    # The table must surface each tier so a reader can tell a REQUIRED miss
    # (breaks the run) from an OPTIONAL one (degrades gracefully) at a glance.
    canned = [
        _check("uv", Tier.REQUIRED, present=True),
        _check("mypy", Tier.PER_TOOL, present=True),
        _check("tokei", Tier.OPTIONAL, present=False),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    out = runner.invoke(app, ["doctor"]).stdout
    assert "required" in out
    assert "per-tool" in out
    assert "optional" in out


def test_doctor_prints_summary_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    # One healthy, one degraded (present but not usable), one missing.
    canned = [
        _check("uv", Tier.REQUIRED, present=True),
        _check("pyright", Tier.PER_TOOL, present=True, healthy=False),
        _check("tokei", Tier.OPTIONAL, present=False),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    out = runner.invoke(app, ["doctor"]).stdout
    assert "1 healthy" in out
    assert "1 degraded" in out
    assert "1 missing" in out


def test_doctor_status_uses_glyphs(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = [
        _check("uv", Tier.REQUIRED, present=True),
        _check("pyright", Tier.PER_TOOL, present=True, healthy=False),
        _check("tokei", Tier.OPTIONAL, present=False),
    ]
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: canned)
    out = runner.invoke(app, ["doctor"]).stdout
    assert "✓" in out  # check mark for healthy
    assert "✗" in out  # cross for missing
    assert "⚠" in out  # warning for degraded


def test_doctor_with_config_lists_configured_checkers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "typebench.toml"
    config.write_text('[[checker]]\ntool = "mypy"\nversion = "1.18.2"\n', encoding="utf-8")

    def fake_checker_cache_status(*_args: object) -> tuple[str, str | None]:
        return ("will-build", None)

    monkeypatch.setattr("typebench.cli.checker_cache_status", fake_checker_cache_status)
    monkeypatch.setattr("typebench.cli.run_doctor", lambda: [])
    result = runner.invoke(app, ["doctor", "-c", str(config)])
    assert result.exit_code == 0
    assert "mypy@1.18.2" in result.stdout
    assert "will-build" in result.stdout
