import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench import cli
from typebench.envman import PrepareError
from typebench.models import PreparedProject

runner = CliRunner()
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SUITE = Path(__file__).parent.parent / "corpus" / "suite.toml"


def _fake_prepared() -> PreparedProject:
    src = _FIXTURES / "pkg_project" / "pkg"
    return PreparedProject(
        name="httpx",
        checkout=str(_FIXTURES / "pkg_project"),
        venv_python="",
        src_roots=(str(src),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        python_platform="linux",
        sha="80960fa31918d7663c3f4c3ad61661cf0e80628f",
        lock_hash="h",
        frozen=(),
        canonical_files=0,
        canonical_loc=0,
        fingerprint="fp",
    )


def _fake_prepare(_entry: object, _cache_root: object) -> PreparedProject:
    return _fake_prepared()


def test_preflight_writes_report_for_known_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "prepare_project", _fake_prepare)
    out = tmp_path / "report.json"
    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "--corpus",
            str(_SUITE),
            "--project",
            "httpx",
            "--tool",
            "stub",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text())
    assert report["project"] == "httpx"
    assert report["canonical_files"] == 0
    assert report["ready"] is True


def test_preflight_unknown_project_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "--corpus",
            str(_SUITE),
            "--project",
            "nope",
            "--output",
            str(tmp_path / "r.json"),
        ],
    )
    assert result.exit_code == 2
    assert "nope" in result.output


def test_run_corpus_mode_derives_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "prepare_project", _fake_prepare)
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--tool",
            "stub",
            "--corpus",
            str(_SUITE),
            "--corpus-project",
            "httpx",
            "--runs",
            "2",
            "--warmup",
            "1",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data["project"] == "httpx"


def test_run_requires_project_or_corpus_project(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["run", "--tool", "stub", "--output", str(tmp_path / "r.json")])
    assert result.exit_code == 2
    assert "project" in result.output.lower()


def _boom_prepare(_entry: object, _cache_root: object) -> PreparedProject:
    raise PrepareError("clone failed: boom")


def test_run_corpus_mode_prepare_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A clone/install/lock-drift failure in corpus run mode must surface as a
    # controlled CLI error (exit 1), like the preflight command — not a traceback.
    monkeypatch.setattr(cli, "prepare_project", _boom_prepare)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--tool",
            "stub",
            "--corpus",
            str(_SUITE),
            "--corpus-project",
            "httpx",
            "--output",
            str(tmp_path / "r.json"),
        ],
    )
    assert result.exit_code == 1
    assert "boom" in result.output
